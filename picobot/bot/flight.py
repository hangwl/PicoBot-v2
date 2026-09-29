"""Flight recorder: the player dot's whole path through one move.

A landing only says where a move ended. The recorder samples the dot on
its own thread (screen grabs and the dot tracker are per-thread) at a
fixed rate while a move runs, so its peak and airtime are seen too.
Marks timestamp key events on the same clock ("jump", "rejump").
"""

from __future__ import annotations

import statistics
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

Point = Tuple[int, int]
Sample = Tuple[float, int, int]           # (seconds since start, x, y)

STABLE_SAMPLES = 4


@dataclass
class Flight:
    samples: List[Sample]
    marks: Dict[str, float] = field(default_factory=dict)

    def _t0(self) -> float:
        return self.marks.get("jump", 0.0)

    def start_y(self) -> Optional[float]:
        """Standing row before takeoff (median of the pre-jump samples)."""
        if not self.samples:
            return None
        t0 = self._t0()
        before = [y for t, _, y in self.samples if t <= t0] or [self.samples[0][2]]
        return float(statistics.median(before))

    def _smoothed(self) -> List[Sample]:
        """Median-of-3 on y: one stray detection can't fake a peak."""
        s = self.samples
        if len(s) < 3:
            return list(s)
        out = [s[0]]
        for i in range(1, len(s) - 1):
            t, x, _ = s[i]
            out.append((t, x, sorted((s[i - 1][2], s[i][2], s[i + 1][2]))[1]))
        out.append(s[-1])
        return out

    def peak(self) -> Optional[Tuple[float, float]]:
        """(seconds after the jump, rise in px) at the highest point."""
        y0 = self.start_y()
        if y0 is None:
            return None
        t0 = self._t0()
        air = [(t, y) for t, _, y in self._smoothed() if t >= t0]
        if not air:
            return None
        t, y = min(air, key=lambda ty: (ty[1], ty[0]))
        return t - t0, y0 - y

    def landing(self) -> Optional[Tuple[float, float]]:
        """(seconds after the jump, rise in px) where the dot comes to
        rest after the peak — None if it never settles."""
        pk = self.peak()
        y0 = self.start_y()
        if pk is None or y0 is None:
            return None
        t0 = self._t0()
        after = [(t, y) for t, _, y in self.samples if t >= t0 + pk[0]]
        for i in range(len(after) - STABLE_SAMPLES + 1):
            run = after[i:i + STABLE_SAMPLES]
            ys = [y for _, y in run]
            if max(ys) - min(ys) <= 1 and y0 - ys[0] < pk[1] - 1:
                return run[0][0] - t0, y0 - ys[0]
        return None

    def gap(self, first: str = "jump", second: str = "rejump") -> Optional[float]:
        if first in self.marks and second in self.marks:
            return self.marks[second] - self.marks[first]
        return None


class FlightRecorder:
    def __init__(
        self,
        capture: Callable[[], Optional[Point]],
        period: float = 1.0 / 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._capture = capture
        self.period = period
        self._clock = clock
        self._samples: List[Sample] = []
        self._marks: Dict[str, float] = {}
        self._t0 = 0.0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._samples, self._marks = [], {}
        self._t0 = self._clock()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="FlightRecorder", daemon=True
        )
        self._thread.start()

    def mark(self, name: str) -> None:
        self._marks[name] = self._clock() - self._t0

    def stop(self) -> Flight:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self._thread = None
        return Flight(list(self._samples), dict(self._marks))

    def _run(self) -> None:
        while not self._stop.is_set():
            started = self._clock()
            try:
                pos = self._capture()
            except Exception:
                pos = None
            if pos is not None:
                self._samples.append((started - self._t0, int(pos[0]), int(pos[1])))
            self._stop.wait(max(0.002, self.period - (self._clock() - started)))


__all__ = ["Flight", "FlightRecorder"]
