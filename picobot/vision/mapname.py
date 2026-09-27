"""OCR reader for the map-name text strip near the minimap.

The game prints the street/map name above the minimap frame — the map
identity signal. Uses RapidOCR (ONNX runtime, bundled models). When the
engine can't load, every read returns None and only the pin applies.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Optional, Tuple

import numpy as np

from . import framelog

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


def title_score(ocr_text: Optional[str], candidate: Optional[str]) -> float:
    """0–1 similarity of an OCR'd title to a stored name.

    Best of: substring containment (weighted by how much of the OCR text
    the candidate covers), prefix similarity for titles the client
    clipped (``Happir`` vs ``Happiness``), and whole-string similarity
    for misreads.
    """
    from difflib import SequenceMatcher

    ocr, cand = normalize_name(ocr_text), normalize_name(candidate)
    if not ocr or not cand:
        return 0.0
    if ocr == cand:
        return 1.0
    score = SequenceMatcher(None, ocr, cand).ratio()
    if len(cand) >= 5 and cand in ocr:
        score = max(score, 0.85 + 0.15 * len(cand) / len(ocr))
    if len(ocr) >= 6 and len(ocr) < len(cand):
        prefix = SequenceMatcher(None, ocr, cand[: len(ocr)]).ratio()
        score = max(score, 0.85 * prefix + 0.15 * len(ocr) / len(cand))
    return min(1.0, score)


def name_strip_region(
    minimap_region: Region,
    header_px: int = 90,
    pad: int = 4,
) -> Region:
    """Title band: the panel's width (± ``pad``), from the window top to
    ``header_px`` into the minimap frame.

    The client clips the title at the panel edge, so nothing useful lies
    outside the frame's width — and a wider band picks up other UI text.
    :func:`title_scan` cuts it back at the frame's top edge.
    """
    x, y, w, h = minimap_region
    bx = max(0, x - pad)
    return (bx, 0, x + w + pad - bx, y + min(header_px, h))


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


def _icon_tile_end(zone: np.ndarray, min_h: int = 20) -> Optional[int]:
    """Right edge of the region-icon tile left of the title, or None.

    The tile is a square with a light frame spanning both title lines,
    so its sides are near-full-height unbroken white columns — taller
    than any glyph. Its colourful interior can fail the near-white test,
    so density alone doesn't catch it.
    """
    h, w = zone.shape
    if h < min_h:
        return None
    run = np.zeros(w, dtype=np.int32)
    best = np.zeros(w, dtype=np.int32)
    for row in zone:
        run = (run + 1) * row
        np.maximum(best, run, out=best)
    tall = np.flatnonzero(best >= 0.7 * h)
    if tall.size == 0 or tall[0] >= w // 2:
        return None
    side = tall[tall <= tall[0] + int(1.6 * h)]
    return int(side[-1]) + 1


def title_scan(
    band: np.ndarray,
    white_thresh: int = 170,
    min_row_px: int = 2,
    gap_rows: int = 3,
    min_line_h: int = 4,
    min_line_px: int = 15,
    col_gap: int = 4,
    icon_fill: float = 0.5,
    divider_run: int = 120,
    tail_thresh: int = 80,
    tail_gap: int = 12,
) -> tuple:
    """Segment the header band -> ``(lines, divider_y)``.

    ``lines`` are ``[(x0, y0, x1, y1), ...]`` text-line boxes (band
    coords), top→down. ``divider`` is ``(y, x0, x1)`` — the row and
    x-extent of the panel's solid separator, i.e. the panel's own
    edges — or ``None`` when no divider was found. Its right edge is
    where the game clips/fades the title, so callers can extend the
    title zone to it and recover characters the near-white test dropped.

    - Near-white mask: the map title renders in white; the toolbar icons
      and the colored map icon mostly fail the all-channels test.
    - Rows with text are grouped into lines — the divider and thin map
      lines are 1-2px tall and never reach ``min_line_h``.
    - The region-icon tile left of the title is cut by its frame
      (:func:`_icon_tile_end`); remaining dense column runs
      (``fill > icon_fill``) are dropped too — icons are filled blocks,
      text is sparse.
    - Stops at the divider row — the first row with a contiguous
      bright run ≥ ``divider_run`` px (a solid panel separator vs.
      ~15px glyph runs) — so map content can't be mistaken for text.
    """
    if not isinstance(band, np.ndarray) or band.size == 0:
        return [], None
    white = band.min(axis=2) > white_thresh
    w = white.shape[1]
    # Divider: first row carrying a *contiguous* bright run — the panel
    # separator is one solid line, text rows are sparse. Anchored to
    # structure, not to where the minimap region claims the title is.
    div = None
    cut = white.shape[0]
    for y in range(white.shape[0]):
        row = white[y]
        # longest contiguous True run
        idx = np.flatnonzero(np.diff(np.concatenate(([False], row, [False]))))
        if len(idx) >= 2:
            lens = idx[1::2] - idx[0::2]
            k = int(lens.argmax())
            if lens[k] >= divider_run:
                div = (y, int(idx[2 * k]), int(idx[2 * k + 1]))
                cut = y
                break
    white = white[:cut]
    if white.size == 0:
        return [], div
    def line_groups():
        return [
            (a, b)
            for a, b in _row_groups(white, min_row_px, gap_rows)
            if b - a + 1 >= min_line_h and white[a : b + 1].sum() >= min_line_px
        ]

    groups = line_groups()
    if not groups:
        return [], div
    tile_end = _icon_tile_end(white[groups[0][0] : groups[-1][1] + 1])
    if tile_end is not None:
        white = white.copy()
        white[:, : tile_end + 1] = False
        groups = line_groups()
        if not groups:
            return [], div
    # Icon cut: column runs over the whole text zone; dense leading
    # runs are icons, sparse runs are glyphs.
    zone = white[groups[0][0] : groups[-1][1] + 1]
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
    # Faded tail: clients that clip overflowing titles often render the
    # tail with a fade-out, dropping those glyphs below ``white_thresh``
    # and truncating the detected extent mid-word. Keep extending while
    # dimmer-but-bright columns appear inside the text rows — gaps
    # beyond ``tail_gap`` dark columns end the title.
    dim = band.min(axis=2) > tail_thresh
    tail_cols = np.flatnonzero(
        dim[groups[0][0] : groups[-1][1] + 1].any(axis=0)
    )
    for x in tail_cols:
        if x < x1:
            continue
        if x - x1 > tail_gap:
            break
        x1 = x
    x0 = max(0, x0 - 2)
    x1 = min(w, x1 + 2)
    lines = [
        (x0, max(0, a - 1), x1, min(cut, b + 2)) for a, b in groups
    ]
    return lines, div


def title_lines(band: np.ndarray, **kw) -> list:
    """Accepted text-line boxes of the header band — see title_scan."""
    return title_scan(band, **kw)[0]


def title_crop(
    band: np.ndarray, vpad: int = 4, margin: int = 8, **kw
) -> Optional[np.ndarray]:
    """Crop covering all detected title lines, else None.

    The right edge extends to the divider's end — the game fades the
    title out at the panel edge and RapidOCR can still read the faded
    tail. The crop is framed by ``margin`` px of its median background
    colour: the recogniser misreads glyphs touching the crop edge, and
    padding from the band itself would pull the region icon back in.
    """
    lines, div = title_scan(band, **kw)
    if not lines:
        return None
    y0 = max(0, min(b[1] for b in lines) - vpad)
    y1 = min(band.shape[0], max(b[3] for b in lines) + vpad)
    x0 = min(b[0] for b in lines)
    x1 = max(b[2] for b in lines)
    if div is not None:
        x1 = max(x1, div[2])
    crop = band[y0:y1, x0 : min(x1, band.shape[1])]
    bg = np.median(crop.reshape(-1, crop.shape[2]), axis=0).astype(band.dtype)
    out = np.empty(
        (crop.shape[0] + 2 * margin, crop.shape[1] + 2 * margin, crop.shape[2]),
        dtype=band.dtype,
    )
    out[:] = bg
    out[margin : margin + crop.shape[0], margin : margin + crop.shape[1]] = crop
    return out


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
        rec = framelog.recorder()
        crop = None
        try:
            crop = title_crop(img)
            if crop is None:
                rec.snapshot("ocr_nocrop", {"band": img})
                return None
            out = engine(self._prepare(crop))
        except Exception as exc:
            logger.debug("map-name OCR failed", exc_info=True)
            rec.snapshot("ocr_error", {"band": img, "crop": crop}, error=str(exc))
            return None
        txts = list(getattr(out, "txts", None) or ())
        scores = list(getattr(out, "scores", None) or (1.0,) * len(txts))
        texts = [t for t, c in zip(txts, scores) if c >= self.min_confidence]
        name = " ".join(texts).strip() or None
        rec.snapshot(
            "ocr", {"band": img, "crop": crop},
            text=name, txts=txts, scores=[round(float(s), 3) for s in scores],
        )
        return name

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
    "title_scan",
    "title_score",
]
