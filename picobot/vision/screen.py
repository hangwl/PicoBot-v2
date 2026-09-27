"""Screen capture and template matching for PicoBot.

`mss` does capture; `cv2` is only needed for template matching and is
imported lazily so the minimap path stays pure-NumPy.
"""

from __future__ import annotations

import threading
from typing import Callable, Optional, Tuple

import numpy as np

from .minimap import Region


def _new_mss():
    import mss  # deferred: only needed when actually capturing

    return mss.mss()


class ScreenGrabber:
    """Captures BGR screenshots of a region of the virtual desktop.

    Safe to share across threads: mss handles are thread-bound on
    Windows, so each calling thread lazily gets its own instance.
    """

    def __init__(self, factory: Callable[[], object] = _new_mss) -> None:
        self._factory = factory
        self._local = threading.local()
        self._all: list = []
        self._lock = threading.Lock()
        self._closed = False

    def _sct(self):
        if self._closed:
            return None
        sct = getattr(self._local, "sct", None)
        if sct is None:
            sct = self._factory()
            self._local.sct = sct
            with self._lock:
                self._all.append(sct)
        return sct

    def close(self) -> None:
        with self._lock:
            self._closed = True
            all_, self._all = self._all, []
        for sct in all_:
            try:
                sct.close()
            except Exception:
                pass

    def capture(self, region: Region) -> Optional[np.ndarray]:
        """Capture ``(left, top, width, height)`` and return a BGR ndarray."""
        left, top, width, height = (int(v) for v in region)
        if width <= 0 or height <= 0:
            return None
        sct = self._sct()
        if sct is None:
            return None
        try:
            shot = sct.grab(
                {"left": left, "top": top, "width": width, "height": height}
            )
        except Exception:
            return None
        # mss returns BGRA; drop the alpha channel.
        return np.asarray(shot)[:, :, :3]


def template_rect(
    scene: np.ndarray,
    template: np.ndarray,
    threshold: float = 0.9,
) -> Optional[Tuple[int, int, int, int]]:
    """Locate ``template`` inside ``scene``.

    Returns ``(x, y, w, h)`` of the best match, or None below ``threshold``.
    """
    import cv2  # deferred heavy dependency

    if scene is None or template is None:
        return None
    if scene.shape[0] < template.shape[0] or scene.shape[1] < template.shape[1]:
        return None
    result = cv2.matchTemplate(scene, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    if max_val < threshold:
        return None
    h, w = template.shape[:2]
    return int(max_loc[0]), int(max_loc[1]), w, h


__all__ = ["ScreenGrabber", "template_rect"]
