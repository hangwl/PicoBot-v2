import unittest

import numpy as np

from picobot.vision.minimap import (
    MinimapAnalyzer,
    MinimapColors,
    blob_centroid,
    color_mask,
    erode3,
    fingerprint,
    fingerprint_distance,
    fingerprint_score,
    largest_blob_centroid,
    platform_covered,
    platform_mask,
    platform_row_at,
    platform_span_at,
    structure_mask,
)


def _blank(w=200, h=120):
    return np.zeros((h, w, 3), dtype=np.uint8)


def _dot(img, cx, cy, color, r=2):
    img[max(0, cy - r) : cy + r + 1, max(0, cx - r) : cx + r + 1] = color
    return img


class ColorMaskTests(unittest.TestCase):
    def test_exact_color_matches(self):
        img = _dot(_blank(), 10, 10, (12, 240, 239))
        mask = color_mask(img, (12, 240, 239), 10)
        self.assertTrue(mask[10, 10])
        self.assertFalse(mask[0, 0])

    def test_tolerance_boundary(self):
        img = _blank()
        img[5, 5] = (12, 240, 200)  # within tolerance*3 of (12,240,239)? |39| < 30? no
        mask = color_mask(img, (12, 240, 239), 10)
        self.assertFalse(mask[5, 5])
        img[5, 5] = (12, 240, 230)  # diff 9 < 30
        mask = color_mask(img, (12, 240, 239), 10)
        self.assertTrue(mask[5, 5])


class ErodeTests(unittest.TestCase):
    def test_single_pixel_is_eroded(self):
        img = _blank()
        img[10, 10] = (118, 45, 253)
        mask = color_mask(img, (118, 45, 253), 10)
        self.assertFalse(erode3(mask).any())

    def test_solid_dot_survives(self):
        img = _dot(_blank(), 30, 30, (118, 45, 253), r=2)
        mask = color_mask(img, (118, 45, 253), 10)
        self.assertTrue(erode3(mask).any())

    def test_blob_centroid(self):
        img = _dot(_blank(), 60, 40, (12, 240, 239), r=2)
        mask = erode3(color_mask(img, (12, 240, 239), 10))
        cx, cy = blob_centroid(mask)
        self.assertAlmostEqual(cx, 60, delta=1)
        self.assertAlmostEqual(cy, 40, delta=1)

    def test_blob_centroid_empty(self):
        self.assertIsNone(blob_centroid(np.zeros((10, 10), dtype=bool)))


class MinimapAnalyzerTests(unittest.TestCase):
    def test_player_pos_finds_dot(self):
        analyzer = MinimapAnalyzer(region=(0, 0, 200, 120))
        img = _dot(_blank(), 50, 50, (12, 240, 239), r=2)
        # Feet-anchored: the r=2 square erodes to rows 49..51 → bottom 51.
        self.assertEqual(analyzer.player_pos(img), (50, 51))

    def test_player_pos_is_bottom_of_marker_not_center(self):
        # A 7px-tall marker glyph (like the client's player icon): its
        # bottom tip is the point on the platform, so the reported y is
        # the blob's bottom row — anchors and the floor boundary land on
        # the platform ink instead of floating at icon-center height.
        analyzer = MinimapAnalyzer(region=(0, 0, 200, 120))
        img = _blank()
        img[40:47, 48:53] = (12, 240, 239)   # 7px tall marker, bottom=46
        x, y = analyzer.player_pos(img)
        self.assertEqual((x, y), (50, 45))   # erode shaves 1px off the tip

    def test_player_pos_none_on_blank(self):
        analyzer = MinimapAnalyzer(region=(0, 0, 200, 120))
        self.assertIsNone(analyzer.player_pos(_blank()))

    def test_has_rune_and_others(self):
        analyzer = MinimapAnalyzer(region=(0, 0, 200, 120))
        img = _dot(_blank(), 20, 20, (255, 102, 221), r=3)
        img = _dot(img, 80, 80, (118, 45, 253), r=3)
        self.assertTrue(analyzer.has_rune(img))
        self.assertTrue(analyzer.has_other_players(img))

    def test_custom_colors(self):
        colors = MinimapColors(player=(0, 0, 255))
        analyzer = MinimapAnalyzer(colors=colors, region=(0, 0, 200, 120))
        img = _dot(_blank(), 30, 30, (0, 0, 255), r=2)
        self.assertIsNotNone(analyzer.player_pos(img))

    def test_locate_finds_border_rect(self):
        # 400x400 window; minimap border rect at (20, 20) size 100x80 —
        # must fit inside the top-left quadrant that locate() searches.
        img = np.zeros((400, 400, 3), dtype=np.uint8)
        img[20:100, 20] = (228, 228, 228)
        img[20:100, 120] = (228, 228, 228)
        img[20, 20:121] = (228, 228, 228)
        img[100, 20:121] = (228, 228, 228)
        analyzer = MinimapAnalyzer()
        region = analyzer.locate(img)
        self.assertIsNotNone(region)
        x, y, w, h = region
        self.assertAlmostEqual(x, 20, delta=2)
        self.assertAlmostEqual(y, 20, delta=2)
        self.assertAlmostEqual(w, 100, delta=2)
        self.assertAlmostEqual(h, 80, delta=2)
        # second call returns cached region
        self.assertEqual(analyzer.locate(img), region)

    def test_locate_returns_none_without_border(self):
        analyzer = MinimapAnalyzer()
        self.assertIsNone(analyzer.locate(np.zeros((200, 200, 3), np.uint8)))

    def test_colors_from_dict_validation(self):
        self.assertEqual(MinimapColors.from_dict(None), MinimapColors())
        colors = MinimapColors.from_dict({"player": [1, 2, 3]})
        self.assertEqual(colors.player, (1, 2, 3))
        with self.assertRaises(ValueError):
            MinimapColors.from_dict({"player": [1, 2]})


_BORDER = (228, 228, 228)  # default MinimapColors.border — the "ink"


def _hlines(w=200, h=150):
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[::15, :] = _BORDER
    return img


def _vlines(w=200, h=150):
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:, ::15] = _BORDER
    return img


class MapChangeWatchdogTests(unittest.TestCase):
    def test_same_map_frames_do_not_trip(self):
        a = MinimapAnalyzer(region=(0, 0, 200, 150))
        self.assertFalse(a.note_frame(_hlines()))
        self.assertFalse(a.note_frame(_hlines()))

    def test_persistent_change_trips_after_n_frames(self):
        a = MinimapAnalyzer()
        a._region = (0, 0, 200, 150)
        a._region_source = "auto"   # simulate auto-located region
        a.note_frame(_hlines())     # baseline
        self.assertFalse(a.note_frame(_vlines()))   # miss 1
        self.assertFalse(a.note_frame(_vlines()))   # miss 2
        self.assertTrue(a.note_frame(_vlines()))    # miss 3 → change
        self.assertIsNone(a.region)                 # auto region dropped

    def test_inconsistent_misses_never_confirm(self):
        # Flicker frames that don't match each other can't accumulate —
        # translucency noise and loading blanks die here.
        a = MinimapAnalyzer()
        a._region = (0, 0, 200, 150)
        a._region_source = "auto"
        a.note_frame(_hlines())
        alt = _vlines()
        alt2 = np.zeros_like(alt); alt2[:, 3::15] = _BORDER
        for i in range(6):
            # alternating new scenes — each disagrees with the last miss
            self.assertFalse(a.note_frame(alt if i % 2 else alt2))
        self.assertEqual(a.region, (0, 0, 200, 150))

    def test_manual_region_dropped_on_change(self):
        # A hand-drawn region belongs to the old map's identity — only a
        # config-pinned region survives a confirmed change.
        a = MinimapAnalyzer()
        a.set_region((0, 0, 200, 150), explicit=True)
        a.note_frame(_hlines())
        for _ in range(2):
            self.assertFalse(a.note_frame(_vlines()))
        self.assertTrue(a.note_frame(_vlines()))
        self.assertIsNone(a.region)

    def test_explicit_region_kept_on_change(self):
        a = MinimapAnalyzer(region=(0, 0, 200, 150))
        a.note_frame(_hlines())
        for _ in range(2):
            self.assertFalse(a.note_frame(_vlines()))
        self.assertTrue(a.note_frame(_vlines()))
        self.assertEqual(a.region, (0, 0, 200, 150))

    def test_transient_flicker_does_not_trip(self):
        a = MinimapAnalyzer()
        a._region = (0, 0, 200, 150)
        a.note_frame(_hlines())
        self.assertFalse(a.note_frame(_vlines()))   # 1 miss
        self.assertFalse(a.note_frame(_hlines()))   # recovers → counter reset
        self.assertFalse(a.note_frame(_hlines()))
        self.assertEqual(a.region, (0, 0, 200, 150))

    def test_baseline_rearms_after_reset(self):
        a = MinimapAnalyzer(map_change_frames=2)
        a.note_frame(_hlines())
        a.note_frame(_vlines())
        self.assertTrue(a.note_frame(_vlines()))    # change confirmed
        # New baseline adopted; same new content must not re-trip.
        self.assertFalse(a.note_frame(_vlines()))


class SetRegionTests(unittest.TestCase):
    def test_installs_region_and_reanchors_baseline(self):
        a = MinimapAnalyzer()
        a.note_frame(_hlines())                     # old baseline
        a.set_region((10, 20, 200, 150))
        self.assertEqual(a.region, (10, 20, 200, 150))
        self.assertEqual(a.region_source, "stored")
        # The swap itself isn't a map change — baseline re-anchors.
        self.assertFalse(a.note_frame(_vlines()))
        self.assertFalse(a.note_frame(_vlines()))

    def test_stored_region_stays_resettable(self):
        a = MinimapAnalyzer()
        a.set_region((10, 20, 200, 150))
        a.note_frame(_hlines())
        a.note_frame(_vlines())
        a.note_frame(_vlines())
        self.assertTrue(a.note_frame(_vlines()))
        self.assertIsNone(a.region)                 # watchdog can still drop
        self.assertIsNone(a.region_source)

    def test_config_region_source_and_reset(self):
        a = MinimapAnalyzer(region=(0, 0, 200, 150))
        self.assertEqual(a.region_source, "config")
        a.reset_region()
        self.assertIsNone(a.region)
        self.assertIsNone(a.region_source)
        self.assertFalse(a._region_explicit)


class LargestBlobTests(unittest.TestCase):
    def test_picks_biggest_blob_over_specks(self):
        mask = np.zeros((50, 50), dtype=bool)
        mask[5, 5] = mask[45, 45] = True          # specks
        mask[20:30, 20:30] = True                  # the real marker
        self.assertEqual(largest_blob_centroid(mask), (24, 24))

    def test_empty_mask(self):
        self.assertIsNone(largest_blob_centroid(np.zeros((10, 10), bool)))

    def test_marker_inset_excludes_rim(self):
        a = MinimapAnalyzer(marker_inset=6)
        img = _blank(100, 80)
        _dot(img, 2, 2, a.colors.player)           # on the rim
        self.assertIsNone(a.player_pos(img))
        _dot(img, 40, 40, a.colors.player)         # interior
        self.assertEqual(a.player_pos(img), (40, 41))  # feet = bottom row

    def test_rune_pos_reports_blob_centre_not_mean(self):
        a = MinimapAnalyzer()
        img = _blank(100, 80)
        _dot(img, 30, 40, a.colors.rune, r=3)      # real rune marker
        _dot(img, 90, 10, a.colors.rune, r=1)      # stray speck (survives erosion)
        pos = a.rune_pos(img)
        self.assertTrue(abs(pos[0] - 30) <= 2 and abs(pos[1] - 40) <= 2)


class PlatformMaskTests(unittest.TestCase):
    """Structural line detection — geometry, not colour."""

    def test_detects_line_of_any_colour(self):
        # A green platform line on dark bg — the whole point of the
        # structural detector is that colour doesn't matter.
        img = _blank()
        img[:] = (10, 20, 30)
        img[40, 20:120] = (40, 180, 90)
        mask = platform_mask(img)
        self.assertTrue(mask[40, 60])
        self.assertFalse(mask[40, 150])    # past the run's end
        self.assertFalse(mask[45, 60])     # plain background row

    def test_rejects_dots_and_short_runs(self):
        img = _blank()
        _dot(img, 60, 40, (200, 200, 200), r=2)   # 5px marker blob
        img[70, 20:26] = (200, 200, 200)          # 6px run < min_run
        self.assertFalse(platform_mask(img).any())

    def test_thick_region_interior_and_edges_rejected(self):
        # A tall filled block isn't a line: interior rows have no vertical
        # contrast and its edge rows only contrast on one side.
        img = _blank()
        img[30:80, 20:150] = (60, 60, 60)
        self.assertFalse(platform_mask(img).any())

    def test_blended_line_with_two_rendered_colours(self):
        # Alpha-blend look: one logical line rendered as two different
        # colours over different backgrounds — still one platform row.
        img = _blank()
        img[:] = (10, 20, 30)
        img[40, 10:60] = (100, 100, 100)
        img[40, 60:120] = (80, 90, 95)
        mask = platform_mask(img)
        self.assertTrue(mask[40, 30])
        self.assertTrue(mask[40, 90])

    def test_multiple_platforms_all_detected(self):
        img = _blank()
        img[:] = (5, 5, 8)
        for y in (20, 60, 100):
            img[y, 30:170] = (150, 140, 130)
        mask = platform_mask(img)
        for y in (20, 60, 100):
            self.assertTrue(mask[y, 100])
        self.assertFalse(mask[40, 100])

    def test_fingerprint_include_mask_ignores_background(self):
        # Same platform lines over a wildly different background must
        # hash identically — the structural mask isolates the lines.
        base = _blank()
        base[:] = (10, 20, 30)
        other = _blank()
        other[:] = (250, 200, 100)
        for img in (base, other):
            img[40, 10:150] = (90, 90, 90)
            img[90, 40:180] = (90, 90, 90)
        fa = fingerprint(base, include_mask=structure_mask(base))
        fb = fingerprint(other, include_mask=structure_mask(other))
        self.assertEqual(fa, fb)

    def test_structure_mask_unions_configured_ink(self):
        # An ink-coloured feature too short for structural detection
        # still enters the mask when the ink colour is configured.
        img = _blank()
        img[40, 10:14] = (7, 8, 9)  # 4px run — too short for geometry
        colors = MinimapColors(ink=(7, 8, 9))
        mask = structure_mask(img, colors)
        self.assertTrue(mask[40, 11])
        self.assertFalse(platform_mask(img)[40, 11])


class GeometryFingerprintTests(unittest.TestCase):
    """include-mask fingerprints hash structure, not pixel colour."""

    def test_mask_fingerprint_is_colour_free(self):
        # The translucency fix: identical line *positions* over wildly
        # different rendered colours hash the same — the scene behind
        # the translucent panel can no longer perturb the fingerprint.
        rng = np.random.default_rng(0)
        mask = np.zeros((150, 200), dtype=bool)
        mask[40, 10:150] = True
        mask[90, 40:180] = True
        fa = fingerprint(
            rng.integers(0, 256, (150, 200, 3), dtype=np.uint8),
            include_mask=mask,
        )
        fb = fingerprint(
            rng.integers(0, 256, (150, 200, 3), dtype=np.uint8),
            include_mask=mask,
        )
        self.assertEqual(fa, fb)
        self.assertTrue(fa.startswith("g2:"))

    def test_geometry_and_legacy_schemes_never_match(self):
        img = _hlines()
        g = fingerprint(img, include_mask=structure_mask(img))
        legacy = "00" * 512
        self.assertEqual(fingerprint_distance(g, legacy), float("inf"))
        self.assertEqual(fingerprint_distance(legacy, g), float("inf"))

    def test_score_scale(self):
        img = _hlines()
        fa = fingerprint(img, include_mask=structure_mask(img))
        self.assertEqual(fingerprint_score(fa, fa), 1.0)
        # Wrong scheme -> no match -> 0 confidence.
        self.assertEqual(fingerprint_score(fa, "00" * 512), 0.0)
        other = fingerprint(_vlines()) or "g2:" + "ff" * 512
        self.assertEqual(fingerprint_score(fa, other), 0.0)


class DrawnPlatformTests(unittest.TestCase):
    """Hand-drawn platform segments — the map's authoritative geometry."""

    SEGS = [(10, 80, 190, 80), (40, 30, 120, 30)]  # two horizontal floors

    def test_row_at_snaps_point_onto_nearest_segment(self):
        self.assertEqual(platform_row_at(self.SEGS, 100, 86), 80)
        self.assertEqual(platform_row_at(self.SEGS, 60, 34), 30)

    def test_row_at_beyond_snap_radius_returns_none(self):
        # 15px off the floor isn't jitter — it's off drawn geometry.
        self.assertIsNone(platform_row_at(self.SEGS, 100, 95))

    def test_row_at_uncovered_column_returns_none(self):
        self.assertIsNone(platform_row_at(self.SEGS, 5, 80))
        self.assertTrue(platform_covered(self.SEGS, 12))
        self.assertFalse(platform_covered(self.SEGS, 4))

    def test_row_at_lerps_sloped_segment(self):
        segs = [(20, 40, 80, 60)]   # diagonal line, e.g. stairs
        self.assertEqual(platform_row_at(segs, 50, 55), 50)

    def test_span_at_returns_segment_extent(self):
        self.assertEqual(platform_span_at(self.SEGS, 100, 81), (10, 190))
        self.assertEqual(platform_span_at(self.SEGS, 60, 29), (40, 120))

    def test_span_at_needs_a_segment_under_the_point(self):
        self.assertIsNone(platform_span_at(self.SEGS, 100, 50))
        self.assertIsNone(platform_span_at(self.SEGS, 4, 80))
        self.assertIsNone(platform_span_at([], 100, 80))


if __name__ == "__main__":
    unittest.main()
