import unittest

import numpy as np

from picobot.vision.minimap import (
    MinimapAnalyzer,
    MinimapColors,
    blob_centroid,
    color_mask,
    erode3,
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


if __name__ == "__main__":
    unittest.main()
