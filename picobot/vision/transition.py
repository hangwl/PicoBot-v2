"""Map-transfer detection from the loading blackout.

Every map transfer blacks out the whole client (all pixels 0) for about
a second. Translucent UI can't produce that, so it is the only map-change
trigger.
"""

from __future__ import annotations

import time
from typing import Callable, Optional

import numpy as np


def is_dark(img: Optional[np.ndarray], level: int = 12) -> bool:
    """True when (nearly) every pixel is black."""
    if img is None or img.size == 0:
        return False
    sample = img[::2, ::2]
    return float(np.percentile(sample, 99)) <= level


class TransitionDetector:
    """``normal`` → ``dark`` (after ``min_dark_s``) → ``settling`` →
    ``normal`` (after ``settle_s`` of non-dark frames).

    :meth:`note` returns ``"loading"`` when a blackout is confirmed and
    ``"arrived"`` once the new map has been visible for ``settle_s``.
    """

    def __init__(
        self,
        *,
        level: int = 12,
        min_dark_s: float = 0.15,
        settle_s: float = 0.6,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.level = level
        self.min_dark_s = min_dark_s
        self.settle_s = settle_s
        self._clock = clock
        self.state = "normal"
        self._dark_since: Optional[float] = None
        self._settle_since: Optional[float] = None

    @property
    def loading(self) -> bool:
        return self.state != "normal"

    def reset(self) -> None:
        self.state = "normal"
        self._dark_since = self._settle_since = None

    def note(self, img: Optional[np.ndarray]) -> Optional[str]:
        if img is None:
            return None
        now = self._clock()
        if is_dark(img, self.level):
            if self.state == "settling":
                self.state = "dark"
                return None
            if self.state == "normal":
                if self._dark_since is None:
                    self._dark_since = now
                if now - self._dark_since >= self.min_dark_s:
                    self.state = "dark"
                    return "loading"
            return None
        self._dark_since = None
        if self.state == "dark":
            self.state = "settling"
            self._settle_since = now
        if self.state == "settling" and now - self._settle_since >= self.settle_s:
            self.state = "normal"
            return "arrived"
        return None


__all__ = ["TransitionDetector", "is_dark"]
