"""OCR reader for the map-name text strip near the minimap.

The game UI prints the area/map name as text adjacent to the minimap —
an exact, human-readable identity signal: immune to translucent
backgrounds, minimap animation, and look-alike layouts that confuse
pixel-hash fingerprints. Uses RapidOCR (ONNX runtime, bundled models, no
external binary). When the engine can't load, every read returns None
and callers fall back to fingerprint matching.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

Region = Tuple[int, int, int, int]

_engine = None
_engine_failed = False
_engine_lock = threading.Lock()


def _get_engine():
    """Process-wide lazily-built RapidOCR engine (first load is ~1s)."""
    global _engine, _engine_failed
    if _engine is not None or _engine_failed:
        return _engine
    with _engine_lock:
        if _engine is not None or _engine_failed:
            return _engine
        try:
            from rapidocr import RapidOCR

            _engine = RapidOCR()
        except Exception as exc:
            logger.warning("RapidOCR unavailable: %s", exc)
            _engine_failed = True
            _engine = None
    return _engine


def available() -> bool:
    """Whether the OCR engine could be built (models + onnxruntime)."""
    return _get_engine() is not None


def normalize_name(text: Optional[str]) -> str:
    """Lowercase alnum-only form for equality matching.

    OCR of small game text adds spacing/punctuation noise
    (``"Limina : 1-5"``), so comparisons ignore everything but letters
    and digits.
    """
    if not text:
        return ""
    return re.sub(r"[^0-9a-z]+", "", text.lower())


def name_strip_region(minimap_region: Region, height: int = 26) -> Region:
    """Everything above the minimap: same width, from the window's top
    edge down to the minimap's top edge.

    The minimap always sits top-left, so the area above it is where the
    client draws the area/map name — scanning the whole band avoids
    guessing the title's exact offset. When the minimap is flush with
    the top edge (y == 0) there is no band: fall back to a
    ``height``-tall strip inside the minimap's top edge, where clients
    that embed the title in the panel header put it.
    """
    x, y, w, h = minimap_region
    if y > 0:
        return (x, 0, w, y)
    return (x, y, w, min(height, h))


class MapNameReader:
    """OCR a BGR name-strip capture -> raw text (or None)."""

    def __init__(self, min_confidence: float = 0.6, upscale: int = 3) -> None:
        self.min_confidence = min_confidence
        self.upscale = upscale

    def read(self, img: Optional[np.ndarray]) -> Optional[str]:
        if img is None or img.size == 0:
            return None
        engine = _get_engine()
        if engine is None:
            return None
        prep = self._prepare(img)
        try:
            out = engine(prep)
        except Exception:
            logger.debug("map-name OCR failed", exc_info=True)
            return None
        txts = getattr(out, "txts", None)
        if not txts:
            return None
        scores = getattr(out, "scores", None) or (1.0,) * len(txts)
        texts = [t for t, c in zip(txts, scores) if c >= self.min_confidence]
        name = " ".join(texts).strip()
        return name or None

    def _prepare(self, img: np.ndarray) -> np.ndarray:
        """Upscale small text for the detector; BGR -> RGB."""
        from PIL import Image

        h, w = img.shape[:2]
        rgb = img[:, :, ::-1]
        pil = Image.fromarray(rgb).resize(
            (w * self.upscale, h * self.upscale), Image.LANCZOS
        )
        return np.asarray(pil)


__all__ = [
    "MapNameReader",
    "Region",
    "available",
    "name_strip_region",
    "normalize_name",
]
