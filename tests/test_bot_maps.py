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

    def test_include_colors_ignores_transparent_background(self):
        """Translucent minimap: scene pixels behind it must not perturb fp."""
        ink = (200, 200, 200)

        def map_img(seed):
            # arbitrary "scene" showing through the transparent background
            img = np.random.default_rng(seed).integers(
                0, 160, (150, 200, 3), dtype=np.uint8
            )
            img[30, :] = ink       # platform lines
            img[80, 40:160] = ink
            img[:, 150] = ink
            return img

        a = fingerprint(map_img(1), include_colors=[ink])
        b = fingerprint(map_img(2), include_colors=[ink])
        self.assertEqual(a, b)
        # A genuinely different platform layout must still differ.
        c_img = np.random.default_rng(5).integers(
            0, 160, (150, 200, 3), dtype=np.uint8
        )
        c_img[:, ::20] = ink  # vertical stripes instead of the a/b layout
        c = fingerprint(c_img, include_colors=[ink])
        self.assertGreater(fingerprint_distance(a, c), 15)

    def test_include_colors_blank_frame_returns_empty(self):
        img = np.zeros((150, 200, 3), dtype=np.uint8)  # loading screen
        self.assertEqual(
            fingerprint(img, include_colors=[(200, 200, 200)]), ""
        )


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

    def test_map_name_preserved_as_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = MapStore(tmp)
            entry = MapEntry(
                name="farm_1",
                map_name="Limina : 1-5 East",
            )
            store.save(entry)
            # map_name is a preserved label, not a matching input.
            self.assertEqual(store.get("farm_1").map_name, "Limina : 1-5 East")

    def test_map_name_roundtrip(self):
        entry = MapEntry(name="m", map_name="Kerning : Square")
        data = entry.to_dict()
        self.assertEqual(data["map_name"], "Kerning : Square")
        self.assertEqual(MapEntry.from_dict(data).map_name, "Kerning : Square")

    def test_minimap_region_roundtrip(self):
        entry = MapEntry(name="m", minimap_region=(8, 40, 200, 150))
        data = entry.to_dict()
        self.assertEqual(data["minimap_region"], [8, 40, 200, 150])
        self.assertEqual(
            MapEntry.from_dict(data).minimap_region, (8, 40, 200, 150)
        )

    def test_minimap_region_malformed_dropped(self):
        for bad in ([1, 2], "nope", {"x": 1}):
            entry = MapEntry.from_dict(
                {"name": "m", "minimap_region": bad}
            )
            self.assertIsNone(entry.minimap_region)

    def test_minimap_region_absent(self):
        entry = MapEntry.from_dict({"name": "m"})
        self.assertIsNone(entry.minimap_region)
        self.assertIsNone(entry.to_dict()["minimap_region"])

    def test_walls_roundtrip(self):
        entry = MapEntry(name="m", walls={"left": 0.25, "right": 0.8})
        data = entry.to_dict()
        self.assertEqual(data["walls"], {"left": 0.25, "right": 0.8})
        self.assertEqual(
            MapEntry.from_dict(data).walls, {"left": 0.25, "right": 0.8}
        )

    def test_walls_partial_and_malformed(self):
        entry = MapEntry.from_dict(
            {"name": "m", "walls": {"left": 0.1, "right": "nope", "up": 0.5}}
        )
        self.assertEqual(entry.walls, {"left": 0.1})
        entry = MapEntry.from_dict({"name": "m", "walls": {"left": 9}})
        self.assertIsNone(entry.walls)
        entry = MapEntry.from_dict({"name": "m"})
        self.assertIsNone(entry.walls)
        self.assertIsNone(entry.to_dict()["walls"])

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
