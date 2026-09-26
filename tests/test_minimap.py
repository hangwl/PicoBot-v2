import unittest

import numpy as np

from picobot.vision.minimap import (
    MinimapAnalyzer,
    MinimapColors,
    blob_centroid,
    color_mask,
    erode3,
    largest_blob_centroid,
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
        self.assertEqual(analyzer.player_pos(img), (50, 50))

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
        a = MinimapAnalyzer(region=(0, 0, 200, 150))
        a._region_explicit = False  # simulate auto-located region
        a.note_frame(_hlines())     # baseline
        self.assertFalse(a.note_frame(_vlines()))   # miss 1
        self.assertFalse(a.note_frame(_vlines()))   # miss 2
        self.assertTrue(a.note_frame(_vlines()))    # miss 3 → change
        self.assertIsNone(a.region)                 # auto region dropped

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

    def test_explicit_flag_respected(self):
        a = MinimapAnalyzer()
        a.set_region((0, 0, 100, 100), explicit=True)
        self.assertEqual(a.region_source, "manual")
        a.note_frame(_hlines())
        for _ in range(2):
            a.note_frame(_vlines())
        self.assertTrue(a.note_frame(_vlines()))
        self.assertEqual(a.region, (0, 0, 100, 100))

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
        self.assertEqual(a.player_pos(img), (40, 40))

    def test_rune_pos_reports_blob_centre_not_mean(self):
        a = MinimapAnalyzer()
        img = _blank(100, 80)
        _dot(img, 30, 40, a.colors.rune, r=3)      # real rune marker
        _dot(img, 90, 10, a.colors.rune, r=1)      # stray speck (survives erosion)
        pos = a.rune_pos(img)
        self.assertTrue(abs(pos[0] - 30) <= 2 and abs(pos[1] - 40) <= 2)


class PlatformYTests(unittest.TestCase):
    def _img(self, rows=(80,), w=200, h=120):
        img = _blank(w, h)
        for y in rows:
            img[y, :] = (228, 228, 228)  # platform ink = border color
        return img

    def test_snaps_point_below_floor_up_onto_platform(self):
        a = MinimapAnalyzer()
        # Target recorded beneath the lowest platform (y=95 < ink at 80)
        self.assertEqual(a.platform_y(self._img(), 50, 95), 80)

    def test_nearest_row_wins(self):
        a = MinimapAnalyzer()
        img = self._img(rows=(20, 80))
        self.assertEqual(a.platform_y(img, 50, 70), 80)
        self.assertEqual(a.platform_y(img, 50, 40), 20)

    def test_column_without_ink_returns_none(self):
        a = MinimapAnalyzer()
        img = self._img()
        img[:, 100:110] = 0  # carve an ink-free column
        self.assertIsNone(a.platform_y(img, 105, 80))

    def test_inset_rim_ignored(self):
        a = MinimapAnalyzer(marker_inset=4)
        img = _blank(100, 80)
        img[2, :] = (228, 228, 228)   # frame rim only
        img[50, :] = (228, 228, 228)  # real platform
        self.assertEqual(a.platform_y(img, 50, 60), 50)


if __name__ == "__main__":
    unittest.main()
