import unittest
from unittest.mock import patch

import numpy as np

from picobot.vision.mapname import (
    MapNameReader,
    name_strip_region,
    normalize_name,
)


class NormalizeTests(unittest.TestCase):
    def test_strips_punctuation_and_case(self):
        self.assertEqual(normalize_name("Limina : 1-5"), "limina15")
        self.assertEqual(normalize_name("  Kerning Square  "), "kerningsquare")

    def test_empty(self):
        self.assertEqual(normalize_name(None), "")
        self.assertEqual(normalize_name(""), "")
        self.assertEqual(normalize_name("!!!"), "")


class NameStripRegionTests(unittest.TestCase):
    def test_everything_above_minimap(self):
        self.assertEqual(
            name_strip_region((100, 200, 300, 150)),
            (100, 0, 300, 200),
        )

    def test_short_gap_uses_full_gap(self):
        self.assertEqual(
            name_strip_region((100, 10, 300, 150)),
            (100, 0, 300, 10),
        )

    def test_flush_top_falls_back_to_inner_strip(self):
        self.assertEqual(
            name_strip_region((100, 0, 300, 150), height=26),
            (100, 0, 300, 26),
        )


class ReaderTests(unittest.TestCase):
    def _engine(self, txts=None, scores=None):
        from types import SimpleNamespace

        class Fake:
            def __call__(self, img):
                return SimpleNamespace(txts=txts, scores=scores)

        return Fake()

    def test_returns_joined_text(self):
        reader = MapNameReader(min_confidence=0.5)
        img = np.zeros((24, 200, 3), dtype=np.uint8)
        with patch(
            "picobot.vision.mapname._get_engine",
            return_value=self._engine(("Limina : 1-5",), (0.9,)),
        ):
            self.assertEqual(reader.read(img), "Limina : 1-5")

    def test_filters_low_confidence(self):
        reader = MapNameReader(min_confidence=0.6)
        img = np.zeros((24, 200, 3), dtype=np.uint8)
        with patch(
            "picobot.vision.mapname._get_engine",
            return_value=self._engine(
                ("junk", "Real Name"), (0.3, 0.9)
            ),
        ):
            self.assertEqual(reader.read(img), "Real Name")

    def test_no_result_returns_none(self):
        reader = MapNameReader()
        img = np.zeros((24, 200, 3), dtype=np.uint8)
        with patch(
            "picobot.vision.mapname._get_engine",
            return_value=self._engine(None),
        ):
            self.assertIsNone(reader.read(img))

    def test_engine_missing_returns_none(self):
        reader = MapNameReader()
        img = np.zeros((24, 200, 3), dtype=np.uint8)
        with patch("picobot.vision.mapname._get_engine", return_value=None):
            self.assertIsNone(reader.read(img))

    def test_none_image_returns_none(self):
        self.assertIsNone(MapNameReader().read(None))


if __name__ == "__main__":
    unittest.main()
