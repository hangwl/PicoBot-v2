"""Minimap analysis for PicoBot.

Pure-NumPy detection over a screenshot of the game's minimap region. No
OpenCV dependency: markers use a per-pixel color distance mask plus a 3x3
binary erosion (implemented as shifted ANDs) to reject single pixel noise,
mirroring the approach used by OpenCV-based bots. Platform geometry is
hand-drawn per map (``platforms`` on the map file) — translucent minimaps
alpha-blend platform lines over the live scene so reliably that no
detector proved trustworthy; :func:`platform_mask`/:func:`structure_mask`
remain only as the fingerprint include-mask, where a rough line guess is
good enough because it's only used to *exclude* drifting background.

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
    # Platform-line colour override for platform detection. None ->
    # structural line detection (colour-free — see platform_mask()).
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


def platform_mask(
    img: np.ndarray,
    *,
    tolerance: int = 12,
    min_run: int = 8,
    vband: int = 2,
    contrast: int = 10,
) -> np.ndarray:
    """Mask of platform/rope line pixels — detected structurally, color-free.

    Platform lines are UI-drawn thin horizontal strokes. On translucent
    minimaps their rendered color is alpha-blended with whatever the live
    scene behind the panel shows, so it varies pixel-to-pixel and
    location-to-location — no single BGR color can match them. Geometry
    is invariant instead: a platform is a horizontal run of near-uniform
    rendered color at least ``min_run`` px long whose row differs from
    the rows ``vband`` px above/below (a thin line over a different
    background). The vertical-contrast test rejects filled regions,
    marker blobs, and tall uniform areas; the run-length test rejects
    dots, text strokes, and short noise.
    """
    px = img.astype(np.int16)
    h, w = px.shape[:2]
    mask = np.zeros((h, w), dtype=bool)
    if h < 2 * vband + 2 or w < min_run + 2:
        return mask
    def _h_runs(similar: np.ndarray, out: np.ndarray) -> np.ndarray:
        """Mark pixels covered by a horizontal run of >=min_run similars."""
        last_false = np.maximum.accumulate(
            np.where(similar, -1, np.arange(w - 1)), axis=1
        )
        ends = similar & (np.arange(w - 1) - last_false >= min_run - 1)
        pad = np.zeros((h, w + min_run), dtype=bool)
        pad[:, 1:w] = ends
        for s in range(min_run):
            out |= pad[:, s : s + w]
        return out

    # hsim[i] = pixel i similar to pixel i+1 (summed per-channel diff).
    _h_runs(np.abs(np.diff(px, axis=1)).sum(axis=2) < tolerance * 3, mask)
    # Thin-line test: the row must contrast with rows vband away on BOTH
    # sides — otherwise every uniform background row adjacent to a line
    # would qualify (halo) and fill regions would pass at their interior.
    v = np.ones((h, w), dtype=bool)
    v[vband:] &= np.abs(px[vband:] - px[:-vband]).sum(axis=2) > contrast * 3
    v[:-vband] &= np.abs(px[:-vband] - px[vband:]).sum(axis=2) > contrast * 3
    mask &= v
    # Second pass in mask space: a real platform survives as a contiguous
    # run of mask pixels; sporadic noise that lucked through run+contrast
    # is scattered and dies here.
    if mask.any():
        mask = _h_runs(mask[:, 1:] & mask[:, :-1], np.zeros_like(mask))
    return mask


def structure_mask(
    img: np.ndarray,
    colors: Optional["MinimapColors"] = None,
    tolerance: int = 10,
) -> np.ndarray:
    """Fingerprint include-mask: platform-line geometry (+ configured ink).

    Map identity fingerprints hash only platform structure, so they are
    stable on translucent minimaps and across clients regardless of the
    platform-line color. When ``colors.ink`` is explicitly configured the
    color match is unioned in as an additional stable signal.
    """
    mask = platform_mask(img)
    ink = getattr(colors, "ink", None)
    if ink is not None:
        mask |= color_mask(img, ink, tolerance)
    return mask


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
    """Row y of the drawn platform segment under column ``x`` nearest ``y``.

    Platform geometry is hand-drawn on the dashboard (``platforms`` on the
    map file) — the bot trusts drawn segments exactly. A snap is only
    legitimate within ~one platform-spacing (``max_snap``); beyond that
    the point is off drawn geometry and None is returned rather than
    teleporting it to a distant line.
    """
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
    """(x0, x1) px extent of the drawn platform segment under ``(x, y)``.

    The nearest segment (in y) spanning column ``x`` and within ``band``
    px of ``y`` wins. None when nothing is drawn under the point —
    callers fall back to a fixed range.
    """
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
    """Centroid of the largest connected component in ``mask``.

    Unlike :func:`blob_centroid`, scattered noise pixels can't drag the
    result toward a meaningless midpoint — only the biggest contiguous
    blob wins. Returns None if the mask is empty.
    """
    blob = _largest_blob(mask)
    return (blob[0], blob[1]) if blob is not None else None


def fingerprint(
    img: np.ndarray,
    ignore_colors: Optional[Iterable[Tuple[int, int, int]]] = None,
    include_colors: Optional[Iterable[Tuple[int, int, int]]] = None,
    include_mask: Optional[np.ndarray] = None,
    tolerance: int = 10,
    size: int = 16,
) -> str:
    """Content hash of a minimap capture, for map identification.

    The image is divided into ``size``×``size`` cells; each cell's mean AND
    max grayscale become the hash (hex string, 2 bytes per cell). The max
    channel preserves thin platform lines that mean-pooling would wash
    out. Pixels matching ``ignore_colors`` (e.g. the roaming marker dots)
    are excluded, so markers don't perturb the hash of a static minimap.

    ``include_colors``/``include_mask`` switch the hash to *geometry*
    mode: the masked structure itself is hashed (cell coverage + a
    has-structure bit), not the masked pixels' colours. On translucent
    minimaps the live scene bleeds through platform-line pixels, so
    colour content drifts while line positions stay fixed — hashing the
    mask is what makes the fingerprint stable there. Geometry
    fingerprints carry a ``g2:`` scheme prefix so legacy colour-hash
    fingerprints can never match them by accident. Returns "" when too
    few pixels qualify (blank/transition frames), which never matches
    anything in :func:`fingerprint_distance`.
    """
    h, w = img.shape[:2]
    bh, bw = max(1, h // size), max(1, w // size)
    crop = img[: bh * size, : bw * size]
    excluded = np.zeros(crop.shape[:2], dtype=bool)
    scheme = ""
    if include_colors or include_mask is not None:
        included = np.zeros(crop.shape[:2], dtype=bool)
        if include_colors:
            for bgr in include_colors:
                included |= color_mask(crop, bgr, tolerance)
        if include_mask is not None:
            m = np.asarray(include_mask, dtype=bool)
            included |= m[: crop.shape[0], : crop.shape[1]]
        if included.sum() < 8:
            # Not enough structure to identify a map — no fingerprint.
            return ""
        # Geometry hash: the mask IS the signal (0/255 "image").
        gray = included.astype(np.float32) * 255.0
        scheme = "g2:"
    else:
        gray = (
            0.114 * crop[:, :, 0] + 0.587 * crop[:, :, 1]
            + 0.299 * crop[:, :, 2]
        ).astype(np.float32)
    if ignore_colors:
        for bgr in ignore_colors:
            excluded |= color_mask(crop.astype(np.uint8), bgr, tolerance)
    weights = (~excluded).astype(np.float32)
    if weights.sum() < 8:
        return ""
    num = (gray * weights).reshape(size, bh, size, bw).sum(axis=(1, 3))
    den = weights.reshape(size, bh, size, bw).sum(axis=(1, 3))
    means = np.where(den > 0, num / np.maximum(den, 1), 127.0)
    # Excluded pixels get -1 so they never win the per-cell max.
    maxes = np.where(excluded, -1.0, gray).reshape(size, bh, size, bw).max(axis=(1, 3))
    maxes = np.where(maxes >= 0, maxes, 127.0)
    cells = np.concatenate([means, maxes]).astype(np.uint8)
    return scheme + cells.tobytes().hex()


def fingerprint_distance(a: str, b: str) -> float:
    """Mean absolute cell difference between two fingerprints (0-255).

    Returns ``inf`` for missing/mismatched/invalid inputs so unknown maps
    never match. Scheme prefixes (``g2:``) are compared — fingerprints
    from different schemes never match.
    """
    if not a or not b or len(a) != len(b):
        return float("inf")
    if ":" in a:
        sa, a = a.split(":", 1)
        sb, b = b.split(":", 1) if ":" in b else ("", b)
        if sa != sb:
            return float("inf")
    elif ":" in b:
        return float("inf")
    try:
        pa = np.frombuffer(bytes.fromhex(a), dtype=np.uint8).astype(np.int16)
        pb = np.frombuffer(bytes.fromhex(b), dtype=np.uint8).astype(np.int16)
    except ValueError:
        return float("inf")
    return float(np.abs(pa - pb).mean())


def fingerprint_score(a: str, b: str, threshold: float = 15.0) -> float:
    """Match confidence 0-1: 1.0 = identical, 0 = at/past ``threshold``.

    A convenience for display — distances well under the match threshold
    read as high confidence, distances past it clamp to 0.
    """
    dist = fingerprint_distance(a, b)
    if dist == float("inf") or threshold <= 0:
        return 0.0
    return max(0.0, 1.0 - dist / threshold)


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
        self._pending_fp: Optional[str] = None   # candidate new scene
        self._fp_misses = 0
        # Diagnostics: last frame's distance/score vs the baseline.
        self.last_dist: Optional[float] = None
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
            self._pending_fp = None
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
            self._pending_fp = None
            self._fp_misses = 0

    def note_frame(self, minimap_img: np.ndarray) -> bool:
        """Watchdog: report True when a map change is confirmed.

        Compares each capture's content fingerprint to a baseline taken on
        the current map. A miss only counts toward a change when it is
        *self-consistent* — the new frame must match the previous miss
        frame, i.e. the scene is showing a stable *different* picture.
        Transitional frames (loading blanks, fades) neither match the
        baseline nor each other, so they can never accumulate into a
        false positive no matter how many fire. ``map_change_frames``
        consecutive consistent misses confirm a real change.

        On confirmation the baseline resets and the region is dropped so
        ``locate`` re-detects — unless it came from ``config`` (a pinned
        region is authoritative and survives map changes). Stored and
        hand-drawn regions belong to the old map's identity and are
        dropped; a different map's remembered layout is re-applied by the
        caller's region-seeding pass.
        """
        ignore = (
            self.colors.player, self.colors.other_player, self.colors.rune
        )
        mask = structure_mask(minimap_img, self.colors)
        if mask.sum() >= 8:
            fp = fingerprint(
                minimap_img, ignore_colors=ignore, include_mask=mask
            )
        else:
            # No platform structure at all — hash the whole frame instead
            # so background drift still registers as change.
            fp = fingerprint(minimap_img, ignore_colors=ignore)
        with self._lock:
            if not fp:
                # Blank/transition frame — no signal; neither match nor miss.
                return False
            if self._baseline_fp is None:
                self._baseline_fp = fp
                self._pending_fp = None
                self._fp_misses = 0
                return False
            dist = fingerprint_distance(fp, self._baseline_fp)
            self.last_dist = dist
            if dist <= self._map_change_threshold:
                self._fp_misses = 0
                self._pending_fp = None
                return False
            # A miss counts only if the new scene agrees with itself —
            # flicker frames can't match each other and reset the streak.
            if self._pending_fp is not None and fingerprint_distance(
                fp, self._pending_fp
            ) <= self._map_change_threshold:
                self._fp_misses += 1
            else:
                self._pending_fp = fp
                self._fp_misses = 1
            if self._fp_misses < self._map_change_frames:
                return False
            self._fp_misses = 0
            self._pending_fp = None
            self._baseline_fp = None
            if self._region_source != "config":
                self._region = None
                self._region_source = None
                self._region_explicit = False
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
        # "feet" = the icon's bottom row. The marker glyph is anchored at
        # its bottom tip — the point touching the platform — so feet-space
        # positions sit on the platform line rather than floating ~half an
        # icon above it the way a centroid does.
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
    "fingerprint",
    "fingerprint_distance",
    "largest_blob_centroid",
    "platform_covered",
    "platform_mask",
    "platform_row_at",
    "platform_span_at",
    "structure_mask",
]
