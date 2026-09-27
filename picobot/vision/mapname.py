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
    if not text or not isinstance(text, str):
        return ""
    return re.sub(r"[^0-9a-z]+", "", text.lower())


def name_strip_region(
    minimap_region: Region,
    header_px: int = 90,
    window_w: Optional[int] = None,
) -> Region:
    """Capture band covering the title wherever the client draws it.

    Spans from the window's top edge to ``header_px`` into the minimap
    region — covers both layouts: the title strip *above* the map frame
    (border-box detection) and the title *inside* the panel top (panel
    detection). Width is the full window width (``window_w``) when
    known — titles can overflow the map frame, so a region-width band
    would clip long names mid-glyph. :func:`title_lines` finds the text
    inside either way, so the capture can be generous.
    """
    x, y, w, h = minimap_region
    band_w = max(w, window_w) if window_w else w
    return (0, 0, band_w, min(y + header_px, y + h))


def _row_groups(white: np.ndarray, min_row_px: int, gap: int):
    """Contiguous row-runs carrying text, merging ≤``gap``-row gaps."""
    counts = white.sum(axis=1)
    lines = []
    start = None
    blank = 0
    for y, c in enumerate(counts):
        if c >= min_row_px:
            if start is None:
                start = y
            last = y
            blank = 0
        elif start is not None:
            blank += 1
            if blank > gap:
                lines.append((start, last))
                start = None
                blank = 0
    if start is not None:
        lines.append((start, last))
    return lines


def title_lines(
    band: np.ndarray,
    white_thresh: int = 170,
    min_row_px: int = 2,
    gap_rows: int = 3,
    min_line_h: int = 4,
    min_line_px: int = 15,
    col_gap: int = 4,
    icon_fill: float = 0.5,
    divider_run: int = 120,
) -> list:
    """Segment white-text title lines out of a header band.

    Returns ``[(x0, y0, x1, y1), ...]`` boxes (band coords), top→down.

    - Near-white mask: the map title renders in white; the toolbar icons
      and the colored map icon mostly fail the all-channels test.
    - Rows with text are grouped into lines — the divider and thin map
      lines are 1-2px tall and never reach ``min_line_h``.
    - The map icon *does* have white highlights, so within the text zone
      columns are split on ``col_gap``-wide gaps and dense runs
      (``fill > icon_fill``) are dropped — icons are filled blocks,
      text is sparse. Works at any panel width.
    - Stops at the divider row — the first row with a contiguous
      bright run ≥ ``divider_run`` px (a solid panel separator vs.
      ~15px glyph runs) — so map content can't be mistaken for text.
    """
    if not isinstance(band, np.ndarray) or band.size == 0:
        return []
    white = band.min(axis=2) > white_thresh
    w = white.shape[1]
    # Divider: first row carrying a *contiguous* bright run — the panel
    # separator is one solid line, text rows are sparse. Anchored to
    # structure, not to where the minimap region claims the title is.
    div = white.shape[0]
    for y in range(white.shape[0]):
        row = white[y]
        # longest contiguous True run
        idx = np.flatnonzero(np.diff(np.concatenate(([False], row, [False]))))
        if len(idx) >= 2 and (idx[1::2] - idx[0::2]).max() >= divider_run:
            div = y
            break
    white = white[:div]
    if white.size == 0:
        return []
    lines = [
        (a, b)
        for a, b in _row_groups(white, min_row_px, gap_rows)
        if b - a + 1 >= min_line_h and white[a : b + 1].sum() >= min_line_px
    ]
    if not lines:
        return []
    # Icon cut: column runs over the whole text zone; dense leading
    # runs are icons, sparse runs are glyphs.
    zone = white[lines[0][0] : lines[-1][1] + 1]
    colc = zone.sum(axis=0)
    on = colc > 0
    # runs of on-columns
    runs = []
    rs = None
    for x in range(w + 1):
        if x < w and on[x]:
            if rs is None:
                rs = x
        elif rs is not None:
            runs.append((rs, x))
            rs = None
    # merge runs separated by < col_gap (letter gaps inside words)
    merged = []
    for a, b in runs:
        if merged and a - merged[-1][1] < col_gap:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    keep = []
    for a, b in merged:
        fill = colc[a:b].sum() / ((b - a) * zone.shape[0])
        if fill <= icon_fill:
            keep.append((a, b))
    if not keep:
        keep = merged  # nothing looked like an icon — take everything
    x0 = min(a for a, _ in keep)
    x1 = max(b for _, b in keep)
    x0 = max(0, x0 - 2)
    x1 = min(w, x1 + 2)
    return [
        (x0, max(0, a - 1), x1, min(div, b + 2)) for a, b in lines
    ]


def title_crop(band: np.ndarray, **kw) -> Optional[np.ndarray]:
    """One tight crop covering all detected title lines, else None."""
    lines = title_lines(band, **kw)
    if not lines:
        return None
    y0 = min(b[1] for b in lines)
    y1 = max(b[3] for b in lines)
    x0 = min(b[0] for b in lines)
    x1 = max(b[2] for b in lines)
    return band[y0:y1, x0:x1]


class MapNameReader:
    """OCR a BGR name-strip capture -> raw text (or None).

    The band is segmented first (:func:`title_crop`) — the engine only
    sees tight white-text lines, never the icon row or map content.
    No text lines -> None (fail closed; icons can't produce artifacts)."""

    def __init__(self, min_confidence: float = 0.6, upscale: int = 3) -> None:
        self.min_confidence = min_confidence
        self.upscale = upscale

    def read(self, img: Optional[np.ndarray]) -> Optional[str]:
        if not isinstance(img, np.ndarray) or img.size == 0:
            return None
        engine = _get_engine()
        if engine is None:
            return None
        try:
            crop = title_crop(img)
            if crop is None:
                return None
            prep = self._prepare(crop)
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
    "title_crop",
    "title_lines",
]
