"""Screen capture and template matching for PicoBot.

`mss` does capture; `cv2` is only needed for template matching and is
imported lazily so the minimap path stays pure-NumPy.
"""

from __future__ import annotations

from typing import Optional, Tuple

import numpy as np

from .minimap import Region


class ScreenGrabber:
    """Captures BGR screenshots of a region of the virtual desktop."""

    def __init__(self) -> None:
        import mss  # deferred: only needed when actually capturing

        self._sct = mss.mss()

    def close(self) -> None:
        sct = getattr(self, "_sct", None)
        if sct is not None:
            sct.close()
            self._sct = None

    def capture(self, region: Region) -> Optional[np.ndarray]:
        """Capture ``(left, top, width, height)`` and return a BGR ndarray."""
        if self._sct is None:
            return None
        left, top, width, height = (int(v) for v in region)
        if width <= 0 or height <= 0:
            return None
        try:
            shot = self._sct.grab(
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
