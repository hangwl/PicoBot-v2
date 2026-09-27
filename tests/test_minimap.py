import unittest

import numpy as np

from picobot.vision.minimap import (
    MinimapAnalyzer,
    MinimapColors,
    blob_centroid,
    color_mask,
    erode3,
    find_frame,
    largest_blob_centroid,
    platform_covered,
    platform_row_at,
    platform_span_at,
)
from picobot.vision.transition import TransitionDetector


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


_BORDER = (228, 228, 228)


def _hlines(w=200, h=150):
    img = np.full((h, w, 3), 40, dtype=np.uint8)
    img[::15, :] = _BORDER
    return img


def _vlines(w=200, h=150):
    img = np.full((h, w, 3), 40, dtype=np.uint8)
    img[:, ::15] = _BORDER
    return img


def _black(w=200, h=150):
    return np.zeros((h, w, 3), dtype=np.uint8)


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _analyzer(region=None, source="auto"):
    clock = _Clock()
    a = MinimapAnalyzer(
        region=region, transition=TransitionDetector(clock=clock)
    )
    if region is None:
        a._region = (0, 0, 200, 150)
        a._region_source = source
    return a, clock


def _transfer(a, clock, dark_s=1.0, settle_s=1.0, step=0.1):
    """Feed a blackout then the new map; returns note_frame results."""
    out = []
    for _ in range(int(dark_s / step)):
        clock.t += step
        out.append(a.note_frame(_black()))
    for _ in range(int(settle_s / step)):
        clock.t += step
        out.append(a.note_frame(_vlines()))
    return out


class MapTransferTests(unittest.TestCase):
    def test_translucent_ui_changes_never_trigger(self):
        # The false-trigger class from the debug captures: content changes
        # a lot (overlays, scenery) but never goes black.
        a, clock = _analyzer()
        for i in range(50):
            clock.t += 0.1
            self.assertFalse(a.note_frame(_hlines() if i % 7 < 3 else _vlines()))
        self.assertEqual(a.region, (0, 0, 200, 150))

    def test_blackout_then_arrival_drops_auto_region(self):
        a, clock = _analyzer()
        out = _transfer(a, clock)
        self.assertEqual(out.count(True), 1)
        self.assertIsNone(a.region)

    def test_loading_flag_spans_blackout_and_settle(self):
        a, clock = _analyzer()
        clock.t += 0.1
        a.note_frame(_black())
        self.assertFalse(a.loading)          # not confirmed yet
        for _ in range(3):
            clock.t += 0.1
            a.note_frame(_black())
        self.assertTrue(a.loading)
        clock.t += 0.1
        a.note_frame(_vlines())
        self.assertTrue(a.loading)           # settling
        clock.t += 1.0
        a.note_frame(_vlines())
        self.assertFalse(a.loading)

    def test_brief_dark_blip_is_ignored(self):
        a, clock = _analyzer()
        clock.t += 0.1
        a.note_frame(_black())
        clock.t += 0.1
        self.assertFalse(a.note_frame(_hlines()))
        clock.t += 2.0
        self.assertFalse(a.note_frame(_hlines()))
        self.assertEqual(a.region, (0, 0, 200, 150))

    def test_manual_and_stored_regions_dropped_on_arrival(self):
        for explicit in (True, False):
            a, clock = _analyzer()
            a.set_region((0, 0, 200, 150), explicit=explicit)
            _transfer(a, clock)
            self.assertIsNone(a.region)

    def test_config_region_kept_on_arrival(self):
        a, clock = _analyzer(region=(0, 0, 200, 150))
        self.assertIn(True, _transfer(a, clock))
        self.assertEqual(a.region, (0, 0, 200, 150))


def _frame(img, x, y, w, h, r=4):
    """Draw a 2px rounded-corner frame like the client's minimap."""
    img[y, x + r : x + w - r + 1] = _BORDER
    img[y + 1, x + r - 2 : x + w - r + 3] = _BORDER
    img[y + h, x + r : x + w - r + 1] = _BORDER
    img[y + h - 1, x + r - 2 : x + w - r + 3] = _BORDER
    img[y + r : y + h - r + 1, x] = _BORDER
    img[y + r - 2 : y + h - r + 3, x + 1] = _BORDER
    img[y + r : y + h - r + 1, x + w] = _BORDER
    img[y + r - 2 : y + h - r + 3, x + w - 1] = _BORDER
    return img


class FindFrameTests(unittest.TestCase):
    def _window(self):
        return np.full((768, 1366, 3), 50, dtype=np.uint8)

    def test_rounded_frame_found_exactly(self):
        img = _frame(self._window(), 7, 68, 216, 90)
        img[30:40, 50:200:3] = _BORDER          # title glyph-ish clutter
        self.assertEqual(find_frame(img), (7, 68, 216, 90))

    def test_per_map_sizes(self):
        for rect in ((7, 68, 185, 82), (7, 68, 213, 109), (7, 28, 170, 82)):
            img = _frame(self._window(), *rect)
            self.assertEqual(find_frame(img), rect)

    def test_occluded_side_returns_none(self):
        img = _frame(self._window(), 7, 68, 216, 90)
        img[60:170, 150:300] = 90               # a window covers the right
        self.assertIsNone(find_frame(img))

    def test_long_white_line_alone_is_not_a_frame(self):
        img = self._window()
        img[100, 10:300] = _BORDER
        img[180, 10:300] = _BORDER
        self.assertIsNone(find_frame(img))

    def test_locate_uses_frame_and_marks_auto(self):
        a = MinimapAnalyzer()
        img = _frame(self._window(), 7, 68, 216, 90)
        self.assertEqual(a.locate(img), (7, 68, 216, 90))
        self.assertEqual(a.region_source, "auto")


class PanelMovedTests(unittest.TestCase):
    def _setup(self):
        clock = _Clock()
        a = MinimapAnalyzer(transition=TransitionDetector(clock=clock))
        win = _frame(np.full((768, 1366, 3), 50, np.uint8), 7, 68, 216, 90)
        a.locate(win)
        return a, clock, win

    def _crop(self, win, a):
        x, y, w, h = a.region
        return win[y : y + h, x : x + w]

    def _tick(self, a, clock, img, n=15):
        for _ in range(n):
            clock.t += 0.1
            a.note_frame(img)

    def test_edge_present_never_lost(self):
        a, clock, win = self._setup()
        self._tick(a, clock, self._crop(win, a))
        self.assertFalse(a.edge_lost)

    def test_toggled_panel_relocates(self):
        a, clock, _ = self._setup()
        moved = _frame(np.full((768, 1366, 3), 50, np.uint8), 7, 28, 170, 82)
        self._tick(a, clock, self._crop(moved, a))
        self.assertTrue(a.edge_lost)
        self.assertTrue(a.relocate(moved))
        self.assertEqual(a.region, (7, 28, 170, 82))
        self.assertFalse(a.edge_lost)

    def test_overlay_hiding_edge_keeps_region(self):
        a, clock, win = self._setup()
        covered = win.copy()
        covered[60:170, 0:300] = 90
        self._tick(a, clock, self._crop(covered, a))
        self.assertTrue(a.edge_lost)
        self.assertFalse(a.relocate(covered))
        self.assertEqual(a.region, (7, 68, 216, 90))
        self.assertFalse(a.edge_lost)        # waits another interval

    def test_config_and_manual_regions_not_tracked(self):
        a, clock = _analyzer(region=(0, 0, 200, 150))
        self._tick(a, clock, _hlines())
        self.assertFalse(a.edge_lost)


class SetRegionTests(unittest.TestCase):
    def test_installs_stored_region(self):
        a = MinimapAnalyzer()
        a.set_region((10, 20, 200, 150))
        self.assertEqual(a.region, (10, 20, 200, 150))
        self.assertEqual(a.region_source, "stored")

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
