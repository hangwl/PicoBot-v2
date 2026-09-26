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
    def test_strip_above_minimap(self):
        self.assertEqual(
            name_strip_region((100, 200, 300, 150), height=26),
            (100, 174, 300, 26),
        )

    def test_clamped_to_window_top(self):
        self.assertEqual(
            name_strip_region((100, 10, 300, 150), height=26),
            (100, 0, 300, 10),
        )


class ReaderTests(unittest.TestCase):
    def _engine(self, result):
        class Fake:
            def __call__(self, img):
                return result, [0.0]

        return Fake()

    def _box(self, text, conf):
        return [[[0, 0], [10, 0], [10, 10], [0, 10]], text, conf]

    def test_returns_joined_text(self):
        reader = MapNameReader(min_confidence=0.5)
        img = np.zeros((24, 200, 3), dtype=np.uint8)
        with patch(
            "picobot.vision.mapname._get_engine",
            return_value=self._engine([self._box("Limina : 1-5", 0.9)]),
        ):
            self.assertEqual(reader.read(img), "Limina : 1-5")

    def test_filters_low_confidence(self):
        reader = MapNameReader(min_confidence=0.6)
        img = np.zeros((24, 200, 3), dtype=np.uint8)
        with patch(
            "picobot.vision.mapname._get_engine",
            return_value=self._engine(
                [self._box("junk", 0.3), self._box("Real Name", 0.9)]
            ),
        ):
            self.assertEqual(reader.read(img), "Real Name")

    def test_no_result_returns_none(self):
        reader = MapNameReader()
        img = np.zeros((24, 200, 3), dtype=np.uint8)
        with patch(
            "picobot.vision.mapname._get_engine", return_value=self._engine(None)
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
