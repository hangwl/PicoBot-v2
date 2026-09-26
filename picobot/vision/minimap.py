"""Minimap analysis for PicoBot.

Pure-NumPy color-keyed detection over a screenshot of the game's minimap
region. No OpenCV dependency: detection is a per-pixel color distance mask
plus a 3x3 binary erosion (implemented as shifted ANDs) to reject single
pixel noise, mirroring the approach used by OpenCV-based bots.

All colors are BGR tuples and configurable because marker colors differ
between game clients (e.g. GMS-based private servers vs. other versions).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

import numpy as np

Region = Tuple[int, int, int, int]  # (left, top, width, height)


@dataclass
class MinimapColors:
    """BGR marker colors used by the in-game minimap."""

    player: Tuple[int, int, int] = (12, 240, 239)        # yellow dot
    other_player: Tuple[int, int, int] = (118, 45, 253)  # pink/red dots
    rune: Tuple[int, int, int] = (255, 102, 221)         # purple rune
    border: Tuple[int, int, int] = (228, 228, 228)       # minimap frame

    @classmethod
    def from_dict(cls, data: dict | None) -> "MinimapColors":
        if not data:
            return cls()
        kwargs = {}
        for name in ("player", "other_player", "rune", "border"):
            value = data.get(name)
            if value is None:
                continue
            if not isinstance(value, (list, tuple)) or len(value) != 3:
                raise ValueError(f"minimap color '{name}' must be [B, G, R]")
            kwargs[name] = tuple(int(v) for v in value)
        return cls(**kwargs)


def color_mask(img: np.ndarray, bgr: Tuple[int, int, int], tolerance: int) -> np.ndarray:
    """Boolean mask of pixels within ``tolerance`` of a BGR color.

    Tolerance compares the summed per-channel absolute difference against
    ``tolerance * 3`` (same convention as the reference implementation).
    """
    target = np.asarray(bgr, dtype=np.int32)
    diff = np.abs(img.astype(np.int32) - target).sum(axis=2)
    return diff < tolerance * 3


def erode3(mask: np.ndarray) -> np.ndarray:
    """3x3 binary erosion via shifted ANDs.

    A pixel survives only if it and all 8 neighbours are set, which removes
    isolated single-pixel noise while keeping marker dots (typically ~3-7px)
    intact.
    """
    m = mask
    h, w = mask.shape
    out = mask.copy()
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy == 0 and dx == 0:
                continue
            shifted = np.zeros_like(mask)
            src_y = slice(max(0, -dy), h - max(0, dy))
            src_x = slice(max(0, -dx), w - max(0, dx))
            dst_y = slice(max(0, dy), h - max(0, -dy))
            dst_x = slice(max(0, dx), w - max(0, -dx))
            shifted[dst_y, dst_x] = m[src_y, src_x]
            out &= shifted
    return out


def blob_centroid(mask: np.ndarray) -> Optional[Tuple[int, int]]:
    """Mean (x, y) of set pixels. Returns None if the mask is empty."""
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return None
    return int(xs.mean()), int(ys.mean())


class MinimapAnalyzer:
    """Locates the minimap inside a window capture and reads markers off it.

    ``region`` may be supplied explicitly (recommended — border auto-detection
    is fragile across clients/resolutions). If omitted, ``locate`` searches a
    window capture for the frame-colored border rectangle.
    """

    def __init__(
        self,
        colors: MinimapColors | None = None,
        region: Region | None = None,
        *,
        border_tolerance: int = 10,
    ) -> None:
        self.colors = colors or MinimapColors()
        self._region = region
        self._border_tolerance = border_tolerance

    @property
    def region(self) -> Optional[Region]:
        return self._region

    def locate(self, window_img: np.ndarray) -> Optional[Region]:
        """Find the minimap frame in a window capture, once; result is cached.

        Searches the top-left quadrant, then takes the bounding box of
        border-colored pixels on rows/columns where the border color is dense
        (the frame's edges). Returns the region relative to ``window_img``.
        """
        if self._region is not None:
            return self._region

        h, w = window_img.shape[:2]
        roi = window_img[: h // 2, : w // 2]
        mask = color_mask(roi, self.colors.border, self._border_tolerance)

        col_counts = mask.sum(axis=0)
        row_counts = mask.sum(axis=1)
        if col_counts.max() == 0 or row_counts.max() == 0:
            return None

        # Border rows/cols contain a long run of border-colored pixels.
        cols = np.nonzero(col_counts > col_counts.max() * 0.5)[0]
        rows = np.nonzero(row_counts > row_counts.max() * 0.5)[0]
        if cols.size == 0 or rows.size == 0:
            return None

        left, right = int(cols.min()), int(cols.max())
        top, bottom = int(rows.min()), int(rows.max())
        width, height = right - left, bottom - top
        if width < 50 or height < 50:
            return None

        self._region = (left, top, width, height)
        return self._region

    def crop(self, window_img: np.ndarray) -> Optional[np.ndarray]:
        """Crop the minimap region out of a full window capture."""
        region = self.locate(window_img)
        if region is None:
            return None
        x, y, w, h = region
        return window_img[y : y + h, x : x + w]

    # -- Marker detectors -----------------------------------------------------
    def player_pos(
        self, minimap_img: np.ndarray, tolerance: int = 10
    ) -> Optional[Tuple[int, int]]:
        mask = erode3(color_mask(minimap_img, self.colors.player, tolerance))
        return blob_centroid(mask)

    def rune_pos(
        self, minimap_img: np.ndarray, tolerance: int = 10
    ) -> Optional[Tuple[int, int]]:
        mask = erode3(color_mask(minimap_img, self.colors.rune, tolerance))
        return blob_centroid(mask)

    def has_rune(self, minimap_img: np.ndarray, tolerance: int = 10) -> bool:
        return self.rune_pos(minimap_img, tolerance) is not None

    def has_other_players(
        self, minimap_img: np.ndarray, tolerance: int = 10
    ) -> bool:
        mask = erode3(color_mask(minimap_img, self.colors.other_player, tolerance))
        return bool(mask.any())


__all__ = [
    "MinimapAnalyzer",
    "MinimapColors",
    "Region",
    "blob_centroid",
    "color_mask",
    "erode3",
]
