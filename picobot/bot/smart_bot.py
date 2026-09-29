"""SmartBot: closed-loop grinding driven by minimap perception.

Replaces blind macro playback with a perceive -> decide -> act loop.
The bot runs a *rotation*: a graph of anchors (farming spots) connected
by legs (walk / flash-jump / climb steps) read from config or a map file.
An FSM composes the behaviour (GRIND patrol / TRAVEL leg /
PAUSE), movement is goal-directed, and skills fire off per-skill
cooldowns — the way a player actually works a map.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Dict, List, Optional, Tuple

from ..vision.game_window import GameWindow
from ..vision.minimap import (
    MinimapAnalyzer,
    platform_row_at,
    platform_span_at,
)
from ..vision.screen import ScreenGrabber
from .base import BotBase
from .config import BotConfig
from .identity import MapIdentity
from .inputs import HidController
from .monitor import MapMonitor
from .navgraph import GraphCache, NavGraph
from .navigator import Navigator
from .patrol import Patrol
from .reach import ReachModel, base_reach
from .machine import Machine
from .maps import MapEntry, MapStore
from .rotation import Anchor, Rotation, Step, resolve_coord
from .skills import Skill, SkillBook
from .timing import human_between, key_gap, new_session, release_lag

logger = logging.getLogger(__name__)

_UNSET = object()


class SmartBot(BotBase):
    _hop_px: float = 14.0   # learned minimap px one flash weave covers
    _patrol: Optional[Patrol] = None
    _reach: Optional[ReachModel] = None
    _roam_origin = None
    def __init__(
        self,
        controller: HidController,
        window_title: str,
        config: BotConfig | None = None,
        *,
        minimap: Optional[MinimapAnalyzer] = None,
        monitor: Optional[MapMonitor] = None,
        identity: Optional[MapIdentity] = None,
        reach: Optional[ReachModel] = None,
        log_callback=None,
        notify_callback=None,
        event_bus=None,
    ) -> None:
        config = config or BotConfig()
        window = GameWindow(window_title)
        screen = ScreenGrabber()
        # The host shares its analyzer + identity so region provenance
        # and the resolved map carry into the bot.
        minimap = minimap or MinimapAnalyzer(
            colors=config.minimap_colors,
            region=config.minimap_region,
            marker_inset=config.marker_inset_px,
        )
        super().__init__(
            controller, window, screen, minimap, config,
            log_callback=log_callback, notify_callback=notify_callback,
            event_bus=event_bus,
        )
        self.skills = SkillBook(config.skills)
        self.identity = identity or MapIdentity(
            MapStore(config.maps_dir),
            pin=config.active_map,
            ocr_enabled=config.name_ocr,
            on_event=lambda k, m: self.event(k, m),
        )
        self.maps = self.identity.store
        self._own_monitor = monitor is None
        self.monitor = monitor or MapMonitor(
            window, minimap, self.identity,
            config=config, on_event=lambda k, m: self.event(k, m),
        )
        self._identity_version = -1
        self._nav_cache = GraphCache()
        self._reach = reach or ReachModel(
            base_reach(config), path=config.reach_path()
        )
        self._map: Optional[MapEntry] = None
        self._anchor_idx = 0
        self._travel_target: Optional[int] = None
        self._arrive_pending: List[Tuple[Skill, float]] = []
        self._minimap_warned = False
        self._last_up_skill = 0.0
        self._weave_dir: Optional[str] = None
        self._weave_bounds: Optional[Tuple[int, int]] = None
        # Checkpoint route: anchor indices left to visit, nearest-
        # neighbour ordered from the player's position; replanned when
        # empty so the current location seeds the next loop.
        self._route: List[int] = []
        self._ckpt_idx: Optional[int] = None
        self._ckpt_deadline: float = 0.0
        # Unreachable-checkpoint backoff: a checkpoint whose leg failed
        # (or that stalled the patrol) is held out of route planning for
        # a while so a bad anchor can't trap the loop.
        self._ckpt_ban: Dict[int, float] = {}
        # Cooldown-spaced gate for attack presses woven into travel legs.
        self._travel_attack_at = 0.0
        # Latest-observation snapshot consumed by the dashboard streamer.
        self.viz: dict = {
            "state": "IDLE", "img": None, "player": None, "hazard": None,
            "target": None, "map": None, "title": None, "route": None,
        }

    def _viz_state(self, name: str) -> None:
        self.viz["state"] = name
        self.event("fsm", name)

    def viz_snapshot(self) -> dict:
        """Read-only snapshot of what the bot currently sees/does."""
        snap = dict(self.viz)
        rot = self.effective_rotation()
        region = self.minimap.region
        if rot.anchors and region:
            snap["anchors"] = [
                (self._rx(a.x), self._ry(a.y)) for a in rot.anchors
            ]
        return snap

    @classmethod
    def connect(
        cls,
        port: str,
        window_title: str,
        config: BotConfig | None = None,
        **kwargs,
    ) -> "SmartBot":
        """Standalone path: open our own serial session on ``port``."""
        from ..transport import SerialManager

        manager = SerialManager(port)
        manager.open()
        if not manager.wait_for_ready(timeout=12.0):
            manager.close()
            raise RuntimeError(f"Pico on {port} did not signal PICO_READY")
        return cls(
            HidController.from_serial_manager(manager),
            window_title, config, **kwargs,
        )

    # -- Perception --------------------------------------------------------------
    def _window_capture(self):
        """BGR capture of the game window's client area, or None."""
        l, t, r, b = self.window.client_rect()
        return self.screen.capture((l, t, r - l, b - t))

    def minimap_frame(self):
        """BGR capture of the minimap, or None until the panel is located.

        Map-change detection, panel location and title reads run on the
        :class:`MapMonitor` thread; this applies identity changes on the
        bot thread.
        """
        self._sync_map()
        region = self.minimap.region
        if region is None:
            if not self._minimap_warned and not self.minimap.loading:
                self.log(
                    "Minimap not found — check the minimap is open "
                    "and minimap_colors.border matches its frame."
                )
                self._minimap_warned = True
            return None
        self._minimap_warned = False
        x, y, w, h = region
        img = self.screen.capture(
            (self.window.client_left + x, self.window.client_top + y, w, h)
        )
        return self._stash_frame(img)

    def _stash_frame(self, img):
        self.viz["img"] = img
        return img

    def _platform_segments_px(self) -> list:
        """The map's hand-drawn platform segments, in minimap px."""
        entry = self._current_map_entry()
        if entry is None or not entry.platforms:
            return []
        w, h = self._region_wh()
        if not w or not h:
            return []
        return [
            (s[0] * w, s[1] * h, s[2] * w, s[3] * h)
            for s in entry.platforms
        ]

    # -- Map-name OCR ----------------------------------------------------------
    def name_region(self):
        """Client-area rect of the title band, or None."""
        return self.monitor.name_region()

    def name_img(self):
        region = self.name_region()
        if region is None:
            return None
        x, y, w, h = region
        return self.screen.capture(
            (self.window.client_left + x, self.window.client_top + y, w, h)
        )

    def _note_pos(self, pos) -> None:
        """Feed the host's platform-fit diagnostic (if attached)."""
        fit = getattr(self, "platfit", None)
        if fit is None:
            return
        entry = self._current_map_entry()
        fit.observe(
            entry.name if entry else None,
            entry.platforms if entry else None,
            self.minimap.region,
            pos,
        )

    def player_pos(self, img=None) -> Optional[Tuple[int, int]]:
        if img is None:
            img = self.minimap_frame()
        if img is None:
            self.viz["player"] = None
            return None
        pos = self.minimap.player_pos(img)
        self.viz["player"] = pos
        return pos

    def rune_present(self) -> bool:
        img = self.minimap_frame()
        return bool(img is not None and self.minimap.has_rune(img))

    def rune_pos(self) -> Optional[Tuple[int, int]]:
        img = self.minimap_frame()
        if img is None:
            return None
        return self.minimap.rune_pos(img)

    def other_players_present(self) -> bool:
        img = self.minimap_frame()
        return bool(img is not None and self.minimap.has_other_players(img))

    def check_lie_detector(self):
        """Future hook: detect a human-verification / lie-detector prompt.

        TODO: capture the screen area where the prompt appears and match it
        against template images via ``vision.template_rect``. Until templates
        exist this always returns None (keep ``pause_on_lie_detector`` off).
        """
        return None

    # -- Safety ------------------------------------------------------------------
    def unsafe_reason(self, img=_UNSET) -> Optional[str]:
        """Why the bot should pause right now, or None if the map is clean."""
        if self.config.pause_on_lie_detector and self.check_lie_detector():
            return "verification prompt"
        if img is _UNSET:
            img = self.minimap_frame()
        return self._img_hazard(img)

    def _img_hazard(self, img) -> Optional[str]:
        """Hazard checks that all run off one minimap capture."""
        cfg = self.config
        if img is None:
            self.viz["hazard"] = None
            return None
        reason = None
        rune = None
        if self.minimap.loading:
            reason = "map transfer (loading screen)"
        else:
            rune = self.minimap.rune_pos(img)
            if cfg.stop_when_rune_appears and rune is not None:
                reason = "rune"
            elif cfg.stop_when_players_appear and self.minimap.has_other_players(img):
                reason = "other players"
        self.viz["rune"] = rune
        if reason != self.viz["hazard"] and reason is not None:
            self.event("safety", reason)
        self.viz["hazard"] = reason
        return reason

    # -- Maps & rotation -----------------------------------------------------------
    def effective_rotation(self) -> Rotation:
        if self._map is not None:
            return self._map.rotation
        return self.config.rotation

    def rotation_active(self) -> bool:
        return bool(self.effective_rotation().anchors)

    def _sync_map(self) -> None:
        """Apply the shared identity's latest resolution (bot thread)."""
        ident = self.identity
        if ident.version == self._identity_version:
            return
        self._identity_version = ident.version
        res = ident.current
        entry = ident.entry()
        self.viz["title"] = res.title
        if (entry.name if entry else None) != (self._map.name if self._map else None):
            self._map = entry
            self.viz["map"] = entry.name if entry else None
            merged = dict(self.config.skills)
            if entry is not None:
                merged.update(entry.skills)
                self.log(f"Map: {entry.name} ({res.via})")
            self.skills = SkillBook(merged)
            self._anchor_idx = 0
            self._travel_target = None
            self._weave_dir = None
            self._weave_bounds = None
            self._route = []
            if self._patrol is not None:
                self._patrol.reset()
        if res.via == "ocr":
            self._apply_stored_layout(entry)

    def _apply_stored_layout(self, entry) -> None:
        """Reinstall a title-verified map's remembered region, so the
        normalized anchors/platforms line up with the layout they were
        recorded in. Skipped when ``minimap_region`` is pinned in config.
        """
        if entry is None or not entry.minimap_region:
            return
        if self.config.minimap_region:
            return
        try:
            region = tuple(int(v) for v in entry.minimap_region)
        except (TypeError, ValueError):
            return
        if len(region) != 4 or tuple(self.minimap.region or ()) == region:
            return
        self.minimap.set_region(region)
        self.event("vision", f"layout restored: {list(region)}")

    def _region_wh(self) -> Tuple[int, int]:
        region = self.minimap.region
        return (region[2], region[3]) if region else (200, 150)

    def _rx(self, value: float) -> int:
        return resolve_coord(value, self._region_wh()[0])

    def _ry(self, value: float) -> int:
        return resolve_coord(value, self._region_wh()[1])

    # -- Movement ----------------------------------------------------------------
    def up_jump(self) -> bool:
        """Vertical boost: rope lift when it's ready, else jump + Up + jump
        — never waits on the rope-lift cooldown. False only when the
        window isn't focused."""
        if not self.is_window_focused():
            return False
        if self.rope_lift():
            return True
        jk = self.config.jump_key
        self.hid.press(jk)
        self.sleep(human_between(0.1, 0.07, 0.14))
        self.hid.key_down("up")
        self.hid.key_down(jk)
        self.sleep(human_between(0.5, 0.4, 0.62))
        self.hid.key_up(jk)
        self.hid.key_up("up")
        self.sleep(human_between(0.3, 0.22, 0.45))
        return True

    def rope_lift_remaining(self) -> float:
        """Seconds until the rope-lift skill is usable (inf = none bound)."""
        if not self.config.up_jump_skill_key:
            return float("inf")
        last = getattr(self, "_last_up_skill", 0.0)
        return max(0.0, self.config.up_jump_skill_cooldown - (time.time() - last))

    def rope_lift(self) -> bool:
        """Press the rope-lift skill; False when unbound or cooling down."""
        if self.rope_lift_remaining() > 0 or not self.is_window_focused():
            return False
        self._last_up_skill = time.time()
        self.hid.press(self.config.up_jump_skill_key)
        self.sleep(human_between(0.45, 0.35, 0.6))
        return True

    def _up_flash(self, direction: Optional[str] = None) -> None:
        """Jump, then Up + jump mid-air — the upward flash jump. Holding a
        ``direction`` adds the sideways drift of a diagonal takeoff."""
        jk = self._flash_key()
        if direction:
            self.hid.key_down(direction)
        try:
            self.hid.press(jk)
            self._lead_sleep(self._repress(self.config.flash_repress_seconds * 0.8))
            self.hid.key_down("up")
            self.hid.press(jk)
            self.hid.key_up("up")
            self._after_flash(0.36)
        finally:
            if direction:
                self.hid.key_up(direction)

    def _up_side_flash(self, direction: str) -> None:
        """Double flash: upward flash, then a sideways flash mid-air —
        up and over onto a higher platform across a gap."""
        jk = self._flash_key()
        self.hid.key_down(direction)
        try:
            self.hid.press(jk)
            self._lead_sleep(self._repress(self.config.flash_repress_seconds * 0.8))
            self.hid.key_down("up")
            self.hid.press(jk)
            self.sleep(self._repress(self.config.combo_repress_seconds))
            self.hid.key_up("up")
            self.hid.press(jk)
            self._after_flash(0.33)
        finally:
            self.hid.key_up(direction)

    def _double_flash(self) -> None:
        """Two sideways flashes in one airtime (caller holds the direction)."""
        jk = self._flash_key()
        self.hid.press(jk)
        self.sleep(human_between(0.17, 0.11, 0.26))
        self.hid.press(jk)
        self.sleep(self._repress(self.config.combo_repress_seconds))
        self.hid.press(jk)
        self._after_flash(0.33)

    def _current_map_entry(self) -> Optional[MapEntry]:
        """The resolved map, refreshed against the store.

        Dashboard boundary edits save + reload the map file, which swaps
        the ``MapEntry`` objects — ``self._map`` can point at the stale
        pre-reload instance, so walls set mid-run would be invisible
        until the next identity change. This re-reads the store's copy.
        """
        if self._map is None:
            return None
        return self.maps.get(self._map.name) or self._map

    @property
    def reach(self) -> ReachModel:
        if self._reach is None:
            self._reach = ReachModel(base_reach(self.config))
        return self._reach

    @property
    def patrol(self) -> Patrol:
        if self._patrol is None:
            self._patrol = Patrol(self)
        return self._patrol

    def _nav_graph(self) -> Optional[NavGraph]:
        """Movement graph of the current map's drawn platforms, or None."""
        cfg = self.config
        return self._nav_cache.get(
            self._current_map_entry(), self.minimap.region, self.reach,
            cfg.rope_penalty,
            allow_flash=cfg.class_travel == "flash" and cfg.flash_jump_enabled,
            allow_teleport=cfg.class_travel == "teleport"
            and bool(cfg.teleport_key),
        )

    def _walk_span(self, x: float, y: float):
        """Span travel flashes may use: the drawn platform under the
        player — its ends are the boundaries."""
        return platform_span_at(self._platform_segments_px(), x, y)

    def down_jump(self, img=None) -> None:
        if not self.is_window_focused():
            return
        self.hid.key_down("down")
        self.hid.press(self.config.jump_key)
        self.sleep(human_between(0.1, 0.07, 0.14))
        self.hid.key_up("down")
        self.sleep(human_between(0.4, 0.3, 0.55))

    def _flash_key(self) -> str:
        return self.config.flash_jump_key or self.config.jump_key

    def _flash_hop(self) -> None:
        """One flash jump (caller holds the direction), attacks woven in."""
        jk = self._flash_key()
        self.hid.press(jk)
        self.sleep(self._repress(self.config.flash_repress_seconds))
        self.hid.press(jk)
        self._after_flash(0.34)

    def _lead_sleep(self, seconds: float) -> None:
        """Sleep before a key that leads the next press (Up before the
        re-press jump): the HID spacing will put a gap between the two,
        so take one out here — the jump still lands on time."""
        self.sleep(max(0.0, seconds - key_gap()))

    def _repress(self, mean: float) -> float:
        """Log-normal gap around ``mean`` for a mid-air re-press."""
        return human_between(mean, mean * 0.65, mean * 1.5)

    def _after_flash(self, airtime: float) -> None:
        """The movement rule's tail: once a flash has triggered, weave 1–2
        attacks, then ride out the rest of the airtime. Classes that
        can't attack airborne attack after landing instead."""
        if not self.config.air_attacks:
            self.sleep(human_between(airtime, airtime * 0.6, airtime * 1.6))
            self._weave_attacks()
            return
        self.sleep(human_between(0.09, 0.05, 0.15))
        n = self._weave_attacks()
        rest = airtime if n < 2 else airtime * 0.65
        self.sleep(human_between(rest, rest * 0.6, rest * 1.6))

    def _weave_move(self, direction: str) -> None:
        """One travel weave in the class's style: flash (jump → mid-air
        re-press → attacks), teleport (blink → attacks on landing), or a
        plain walk weave."""
        if self.config.class_travel == "teleport" and self.config.teleport_key:
            self._teleport_weave(direction)
        else:
            self._flash_weave(direction)

    def _teleport_weave(self, direction: str) -> None:
        """Hold ``direction``, blink, then 1–2 attacks — on landing for
        classes that can't attack airborne."""
        self.hid.key_down(direction)
        try:
            self.hid.press(self.config.teleport_key)
            self._last_teleport = time.time()
            self._after_flash(0.3)
        finally:
            self.hid.key_up(direction)

    def _flash_weave(self, direction: str) -> None:
        """The movement rule: hold ``direction`` through jump → flash-jump
        re-press → 1–2 attacks once the flash has triggered (an earlier
        attack eats the re-press window). Keys are released before return
        so a hazard pause can't leave a direction held. Without flash
        jump, attacks weave into a short walk instead."""
        cfg = self.config
        self.hid.key_down(direction)
        try:
            if cfg.flash_jump_enabled:
                jk = self._flash_key()
                self.hid.press(jk)
                self.sleep(self._repress(self.config.flash_repress_seconds))
                self.hid.press(jk)
                self._after_flash(0.34)
            else:
                self._weave_attacks()
                self.sleep(human_between(0.4, 0.28, 0.6))
        finally:
            self.hid.key_up(direction)

    def _weave_attacks(self) -> int:
        """1 attack, or 2 with ``weave_double_chance``; returns how many."""
        n = 2 if random.random() < self.config.weave_double_chance else 1
        done = 0
        for i in range(n):
            skill = self._pick_attack()
            if skill is None:
                break
            if i:
                self.sleep(human_between(0.13, 0.08, 0.22))
            done += bool(self._use_skill(skill))
        return done

    def move_to_point(
        self,
        target_x: int,
        target_y: int,
        threshold: Optional[int] = None,
        *,
        style: str = "walk",
        flat: bool = False,
    ) -> bool:
        """Navigate on the minimap toward (x, y); True if reached.

        ``flat`` treats the leg as horizontal: no vertical jumps, arrival
        on x alone — walk legs never stall on a few px of drawing error.

        ``walk`` holds the direction key. ``flash``/``mixed``: travel by
        flash weaves (jump → flash → 1–2 attacks) until inside
        ``walk_band_px`` of the target, then walk the last stretch for a
        precise stop. Hop distance is learned from observed hops. Aborts
        (False) on hazards, focus loss, or stop.
        """
        kit = self.config.class_travel
        threshold = threshold or self.config.nav_threshold_px
        # Hysteresis band: release the direction inside `stop_band`, only
        # acquire it beyond `start_band` — stops left/right flapping on the
        # target column.
        stop_band = threshold * 0.75
        start_band = threshold * 1.5
        flash_ok = (
            (self.config.flash_jump_enabled if kit == "flash"
             else bool(self.config.teleport_key))
            and style in ("flash", "mixed")
        )
        self.viz["target"] = (target_x, target_y)
        self.event("nav", f"→ ({target_x}, {target_y})", {"style": style})
        self.log(f"Navigating to ({target_x}, {target_y}) [{style}]")
        held_dir = None
        last_vert_jump = 0.0
        escapes = 0
        snapped = False
        vert_ref = None      # y at the previous vertical jump attempt
        vert_fails = 0       # consecutive jumps that produced no y change

        def vert_stuck(verb: str) -> bool:
            """Verdict when vertical jumps stop making progress."""
            if abs(dx) <= start_band:
                self.log(
                    f"Vertically blocked ({verb}) — horizontally aligned, "
                    "accepting position"
                )
                self.viz["target"] = None
                return True
            self.log(f"Vertically blocked ({verb}) — aborting leg")
            return False

        def sync_dir(new_dir, *, lag: bool = False):
            nonlocal held_dir
            if new_dir == held_dir:
                return
            if held_dir:
                if lag:
                    # Seeing the goal and letting go aren't simultaneous.
                    self.sleep(release_lag())
                self.hid.key_up(held_dir)
            if new_dir:
                self.hid.key_down(new_dir)
            held_dir = new_dir

        stuck = 0
        last = None
        hop_from = None
        try:
            while self.should_continue() and self.is_window_focused():
                img = self.minimap_frame()
                if img is None:
                    self.sleep(0.5)
                    continue
                reason = self._img_hazard(img)
                if reason:
                    self.log(f"Navigation aborted: {reason}")
                    return False
                pos = self.minimap.player_pos(img)
                if pos is None:
                    self.sleep(0.5)
                    continue
                self._note_pos(pos)
                cx, cy = pos
                if hop_from is not None:
                    moved = abs(cx - hop_from)
                    if moved > 2:
                        self._hop_px = 0.7 * self._hop_px + 0.3 * moved
                    hop_from = None
                if not snapped and not flat:
                    # Project the target onto the map's drawn platform —
                    # a target recorded beneath the lowest platform is
                    # unreachable, so snap to the nearest real floor.
                    snapped = True
                    py = platform_row_at(
                        self._platform_segments_px(), target_x, target_y
                    )
                    if py is not None and py != target_y:
                        self.log(
                            f"Target snapped to platform: y {target_y}→{py}"
                        )
                        target_y = py
                        self.viz["target"] = (target_x, target_y)
                dx = target_x - cx
                dy = 0 if flat else target_y - cy
                if abs(dx) <= threshold and abs(dy) <= threshold:
                    self.log("Navigation target reached")
                    self.viz["target"] = None
                    return True
                span = self._walk_span(cx, cy) if flash_ok else None
                room = (
                    span is None
                    or (span[1] - cx > self._hop_px if dx > 0
                        else cx - span[0] > self._hop_px)
                )
                if flash_ok and room and abs(dx) > self._hop_px:
                    sync_dir(None)
                    hop_from = cx
                    self._weave_move("right" if dx > 0 else "left")
                    stuck = stuck + 1 if last == pos else 0
                    last = pos
                    if stuck >= 4:
                        self.log("Flash hops aren't moving — walking instead")
                        flash_ok = False
                        stuck = 0
                    continue
                if flash_ok and abs(dx) > self.config.walk_band_px:
                    # One hop away: a plain jump closes it without the
                    # overshoot that ping-pongs over the target.
                    sync_dir(None)
                    d = "right" if dx > 0 else "left"
                    self.hid.key_down(d)
                    try:
                        self.hid.press(self.config.jump_key)
                        self._after_flash(0.4)
                    finally:
                        self.hid.key_up(d)
                    stuck = stuck + 1 if last == pos else 0
                    last = pos
                    if stuck >= 4:
                        self.log("Approach hops aren't moving — walking instead")
                        flash_ok = False
                        stuck = 0
                    continue
                if not flash_ok:
                    self._travel_attack()
                if held_dir == "right" and dx <= stop_band:
                    sync_dir(None, lag=True)
                elif held_dir == "left" and dx >= -stop_band:
                    sync_dir(None, lag=True)
                if held_dir is None:
                    if dx > threshold:
                        sync_dir("right")
                    elif dx < -threshold:
                        sync_dir("left")
                if abs(dx) <= threshold * 3:
                    # Rate-limit vertical jumps — spamming them never helps.
                    now = time.time()
                    if dy < -threshold and now - last_vert_jump >= self.config.vert_jump_interval:
                        vert_fails = (
                            vert_fails + 1
                            if vert_ref is not None and cy >= vert_ref - 1
                            else 0
                        )
                        if vert_fails >= 2:
                            return vert_stuck("ascend")
                        if self.up_jump() is False:
                            # Up-skill on cooldown — ride it out; a suppressed
                            # press is neither a failed attempt nor a stuck
                            # poll (skipped by the continue).
                            self.sleep(0.05)
                            continue
                        vert_ref = cy
                        last_vert_jump = now
                    elif dy > threshold and now - last_vert_jump >= self.config.vert_jump_interval:
                        vert_fails = (
                            vert_fails + 1
                            if vert_ref is not None and cy <= vert_ref + 1
                            else 0
                        )
                        vert_ref = cy
                        last_vert_jump = now
                        if vert_fails >= 2:
                            return vert_stuck("descend")
                        self.down_jump(img)
                    else:
                        self.sleep(0.05)
                else:
                    self.sleep(0.05)
                stuck = stuck + 1 if last == pos else 0
                last = pos
                if stuck >= self.config.nav_stuck_limit:
                    escapes += 1
                    if escapes >= 2:
                        self.log("Stuck twice — abandoning leg")
                        return False
                    self.log("Stuck — sidestepping")
                    sync_dir(None)
                    back = "left" if dx > 0 else "right"
                    self.hid.key_down(back)
                    self.sleep(human_between(0.4, 0.28, 0.6))
                    self.hid.key_up(back)
                    self.up_jump()
                    stuck = 0
                    last = None
        finally:
            sync_dir(None)
            self.hid.release_all()
            self.viz["target"] = None
        return False

    def teleport_remaining(self) -> float:
        """Seconds until the teleport skill is usable (inf = none bound)."""
        if not self.config.teleport_key:
            return float("inf")
        last = getattr(self, "_last_teleport", 0.0)
        return max(0.0, self.config.teleport_cooldown - (time.time() - last))

    def teleport(self, direction: Optional[str] = None) -> bool:
        """Blink in ``direction`` (mage-style travel); False when unbound
        or cooling down — the planner excludes it while cooling."""
        if self.teleport_remaining() > 0 or not self.is_window_focused():
            return False
        self._last_teleport = time.time()
        self.hid.key_down(direction) if direction else None
        try:
            self.hid.press(self.config.teleport_key)
            self.sleep(human_between(0.3, 0.2, 0.45))
        finally:
            if direction:
                self.hid.key_up(direction)
        return True

    def rope_exit(self, direction: str) -> None:
        """Leap off a rope: hold a direction and jump — there is no single
        button that releases from a rope. Used to recover from accidental
        or failed climbs; the character lands wherever gravity takes it."""
        self.hid.key_down(direction)
        try:
            self.hid.press(self.config.jump_key)
            self.sleep(human_between(0.55, 0.4, 0.75))
        finally:
            self.hid.key_up(direction)

    def probe_rope(self) -> Optional[bool]:
        """Is the player hanging on a rope? Hold Down briefly: on a rope
        the character slides down (True); on ground it only crouches
        (False). Down, not Up — Up on a portal would change maps. None
        when the dot cannot be read."""
        before = self.player_pos()
        if before is None or not self.is_window_focused():
            return None
        self.hid.key_down("down")
        try:
            self.sleep(human_between(0.35, 0.25, 0.5))
        finally:
            self.hid.key_up("down")
        self.sleep(human_between(0.12, 0.08, 0.2))
        after = self.player_pos()
        if after is None:
            return None
        return after[1] - before[1] >= 2

    def rope_up(
        self,
        until_y: float,
        *,
        direction: Optional[str] = None,
        timeout: float = 12.0,
    ) -> bool:
        """Jump-grab a rope and climb it: hold ``direction`` (toward the
        rope) and **up** through the jump, latch on contact, then climb
        until y crosses ``until_y`` (the top platform's row; holding up
        there mounts it). Fails on a missed grab, stall, lost dot, or
        hazard — the caller recovers with ``rope_exit``."""
        target = self._ry(until_y)
        self.log(f"Rope grab {'→ ' + direction if direction else ''}→ y≈{target}")
        deadline = time.time() + timeout
        self.hid.key_down("up")
        if direction:
            self.hid.key_down(direction)
        try:
            self.hid.press(self.config.jump_key)
            self.sleep(human_between(0.12, 0.08, 0.18))
            grabbed = False
            last_y = None
            still = 0
            lost = 0
            while (
                self.should_continue()
                and self.is_window_focused()
                and time.time() < deadline
            ):
                img = self.minimap_frame()
                if img is None:
                    self.sleep(0.3)
                    continue
                reason = self._img_hazard(img)
                if reason:
                    self.log(f"Rope grab aborted: {reason}")
                    return False
                pos = self.minimap.player_pos(img)
                if pos is None:
                    lost += 1
                    if lost >= 10:
                        self.log("Rope grab aborted: position lost")
                        return False
                    self.sleep(0.15)
                    continue
                lost = 0
                y = pos[1]
                if y <= target + 2:
                    # Hold up a moment longer — the mount onto the platform
                    # happens at the rope's top.
                    self.sleep(human_between(0.3, 0.2, 0.45))
                    return True
                if last_y is not None:
                    # 1px of detection jitter is neither progress nor a fall.
                    if y <= last_y - 2:
                        grabbed = True          # climbing (or jump arc)
                        still = 0
                    elif abs(y - last_y) <= 1:
                        still += 1              # latched and holding, or landed
                    else:
                        still = 0               # falling — keep waiting
                        if grabbed and y > target + 30:
                            self.log("Rope grab failed: fell off")
                            return False
                if last_y is None or abs(y - last_y) > 1:
                    last_y = y
                if not grabbed and time.time() - (deadline - timeout) > 2.5:
                    self.log("Rope grab failed: never latched")
                    return False
                if still >= 10:
                    self.log("Rope grab failed: stalled")
                    return False
                self.sleep(0.15)
            self.log("Rope grab failed: timed out")
            return False
        finally:
            self.hid.key_up("up")
            if direction:
                self.hid.key_up(direction)

    def climb(
        self,
        direction: str,
        until_y: float,
        *,
        x: Optional[float] = None,
        timeout: float = 10.0,
    ) -> bool:
        """Hold ``direction`` on a rope until player y crosses ``until_y``.

        If ``x`` is given, aligns to that x first (the rope's position).
        Fails (False) if the grab never latches, progress stalls, the
        player dot vanishes (portal?), or a hazard appears — so a missed
        grab next to a portal pauses the bot instead of changing maps.
        """
        if x is not None:
            pos = self.player_pos()
            if pos is not None and not self.move_to_point(
                self._rx(x), pos[1], threshold=2, style="walk"
            ):
                return False
        target_y = self._ry(until_y)
        self.log(f"Climb {direction} → y≈{target_y}")
        deadline = time.time() + timeout
        grabbed = False
        last_y = None
        still = 0
        lost = 0
        self.hid.key_down(direction)
        try:
            while (
                self.should_continue()
                and self.is_window_focused()
                and time.time() < deadline
            ):
                img = self.minimap_frame()
                if img is None:
                    self.sleep(0.3)
                    continue
                reason = self._img_hazard(img)
                if reason:
                    self.log(f"Climb aborted: {reason}")
                    return False
                pos = self.minimap.player_pos(img)
                if pos is None:
                    lost += 1
                    if lost >= 10:
                        self.log("Climb aborted: position lost")
                        return False
                    self.sleep(0.15)
                    continue
                lost = 0
                y = pos[1]
                if direction == "up" and y <= target_y + 2:
                    return True
                if direction == "down" and y >= target_y - 2:
                    return True
                if last_y is not None and abs(y - last_y) <= 1:
                    still += 1                  # jitter is not progress
                else:
                    still = 0
                    grabbed = last_y is not None or grabbed
                    last_y = y
                if not grabbed and time.time() - (deadline - timeout) > 2.5:
                    self.log("Climb failed: rope never latched")
                    return False
                if grabbed and still >= 10:
                    self.log("Climb failed: stalled on rope")
                    return False
                self.sleep(0.15)
            self.log("Climb failed: timed out")
            return False
        finally:
            self.hid.key_up(direction)

    # -- Rotation ----------------------------------------------------------------
    def begin_travel(self) -> bool:
        """Arm the next leg + map-change baseline.

        Targets come from the patrol route — a level-change handoff
        presets ``_travel_target``; otherwise the route's head is the
        next checkpoint (used by the degenerate <2-anchor path)."""
        self._sync_map()
        rot = self.effective_rotation()
        if not rot.anchors:
            return False
        if self._anchor_idx >= len(rot.anchors):
            self._anchor_idx = 0
        if self._travel_target is not None:
            target = self._travel_target
        elif self._route:
            target = self._route[0]
        else:
            pos = self.player_pos()
            if pos is not None:
                target = min(
                    range(len(rot.anchors)),
                    key=lambda i: (self._rx(rot.anchors[i].x) - pos[0]) ** 2
                    + (self._ry(rot.anchors[i].y) - pos[1]) ** 2,
                )
            else:
                target = self._anchor_idx
        self._travel_target = target
        self.log(f"TRAVEL: → {rot.anchors[target].name}")
        return True

    def run_travel(self) -> bool:
        """Execute the current leg; True if the anchor was reached."""
        rot = self.effective_rotation()
        target = self._travel_target
        if target is None:
            return False
        anchor = rot.anchors[target]
        goal = (self._rx(anchor.x), self._ry(anchor.y))
        graph = self._nav_graph()
        # Hand-authored legs win — they can express ropes the graph lacks.
        recorded = rot.legs.get((self._anchor_idx, target))
        if recorded is None and graph is not None and graph.locate(*goal) is not None:
            try:
                ok = Navigator(self, graph).go(goal) and self.unsafe_reason() is None
            finally:
                self.viz["route"] = None
        else:
            ok = self._run_leg(rot.leg_steps(self._anchor_idx, target))
        self._travel_target = None
        if ok:
            self._anchor_idx = target
            self._ckpt_ban.pop(target, None)
        else:
            # Hold the failed checkpoint out of route planning for a
            # bit — without recorded legs an unreachable target would
            # otherwise retry-forever.
            self._ckpt_ban[target] = time.time() + 45.0
            self.log(
                "Leg incomplete — checkpoint held out of routes for a bit"
            )
        return ok

    def _run_leg(self, steps: List[Step]) -> bool:
        rot = self.effective_rotation()
        jitter = rot.position_jitter_px
        for step in steps:
            if not self.should_continue() or not self.is_window_focused():
                return False
            if step.kind == "walk_to":
                jx = random.randint(-jitter, jitter) if jitter else 0
                jy = random.randint(-jitter, jitter) if jitter else 0
                style = step.style or rot.travel_style or self.config.travel_style
                if not self.move_to_point(
                    self._rx(step.x) + jx, self._ry(step.y) + jy, style=style
                ):
                    return False
            elif step.kind == "climb":
                if not self.climb(step.direction, step.until_y, x=step.x):
                    return False
            elif step.kind == "up_jump":
                self.up_jump()
            elif step.kind == "down_jump":
                self.down_jump()
            elif step.kind == "wait":
                if self.sleep(step.seconds):
                    return False
        return self.unsafe_reason() is None

    # -- Grind ---------------------------------------------------------------------
    def begin_grind(self) -> None:
        """Enter GRIND at the current anchor: face it and queue its
        arrival skills. Farming runs until a tick hands a target to
        TRAVEL (``travel_due``)."""
        rot = self.effective_rotation()
        now = time.time()
        self._weave_dir = None
        self._weave_bounds = None
        self._roam_origin = None
        if rot.anchors and self._anchor_idx < len(rot.anchors):
            anchor = rot.anchors[self._anchor_idx]
            if anchor.face:
                self.hid.press(anchor.face)
            self._arrive_pending = self._arrival_skills(anchor, now)
        else:
            self._arrive_pending = []

    def _arrival_skills(self, anchor, now: float) -> list:
        """(skill, deadline) pairs to fire at ``anchor``: its ``on_arrive``
        list, or — for anchors placed without one — every registered
        summon (cast at the next checkpoint once off cooldown)."""
        names = anchor.on_arrive or [
            s.name for s in self.skills.skills.values() if s.kind == "summon"
        ]
        return [
            (s, now + s.wait_on_arrival)
            for name in names
            if (s := self.skills.get(name)) is not None
        ]

    def travel_due(self) -> bool:
        return self._travel_target is not None

    def grind_tick(self) -> None:
        """One farming tick: arrival skills, buffs, then patrol/weave."""
        now = time.time()
        for skill, deadline in list(self._arrive_pending):
            if self.skills.ready(skill.name, now):
                self._use_skill(skill)
                self._arrive_pending.remove((skill, deadline))
            elif now > deadline:
                self._arrive_pending.remove((skill, deadline))
        for buff in self.skills.due_buffs():
            if not self.should_continue():
                break
            if self._use_skill(buff):
                self.sleep(human_between(0.3, 0.2, 0.45))
        if len(self.effective_rotation().anchors) >= 2:
            self.patrol.tick()
        else:
            self._weave_attack()

    def _weave_attack(self) -> None:
        """Move while farming: bounce across the anchor's platform and
        weave attack presses into the flash-hop — jump, attack mid-air,
        jump again (the FJ re-press). Players don't stand still on a
        farm; neither should the bot. The hop is bounded inside one tick
        (keys released before return) so a hazard pause can't leave a
        direction held. A player standing off the anchor's platform
        hands the anchor to TRAVEL instead."""
        rot = self.effective_rotation()
        if not rot.anchors or self._anchor_idx >= len(rot.anchors):
            self.grind_once()
            return
        anchor = rot.anchors[self._anchor_idx]
        self._weave_around(
            self._rx(anchor.x), self._ry(anchor.y), home=self._anchor_idx
        )

    def _weave_around(
        self, ax: float, ay: float, home: Optional[int] = None
    ) -> None:
        """One flash weave bouncing across the platform under (ax, ay).
        With ``home`` set, a player away from that platform queues a
        TRAVEL back to anchor ``home`` rather than weaving — or, while a
        failed leg has ``home`` banned, halts until the ban lapses."""
        cfg = self.config
        img = self.minimap_frame()
        pos = self.minimap.player_pos(img) if img is not None else None
        self.viz["player"] = pos
        if home is not None and pos is not None and self._off_home(pos, (ax, ay)):
            if self._ckpt_ban.get(home, 0.0) > time.time():
                self._no_path_break()
            else:
                self._travel_target = home
            return
        # Platform bounds: the drawn platform segment under the anchor,
        # cached per dwell; ±weave_range fallback when none is drawn.
        if self._weave_bounds is None:
            self._weave_bounds = platform_span_at(
                self._platform_segments_px(), ax, ay
            )
        m = cfg.weave_edge_margin_px
        lo, hi = ax - cfg.weave_range_px, ax + cfg.weave_range_px
        if self._weave_bounds is not None:
            blo, bhi = self._weave_bounds[0] + m, self._weave_bounds[1] - m
            if blo < bhi:
                lo, hi = blo, bhi
        direction = self._weave_dir or random.choice(("left", "right"))
        if pos is not None:
            # The drawn platform's ends are the boundaries — bounce before
            # a hop could leave the platform.
            if pos[0] <= lo:
                direction = "right"
            elif pos[0] >= hi:
                direction = "left"
            elif random.random() < 0.06:
                direction = "left" if direction == "right" else "right"
            room_l = pos[0] - lo
            room_r = hi - pos[0]
            if direction == "left" and room_l < self._hop_px <= room_r:
                direction = "right"
            elif direction == "right" and room_r < self._hop_px <= room_l:
                direction = "left"
            elif room_l < self._hop_px and room_r < self._hop_px:
                direction = "left" if room_l > room_r else "right"
        self._weave_dir = direction
        self._weave_hop(direction)

    def _off_home(self, pos, goal) -> bool:
        """``pos`` stands where a weave can't reach ``goal``: another
        drawn platform, or — with none drawn — another level."""
        if self._nav_graph() is not None:
            return self._other_platform(pos, goal)
        return abs(goal[1] - pos[1]) > max(8, self.config.nav_threshold_px * 2)

    def _no_path_break(self) -> None:
        """No path home: halt rather than weave off-plan."""
        now = time.time()
        if now - getattr(self, "_break_logged", 0.0) > 5.0:
            self._break_logged = now
            self.log("No planned path home — taking a break")
        self.sleep(2.0)

    def _weave_hop(self, direction: str) -> None:
        self._weave_move(direction)

    def _plan_route(self, pos) -> None:
        """Order the anchors into a checkpoint route from ``pos``.

        Nearest-neighbour ordering seeded at the player's position — the
        current location is the route's origin, so anchors already within
        arrival reach are dropped (their arrival would be instant and
        would double-fire on_arrive). The route sweeps the map instead of
        walking the declared anchor order."""
        rot = self.effective_rotation()
        cfg = self.config
        band = max(8, cfg.nav_threshold_px * 2)
        px = [(self._rx(a.x), self._ry(a.y)) for a in rot.anchors]
        remaining = [
            i for i, (ax, ay) in enumerate(px)
            if abs(ax - pos[0]) > cfg.nav_threshold_px
            or abs(ay - pos[1]) > band
        ]
        now = time.time()
        self._ckpt_ban = {
            i: t for i, t in self._ckpt_ban.items() if t > now
        }
        remaining = [i for i in remaining if i not in self._ckpt_ban]
        route = []
        cur = pos
        while remaining:
            nxt = min(
                remaining,
                key=lambda i: (px[i][0] - cur[0]) ** 2
                + (px[i][1] - cur[1]) ** 2,
            )
            route.append(nxt)
            remaining.remove(nxt)
            cur = px[nxt]
        self._route = route
        self._ckpt_idx = None
        self.log(
            "Patrol route: "
            + " → ".join(rot.anchors[i].name for i in route)
        )

    def _patrol_tick(self) -> None:
        """Checkpoint patrol: weave-attack toward the route's head.

        Every tick attacks — arrival bookkeeping and cross-level handoffs
        no longer burn a hop. Checkpoints on another level set
        ``_travel_target`` so TRAVEL runs the leg; a
        head that can't be reached in ~20s is skipped (and briefly
        banned) so a bad checkpoint can't stall the loop forever."""
        rot = self.effective_rotation()
        if len(rot.anchors) < 2:
            self._weave_attack()
            return
        cfg = self.config
        img = self.minimap_frame()
        pos = self.minimap.player_pos(img) if img is not None else None
        self.viz["player"] = pos
        if pos is None:
            self._blind_wait()
            return
        now = time.time()
        level_band = max(8, cfg.nav_threshold_px * 2)
        # Resolve the route head: pop reached checkpoints, hand off
        # cross-level ones to TRAVEL. Bounded by the anchor count.
        idx = target = tx = ty = None
        for _ in range(len(rot.anchors) + 1):
            if not self._route:
                self._plan_route(pos)
                if not self._route:
                    # Every anchor is already within reach — weave here.
                    self._weave_attack()
                    return
            idx = self._route[0]
            target = rot.anchors[idx]
            tx, ty = self._rx(target.x), self._ry(target.y)
            if abs(ty - pos[1]) > level_band or self._other_platform(pos, (tx, ty)):
                # Different level — TRAVEL owns this transition.
                self._route.pop(0)
                self._ckpt_idx = None
                self._travel_target = idx
                return
            if abs(tx - pos[0]) > cfg.nav_threshold_px:
                break  # not there yet — head for it below
            self._route.pop(0)
            self._anchor_idx = idx
            self._ckpt_idx = None
            self._arrive_pending = self._arrival_skills(target, now)
            if target.face:
                self.hid.press(target.face)
            self.log(f"Checkpoint: {target.name}")
        else:
            self._weave_attack()
            return
        # Stall guard: a head that won't arrive in time is skipped so a
        # bad checkpoint can't hold the route forever.
        if self._ckpt_idx != idx:
            self._ckpt_idx = idx
            self._ckpt_deadline = now + 20.0
        elif now > self._ckpt_deadline:
            self.log(f"Checkpoint {target.name} unreachable — skipping")
            self._route.pop(0)
            self._ckpt_ban[idx] = now + 30.0
            self._ckpt_idx = None
            return
        # Head toward the checkpoint; the drawn platform's ends bound it.
        dx = tx - pos[0]
        direction = self._weave_dir or ("right" if dx >= 0 else "left")
        if dx > cfg.nav_threshold_px:
            direction = "right"
        elif dx < -cfg.nav_threshold_px:
            direction = "left"
        self._weave_dir = direction
        self._weave_hop(direction)

    def _other_platform(self, pos, goal) -> bool:
        """Both points are on drawn platforms, but different ones."""
        graph = self._nav_graph()
        if graph is None:
            return False
        a, b = graph.locate(*pos), graph.locate(*goal)
        return a is not None and b is not None and a != b

    def _travel_attack(self) -> None:
        """Weave one attack press into a travel leg, cooldown-spaced.

        Registered attack skills fire while the bot walks/hops between
        checkpoints — nothing registered means nothing fires. The ~0.4s
        gate keeps a 0-cooldown key from becoming a 20Hz spam loop; the
        not-ready path doesn't sleep (the nav loop owns the pacing).
        """
        now = time.time()
        if now < self._travel_attack_at:
            return
        skill = self._pick_attack()
        if skill is not None and self._use_skill(skill):
            self._travel_attack_at = now + human_between(0.43, 0.33, 0.6)

    # -- Attacks & skills -----------------------------------------------------------
    def _use_skill(self, skill: Skill) -> bool:
        if not self.is_window_focused() or not self.should_continue():
            return False
        if self.hid.press(skill.key, skill.hold):
            self.skills.mark_used(skill.name)
            self.log(f"Skill: {skill.name}")
            self.event("skill", skill.name, {"key": skill.key})
            return True
        return False

    def _pick_attack(self) -> Optional[Skill]:
        ready = self.skills.ready_attacks()
        if not ready:
            return None
        cooldown_ready = [s for s in ready if s.cooldown > 0]
        return random.choice(cooldown_ready or ready)

    def grind_once(self) -> None:
        """No-rotation tick: due buffs, then keep moving — weave-hop
        around where grinding started."""
        if not self.is_window_focused() or not self.should_continue():
            return
        for buff in self.skills.due_buffs():
            if self._use_skill(buff):
                self.sleep(human_between(0.3, 0.2, 0.45))
        if self._roam_origin is None:
            self._roam_origin = self.player_pos()
        if self._roam_origin is None:
            self._blind_wait()
            return
        self._weave_around(*self._roam_origin)

    def _blind_wait(self) -> None:
        """No player dot: wait briefly — attacks only happen inside flash
        moves, and moving blind could walk into a wall zone."""
        now = time.time()
        if now - getattr(self, "_blind_logged", 0.0) > 5.0:
            self._blind_logged = now
            self.log("Player dot not visible — waiting")
        self.sleep(0.15)

    # -- Entry ---------------------------------------------------------------------
    def start(self) -> None:
        try:
            new_session()               # this run's pace differs from the last
            if self.identity.current.title is None and not self.identity.pending:
                self.identity.request("startup")
            if self._own_monitor:
                self.monitor.start()
            self.window.activate()
            self.sleep(1)
            self.minimap_frame()
            Machine(self).run()
            self.log("Smart bot stopped")
        except KeyboardInterrupt:
            self.log("Smart bot interrupted")
        finally:
            self.reach.save(force=True)
            if self._own_monitor:
                self.monitor.stop()
            self.hid.release_all()
            self.cleanup()


__all__ = ["SmartBot"]
