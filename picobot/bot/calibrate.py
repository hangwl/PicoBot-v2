"""Calibration recorder: mark a map's farming anchors, get a map file.

The recorder samples the player dot on the minimap while the user
visits each farming spot. A hotkey (default F9) marks an anchor at the
current position; ESC finishes. Movement between anchors is NOT
recorded — checkpoint travel is generated live (weave-attacks plus the
drawn platform geometry), not replayed. What is inferred at each mark
is its dwell range (how long you stood there) and which keys you
pressed on arrival (candidate ``on_arrive`` skills).

CLI (Windows host, no Pico needed)::

    python -m picobot.bot.calibrate --window "Eluna (x64)" --name my_map

Then edit ``maps/my_map.json`` to set real cooldowns / skill kinds.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Dict, List, Optional, Tuple

from .maps import MapEntry, MapStore
from .rotation import Rotation
from .skills import Skill

logger = logging.getLogger(__name__)

_Pt = Tuple[float, float]  # minimap px


def _dist(a: _Pt, b: _Pt) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def _key_groups(
    events: List[Tuple[float, str]],
) -> Dict[str, List[float]]:
    """key -> sorted press times within one anchor's arrival window."""
    groups: Dict[str, List[float]] = {}
    for t, k in events:
        groups.setdefault(k, []).append(t)
    return groups


def merge_recording(
    entry: MapEntry,
    existing: Optional[MapEntry],
    map_name: Optional[str] = None,
) -> MapEntry:
    """Preserve what a recording can't observe on an existing map.

    Recording only produces anchors + inferred skills — drawn walls/
    platforms, the OCR'd title, and user-tuned skill bindings would be
    wiped by a plain save. Existing skills win over freshly inferred
    ``key_*`` skills of the same name.
    """
    if existing is not None:
        entry.walls = existing.walls
        entry.platforms = existing.platforms
        merged = dict(entry.skills)
        merged.update(existing.skills)
        entry.skills = merged
        if not map_name:
            map_name = existing.map_name
    if map_name:
        entry.map_name = map_name
    return entry


class TraceRecorder:
    """Pure calibration logic: feed position samples + mark events.

    Units are minimap pixels; ``region_wh`` normalises them to 0-1
    fractions for the emitted rotation.
    """

    def __init__(
        self,
        region_wh: Tuple[int, int] = (200, 150),
        *,
        anchor_dedupe_px: float = 8.0,
    ) -> None:
        self.wh = region_wh
        self.anchor_dedupe_px = anchor_dedupe_px
        self.samples: List[Tuple[float, float, float]] = []  # (t, x, y)
        self.keys: List[Tuple[float, str]] = []              # (t, key)
        self.anchors: List[dict] = []                        # pos/dwell/keys
        self._at: Optional[int] = None   # anchor the player is at
        self._dwell_start: Optional[float] = None
        self._mark_time: Optional[float] = None

    # -- Feeding --------------------------------------------------------------
    def sample(self, pos: _Pt, t: Optional[float] = None) -> None:
        t = time.time() if t is None else t
        self.samples.append((t, pos[0], pos[1]))
        if self._dwell_start is not None and self._at is not None:
            anchor = self.anchors[self._at]
            if _dist(pos, anchor["pos"]) > 10.0:
                observed = t - self._dwell_start
                lo = max(4.0, observed * 0.7)
                hi = min(90.0, max(observed * 1.3, lo + 2.0))
                anchor["dwell"] = (round(lo, 1), round(hi, 1))
                self._dwell_start = None

    def record_key(self, key: str, t: Optional[float] = None) -> None:
        self.keys.append((time.time() if t is None else t, key))

    def mark(self, t: Optional[float] = None) -> int:
        """Anchor at the current position; returns the anchor index.

        Marking within ``anchor_dedupe_px`` of an existing anchor merges
        into it instead of creating a duplicate — re-marking the same
        spot is idempotent.
        """
        t = time.time() if t is None else t
        pos = self._last_pos(t)
        existing = self._near_anchor(pos)
        if existing is not None:
            anchor_idx = existing
        else:
            anchor_idx = len(self.anchors)
            self.anchors.append(
                {"pos": pos, "dwell": (8.0, 14.0), "key_events": []}
            )
        # Keys pressed within the arrival window after the *previous* mark
        # belong to that anchor (summons get dropped right on arrival).
        if self._at is not None and self._mark_time is not None:
            prev = self.anchors[self._at]
            prev["key_events"] = [
                (kt, k) for kt, k in self.keys
                if self._mark_time <= kt < self._mark_time + 8.0
                and k.lower() not in ("f9", "escape")
            ]
        self._at = anchor_idx
        self._dwell_start = t
        self._mark_time = t
        return anchor_idx

    def finish(
        self,
        name: str,
        fingerprint: Optional[str] = None,
        minimap_region=None,
        key_map: Optional[Dict[str, Skill]] = None,
    ) -> MapEntry:
        """Build the map entry: anchors + observed arrival skills.

        ``key_map`` binds raw key names to configured skills (key ->
        Skill). Recorded presses of a bound key are credited to that
        skill's real name in ``on_arrive``; ``movement``-kind bindings
        are dropped entirely, and bound keys never get auto-generated
        ``key_*`` skills.
        """
        from .rotation import Anchor

        key_map = key_map or {}

        def bound(k: str) -> Optional[Skill]:
            s = key_map.get(k)
            return s if s is not None and s.kind != "movement" else None

        # Attribute the arrival-window keys of the final anchor too.
        if self.anchors and self._at is not None and self._mark_time is not None:
            last = self.anchors[self._at]
            if not last["key_events"]:
                last["key_events"] = [
                    (kt, k) for kt, k in self.keys
                    if self._mark_time <= kt < self._mark_time + 8.0
                    and k.lower() not in ("f9", "escape")
                ]

        anchors = [
            Anchor(
                name=f"anchor_{i}",
                x=a["pos"][0] / self.wh[0],
                y=a["pos"][1] / self.wh[1],
                dwell=a["dwell"],
                on_arrive=tuple(
                    dict.fromkeys(
                        s.name if (s := bound(k)) is not None else f"key_{k}"
                        for _, k in a["key_events"]
                        if key_map.get(k) is None
                        or key_map[k].kind != "movement"
                    )
                ),
            )
            for i, a in enumerate(self.anchors)
        ]
        # A key pressed 3+ times in an arrival window is being spammed —
        # that's an attack, not a summon. Use its observed cadence as the
        # cooldown so the book spaces presses like the recording did.
        skills: Dict[str, Skill] = {}
        for a in self.anchors:
            for k, times in _key_groups(a["key_events"]).items():
                if k in key_map:
                    continue  # bound in config — its kind/cooldown apply
                name = f"key_{k}"
                if name in skills:
                    continue
                if len(times) >= 3:
                    gaps = [b - t for t, b in zip(times, times[1:])]
                    cd = sorted(gaps)[len(gaps) // 2]
                    skills[name] = Skill(
                        name, k, min(15.0, max(0.5, cd)), "attack"
                    )
                else:
                    skills[name] = Skill(name, k, 30.0, "summon")
        return MapEntry(
            name=name,
            rotation=Rotation(anchors=anchors),
            skills=skills,
            fingerprint=fingerprint,
            minimap_region=minimap_region,
        )

    # -- Internals ----------------------------------------------------------------
    def _last_pos(self, t: float) -> _Pt:
        if not self.samples:
            raise RuntimeError("mark() before any position sample")
        return (self.samples[-1][1], self.samples[-1][2])

    def _near_anchor(self, pos: _Pt) -> Optional[int]:
        for i, a in enumerate(self.anchors):
            if _dist(pos, a["pos"]) <= self.anchor_dedupe_px:
                return i
        return None


class CalibrationRunner:
    """Sampling loop that feeds a :class:`TraceRecorder` from a live feed.

    ``frame_fn()`` returns the current minimap capture (or None);
    ``pos_fn(img)`` extracts the player dot. ``event(kind, msg)`` is an
    optional sink for dashboard events. Keypresses observed while running
    should be routed through :meth:`record_key`.
    """

    def __init__(
        self,
        frame_fn,
        pos_fn,
        event=None,
        hz: float = 15.0,
        snap_fn=None,
    ) -> None:
        self._frame_fn = frame_fn
        self._pos_fn = pos_fn
        # Optional: maps (img, x, y) to the nearest drawn-platform row.
        # Contract: int row = snap target; y back = no geometry to verify
        # against; None = off a drawn platform (warns).
        self._snap_fn = snap_fn
        self._emit = event or (lambda kind, msg, data=None: None)
        self._period = 1.0 / max(1.0, hz)
        self.recorder: Optional[TraceRecorder] = None
        self.last_pos: Optional[_Pt] = None
        self.last_img = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._t0 = 0.0

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self, region_wh: Tuple[int, int]) -> None:
        self.stop()
        self.recorder = TraceRecorder(region_wh)
        self._t0 = time.time()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="CalibrationRunner", daemon=True
        )
        self._thread.start()
        self._emit("cal", "recording started")

    def _loop(self) -> None:
        last_stat = 0.0
        while not self._stop.is_set():
            try:
                img = self._frame_fn()
                if img is not None:
                    self.last_img = img
                    pos = self._pos_fn(img)
                    if pos is not None:
                        self.last_pos = pos
                        if self.recorder is not None:
                            self.recorder.sample(pos)
            except Exception:
                logger.debug("calibration sample failed", exc_info=True)
            # ~1Hz live status for the dashboard (not logged).
            now = time.time()
            if self.recorder is not None and now - last_stat >= 1.0:
                last_stat = now
                self._emit("calstat", "", {
                    "anchors": len(self.recorder.anchors),
                    "samples": len(self.recorder.samples),
                    "elapsed": round(now - self._t0, 1),
                })
            self._stop.wait(self._period)

    def record_key(self, key: str) -> None:
        if self.recorder is not None:
            self.recorder.record_key(key)

    def mark(self) -> Optional[int]:
        if self.recorder is None:
            return None
        try:
            idx = self.recorder.mark()
        except RuntimeError:
            self._emit(
                "error",
                "mark ignored — no player position yet "
                "(check minimap tracking in the dashboard)",
            )
            return None
        self._emit("cal", f"anchor {idx} marked")
        # Off-platform sanity check: a mark taken mid-air or while the
        # marker glitched lands off drawn geometry, and the bot can't
        # physically reach it later. snap_fn echoes y back when no
        # platforms are drawn near — nothing to verify, no warning.
        if (
            self._snap_fn is not None
            and self.last_img is not None
            and self.last_pos is not None
        ):
            try:
                py = self._snap_fn(
                    self.last_img, self.last_pos[0], self.last_pos[1]
                )
            except Exception:
                py = self.last_pos[1]
            if py is None or abs(py - self.last_pos[1]) > 8:
                self._emit(
                    "error",
                    f"anchor {idx} marked off drawn platforms — the marker "
                    "may not be standing on a floor; re-mark while standing "
                    "still, or draw the missing platform line",
                )
        return idx

    def finish(
        self,
        name: str,
        fingerprint: Optional[str] = None,
        minimap_region=None,
        key_map: Optional[Dict[str, Skill]] = None,
        existing: Optional[MapEntry] = None,
        map_name: Optional[str] = None,
    ) -> MapEntry:
        recorder = self.recorder
        img = self.last_img
        self.stop()
        if recorder is None:
            raise RuntimeError("no calibration in progress")
        # Snap each anchor's y onto drawn platform segments — marks taken
        # mid-fall or dragged a few px below the floor are unreachable
        # as-is. snap_fn echoes y back where no geometry exists; None
        # means off a drawn platform — left as recorded with a warning.
        if img is not None and self._snap_fn is not None:
            snapped = off = 0
            for a in recorder.anchors:
                try:
                    py = self._snap_fn(img, a["pos"][0], a["pos"][1])
                except Exception:
                    py = None
                if py is None:
                    off += 1
                elif py != int(round(a["pos"][1])):
                    a["pos"] = (a["pos"][0], float(py))
                    snapped += 1
            if snapped:
                self._emit(
                    "cal", f"snapped {snapped} anchor(s) onto platforms"
                )
            if off:
                self._emit(
                    "error",
                    f"{off} anchor(s) are off drawn platforms — left as "
                    "recorded; they may be unreachable. Re-mark while "
                    "standing on a floor, or draw the missing platform "
                    "line in the dashboard.",
                )
        entry = recorder.finish(
            name,
            fingerprint=fingerprint,
            minimap_region=minimap_region,
            key_map=key_map,
        )
        merge_recording(entry, existing, map_name)
        self._emit("cal", f"saved map '{name}'")
        if not entry.rotation.anchors:
            self._emit(
                "error",
                f"map '{name}' has no anchors — the rotation is inactive "
                "and the bot will wander. Re-record and press F9 / "
                "Mark anchor at each farming spot.",
            )
        return entry

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)
        self._thread = None
        self.recorder = None


def main() -> None:
    import argparse

    from ..config import load_config
    from ..settings import configure_logging
    from ..vision.game_window import GameWindow
    from ..vision.minimap import (
        MinimapAnalyzer,
        fingerprint,
        structure_mask,
    )
    from ..vision.screen import ScreenGrabber
    from .config import BotConfig

    parser = argparse.ArgumentParser(prog="picobot.bot.calibrate")
    parser.add_argument("--window", required=True, help="Game window title")
    parser.add_argument("--name", required=True, help="Map name to save as")
    parser.add_argument("--mark-key", default="f9")
    parser.add_argument("--finish-key", default="escape")
    parser.add_argument("--maps-dir", default=None)
    args = parser.parse_args()

    configure_logging()
    import keyboard  # global hook; Windows host only

    bot_config = BotConfig.from_dict(getattr(load_config(), "bot", None))
    window = GameWindow(args.window)
    screen = ScreenGrabber()
    minimap = MinimapAnalyzer(
        colors=bot_config.minimap_colors, region=bot_config.minimap_region
    )

    def frame():
        region = minimap.region
        if region is None:
            full = screen.capture(
                (lambda r: (r[0], r[1], r[2] - r[0], r[3] - r[1]))(
                    window.client_rect()
                )
            )
            if full is None or minimap.locate(full) is None:
                return None
            region = minimap.region
        x, y, w, h = region
        return screen.capture(
            (window.client_left + x, window.client_top + y, w, h)
        )

    first = frame()
    if first is None:
        raise SystemExit(
            "Minimap not found — set minimap_region/colors in config.json"
        )
    recorder = TraceRecorder((first.shape[1], first.shape[0]))
    fp = fingerprint(
        first,
        ignore_colors=(
            minimap.colors.player,
            minimap.colors.other_player,
            minimap.colors.rune,
        ),
        include_mask=structure_mask(first, minimap.colors),
    ) or None
    map_name = None
    try:
        from ..vision.mapname import MapNameReader, name_strip_region

        if minimap.region:
            x, y, w, h = name_strip_region(minimap.region)
            strip = screen.capture(
                (window.client_left + x, window.client_top + y, w, h)
            )
            map_name = MapNameReader().read(strip)
    except Exception:
        pass
    done = {"flag": False}
    keyboard.on_press_key(
        args.mark_key, lambda e: print(f"anchor {recorder.mark()}")
    )
    keyboard.on_press_key(
        args.finish_key, lambda e: done.update(flag=True)
    )
    _presses = []
    keyboard.hook(lambda e: _presses.append(e.name) if e.event_type == "down" else None)

    print(
        f"Recording '{args.name}': visit each farming spot, "
        f"{args.mark_key.upper()} = mark anchor, "
        f"{args.finish_key.upper()} = finish"
    )
    try:
        while not done["flag"]:
            img = frame()
            if img is not None:
                pos = minimap.player_pos(img)
                if pos is not None:
                    recorder.sample(pos)
            for key in _presses:
                recorder.record_key(key)
            _presses.clear()
            time.sleep(0.06)
    finally:
        keyboard.unhook_all()
        screen.close()
    entry = recorder.finish(
        args.name,
        fingerprint=fp,
        minimap_region=minimap.region,
        key_map={s.key: s for s in bot_config.skills.values()},
    )
    # Don't clobber drawn walls/platforms/title on an existing map.
    store = MapStore(args.maps_dir or bot_config.maps_dir)
    merge_recording(entry, store.get(args.name), map_name)
    path = store.save(entry)
    print(f"Saved {len(entry.rotation.anchors)} anchors -> {path}")
    print("Edit the file to set real skill cooldowns/kinds.")


__all__ = ["TraceRecorder", "main"]

if __name__ == "__main__":
    main()
