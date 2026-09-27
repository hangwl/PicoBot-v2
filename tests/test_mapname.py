import unittest
from unittest.mock import patch

import numpy as np

from picobot.vision.mapname import (
    MapNameReader,
    name_strip_region,
    normalize_name,
    title_crop,
    title_lines,
)


def _band(w=232, h=100):
    """Dark header band; callers paint white blocks where text sits."""
    return np.full((h, w, 3), 40, dtype=np.uint8)


def _text(band, y0, y1, x0, x1, density=0.3):
    """Sparse white pixels inside a rect — text-like (not filled)."""
    rng = np.random.default_rng(0)
    region = band[y0:y1, x0:x1]
    mask = rng.random(region.shape[:2]) < density
    region[mask] = 255
    return band


class NormalizeTests(unittest.TestCase):
    def test_strips_punctuation_and_case(self):
        self.assertEqual(normalize_name("Limina : 1-5"), "limina15")
        self.assertEqual(normalize_name("  Kerning Square  "), "kerningsquare")

    def test_empty(self):
        self.assertEqual(normalize_name(None), "")
        self.assertEqual(normalize_name(""), "")
        self.assertEqual(normalize_name("!!!"), "")


class NameStripRegionTests(unittest.TestCase):
    def test_band_spans_window_top_into_region(self):
        # Window top → header_px into the region: covers titles drawn
        # above the map frame AND inside the panel border.
        self.assertEqual(
            name_strip_region((100, 200, 300, 150)),
            (100, 0, 300, 290),
        )

    def test_capped_at_region_bottom(self):
        self.assertEqual(
            name_strip_region((100, 100, 300, 50)),
            (100, 0, 300, 150),
        )


class TitleLinesTests(unittest.TestCase):
    def test_finds_text_lines(self):
        band = _band()
        _text(band, 30, 45, 52, 180)
        lines = title_lines(band)
        self.assertEqual(len(lines), 1)
        x0, y0, x1, y1 = lines[0]
        self.assertLessEqual(y0, 30)
        self.assertGreaterEqual(y1, 45)
        self.assertLessEqual(x0, 52)
        self.assertGreaterEqual(x1, 180)

    def test_icon_column_is_dropped(self):
        band = _band()
        _text(band, 30, 64, 52, 190)                # text (sparse)
        band[20:66, 8:44] = 255                    # map icon (dense block)
        lines = title_lines(band)
        self.assertTrue(lines)
        self.assertGreaterEqual(min(b[0] for b in lines), 45)

    def test_divider_row_cuts_map_content(self):
        band = _band()
        _text(band, 30, 45, 52, 180)               # title
        band[68:70, :] = 255                       # full-width divider
        _text(band, 80, 96, 20, 120)               # "map content" below
        lines = title_lines(band)
        self.assertTrue(lines)
        self.assertLess(max(b[3] for b in lines), 69)

    def test_no_text_returns_empty(self):
        self.assertEqual(title_lines(_band()), [])
        self.assertIsNone(title_crop(_band()))

    def test_thin_lines_dont_count(self):
        band = _band()
        band[30:33, 20:150] = 255                  # 3px line — not text
        self.assertEqual(title_lines(band), [])


class ReaderTests(unittest.TestCase):
    def _engine(self, txts=None, scores=None):
        from types import SimpleNamespace

        class Fake:
            def __call__(self, img):
                return SimpleNamespace(txts=txts, scores=scores)

        return Fake()

    def _band_with_text(self):
        band = _band()
        _text(band, 30, 45, 52, 180)
        return band

    def test_returns_joined_text(self):
        reader = MapNameReader(min_confidence=0.5)
        with patch(
            "picobot.vision.mapname._get_engine",
            return_value=self._engine(("Limina : 1-5",), (0.9,)),
        ):
            self.assertEqual(reader.read(self._band_with_text()), "Limina : 1-5")

    def test_filters_low_confidence(self):
        reader = MapNameReader(min_confidence=0.6)
        with patch(
            "picobot.vision.mapname._get_engine",
            return_value=self._engine(
                ("junk", "Real Name"), (0.3, 0.9)
            ),
        ):
            self.assertEqual(reader.read(self._band_with_text()), "Real Name")

    def test_no_result_returns_none(self):
        reader = MapNameReader()
        with patch(
            "picobot.vision.mapname._get_engine",
            return_value=self._engine(None),
        ):
            self.assertIsNone(reader.read(self._band_with_text()))

    def test_no_text_lines_returns_none(self):
        # Blank/icon-only bands fail closed — the engine never sees them.
        reader = MapNameReader()
        with patch(
            "picobot.vision.mapname._get_engine",
            return_value=self._engine(("junk",), (0.99,)),
        ):
            self.assertIsNone(reader.read(_band()))

    def test_engine_missing_returns_none(self):
        reader = MapNameReader()
        with patch("picobot.vision.mapname._get_engine", return_value=None):
            self.assertIsNone(reader.read(self._band_with_text()))

    def test_none_image_returns_none(self):
        self.assertIsNone(MapNameReader().read(None))


if __name__ == "__main__":
    unittest.main()
