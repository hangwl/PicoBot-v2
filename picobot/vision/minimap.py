"""Minimap analysis for PicoBot.

Pure-NumPy color-keyed detection over a screenshot of the game's minimap
region. No OpenCV dependency: detection is a per-pixel color distance mask
plus a 3x3 binary erosion (implemented as shifted ANDs) to reject single
pixel noise, mirroring the approach used by OpenCV-based bots.

All colors are BGR tuples and configurable because marker colors differ
between game clients (e.g. GMS-based private servers vs. other versions).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Iterable, Optional, Tuple

import numpy as np

Region = Tuple[int, int, int, int]  # (left, top, width, height)


@dataclass
class MinimapColors:
    """BGR marker colors used by the in-game minimap."""

    player: Tuple[int, int, int] = (12, 240, 239)        # yellow dot
    other_player: Tuple[int, int, int] = (118, 45, 253)  # pink/red dots
    rune: Tuple[int, int, int] = (255, 102, 221)         # purple rune
    border: Tuple[int, int, int] = (228, 228, 228)       # minimap frame
    # Platform/line color used to mask fingerprints. None -> reuse `border`.
    ink: Optional[Tuple[int, int, int]] = None

    @classmethod
    def from_dict(cls, data: dict | None) -> "MinimapColors":
        if not data:
            return cls()
        kwargs = {}
        for name in ("player", "other_player", "rune", "border", "ink"):
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


def largest_blob_centroid(mask: np.ndarray) -> Optional[Tuple[int, int]]:
    """Centroid of the largest connected component in ``mask``.

    Unlike :func:`blob_centroid`, scattered noise pixels can't drag the
    result toward a meaningless midpoint — only the biggest contiguous
    blob wins. Returns None if the mask is empty.
    """
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return None
    h, w = mask.shape
    visited = np.zeros_like(mask, dtype=bool)
    best: Optional[Tuple[int, int, int]] = None  # (count, x, y)
    for sy, sx in zip(ys.tolist(), xs.tolist()):
        if visited[sy, sx]:
            continue
        stack = [(sy, sx)]
        visited[sy, sx] = True
        cx = cy = count = 0
        while stack:
            y, x = stack.pop()
            count += 1
            cx += x
            cy += y
            for ny in (y - 1, y, y + 1):
                for nx in (x - 1, x, x + 1):
                    if (
                        0 <= ny < h and 0 <= nx < w
                        and mask[ny, nx] and not visited[ny, nx]
                    ):
                        visited[ny, nx] = True
                        stack.append((ny, nx))
        if best is None or count > best[0]:
            best = (count, cx // count, cy // count)
    return (best[1], best[2])


def fingerprint(
    img: np.ndarray,
    ignore_colors: Optional[Iterable[Tuple[int, int, int]]] = None,
    include_colors: Optional[Iterable[Tuple[int, int, int]]] = None,
    tolerance: int = 10,
    size: int = 16,
) -> str:
    """Content hash of a minimap capture, for map identification.

    The image is divided into ``size``×``size`` cells; each cell's mean AND
    max grayscale become the hash (hex string, 2 bytes per cell). The max
    channel preserves thin platform lines that mean-pooling would wash
    out. Pixels matching ``ignore_colors`` (e.g. the roaming marker dots)
    are excluded, so markers don't perturb the hash of a static minimap.

    ``include_colors`` inverts the selection: only pixels matching those
    colors (the minimap's platform/border "ink") contribute — crucial on
    translucent minimaps where the live scene shows through the background
    and would otherwise drift the hash as the character moves. Returns ""
    when too few pixels qualify (blank/transition frames), which never
    matches anything in :func:`fingerprint_distance`.
    """
    h, w = img.shape[:2]
    bh, bw = max(1, h // size), max(1, w // size)
    crop = img[: bh * size, : bw * size].astype(np.float32)
    excluded = np.zeros(crop.shape[:2], dtype=bool)
    if include_colors:
        included = np.zeros(crop.shape[:2], dtype=bool)
        for bgr in include_colors:
            included |= color_mask(crop.astype(np.uint8), bgr, tolerance)
        excluded |= ~included
    if ignore_colors:
        for bgr in ignore_colors:
            excluded |= color_mask(crop.astype(np.uint8), bgr, tolerance)
    if include_colors or ignore_colors:
        weights = (~excluded).astype(np.float32)
    else:
        weights = np.ones(crop.shape[:2], dtype=np.float32)
    if weights.sum() < 8:
        # Not enough signal to identify a map — treat as no fingerprint.
        return ""
    gray = (
        0.114 * crop[:, :, 0] + 0.587 * crop[:, :, 1] + 0.299 * crop[:, :, 2]
    )
    num = (gray * weights).reshape(size, bh, size, bw).sum(axis=(1, 3))
    den = weights.reshape(size, bh, size, bw).sum(axis=(1, 3))
    means = np.where(den > 0, num / np.maximum(den, 1), 127.0)
    # Excluded pixels get -1 so they never win the per-cell max.
    maxes = np.where(excluded, -1.0, gray).reshape(size, bh, size, bw).max(axis=(1, 3))
    maxes = np.where(maxes >= 0, maxes, 127.0)
    cells = np.concatenate([means, maxes]).astype(np.uint8)
    return cells.tobytes().hex()


def fingerprint_distance(a: str, b: str) -> float:
    """Mean absolute pixel difference between two fingerprints (0-255).

    Returns ``inf`` for missing/mismatched/invalid inputs so unknown maps
    never match.
    """
    if not a or not b or len(a) != len(b):
        return float("inf")
    try:
        pa = np.frombuffer(bytes.fromhex(a), dtype=np.uint8).astype(np.int16)
        pb = np.frombuffer(bytes.fromhex(b), dtype=np.uint8).astype(np.int16)
    except ValueError:
        return float("inf")
    return float(np.abs(pa - pb).mean())


class MinimapAnalyzer:
    """Locates the minimap inside a window capture and reads markers off it.

    ``region`` may be supplied explicitly (recommended — border auto-detection
    is fragile across clients/resolutions). If omitted, ``locate`` searches a
    window capture for the frame-colored border rectangle.

    A fingerprint watchdog (``note_frame``) watches captured content; when it
    changes persistently — i.e. the player changed maps — an auto-located
    region is dropped so the next ``locate`` re-detects the minimap's new
    position/size. Explicitly configured regions are never reset.
    """

    def __init__(
        self,
        colors: MinimapColors | None = None,
        region: Region | None = None,
        *,
        border_tolerance: int = 10,
        map_change_threshold: float = 15.0,
        map_change_frames: int = 3,
        marker_inset: int = 0,
    ) -> None:
        self.colors = colors or MinimapColors()
        self.marker_inset = max(0, int(marker_inset))
        self._region = region
        self._region_explicit = region is not None
        self._region_source = "config" if region is not None else None
        self._border_tolerance = border_tolerance
        self._map_change_threshold = map_change_threshold
        self._map_change_frames = map_change_frames
        self._baseline_fp: Optional[str] = None
        self._fp_misses = 0
        # Guards region/baseline state — the dashboard feed and a running
        # bot can share one analyzer from different threads.
        self._lock = threading.Lock()

    @property
    def region(self) -> Optional[Region]:
        return self._region

    @property
    def region_source(self) -> Optional[str]:
        """Where the current region came from: ``config`` (pinned in
        config.json), ``manual`` (hand-drawn in the dashboard),
        ``stored`` (a map file's remembered layout), ``auto`` (live
        border detection), or None when unknown."""
        return self._region_source

    def set_region(self, region: Region, *, explicit: bool = False) -> None:
        """Install a known region — e.g. a remembered per-map layout or
        a hand-drawn rect (``explicit``).

        Non-explicit regions stay resettable: the watchdog can still
        drop them on a confirmed map change. Re-anchors the content
        baseline so the region swap itself isn't mistaken for a change.
        """
        with self._lock:
            self._region = tuple(int(v) for v in region)
            self._region_explicit = explicit
            self._region_source = "manual" if explicit else "stored"
            self._baseline_fp = None
            self._fp_misses = 0

    def reset_region(self) -> None:
        """Drop the current region (any source) so ``locate`` re-runs on
        the next capture. The user asked for re-detection — also clears
        the explicit flag so the watchdog manages the fresh result."""
        with self._lock:
            self._region = None
            self._region_explicit = False
            self._region_source = None
            self._baseline_fp = None
            self._fp_misses = 0

    def note_frame(self, minimap_img: np.ndarray) -> bool:
        """Watchdog: report True when a map change is confirmed.

        Compares each capture's content fingerprint to a baseline taken on
        the current map. ``map_change_frames`` consecutive mismatches confirm
        a real change (single-frame flicker like loading blanks is ignored).
        On confirmation an auto-located region is cleared so ``locate`` runs
        again on the next capture; the baseline is reset either way.
        """
        fp = fingerprint(
            minimap_img,
            ignore_colors=(
                self.colors.player, self.colors.other_player, self.colors.rune
            ),
            include_colors=(self.colors.ink or self.colors.border,),
        )
        with self._lock:
            if not fp:
                # Blank/transition frame — no signal; neither match nor miss.
                return False
            if self._baseline_fp is None:
                self._baseline_fp = fp
                return False
            if (
                fingerprint_distance(fp, self._baseline_fp)
                <= self._map_change_threshold
            ):
                self._fp_misses = 0
                return False
            self._fp_misses += 1
            if self._fp_misses < self._map_change_frames:
                return False
            self._fp_misses = 0
            self._baseline_fp = None
            if not self._region_explicit:
                self._region = None
                self._region_source = None
            return True

    def locate(self, window_img: np.ndarray) -> Optional[Region]:
        """Find the minimap frame in a window capture, once; result is cached.

        Searches the top-left quadrant, then takes the bounding box of
        border-colored pixels on rows/columns where the border color is dense
        (the frame's edges). Returns the region relative to ``window_img``.
        """
        with self._lock:
            if self._region is not None:
                return self._region
        return self._detect(window_img)

    def _detect(self, window_img: np.ndarray) -> Optional[Region]:
        """Border-scan ``window_img`` for the minimap frame and cache it."""
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

        with self._lock:
            self._region = (left, top, width, height)
            self._region_source = "auto"
        return self._region

    def crop(self, window_img: np.ndarray) -> Optional[np.ndarray]:
        """Crop the minimap region out of a full window capture."""
        region = self.locate(window_img)
        if region is None:
            return None
        x, y, w, h = region
        return window_img[y : y + h, x : x + w]

    # -- Marker detectors -----------------------------------------------------
    def _interior(self, img: np.ndarray) -> Tuple[np.ndarray, int]:
        """Crop ``marker_inset`` px of frame/chrome off each edge.

        Marker detection runs on the map interior only — border-colored
        or UI pixels at the crop's rim can't trigger false markers.
        Returns the inner view plus the offset so reported positions
        stay minimap-relative.
        """
        i = self.marker_inset
        h, w = img.shape[:2]
        if i <= 0 or h <= 2 * i or w <= 2 * i:
            return img, 0
        return img[i:-i, i:-i], i

    def _marker(
        self, minimap_img: np.ndarray, bgr: Tuple[int, int, int], tolerance: int
    ) -> Optional[Tuple[int, int]]:
        inner, off = self._interior(minimap_img)
        mask = erode3(color_mask(inner, bgr, tolerance))
        c = largest_blob_centroid(mask)
        return (c[0] + off, c[1] + off) if c is not None else None

    def player_pos(
        self, minimap_img: np.ndarray, tolerance: int = 10
    ) -> Optional[Tuple[int, int]]:
        return self._marker(minimap_img, self.colors.player, tolerance)

    def rune_pos(
        self, minimap_img: np.ndarray, tolerance: int = 10
    ) -> Optional[Tuple[int, int]]:
        return self._marker(minimap_img, self.colors.rune, tolerance)

    def has_rune(self, minimap_img: np.ndarray, tolerance: int = 10) -> bool:
        return self.rune_pos(minimap_img, tolerance) is not None

    def has_other_players(
        self, minimap_img: np.ndarray, tolerance: int = 10
    ) -> bool:
        inner, _ = self._interior(minimap_img)
        mask = erode3(color_mask(inner, self.colors.other_player, tolerance))
        return bool(mask.any())

    def platform_y(
        self,
        minimap_img: np.ndarray,
        x: float,
        y: float,
        *,
        column: int = 2,
        tolerance: int = 10,
    ) -> Optional[int]:
        """Platform surface (nearest ink row) around column ``x`` at ``y``.

        Scans a narrow x-column of the minimap interior for platform/rope
        ink and returns the ink row closest to ``y``, minimap-relative.
        Used to project recorded or replayed targets onto real geometry:
        a point beneath the lowest platform (mid-air mark, marker jitter)
        snaps up onto the floor instead of becoming an unreachable
        coordinate. Returns None when the column carries no ink.
        """
        inner, off = self._interior(minimap_img)
        h, w = inner.shape[:2]
        xi = int(round(x)) - off
        x0 = max(0, xi - column)
        x1 = min(w, xi + column + 1)
        if x0 >= x1:
            return None
        ink = self.colors.ink or self.colors.border
        mask = color_mask(inner[:, x0:x1], ink, tolerance)
        ys = np.nonzero(mask)[0]
        if ys.size == 0:
            return None
        return int(ys[np.abs(ys - (y - off)).argmin()] + off)


__all__ = [
    "MinimapAnalyzer",
    "MinimapColors",
    "Region",
    "blob_centroid",
    "color_mask",
    "erode3",
    "fingerprint",
    "fingerprint_distance",
    "largest_blob_centroid",
]
