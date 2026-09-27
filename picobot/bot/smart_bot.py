"""SmartBot: closed-loop grinding driven by minimap perception.

Replaces blind macro playback with a perceive -> decide -> act loop.
The bot runs a *rotation*: a graph of anchors (farming spots) connected
by legs (walk / flash-jump / climb steps) read from config or a map file.
An FSM composes the behaviour (GRIND dwell / TRAVEL leg / WANDER detour /
PAUSE), movement is goal-directed, and skills fire off per-skill
cooldowns — the way a player actually works a map.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Dict, List, Optional, Tuple

from ..vision.game_window import GameWindow
from ..vision.mapname import MapNameReader, name_strip_region
from ..vision.minimap import (
    MinimapAnalyzer,
    fingerprint,
    fingerprint_distance,
    platform_row_at,
    platform_span_at,
    structure_mask,
)
from ..vision.screen import ScreenGrabber
from .base import BotBase
from .config import BotConfig
from .inputs import HidController
from .machine import Machine
from .maps import MapEntry, MapStore
from .rotation import Anchor, Rotation, Step, resolve_coord
from .skills import Skill, SkillBook
from .timing import human_delay, jittered

logger = logging.getLogger(__name__)

_UNSET = object()


class SmartBot(BotBase):
    def __init__(
        self,
        controller: HidController,
        window_title: str,
        config: BotConfig | None = None,
        *,
        minimap: Optional[MinimapAnalyzer] = None,
        log_callback=None,
        notify_callback=None,
        event_bus=None,
    ) -> None:
        config = config or BotConfig()
        window = GameWindow(window_title)
        screen = ScreenGrabber()
        # A caller-supplied analyzer (e.g. the dashboard's feed) carries
        # its verified region + provenance into the bot; otherwise build
        # our own seeded by config.
        minimap = minimap or MinimapAnalyzer(
            colors=config.minimap_colors,
            region=config.minimap_region,
            map_change_threshold=config.map_match_threshold,
            marker_inset=config.marker_inset_px,
        )
        super().__init__(
            controller, window, screen, minimap, config,
            log_callback=log_callback, notify_callback=notify_callback,
            event_bus=event_bus,
        )
        self.skills = SkillBook(config.skills)
        self.maps = MapStore(config.maps_dir)
        self._map: Optional[MapEntry] = None
        self._anchor_idx = 0
        self._travel_target: Optional[int] = None
        self._leg_fp: Optional[str] = None
        self._dwell_end = 0.0
        self._rest_until = 0.0
        self._arrive_pending: List[Tuple[Skill, float]] = []
        self._minimap_warned = False
        self._map_warned = False
        self._name_reader = MapNameReader()
        self._name_cache: Tuple[float, Optional[str]] = (0.0, None)
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
            "target": None, "map": None, "title": None,
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
        """BGR capture of the minimap, or None if the region is unknown."""
        region = self.minimap.region
        if region is not None:
            x, y, w, h = region
            img = self.screen.capture(
                (self.window.client_left + x, self.window.client_top + y, w, h)
            )
            if img is not None and self.minimap.note_frame(img):
                self.event("vision", "map change detected — minimap relocated")
                self._minimap_warned = False  # re-warn if re-locate fails
                # Re-identify the map under the new scene so a stale pin
                # or stored layout can't persist past a real change.
                self._resolve_map(img)
            return self._stash_frame(img)
        # Region unknown: try each map's remembered layout first (the
        # fingerprint is meaningless under a wrong region, so seeding the
        # region IS how the map gets identified), then border-detect.
        img = self._window_capture()
        if img is None:
            return None
        if self._resolve_region(img) is None:
            if not self._minimap_warned:
                self.log(
                    "Minimap not found — set 'minimap_region' in the bot "
                    "config or verify the border color."
                )
                self._minimap_warned = True
            return None
        return self.minimap_frame()

    def _resolve_region(self, window_img) -> Optional[Tuple[int, int, int, int]]:
        """Install the best region for the current screen.

        Tries every map file's remembered ``minimap_region``: capture that
        rect, fingerprint it, and keep the candidate whose fingerprint
        matches that map (position is stable across maps, size is not).
        Falls back to border ``locate()`` when nothing matches.
        """
        for entry in self.maps.load_all():
            if not entry.minimap_region or not entry.fingerprint:
                continue
            x, y, w, h = entry.minimap_region
            img = self.screen.capture(
                (self.window.client_left + x, self.window.client_top + y,
                 w, h)
            )
            if img is None:
                continue
            fp = fingerprint(
                img,
                ignore_colors=self._fp_ignored_colors(),
                include_mask=self._fp_mask(img),
            )
            if (
                fp
                and fingerprint_distance(fp, entry.fingerprint)
                <= self.config.map_match_threshold
            ):
                self.minimap.set_region(entry.minimap_region)
                self.log(f"Layout: restored {entry.name}'s remembered region")
                return self.minimap.region
        return self.minimap.locate(window_img)

    def _stash_frame(self, img):
        self.viz["img"] = img
        return img

    def _fp_ignored_colors(self):
        c = self.minimap.colors
        return (c.player, c.other_player, c.rune)

    def _fp_mask(self, img):
        """Fingerprint include-mask: platform-line structure (+ ink)."""
        return structure_mask(img, self.minimap.colors)

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
    def _name_region(self):
        if self.config.minimap_name_region:
            return self.config.minimap_name_region
        region = self.minimap.region
        if region is None:
            return None
        return name_strip_region(region, self.config.name_scan_px)

    def name_img(self):
        """BGR band spanning the title zone (window top → into the
        minimap region); title_lines() segments the text out of it."""
        region = self._name_region()
        if region is None:
            return None
        x, y, w, h = region
        return self.screen.capture(
            (self.window.client_left + x, self.window.client_top + y, w, h)
        )

    def map_name(self, force: bool = False) -> Optional[str]:
        """OCR'd map title, cached a few seconds (it only changes on
        map transitions). None = OCR off / engine missing / unreadable."""
        if not self.config.name_ocr:
            return None
        now = time.time()
        ts, cached = self._name_cache
        if not force and now - ts < 5.0:
            return cached
        name = self._name_reader.read(self.name_img())
        self._name_cache = (now, name)
        if name and name != cached:
            self.event("vision", f"map name: {name}")
        self.viz["title"] = name
        return name

    def _live_map(self, img=None):
        """(entry, via, fp, ocr_text) — the screen's best-guess identity.

        OCR'd title text wins (an exact name match); the structural
        fingerprint is the fallback. When both fire and disagree the
        OCR result is kept — the title literally names the map — but
        the conflict is logged so a misread can be spotted.
        """
        name = self.map_name()
        ocr_entry = self.maps.match_name(name) if name else None
        fp = self.minimap_fingerprint(img)
        fp_entry, _ = self.maps.match_scored(
            fp, self.config.map_match_threshold
        )
        if ocr_entry is not None:
            if fp_entry is not None and fp_entry.name != ocr_entry.name:
                self.log(
                    f"map id conflict — OCR '{name}' → {ocr_entry.name}, "
                    f"fingerprint → {fp_entry.name}; using OCR"
                )
            return ocr_entry, "ocr", fp, name
        if fp_entry is not None:
            return fp_entry, "fp", fp, name
        return None, None, fp, name

    def minimap_fingerprint(self, img=None) -> Optional[str]:
        if img is None:
            img = self.minimap_frame()
        if img is None:
            return None
        return fingerprint(
            img,
            ignore_colors=self._fp_ignored_colors(),
            include_mask=self._fp_mask(img),
        ) or None

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
        rune = self.minimap.rune_pos(img)
        self.viz["rune"] = rune
        if cfg.stop_when_rune_appears and rune is not None:
            reason = "rune"
        elif cfg.stop_when_players_appear and self.minimap.has_other_players(img):
            reason = "other players"
        elif self._leg_fp:
            fp = fingerprint(
                img,
                ignore_colors=self._fp_ignored_colors(),
                include_mask=self._fp_mask(img),
            )
            if fingerprint_distance(fp, self._leg_fp) > cfg.map_match_threshold:
                reason = "map changed unexpectedly (portal?)"
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

    def _resolve_map(self, img=None) -> None:
        """Pin ``active_map`` or auto-select by OCR/fingerprint."""
        cfg = self.config
        if not (cfg.auto_select_map or cfg.active_map):
            return
        entry = None
        live, via, fp, ocr_text = self._live_map(img)
        if cfg.active_map:
            entry = self.maps.get(cfg.active_map)
            if entry is None and not self._map_warned:
                self.log(f"Map '{cfg.active_map}' not found in {cfg.maps_dir}/")
                self._map_warned = True
            elif (
                entry is not None
                and live is not None
                and live.name != entry.name
            ):
                # The pin is rotation scope, not identity — when the
                # screen verifiably shows a different map, the live
                # evidence wins so a stale pin can't hold a dead layout.
                # OCR'd title text is explicit identity and always wins;
                # a fingerprint-only match only beats a pin whose own
                # stored fingerprint disagrees with the screen.
                proven = via == "ocr"
                if not proven and entry.fingerprint and fp:
                    pinned_dist = fingerprint_distance(
                        fp, entry.fingerprint
                    )
                    proven = pinned_dist > cfg.map_match_threshold
                if proven:
                    self.log(
                        f"Pin '{entry.name}' disagrees with the "
                        f"screen ({via}) — switching to {live.name}"
                    )
                    entry = live
        else:
            entry = live
        if entry is not self._map:
            self._map = entry
            self.viz["map"] = entry.name if entry else None
            merged = dict(cfg.skills)
            if entry is not None:
                merged.update(entry.skills)
                self.log(f"Map: {entry.name}")
                self.event("map", entry.name)
            self.skills = SkillBook(merged)
            self._anchor_idx = 0
            self._weave_dir = None
            self._weave_bounds = None
            self._route = []
        self._apply_stored_layout(entry)

    def _apply_stored_layout(self, entry) -> None:
        """Reinstall the map's remembered minimap region.

        Corrects auto-detect drift on known maps: once a layout was
        saved at calibration, identifying the map snaps the region back
        to the known-good rect. Skipped when ``minimap_region`` is
        pinned in the config or the map has none. Stored regions stay
        resettable — a real map change still clears them via the
        watchdog.
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
        """Vertical boost: configured skill key if set, else jump+up+jump.

        Returns False when the skill key was suppressed by its cooldown
        (rope lift & co. are on a real timer — pressing early does nothing
        in-game) or the window isn't focused; True when it pressed.
        """
        if not self.is_window_focused():
            return False
        if self.config.up_jump_skill_key:
            now = time.time()
            last = getattr(self, "_last_up_skill", 0.0)
            if now - last < self.config.up_jump_skill_cooldown:
                return False
            self._last_up_skill = now
            self.hid.press(self.config.up_jump_skill_key)
            self.sleep(0.3)
            return True
        jk = self.config.jump_key
        self.hid.press(jk)
        self.sleep(0.1)
        self.hid.key_down("up")
        self.hid.key_down(jk)
        self.sleep(0.5)
        self.hid.key_up(jk)
        self.hid.key_up("up")
        self.sleep(0.3)
        return True

    def _current_map_entry(self) -> Optional[MapEntry]:
        """The resolved map, refreshed against the store.

        Dashboard boundary edits save + reload the map file, which swaps
        the ``MapEntry`` objects — ``self._map`` can point at the stale
        pre-reload instance, so walls set mid-run would be invisible
        until the next ``_resolve_map``. This re-reads the store's copy.
        """
        if self._map is None:
            return None
        return self.maps.get(self._map.name) or self._map

    def _map_walls(self) -> Optional[dict]:
        entry = self._current_map_entry()
        return entry.walls if entry is not None else None

    def _floor_px(self) -> Optional[int]:
        """Resolved per-map floor y, or None when no floor is set."""
        walls = self._map_walls()
        if walls and walls.get("floor") is not None:
            return self._ry(walls["floor"])
        return None

    def down_jump(self, img=None) -> None:
        if not self.is_window_focused():
            return
        floor = self._floor_px()
        if floor is not None:
            pos = self.player_pos(img)
            if pos is not None and pos[1] >= floor - 2:
                return  # bottom of the map — nothing to drop through
        self.hid.key_down("down")
        self.hid.press(self.config.jump_key)
        self.sleep(0.1)
        self.hid.key_up("down")
        self.sleep(0.4)

    def _flash_key(self) -> str:
        return self.config.flash_jump_key or self.config.jump_key

    def _flash_hop(self) -> None:
        """One flash-jump pair with irregular, human-ish spacing."""
        jk = self._flash_key()
        self.hid.press(jk)
        self.sleep(random.uniform(0.13, 0.26))
        self.hid.press(jk)
        self.sleep(random.uniform(0.32, 0.62))

    def move_to_point(
        self,
        target_x: int,
        target_y: int,
        threshold: Optional[int] = None,
        *,
        style: str = "walk",
    ) -> bool:
        """Navigate on the minimap toward (x, y); True if reached.

        Polls the player dot while holding the correct direction key.
        ``style`` controls horizontal travel: ``walk`` holds the key,
        ``flash`` chains flash-jump hops, ``mixed`` mostly flashes with
        occasional plain-walk stretches. Aborts (False) on hazards,
        focus loss, or stop.
        """
        threshold = threshold or self.config.nav_threshold_px
        # Hysteresis band: release the direction inside `stop_band`, only
        # acquire it beyond `start_band` — stops left/right flapping on the
        # target column.
        stop_band = threshold * 0.75
        start_band = threshold * 1.5
        flash_ok = self.config.flash_jump_enabled and style in ("flash", "mixed")
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

        def sync_dir(new_dir):
            nonlocal held_dir
            if new_dir == held_dir:
                return
            if held_dir:
                self.hid.key_up(held_dir)
            if new_dir:
                self.hid.key_down(new_dir)
            held_dir = new_dir

        stuck = 0
        last = None
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
                cx, cy = pos
                if not snapped:
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
                dx, dy = target_x - cx, target_y - cy
                if abs(dx) <= threshold and abs(dy) <= threshold:
                    self.log("Navigation target reached")
                    self.viz["target"] = None
                    return True
                self._travel_attack()
                if held_dir == "right" and dx <= stop_band:
                    sync_dir(None)
                elif held_dir == "left" and dx >= -stop_band:
                    sync_dir(None)
                if held_dir is None:
                    if dx > start_band:
                        sync_dir("right")
                    elif dx < -start_band:
                        sync_dir("left")
                if abs(dx) <= threshold * 3:
                    # Rate-limit vertical jumps — spamming them never helps.
                    now = time.time()
                    if dy < -threshold and now - last_vert_jump >= 0.9:
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
                    elif dy > threshold and now - last_vert_jump >= 0.9:
                        floor = self._floor_px()
                        if floor is not None and cy >= floor - 2:
                            # Bottom of the map — the target is below the
                            # lowest platform; verdict now, no keypresses.
                            return vert_stuck("descend")
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
                elif flash_ok and (
                    style == "flash" or random.random() < 0.6
                ):
                    self._flash_hop()
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
                    self.sleep(0.4)
                    self.hid.key_up(back)
                    self.up_jump()
                    stuck = 0
                    last = None
        finally:
            sync_dir(None)
            self.hid.release_all()
            self.viz["target"] = None
        return False

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
                if last_y is not None and y == last_y:
                    still += 1
                else:
                    still = 0
                    grabbed = True
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
        self._resolve_map()
        rot = self.effective_rotation()
        if not rot.anchors:
            return False
        if self._anchor_idx >= len(rot.anchors):
            self._anchor_idx = 0
        self._leg_fp = self.minimap_fingerprint()
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
        ok = self._run_leg(rot.leg_steps(self._anchor_idx, target))
        self._leg_fp = None
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

    # -- Dwell ---------------------------------------------------------------------
    def begin_dwell(self) -> None:
        """Start a farming dwell at the current anchor (or legacy timer).

        With ≥2 anchors the dwell is a checkpoint patrol — it runs until
        the route hands off a cross-level checkpoint to TRAVEL, so
        ``_dwell_end`` stays open-ended. Smaller rotations keep the
        per-anchor dwell timer."""
        rot = self.effective_rotation()
        now = time.time()
        self._weave_dir = None
        self._weave_bounds = None
        if rot.anchors and self._anchor_idx < len(rot.anchors):
            anchor = rot.anchors[self._anchor_idx]
            if anchor.face:
                self.hid.press(anchor.face)
            self._arrive_pending = [
                (s, now + s.wait_on_arrival)
                for name in anchor.on_arrive
                if (s := self.skills.get(name)) is not None
            ]
            dwell = anchor.dwell_seconds()
            self._dwell_end = (
                float("inf") if len(rot.anchors) >= 2 else now + dwell
            )
            if random.random() < rot.rest_chance:
                self._rest_until = now + min(dwell * 0.6, random.uniform(5, 25))
                self.log("Taking a breather")
            else:
                self._rest_until = 0.0
        else:
            self._arrive_pending = []
            self._rest_until = 0.0
            self._dwell_end = now + self.config.stationary_seconds

    def dwell_done(self) -> bool:
        return time.time() >= self._dwell_end

    def dwell_tick(self) -> None:
        """One farming tick: arrival skills, buffs, then patrol/weave."""
        if time.time() < self._rest_until:
            self.sleep(0.5)
            return
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
                self.sleep(jittered(0.6))
        if len(self.effective_rotation().anchors) >= 2:
            self._patrol_tick()
        elif self.config.dwell_weave:
            self._weave_attack()
        else:
            self._attack_once()

    def _weave_attack(self) -> None:
        """Move while farming: bounce across the anchor's platform and
        weave attack presses into the flash-hop — jump, attack mid-air,
        jump again (the FJ re-press). Players don't stand still on a
        farm; neither should the bot. The hop is bounded inside one tick
        (keys released before return) so a hazard pause can't leave a
        direction held."""
        rot = self.effective_rotation()
        if not rot.anchors or self._anchor_idx >= len(rot.anchors):
            self._attack_once()
            return
        cfg = self.config
        anchor = rot.anchors[self._anchor_idx]
        ax, ay = self._rx(anchor.x), self._ry(anchor.y)
        img = self.minimap_frame()
        pos = self.minimap.player_pos(img) if img is not None else None
        self.viz["player"] = pos
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
            # Wall zones: inside a left/right wall boundary the only sane
            # facing is inward — overrides platform bounds and prevents
            # wall-banging. Per-map walls (absolute x) replace the global
            # edge margins; absent sides fall back to wall_zone_px.
            walls = self._map_walls()
            map_w = self._region_wh()[0]
            left_wall = (
                self._rx(walls["left"])
                if walls and walls.get("left") is not None
                else cfg.wall_zone_px
            )
            right_wall = (
                self._rx(walls["right"])
                if walls and walls.get("right") is not None
                else map_w - cfg.wall_zone_px
            )
            if pos[0] <= left_wall:
                direction = "right"
            elif pos[0] >= right_wall:
                direction = "left"
            elif pos[0] <= lo:
                direction = "right"
            elif pos[0] >= hi:
                direction = "left"
            elif random.random() < 0.06:
                direction = "left" if direction == "right" else "right"
        self._weave_dir = direction
        self._weave_hop(direction)

    def _weave_hop(self, direction: str) -> None:
        """Hold ``direction`` through one jump→FJ→attack weave.

        Bounded inside the call (keys released before return) so a hazard
        pause can't leave a direction held.
        """
        cfg = self.config
        skill = self._pick_attack()
        self.hid.key_down(direction)
        try:
            if cfg.flash_jump_enabled:
                self.hid.press(cfg.jump_key)
                self.sleep(random.uniform(0.12, 0.22))
                self.hid.press(cfg.jump_key)  # mid-air re-press = FJ
                self.sleep(random.uniform(0.06, 0.12))
                # Attack AFTER the FJ triggers — an early press eats the
                # second jump's input window and the flash never fires.
                if skill is not None:
                    self._use_skill(skill)
                self.sleep(random.uniform(0.28, 0.45))
            else:
                if skill is not None:
                    self._use_skill(skill)
                self.sleep(random.uniform(0.3, 0.5))
        finally:
            self.hid.key_up(direction)

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
        ``_travel_target`` and end the dwell so TRAVEL runs the leg; a
        head that can't be reached in ~20s is skipped (and briefly
        banned) so a bad checkpoint can't stall the loop forever."""
        rot = self.effective_rotation()
        if len(rot.anchors) < 2:
            if self.config.dwell_weave:
                self._weave_attack()
            else:
                self._attack_once()
            return
        cfg = self.config
        img = self.minimap_frame()
        pos = self.minimap.player_pos(img) if img is not None else None
        self.viz["player"] = pos
        if pos is None:
            # Blind tick — attacks don't need vision, movement does.
            self._attack_once()
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
            if abs(ty - pos[1]) > level_band:
                # Different level — TRAVEL owns this transition.
                self._route.pop(0)
                self._ckpt_idx = None
                self._travel_target = idx
                self._dwell_end = now
                return
            if abs(tx - pos[0]) > cfg.nav_threshold_px:
                break  # not there yet — head for it below
            self._route.pop(0)
            self._anchor_idx = idx
            self._ckpt_idx = None
            self._arrive_pending = [
                (s, now + s.wait_on_arrival)
                for name in target.on_arrive
                if (s := self.skills.get(name)) is not None
            ]
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
            self._attack_once()
            return
        # Head toward the checkpoint; walls still override the heading.
        dx = tx - pos[0]
        direction = self._weave_dir or ("right" if dx >= 0 else "left")
        walls = self._map_walls()
        map_w = self._region_wh()[0]
        left_wall = (
            self._rx(walls["left"])
            if walls and walls.get("left") is not None
            else cfg.wall_zone_px
        )
        right_wall = (
            self._rx(walls["right"])
            if walls and walls.get("right") is not None
            else map_w - cfg.wall_zone_px
        )
        if pos[0] <= left_wall:
            direction = "right"
        elif pos[0] >= right_wall:
            direction = "left"
        elif dx > cfg.nav_threshold_px:
            direction = "right"
        elif dx < -cfg.nav_threshold_px:
            direction = "left"
        self._weave_dir = direction
        self._weave_hop(direction)

    def _travel_attack(self) -> None:
        """Weave one attack press into a travel leg, cooldown-spaced.

        Registered attack skills fire while the bot walks/hops between
        checkpoints — nothing registered means nothing fires. The ~0.4s
        gate keeps a 0-cooldown key from becoming a 20Hz spam loop, and
        unlike ``_attack_once`` the not-ready path doesn't sleep (the
        nav loop owns the pacing here).
        """
        now = time.time()
        if now < self._travel_attack_at:
            return
        skill = self._pick_attack()
        if skill is not None and self._use_skill(skill):
            self._travel_attack_at = now + random.uniform(0.35, 0.55)

    def _attack_once(self) -> None:
        """Stationary single attack tick (legacy dwell path)."""
        skill = self._pick_attack()
        if skill is None:
            self.sleep(0.2)
            return
        if self._use_skill(skill):
            lo, hi = self.config.skill_gap_seconds
            self.sleep(human_delay(random.uniform(lo, hi)))

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

    def _attack_cycle(self) -> None:
        for buff in self.skills.due_buffs():
            if not self.should_continue():
                break
            if self._use_skill(buff):
                self.sleep(jittered(0.6))
        self._attack_once()

    def grind_once(self) -> None:
        """Legacy no-rotation tick: due buffs, then a randomized attack."""
        if not self.is_window_focused() or not self.should_continue():
            return
        self._attack_cycle()

    def random_wander(self) -> None:
        """Bounded random walk for ``wander_seconds``, then return home."""
        origin = self.player_pos()
        if origin is None:
            self.log("No player position — skipping wander")
            return
        end = time.time() + self.config.wander_seconds
        margin = self.config.wander_edge_margin_px
        current_dir = random.choice(["left", "right"])
        held_dir = None

        region = self.minimap.region
        map_w = region[2] if region else 200

        def sync_dir(new_dir):
            nonlocal held_dir
            if new_dir == held_dir:
                return
            if held_dir:
                self.hid.key_up(held_dir)
            if new_dir:
                self.hid.key_down(new_dir)
            held_dir = new_dir

        try:
            while (
                self.should_continue()
                and time.time() < end
                and self.is_window_focused()
            ):
                pos = self.player_pos()
                if pos is None:
                    self.sleep(0.3)
                    continue
                if pos[0] <= margin:
                    current_dir = "right"
                elif pos[0] >= map_w - margin:
                    current_dir = "left"
                sync_dir(current_dir)
                if random.random() < 0.08:
                    (self.up_jump if random.random() < 0.5 else self.down_jump)()
                self.sleep(0.1)
        finally:
            sync_dir(None)
            self.hid.release_all()
            if origin:
                self.move_to_point(*origin)

    # -- Entry ---------------------------------------------------------------------
    def start(self) -> None:
        try:
            self.window.activate()
            self.sleep(1)
            self._resolve_map()
            Machine(self).run()
            self.log("Smart bot stopped")
        except KeyboardInterrupt:
            self.log("Smart bot interrupted")
        finally:
            self.hid.release_all()
            self.cleanup()


__all__ = ["SmartBot"]
