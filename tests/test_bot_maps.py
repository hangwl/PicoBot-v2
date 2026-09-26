import tempfile
import unittest
from pathlib import Path

import numpy as np

from picobot.bot.maps import MapEntry, MapStore
from picobot.bot.rotation import Rotation, Step
from picobot.vision.minimap import fingerprint, fingerprint_distance


def _img(seed=0, w=200, h=120):
    rng = np.random.default_rng(seed)
    return rng.integers(0, 255, (h, w, 3), dtype=np.uint8)


def _structured(seed=0, w=200, h=120):
    """Minimap-like image: dark background with platform lines."""
    rng = np.random.default_rng(seed)
    img = np.full((h, w, 3), 30, dtype=np.uint8)
    for _ in range(6):
        y = rng.integers(0, h)
        x0, x1 = sorted(rng.integers(0, w, 2))
        img[y : y + 2, x0:x1] = (228, 228, 228)
    return img


class FingerprintTests(unittest.TestCase):
    def test_same_image_same_hash(self):
        img = _img(1)
        self.assertEqual(fingerprint(img), fingerprint(img))

    def test_different_images_differ(self):
        horizontal = np.full((120, 200, 3), 30, dtype=np.uint8)
        horizontal[20::30, 10:190] = (228, 228, 228)  # platform rows
        vertical = np.full((120, 200, 3), 30, dtype=np.uint8)
        vertical[10:110, 20::40] = (228, 228, 228)    # vertical walls
        dist = fingerprint_distance(
            fingerprint(horizontal), fingerprint(vertical)
        )
        self.assertGreater(dist, 15)

    def test_same_map_with_noise_matches(self):
        base = _structured(4)
        noisy = base.copy()
        noisy[40:46, 90:96] = (12, 240, 239)  # roaming player dot
        dist = fingerprint_distance(
            fingerprint(base, ignore_colors=[(12, 240, 239)]),
            fingerprint(noisy, ignore_colors=[(12, 240, 239)]),
        )
        self.assertLess(dist, 5)

    def test_marker_pixels_are_ignored(self):
        base = _img(3)
        dotted = base.copy()
        dotted[10:16, 10:16] = (12, 240, 239)  # player-coloured dot
        plain = fingerprint_distance(fingerprint(base), fingerprint(dotted))
        masked = fingerprint_distance(
            fingerprint(base, ignore_colors=[(12, 240, 239)]),
            fingerprint(dotted, ignore_colors=[(12, 240, 239)]),
        )
        self.assertLess(masked, plain)

    def test_distance_rejects_garbage(self):
        self.assertEqual(fingerprint_distance("", "aa"), float("inf"))
        self.assertEqual(fingerprint_distance("zz", "zz"), float("inf"))


class MapEntryTests(unittest.TestCase):
    def test_from_dict(self):
        entry = MapEntry.from_dict({
            "name": "test_map",
            "fingerprint": "ab12",
            "rotation": {"anchors": [{"pos": [0.5, 0.5]}]},
            "skills": {"f": {"key": "d", "cooldown": 57, "kind": "summon"}},
        })
        self.assertEqual(entry.name, "test_map")
        self.assertEqual(len(entry.rotation.anchors), 1)
        self.assertEqual(entry.skills["f"].cooldown, 57.0)

    def test_requires_name(self):
        with self.assertRaises(ValueError):
            MapEntry.from_dict({"rotation": {}})


class MapStoreTests(unittest.TestCase):
    def test_save_load_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = MapStore(tmp)
            fp = fingerprint(_img(7))
            entry = MapEntry(
                name="farm_1",
                rotation=Rotation(anchors=[], legs={}),
                fingerprint=fp,
            )
            path = store.save(entry)
            self.assertTrue(Path(path).exists())

            store2 = MapStore(tmp)
            self.assertEqual(store2.names(), ["farm_1"])
            self.assertIs(store2.match(fp), store2.get("farm_1"))
            self.assertIsNone(store2.match(fingerprint(_img(9)), threshold=5))

    def test_missing_dir_is_empty(self):
        store = MapStore("/nonexistent/dir")
        self.assertEqual(store.load_all(), [])
        self.assertIsNone(store.match("aa"))

    def test_bad_file_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "broken.json").write_text("{not json")
            store = MapStore(tmp)
            self.assertEqual(store.load_all(), [])


if __name__ == "__main__":
    unittest.main()
