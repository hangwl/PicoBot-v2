"""Calibration recorder: walk a map's rotation once, get a map file.

The recorder samples the player dot on the minimap while the user plays
the rotation by hand. A hotkey (default F9) marks anchors; ESC finishes.
Between marks, the position trace is segmented into leg steps —
horizontal runs become ``walk_to``, sustained vertical drift at roughly
constant x becomes ``climb`` (which is also how rope traversal is
captured for free). Dwell ranges and candidate ``on_arrive`` skills are
inferred from how long the user stood at each mark and which keys they
pressed there.

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
from .rotation import Rotation, Step
from .skills import Skill

logger = logging.getLogger(__name__)

_Pt = Tuple[float, float]  # minimap px


def _dist(a: _Pt, b: _Pt) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def _simplify(points: List[_Pt], min_step: float) -> List[_Pt]:
    """Drop points closer than ``min_step`` px to the last kept one."""
    kept = [points[0]]
    for p in points[1:]:
        if _dist(p, kept[-1]) >= min_step:
            kept.append(p)
    if kept[-1] != points[-1]:
        kept.append(points[-1])
    return kept


def _trace_to_steps(points: List[_Pt], wh: Tuple[int, int]) -> List[Step]:
    """Segment a raw position trace into leg steps (normalized coords)."""
    if len(points) < 2:
        return []
    w, h = wh
    norm = lambda p: (p[0] / w, p[1] / h)
    pts = _simplify(points, min_step=4.0)

    # Group consecutive edges by dominant axis.
    runs: List[List[_Pt]] = [[pts[0]]]
    run_axis = None
    for a, b in zip(pts, pts[1:]):
        axis = "v" if abs(b[1] - a[1]) > 2 * abs(b[0] - a[0]) else "h"
        if run_axis is None or axis == run_axis:
            run_axis = axis if run_axis is None else run_axis
            runs[-1].append(b)
        else:
            runs.append([a, b])
            run_axis = axis

    steps: List[Step] = []
    for run in runs:
        dx = run[-1][0] - run[0][0]
        dy = run[-1][1] - run[0][1]
        if abs(dy) >= 8 and abs(dy) > abs(dx):
            nx, ny = norm(run[-1])
            steps.append(Step(
                "climb",
                x=norm((sum(p[0] for p in run) / len(run), 0))[0],
                direction="up" if dy < 0 else "down",
                until_y=ny,
            ))
        elif _dist(run[0], run[-1]) >= 4:
            nx, ny = norm(run[-1])
            steps.append(Step("walk_to", x=nx, y=ny))
        # tiny runs are noise; fold into the next step by skipping
    # Merge consecutive same-kind steps (keep the last target).
    merged: List[Step] = []
    for step in steps:
        if merged and merged[-1].kind == step.kind == "walk_to":
            merged[-1] = step
        else:
            merged.append(step)
    return merged


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
        self._leg_traces: Dict[Tuple[int, int], List[_Pt]] = {}
        self._leg_origin: Optional[int] = None
        self._open_trace: List[_Pt] = []
        self._dwell_start: Optional[float] = None
        self._mark_time: Optional[float] = None

    # -- Feeding --------------------------------------------------------------
    def sample(self, pos: _Pt, t: Optional[float] = None) -> None:
        t = time.time() if t is None else t
        self.samples.append((t, pos[0], pos[1]))
        if self._leg_origin is not None:
            self._open_trace.append(pos)
        if self._dwell_start is not None and self._leg_origin is not None:
            anchor = self.anchors[self._leg_origin]
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

        Marking within ``anchor_dedupe_px`` of an existing anchor closes a
        leg to it instead of creating a duplicate — that's how a loop is
        expressed.
        """
        t = time.time() if t is None else t
        pos = self._last_pos(t)
        existing = self._near_anchor(pos)
        if existing is not None:
            anchor_idx = existing
        else:
            anchor_idx = len(self.anchors)
            self.anchors.append({"pos": pos, "dwell": (8.0, 14.0), "keys": []})
        # Keys pressed within the arrival window after the *previous* mark
        # belong to that anchor (summons get dropped right on arrival).
        if self._leg_origin is not None and self._mark_time is not None:
            prev = self.anchors[self._leg_origin]
            prev["keys"] = [
                k for kt, k in self.keys
                if self._mark_time <= kt < self._mark_time + 8.0
                and k.lower() not in ("f9", "escape")
            ]
        if self._leg_origin is not None:
            self._leg_traces[(self._leg_origin, anchor_idx)] = self._open_trace
        self._open_trace = []
        self._leg_origin = anchor_idx
        self._dwell_start = t
        self._mark_time = t
        return anchor_idx

    def finish(self, name: str, fingerprint: Optional[str] = None) -> MapEntry:
        """Build the map entry: anchors, leg steps, observed skills."""
        from .rotation import Anchor

        # Attribute the arrival-window keys of the final anchor too.
        if self.anchors and self._leg_origin is not None and self._mark_time is not None:
            last = self.anchors[self._leg_origin]
            if not last["keys"]:
                last["keys"] = [
                    k for kt, k in self.keys
                    if self._mark_time <= kt < self._mark_time + 8.0
                    and k.lower() not in ("f9", "escape")
                ]

        anchors = [
            Anchor(
                name=f"anchor_{i}",
                x=a["pos"][0] / self.wh[0],
                y=a["pos"][1] / self.wh[1],
                dwell=a["dwell"],
                on_arrive=tuple(f"key_{k}" for k in a["keys"]),
            )
            for i, a in enumerate(self.anchors)
        ]
        legs = {}
        for ij, trace in self._leg_traces.items():
            steps = _trace_to_steps(trace, self.wh)
            if steps:
                legs[ij] = steps
        skills = {
            f"key_{k}": Skill(f"key_{k}", k, 30.0, "summon")
            for a in self.anchors
            for k in a["keys"]
        }
        return MapEntry(
            name=name,
            rotation=Rotation(anchors=anchors, legs=legs),
            skills=skills,
            fingerprint=fingerprint,
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

    def __init__(self, frame_fn, pos_fn, event=None, hz: float = 15.0) -> None:
        self._frame_fn = frame_fn
        self._pos_fn = pos_fn
        self._emit = event or (lambda kind, msg: None)
        self._period = 1.0 / max(1.0, hz)
        self.recorder: Optional[TraceRecorder] = None
        self.last_pos: Optional[_Pt] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self, region_wh: Tuple[int, int]) -> None:
        self.stop()
        self.recorder = TraceRecorder(region_wh)
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="CalibrationRunner", daemon=True
        )
        self._thread.start()
        self._emit("cal", "recording started")

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                img = self._frame_fn()
                if img is not None:
                    pos = self._pos_fn(img)
                    if pos is not None:
                        self.last_pos = pos
                        if self.recorder is not None:
                            self.recorder.sample(pos)
            except Exception:
                logger.debug("calibration sample failed", exc_info=True)
            self._stop.wait(self._period)

    def record_key(self, key: str) -> None:
        if self.recorder is not None:
            self.recorder.record_key(key)

    def mark(self) -> Optional[int]:
        if self.recorder is None:
            return None
        idx = self.recorder.mark()
        self._emit("cal", f"anchor {idx} marked")
        return idx

    def finish(self, name: str, fingerprint: Optional[str] = None) -> MapEntry:
        recorder = self.recorder
        self.stop()
        if recorder is None:
            raise RuntimeError("no calibration in progress")
        entry = recorder.finish(name, fingerprint=fingerprint)
        self._emit("cal", f"saved map '{name}'")
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
    from ..vision.minimap import MinimapAnalyzer, fingerprint
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
                (lambda r: (r[0], r[1], r[2] - r[0], r[3] - r[1]))(window.rect())
            )
            if full is None or minimap.locate(full) is None:
                return None
            region = minimap.region
        x, y, w, h = region
        return screen.capture((window.left + x, window.top + y, w, h))

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
    )
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
        f"Recording '{args.name}': walk your rotation, "
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
    entry = recorder.finish(args.name, fingerprint=fp)
    path = MapStore(args.maps_dir or bot_config.maps_dir).save(entry)
    print(f"Saved {len(entry.rotation.anchors)} anchors, "
          f"{len(entry.rotation.legs)} legs -> {path}")
    print("Edit the file to set real skill cooldowns/kinds.")


__all__ = ["TraceRecorder", "main"]

if __name__ == "__main__":
    main()
