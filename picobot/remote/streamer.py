"""Frame streaming for the web dashboard.

Grabs the latest bot snapshot on a timer, annotates it (anchors, player
dot, nav target, markers), JPEG-encodes, and pushes it to a broadcast
callable as ``dash|{json}`` messages. The provider callable supplies the
image + metadata, so the same streamer serves the live bot view, a raw
window feed, or the calibration recorder.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import threading
import time
from typing import Callable, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

Provider = Callable[[str], Optional[dict]]
"""mode -> {"img": ndarray|None, ...metadata}"""


def _box(img: np.ndarray, cx: int, cy: int, r: int, color) -> None:
    h, w = img.shape[:2]
    x0, x1 = max(0, cx - r), min(w, cx + r + 1)
    y0, y1 = max(0, cy - r), min(h, cy + r + 1)
    img[y0:y1, x0] = color
    img[y0:y1, x1 - 1] = color
    img[y0, x0:x1] = color
    img[y1 - 1, x0:x1] = color


def _cross(img: np.ndarray, cx: int, cy: int, r: int, color) -> None:
    h, w = img.shape[:2]
    for i in range(-r, r + 1):
        x, y = cx + i, cy + i
        if 0 <= x < w and 0 <= cy < h:
            img[cy, x] = color
        if 0 <= cx < w and 0 <= y < h:
            img[y, cx] = color


def _rect(img: np.ndarray, x: int, y: int, w: int, h: int, color) -> None:
    ih, iw = img.shape[:2]
    x0, x1 = max(0, x), min(iw, x + w)
    y0, y1 = max(0, y), min(ih, y + h)
    if x1 <= x0 or y1 <= y0:
        return
    img[y0, x0:x1] = color
    img[y1 - 1, x0:x1] = color
    img[y0:y1, x0] = color
    img[y0:y1, x1 - 1] = color


def annotate(img: np.ndarray, meta: dict) -> np.ndarray:
    """Draw overlay markers onto a copy of the frame."""
    out = img.copy()
    for x, y in meta.get("anchors") or []:
        _box(out, int(x), int(y), 3, (255, 128, 0))      # blue anchors
    pos = meta.get("player")
    if pos:
        _box(out, int(pos[0]), int(pos[1]), 4, (0, 255, 0))  # green player
    target = meta.get("target")
    if target:
        _cross(out, int(target[0]), int(target[1]), 4, (0, 0, 255))  # red
    if meta.get("hazard") == "rune":
        _box(out, out.shape[1] // 2, 6, 5, (255, 0, 255))
    if meta.get("hazard") == "other players":
        _box(out, out.shape[1] // 2, 14, 5, (0, 255, 255))
    return out


def encode_jpeg(img: np.ndarray, quality: int = 70) -> str:
    """BGR ndarray -> base64 JPEG. PIL imported lazily."""
    from PIL import Image

    rgb = img[:, :, ::-1]
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, format="JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode("ascii")


class FrameStreamer:
    """Periodic snapshot -> annotated JPEG -> broadcast."""

    def __init__(
        self,
        provider: Provider,
        send: Callable[[str], None],
        *,
        interval: float = 0.35,
        quality: int = 70,
    ) -> None:
        self.provider = provider
        self.send = send
        self.interval = interval
        self.quality = quality
        self.mode = "minimap"
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="FrameStreamer", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)
        self._thread = None

    def set_mode(self, mode: str) -> None:
        if mode in ("minimap", "window"):
            self.mode = mode

    def _loop(self) -> None:
        while not self._stop.is_set():
            started = time.time()
            try:
                snap = self.provider(self.mode) or {}
                img = snap.get("img")
                if img is not None:
                    frame = annotate(img, snap)
                    payload = {
                        "event": "frame",
                        "mode": self.mode,
                        "w": int(frame.shape[1]),
                        "h": int(frame.shape[0]),
                        "jpeg": encode_jpeg(frame, self.quality),
                    }
                    for key in (
                        "state", "map", "hazard", "player",
                        "layout", "no_rotation",
                    ):
                        if snap.get(key) is not None:
                            payload[key] = snap[key]
                    self.send("dash|" + json.dumps(payload))
            except Exception:
                logger.debug("frame stream failed", exc_info=True)
            elapsed = time.time() - started
            self._stop.wait(max(0.05, self.interval - elapsed))


__all__ = ["FrameStreamer", "Provider", "annotate", "encode_jpeg"]
