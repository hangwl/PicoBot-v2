"""Minimap analysis for PicoBot.

Pure-NumPy detection over a screenshot of the game's minimap region.
Markers use a per-pixel color distance mask plus a 3x3 erosion. The
panel is located by its rounded white frame (:func:`find_frame`); map
changes are detected from the loading blackout (``vision.transition``).
Platform geometry is hand-drawn per map (``platforms`` on the map file).

All colors are BGR tuples and configurable per client.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional, Tuple

import numpy as np

from . import framelog
from .transition import TransitionDetector, is_dark

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
    """Pixels whose summed per-channel difference is < ``tolerance * 3``."""
    target = np.asarray(bgr, dtype=np.int32)
    diff = np.abs(img.astype(np.int32) - target).sum(axis=2)
    return diff < tolerance * 3


def _runs(row: np.ndarray) -> List[Tuple[int, int]]:
    """``[start, end)`` spans of True values in a 1-D bool array."""
    idx = np.flatnonzero(np.diff(np.concatenate(([0], row.astype(np.int8), [0]))))
    return list(zip(idx[0::2].tolist(), idx[1::2].tolist()))


def _side(m: np.ndarray, lo: int, hi: int, x_lo: int, x_hi: int, outer: str,
          fill: float = 0.9) -> Optional[int]:
    """Outermost column in ``[x_lo, x_hi]`` covered over rows ``[lo, hi)``."""
    xs = range(max(0, x_lo), min(m.shape[1], x_hi + 1))
    hits = [x for x in xs if m[lo:hi, x].mean() >= fill]
    if not hits:
        return None
    return hits[0] if outer == "left" else hits[-1]


def find_frame(
    img: np.ndarray,
    border: Tuple[int, int, int] = (228, 228, 228),
    tolerance: int = 10,
    *,
    min_w: int = 80,
    min_h: int = 40,
    corner: int = 6,
) -> Optional[Region]:
    """Locate the minimap's frame in a window capture (top-left quadrant).

    The frame is a thin border-colored rectangle with rounded corners:
    a top and bottom edge with matching x-extent plus two solid sides.
    The largest such rectangle wins. Returns ``(x, y, w, h)`` with
    ``w``/``h`` measured edge-to-edge (right - left, bottom - top).
    """
    h, w = img.shape[:2]
    m = color_mask(img[: h // 2, : w // 2], border, tolerance)
    edges = []
    for y in np.flatnonzero(m.sum(axis=1) >= min_w).tolist():
        edges.extend((y, a, b) for a, b in _runs(m[y]) if b - a >= min_w)
    best = None
    for i, (y0, a0, b0) in enumerate(edges):
        for y1, a1, b1 in edges[i + 1:]:
            if y1 - y0 < min_h or abs(a0 - a1) > 3 or abs(b0 - b1) > 3:
                continue
            lo, hi = y0 + corner, y1 - corner + 1
            a, b = min(a0, a1), max(b0, b1)
            left = _side(m, lo, hi, a - corner, a + 2, "left")
            right = _side(m, lo, hi, b - 3, b + corner, "right")
            if left is None or right is None:
                continue
            area = (right - left) * (y1 - y0)
            if best is None or area > best[0]:
                best = (area, (left, y0, right - left, y1 - y0))
    return best[1] if best else None


def _seg_y(s: Tuple[float, float, float, float], x: float) -> float:
    """y of drawn segment ``(x0, y0, x1, y1)`` at column ``x`` (lerped)."""
    x0, y0, x1, y1 = s
    if x1 == x0:
        return (y0 + y1) / 2.0
    t = (x - x0) / (x1 - x0)
    return y0 + t * (y1 - y0)


def platform_covered(
    segments: Iterable[Tuple[float, float, float, float]],
    x: float,
    slack: float = 4.0,
) -> bool:
    """True when any drawn segment spans column ``x`` (±``slack`` px)."""
    for s in segments:
        if min(s[0], s[2]) - slack <= x <= max(s[0], s[2]) + slack:
            return True
    return False


def platform_row_at(
    segments: Iterable[Tuple[float, float, float, float]],
    x: float,
    y: float,
    *,
    max_snap: float = 8.0,
    x_slack: float = 4.0,
) -> Optional[int]:
    """Row y of the drawn segment under column ``x`` nearest ``y``, or None
    when nothing drawn is within ``max_snap`` px."""
    best = None
    for s in segments:
        if not min(s[0], s[2]) - x_slack <= x <= max(s[0], s[2]) + x_slack:
            continue
        py = _seg_y(s, x)
        d = abs(py - y)
        if d <= max_snap and (best is None or d < best[0]):
            best = (d, py)
    return int(round(best[1])) if best else None


def platform_span_at(
    segments: Iterable[Tuple[float, float, float, float]],
    x: float,
    y: float,
    *,
    band: float = 4.0,
    x_slack: float = 3.0,
) -> Optional[Tuple[int, int]]:
    """(x0, x1) px extent of the drawn segment under ``(x, y)``, or None."""
    best = None
    for s in segments:
        lo, hi = min(s[0], s[2]), max(s[0], s[2])
        if not lo - x_slack <= x <= hi + x_slack:
            continue
        d = abs(_seg_y(s, x) - y)
        if d <= band and (best is None or d < best[0]):
            best = (d, lo, hi)
    if best is None:
        return None
    return int(round(best[1])), int(round(best[2]))


def erode3(mask: np.ndarray) -> np.ndarray:
    """3x3 binary erosion via shifted ANDs — drops isolated pixels."""
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


def _largest_blob(mask: np.ndarray):
    """Largest connected component as (centroid_x, centroid_y, y_max)."""
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return None
    h, w = mask.shape
    visited = np.zeros_like(mask, dtype=bool)
    best = None  # (count, cx, cy_mean, y_max)
    for sy, sx in zip(ys.tolist(), xs.tolist()):
        if visited[sy, sx]:
            continue
        stack = [(sy, sx)]
        visited[sy, sx] = True
        cx = cy = count = ymax = 0
        while stack:
            y, x = stack.pop()
            count += 1
            cx += x
            cy += y
            ymax = max(ymax, y)
            for ny in (y - 1, y, y + 1):
                for nx in (x - 1, x, x + 1):
                    if (
                        0 <= ny < h and 0 <= nx < w
                        and mask[ny, nx] and not visited[ny, nx]
                    ):
                        visited[ny, nx] = True
                        stack.append((ny, nx))
        if best is None or count > best[0]:
            best = (count, cx // count, cy // count, ymax)
    return best[1], best[2], best[3]


def largest_blob_centroid(mask: np.ndarray) -> Optional[Tuple[int, int]]:
    """Centroid of the largest connected component, or None."""
    blob = _largest_blob(mask)
    return (blob[0], blob[1]) if blob is not None else None


class MinimapAnalyzer:
    """Locates the minimap panel and reads markers off it.

    ``note_frame`` watches minimap captures for the loading blackout; on
    arrival at a new map a non-``config`` region is dropped so ``locate``
    re-detects the (per-map sized) panel.
    """

    def __init__(
        self,
        colors: MinimapColors | None = None,
        region: Region | None = None,
        *,
        border_tolerance: int = 10,
        marker_inset: int = 0,
        transition: Optional[TransitionDetector] = None,
    ) -> None:
        self.colors = colors or MinimapColors()
        self.marker_inset = max(0, int(marker_inset))
        self._region = region
        self._region_explicit = region is not None
        self._region_source = "config" if region is not None else None
        self._border_tolerance = border_tolerance
        self.transition = transition or TransitionDetector()
        self._clock = self.transition._clock
        self._edge_lost_since: Optional[float] = None
        self.edge_lost_s = 1.0
        self._miss_logged = 0.0
        self._lock = threading.Lock()

    @property
    def region(self) -> Optional[Region]:
        return self._region

    @property
    def region_source(self) -> Optional[str]:
        """``config`` (pinned in config.json), ``manual`` (hand-drawn),
        ``stored`` (a map file's remembered layout), ``auto`` (frame
        detection), or None."""
        return self._region_source

    @property
    def loading(self) -> bool:
        """True from a confirmed blackout until the new map settles."""
        return self.transition.loading

    def set_region(self, region: Region, *, explicit: bool = False) -> None:
        """Install a known region — a map's stored layout or a hand-drawn
        rect (``explicit``)."""
        with self._lock:
            self._region = tuple(int(v) for v in region)
            self._region_explicit = explicit
            self._region_source = "manual" if explicit else "stored"

    def reset_region(self) -> None:
        """Drop the current region (any source); ``locate`` re-runs."""
        with self._lock:
            self._region = None
            self._region_explicit = False
            self._region_source = None

    def note_frame(
        self,
        minimap_img: np.ndarray,
        context: Optional[Callable[[], dict]] = None,
    ) -> bool:
        """Feed one minimap capture; True when arrival at a new map is
        confirmed (blackout ended and the scene settled).

        ``context`` returns extra images (e.g. ``{"window": ...}``) for
        debug frame capture; only called on arrival.
        """
        with self._lock:
            info = {
                "region": self._region,
                "region_source": self._region_source,
                "dark": is_dark(minimap_img, self.transition.level),
            }
            event = self.transition.note(minimap_img)
            info["state"] = self.transition.state
            info["event"] = event
            if event == "arrived" and self._region_source != "config":
                self._region = None
                self._region_source = None
                self._region_explicit = False
            self._track_edge(minimap_img, info["dark"])
        rec = framelog.recorder()
        if rec.enabled:
            rec.transition_frame(id(self), minimap_img, info, context)
        return event == "arrived"

    def _track_edge(self, img: np.ndarray, dark: bool) -> None:
        """Note whether the frame's top edge is where the region says."""
        if (self._region_source not in ("auto", "stored") or dark
                or self.transition.loading or img.shape[0] < 2):
            self._edge_lost_since = None
            return
        edge = color_mask(img[:2], self.colors.border, self._border_tolerance)
        if edge.mean(axis=1).max() >= 0.6:
            self._edge_lost_since = None
        elif self._edge_lost_since is None:
            self._edge_lost_since = self._clock()

    @property
    def edge_lost(self) -> bool:
        """The frame edge has been missing from the region for a while —
        the panel was moved or resized (e.g. title row toggled)."""
        since = self._edge_lost_since
        return since is not None and self._clock() - since >= self.edge_lost_s

    def relocate(self, window_img: Optional[np.ndarray]) -> bool:
        """Re-detect after :attr:`edge_lost`; switches only when a frame is
        actually found elsewhere (an overlay hiding the edge keeps the
        current region). True when the region changed."""
        found = find_frame(
            window_img, self.colors.border, self._border_tolerance
        ) if window_img is not None else None
        with self._lock:
            self._edge_lost_since = self._clock()
            if found is None or found == self._region:
                return False
            self._region = found
            self._region_source = "auto"
            self._region_explicit = False
            self._edge_lost_since = None
        return True

    def locate(self, window_img: np.ndarray) -> Optional[Region]:
        """The cached region, else detect the panel frame in ``window_img``."""
        with self._lock:
            if self._region is not None:
                return self._region
        return self._detect(window_img)

    def _detect(self, window_img: np.ndarray) -> Optional[Region]:
        region = find_frame(
            window_img, self.colors.border, self._border_tolerance
        )
        if region is None:
            self._log_miss(window_img)
            return None
        with self._lock:
            self._region = region
            self._region_source = "auto"
        return region

    def _log_miss(self, window_img: np.ndarray) -> None:
        now = time.monotonic()
        rec = framelog.recorder()
        if (not rec.enabled or now - self._miss_logged < 30.0
                or is_dark(window_img)):
            return
        self._miss_logged = now
        rec.snapshot("frame_not_found", {"window": window_img})

    def crop(self, window_img: np.ndarray) -> Optional[np.ndarray]:
        """Crop the minimap region out of a full window capture."""
        region = self.locate(window_img)
        if region is None:
            return None
        x, y, w, h = region
        return window_img[y : y + h, x : x + w]

    # -- Marker detectors -----------------------------------------------------
    def _interior(self, img: np.ndarray) -> Tuple[np.ndarray, int]:
        """Crop ``marker_inset`` px of frame off each edge (+ the offset)."""
        i = self.marker_inset
        h, w = img.shape[:2]
        if i <= 0 or h <= 2 * i or w <= 2 * i:
            return img, 0
        return img[i:-i, i:-i], i

    def _marker(
        self,
        minimap_img: np.ndarray,
        bgr: Tuple[int, int, int],
        tolerance: int,
        *,
        feet: bool = False,
    ) -> Optional[Tuple[int, int]]:
        inner, off = self._interior(minimap_img)
        mask = erode3(color_mask(inner, bgr, tolerance))
        blob = _largest_blob(mask)
        if blob is None:
            return None
        cx, cy, ymax = blob
        # The player glyph's bottom tip touches the platform.
        return (cx + off, (ymax if feet else cy) + off)

    def player_pos(
        self, minimap_img: np.ndarray, tolerance: int = 10
    ) -> Optional[Tuple[int, int]]:
        return self._marker(
            minimap_img, self.colors.player, tolerance, feet=True
        )

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


__all__ = [
    "MinimapAnalyzer",
    "MinimapColors",
    "Region",
    "blob_centroid",
    "color_mask",
    "erode3",
    "find_frame",
    "largest_blob_centroid",
    "platform_covered",
    "platform_row_at",
    "platform_span_at",
]
