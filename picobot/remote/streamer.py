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


def assemble_panel(
    band: Optional[np.ndarray],
    map_img: np.ndarray,
    region: Tuple[int, int, int, int],
    band_xy: Tuple[int, int] = (0, 0),
    pad: int = 6,
) -> Tuple[np.ndarray, int, int]:
    """Composite the title band + minimap into one panel image.

    ``band_xy`` is the band capture's top-left in window coords.

    Returns ``(img, dx, dy)`` — ``img`` places the minimap at offset
    ``(dx, dy)``, so region-space overlay coords shift by that much.

    The panel is the union of the located title zone and the minimap
    region; its right edge extends to the title text's end so long map
    names aren't clipped by the map frame's width. A separator line
    divides the name zone from the map area (the panel's own divider
    row when detected, else the title zone's bottom edge).
    """
    from ..vision.mapname import title_scan

    rx, ry, rw, rh = region
    bx, by = band_xy
    lines, div = ([], None)
    if isinstance(band, np.ndarray) and band.size:
        lines, div = title_scan(band)
        lines = [(a + bx, c + by, e + bx, f + by) for a, c, e, f in lines]
        if div is not None:
            div = (div[0] + by, div[1] + bx, div[2] + bx)
    if lines:
        tx0 = min(b[0] for b in lines)
        ty0 = min(b[1] for b in lines)
        tx1 = max(b[2] for b in lines)
        ty1 = max(b[3] for b in lines)
        x0 = max(0, min(rx, tx0 - pad))
        x1 = max(rx + rw, tx1 + pad)
        if div is not None:
            # The game fades/clip the title at the panel edge — the
            # divider's end IS that edge, so reach it even when the
            # faded tail fell below the near-white threshold.
            x1 = max(x1, div[2] + pad)
        y0 = max(0, min(ry, ty0 - pad))
    else:
        tx0 = ty0 = tx1 = ty1 = 0
        x0, x1, y0 = rx, rx + rw, ry
    y1 = ry + rh
    canvas = np.zeros((y1 - y0, x1 - x0, 3), dtype=np.uint8)
    if isinstance(band, np.ndarray) and band.size:
        bh, bw = band.shape[:2]
        # band occupies window rect (bx, by, bw, bh)
        gx0, gx1 = max(bx, x0) - bx, min(bx + bw, x1) - bx
        gy0, gy1 = max(by, y0) - by, min(by + bh, y1) - by
        if gx1 > gx0 and gy1 > gy0:
            canvas[
                by + gy0 - y0 : by + gy1 - y0,
                bx + gx0 - x0 : bx + gx1 - x0,
            ] = band[gy0:gy1, gx0:gx1]
    canvas[ry - y0 : ry - y0 + rh, rx - x0 : rx - x0 + rw] = map_img
    # Title zone markup: green boxes on the accepted lines, the orange
    # zone is exactly what OCR reads.
    for bx0, by0, bx1, by1 in lines:
        _rect(canvas, bx0 - x0, by0 - y0, bx1 - bx0, by1 - by0, (0, 255, 0))
    if lines:
        _zone(canvas, tx0 - x0, ty0 - y0, tx1 - x0, ty1 - y0, (0, 120, 255))
    # Name|map boundary: the panel's own divider row when it was found
    # inside the canvas, else the bottom of the title zone.
    sep_win = None
    if div is not None and y0 <= div[0] <= y1:
        sep_win = div[0]
    elif lines:
        sep_win = ty1 + pad
    if sep_win is not None:
        sep = sep_win - y0
        for sy in (sep, sep + 1):
            if 0 <= sy < canvas.shape[0]:
                canvas[sy, :] = (255, 200, 40)
    # Map-area box so the region extent reads clearly against the band.
    _rect(canvas, rx - x0, ry - y0, rw, rh, (0, 255, 255))
    return canvas, rx - x0, ry - y0


def annotate_title(band: np.ndarray) -> np.ndarray:
    """Title-band debug view: green boxes on the accepted text lines,
    red outline on the full crop OCR sees. Lets the user verify the
    segmentation before trusting the OCR text."""
    from ..vision.mapname import title_lines

    out = band.copy()
    lines = title_lines(band)
    for x0, y0, x1, y1 in lines:
        _rect(out, x0, y0, x1 - x0, y1 - y0, (0, 255, 0))
    if lines:
        y0 = min(b[1] for b in lines)
        y1 = max(b[3] for b in lines)
        x0 = min(b[0] for b in lines)
        x1 = max(b[2] for b in lines)
        _zone(out, x0, y0, x1, y1, (0, 120, 255))   # orange = OCR input
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
        if mode in ("minimap", "window", "title"):
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
                        "state", "map", "map_via", "map_conf", "map_title",
                        "hazard", "player",
                        "layout", "no_rotation", "ox", "oy",
                    ):
                        if snap.get(key) is not None:
                            payload[key] = snap[key]
                    self.send("dash|" + json.dumps(payload))
            except Exception:
                logger.debug("frame stream failed", exc_info=True)
            elapsed = time.time() - started
            self._stop.wait(max(0.05, self.interval - elapsed))


__all__ = ["FrameStreamer", "Provider", "annotate", "annotate_title", "encode_jpeg"]
