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


def _zone(img: np.ndarray, x0: int, y0: int, x1: int, y1: int, color) -> None:
    """Translucent fill + solid border — a forbidden region (wall/floor).

    The wall/floor marks a boundary, not a line to read: shading the
    *blocked* side makes the zone readable at a glance.
    """
    ih, iw = img.shape[:2]
    x0, x1 = max(0, x0), min(iw, x1)
    y0, y1 = max(0, y0), min(ih, y1)
    if x1 <= x0 or y1 <= y0:
        return
    img[y0:y1, x0:x1] = (
        img[y0:y1, x0:x1].astype(np.float32) * 0.7
        + np.asarray(color, np.float32) * 0.3
    ).astype(np.uint8)
    _rect(img, x0, y0, x1 - x0, y1 - y0, color)


def _line(img: np.ndarray, x0, y0, x1, y1, color) -> None:
    """Hand-drawn platform segment overlay."""
    h, w = img.shape[:2]
    n = int(max(abs(x1 - x0), abs(y1 - y0))) + 1
    for i in range(n + 1):
        t = i / n
        x, y = int(round(x0 + (x1 - x0) * t)), int(round(y0 + (y1 - y0) * t))
        if 0 <= x < w and 0 <= y < h:
            img[y, x] = color
            if y + 1 < h:
                img[y + 1, x] = color  # 2px thick so it reads on the stream


def annotate(img: np.ndarray, meta: dict) -> np.ndarray:
    """Draw overlay markers onto a copy of the frame."""
    out = img.copy()
    h, w = out.shape[:2]
    zone = (60, 60, 255)                                     # red zones
    walls = meta.get("walls") or {}
    if walls.get("left") is not None:
        _zone(out, 0, 0, int(walls["left"]), h, zone)          # block left
    if walls.get("right") is not None:
        _zone(out, int(walls["right"]), 0, w, h, zone)         # block right
    floor = meta.get("floor")
    if floor is not None:
        _zone(out, 0, int(floor), w, h, zone)                  # below floor
    for seg in meta.get("platforms") or []:
        _line(out, *seg, (255, 200, 40))                  # cyan platforms
    for x, y in meta.get("anchors") or []:
        _box(out, int(x), int(y), 3, (255, 128, 0))      # blue anchors
    pos = meta.get("player")
    if pos:
        _box(out, int(pos[0]), int(pos[1]), 4, (0, 255, 0))  # green player
    target = meta.get("target")
    if target:
        _cross(out, int(target[0]), int(target[1]), 4, (0, 0, 255))  # red
    rune = meta.get("rune")
    if rune:
        _box(out, int(rune[0]), int(rune[1]), 4, (255, 0, 255))  # purple rune
    elif meta.get("hazard") == "rune":
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
                        "state", "map", "map_conf", "hazard", "player",
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
