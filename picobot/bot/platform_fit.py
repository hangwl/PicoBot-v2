"""Drawn-platform hygiene and a standing-position fit diagnostic.

``tidy_segments`` straightens near-flat drags (hand drags are rarely
level) and merges overlapping segments on one row. ``PlatformFit``
records where the player's feet actually settle on each drawn platform,
so a line drawn too high or too low shows up as a consistent offset.
It only reports — drawn geometry stays authoritative.
"""

from __future__ import annotations

import statistics
import threading
import time
from typing import Callable, Dict, List, Optional, Sequence, Tuple

Seg = Tuple[float, float, float, float]

# Levelling moves each end by half the height difference; the planner
# tolerates feet 2px below a line, so level only up to 4px. Wobble is a
# pixel amount, not an angle — a long gentle ramp is a real slope.
FLAT_PX = 4.0
MERGE_PX = 2.0       # rows this close are the same ledge ...
TOUCH_PX = 0.5       # ... when the pieces overlap or meet (a gap is real)


def straighten(seg: Seg, flat_px: float = FLAT_PX) -> Seg:
    """Level a near-flat segment at its mean height; keep real slopes.
    Always returned left-to-right."""
    x0, y0, x1, y1 = seg
    if x1 < x0:
        x0, y0, x1, y1 = x1, y1, x0, y0
    if abs(y1 - y0) <= flat_px:
        y = (y0 + y1) / 2.0
        return (x0, y, x1, y)
    return (x0, y0, x1, y1)


def _level(s: Seg) -> bool:
    return abs(s[1] - s[3]) < 1e-6


def _touch(a: Seg, b: Seg, merge_px: float) -> bool:
    return (abs(a[1] - b[1]) <= merge_px
            and b[0] <= a[2] + TOUCH_PX and a[0] <= b[2] + TOUCH_PX)


def merge_level(segs: Sequence[Seg], merge_px: float = MERGE_PX) -> List[Seg]:
    """Merge level segments on the same row (within ``merge_px``) whose
    spans overlap or meet; the row is the length-weighted mean. Pieces
    with a gap between them stay apart (the gap may be real). Sloped
    segments pass through untouched."""
    sloped = [s for s in segs if not _level(s)]
    level = [s for s in segs if _level(s)]
    changed = True
    while changed:              # one merge can bring two others into contact
        changed = False
        for i in range(len(level)):
            for j in range(i + 1, len(level)):
                a, b = level[i], level[j]
                if not _touch(a, b, merge_px):
                    continue
                la, lb = max(a[2] - a[0], 1e-6), max(b[2] - b[0], 1e-6)
                y = (a[1] * la + b[1] * lb) / (la + lb)
                level[i] = (min(a[0], b[0]), y, max(a[2], b[2]), y)
                del level[j]
                changed = True
                break
            if changed:
                break
    return sloped + level


def tidy_segments(segs: Sequence[Seg]) -> List[Seg]:
    """Straighten every segment, then merge overlapping level ones."""
    return merge_level([straighten(s) for s in segs])


# -- Fit diagnostic --------------------------------------------------------------
class PlatformFit:
    """Feet positions where the player settled, per drawn platform.

    A sample is taken when the dot has held still (±1px) for ``settle_n``
    reads over at least ``settle_s`` — standing, not mid-jump — and
    exactly one drawn platform is within ``window_px`` of the feet.
    Residual = feet y − drawn row: positive means the feet sit below the
    line (drawn too high), negative above it (drawn too low).
    """

    def __init__(
        self,
        *,
        window_px: float = 8.0,
        settle_n: int = 3,
        settle_s: float = 0.3,
        max_samples: int = 200,
        clock: Callable[[], float] = time.monotonic,
        on_change: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.window_px = window_px
        self.settle_n = settle_n
        self.settle_s = settle_s
        self.max_samples = max_samples
        self._clock = clock
        self.on_change = on_change
        self._lock = threading.Lock()
        # map name -> segment key -> [(x, residual)]
        self._samples: Dict[str, Dict[Seg, List[Tuple[float, float]]]] = {}
        self._run: List[Tuple[float, float]] = []
        self._run_t0 = 0.0
        self._sampled_run = False

    @staticmethod
    def key(seg: Sequence[float]) -> Seg:
        """Orientation-free: a line drawn right-to-left is the same line."""
        x0, y0, x1, y1 = (round(float(v), 4) for v in seg)
        return (x0, y0, x1, y1) if x0 <= x1 else (x1, y1, x0, y0)

    def observe(self, map_name: Optional[str], segs_norm, region, pos) -> None:
        """Feed one player position (minimap px) with the map's drawn
        platforms (normalized) and the minimap region."""
        if not map_name or not segs_norm or not region or pos is None:
            self._run = []
            return
        now = self._clock()
        if self._run and (abs(pos[0] - self._run[0][0]) > 1
                          or abs(pos[1] - self._run[0][1]) > 1):
            self._run = []
        if not self._run:
            self._run_t0 = now
            self._sampled_run = False
        self._run.append(pos)
        if self._sampled_run or len(self._run) < self.settle_n \
                or now - self._run_t0 < self.settle_s:
            return
        self._sampled_run = True        # one sample per standstill
        w, h = region[2], region[3]
        x, y = pos
        hits = []
        for s in segs_norm:
            x0, y0, x1, y1 = s[0] * w, s[1] * h, s[2] * w, s[3] * h
            if x1 < x0:
                x0, y0, x1, y1 = x1, y1, x0, y0
            if not (x0 - 3 <= x <= x1 + 3):
                continue
            t = 0.0 if x1 == x0 else min(1.0, max(0.0, (x - x0) / (x1 - x0)))
            row = y0 + t * (y1 - y0)
            if abs(y - row) <= self.window_px:
                hits.append((s, y - row))
        if len(hits) != 1:
            return                      # nothing near, or stacked tiers
        seg, residual = hits[0]
        with self._lock:
            per = self._samples.setdefault(map_name, {})
            lst = per.setdefault(self.key(seg), [])
            lst.append((float(x), float(residual)))
            del lst[:-self.max_samples]
        if self.on_change:
            self.on_change(map_name)

    def summary(self, map_name: Optional[str], segs_norm, region) -> List[dict]:
        """Per drawn platform: where it is, sample count, median offset
        and spread (px), and how much of its length was stood on."""
        if not map_name or not segs_norm or not region:
            return []
        w, h = region[2], region[3]
        with self._lock:
            per = dict(self._samples.get(map_name, {}))
        out = []
        for s in segs_norm:
            x0, x1 = sorted((s[0] * w, s[2] * w))
            row = (s[1] + s[3]) / 2 * h
            samples = per.get(self.key(s), [])
            item = {
                "x0": round(x0), "x1": round(x1), "row": round(row, 1),
                "n": len(samples),
            }
            if samples:
                res = [r for _, r in samples]
                med = statistics.median(res)
                item["offset"] = round(med, 1)
                item["spread"] = round(
                    statistics.median(abs(r - med) for r in res), 1)
                xs = [x for x, _ in samples]
                item["coverage"] = round(
                    (max(xs) - min(xs)) / max(1.0, x1 - x0), 2)
            out.append(item)
        out.sort(key=lambda d: (d["row"], d["x0"]))
        return out

    def forget(self, map_name: str) -> None:
        with self._lock:
            self._samples.pop(map_name, None)


__all__ = [
    "PlatformFit",
    "merge_level",
    "straighten",
    "tidy_segments",
]
