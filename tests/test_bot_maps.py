import tempfile
import unittest
from pathlib import Path

from picobot.bot.maps import MapEntry, MapStore
from picobot.bot.rotation import Rotation, Step


_LAKE = "Lake of Oblivion Weathered Land of "


class TitleMatchTests(unittest.TestCase):
    """Real OCR reads from debug captures against sibling maps."""

    def _store(self, tmp, *suffixes):
        store = MapStore(tmp)
        for sfx in suffixes:
            store.save(MapEntry(name=sfx.lower(), map_name=_LAKE + sfx))
        return store

    def test_clipped_and_noisy_reads_resolve(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp, "Happiness", "Rage", "Sorrow")
            for text, want in (
                ("Lake  of Oblivion Weathered Land  of Happir", "happiness"),
                ("Lake o of Oblivion Weathered Land of Happir", "happiness"),
                ("Lake of Oblivion Weathered Land I of Rage", "rage"),
                ("Lake of Oblivion Weathered La _and of f Sorrow", "sorrow"),
            ):
                entry, score = store.match_title(text)
                self.assertIsNotNone(entry, text)
                self.assertEqual(entry.name, want, text)

    def test_ambiguous_prefix_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp, "Happiness", "Rage")
            entry, score = store.match_title("Lake of Oblivion")
            self.assertIsNone(entry)
            self.assertGreater(score, 0)

    def test_lone_sibling_does_not_claim_another_title(self):
        # Only Rage is stored; reading Happiness must not resolve to it.
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp, "Rage")
            self.assertIsNone(
                store.match_name(_LAKE + "Happiness")
            )

    def test_entrance_and_room_are_different_maps(self):
        # "... Storehouse Entrance" is not a misread "... Storehouse".
        room = "Identisk Tisk Food Storehouse"
        with tempfile.TemporaryDirectory() as tmp:
            store = MapStore(tmp)
            store.save(MapEntry(name="room", map_name=room))
            self.assertIsNone(store.match_name(room + " Entrance"))
        with tempfile.TemporaryDirectory() as tmp:
            store = MapStore(tmp)
            store.save(MapEntry(name="entrance", map_name=room + " Entrance"))
            self.assertIsNone(store.match_name(room))

    def test_exact_title_wins_even_with_its_sibling_stored(self):
        room = "Identisk Tisk Food Storehouse"
        with tempfile.TemporaryDirectory() as tmp:
            store = MapStore(tmp)
            store.save(MapEntry(name="room", map_name=room))
            store.save(MapEntry(name="entrance", map_name=room + " Entrance"))
            self.assertEqual(store.match_name(room + " Entrance").name, "entrance")
            self.assertEqual(store.match_name(room).name, "room")

    def test_numbered_siblings_are_different_maps(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = MapStore(tmp)
            store.save(MapEntry(name="w26", map_name="Limina End of the World 2-6"))
            # Seen in real captures: 1-2 resolved to the stored 2-6 (0.952).
            self.assertIsNone(store.match_name("Limina End of the World 1-2"))
            self.assertEqual(
                store.match_name("Limina End of the World 2-6").name, "w26")

    def test_region_prefix_and_icon_junk_still_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = MapStore(tmp)
            store.save(MapEntry(name="arc", map_name="Arcana Cavern Lower Path"))
            self.assertEqual(store.match_name("QY Arcana Cavern Lower Path").name, "arc")

    def test_short_alias_cannot_substring_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = MapStore(tmp)
            store.save(MapEntry(name="of"))
            self.assertIsNone(store.match_name(_LAKE + "Rage"))


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
    def test_save_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = MapStore(tmp)
            path = store.save(MapEntry(
                name="farm_1", rotation=Rotation(anchors=[], legs={}),
            ))
            self.assertTrue(Path(path).exists())
            self.assertEqual(MapStore(tmp).names(), ["farm_1"])

    def test_match_name_on_stored_title_substring(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = MapStore(tmp)
            store.save(MapEntry(name="lake1f", map_name="Weathered Land of Happiness"))
            hit = store.match_name(
                "Lake of Oblivion Weathered Land of Happiness"
            )
            self.assertEqual(hit.name, "lake1f")

    def test_match_name_falls_back_to_alias(self):
        # File named like the title matches even without map_name set.
        with tempfile.TemporaryDirectory() as tmp:
            store = MapStore(tmp)
            store.save(MapEntry(name="lake_of_oblivion"))
            self.assertEqual(
                store.match_name("Lake of Oblivion").name,
                "lake_of_oblivion",
            )

    def test_match_name_truncated_ocr(self):
        # OCR clipped mid-name — a ≥6-char fragment inside a stored
        # name still resolves.
        with tempfile.TemporaryDirectory() as tmp:
            store = MapStore(tmp)
            store.save(MapEntry(name="m", map_name="Lake of Oblivion"))
            self.assertEqual(
                store.match_name("Lake of Obliv").name, "m"
            )
            # ...but a tiny fragment must not.
            self.assertIsNone(store.match_name("Lake"))

    def test_match_name_longest_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = MapStore(tmp)
            store.save(MapEntry(name="short", map_name="Limina"))
            store.save(MapEntry(name="east", map_name="Limina : 1-5 East"))
            self.assertEqual(
                store.match_name("Limina : 1-5 East").name, "east"
            )

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

    def test_platforms_roundtrip(self):
        segs = [[0.05, 0.2667, 0.5, 0.2667], [0.2, 0.6, 0.9, 0.6]]
        entry = MapEntry(name="m", platforms=segs)
        data = entry.to_dict()
        self.assertEqual(data["platforms"], segs)
        self.assertEqual(MapEntry.from_dict(data).platforms, segs)
        entry = MapEntry.from_dict(
            {"name": "m", "platforms": ["bad", [1, 2], [0.1, 0.1, 0.5, 0.1]]}
        )
        self.assertEqual(entry.platforms, [[0.1, 0.1, 0.5, 0.1]])
        entry = MapEntry.from_dict({"name": "m"})
        self.assertIsNone(entry.platforms)
        self.assertIsNone(entry.to_dict()["platforms"])

    def test_missing_dir_is_empty(self):
        store = MapStore("/nonexistent/dir")
        self.assertEqual(store.load_all(), [])
        self.assertEqual(store.match_title("anything"), (None, 0.0))

    def test_bad_file_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "broken.json").write_text("{not json")
            store = MapStore(tmp)
            self.assertEqual(store.load_all(), [])


class FileNameCollisionTests(unittest.TestCase):
    def test_names_that_sanitise_alike_get_their_own_files(self):
        import tempfile

        from picobot.bot.maps import MapEntry, MapStore

        with tempfile.TemporaryDirectory() as tmp:
            store = MapStore(tmp)
            a = store.save(MapEntry(name="Foo 2"))
            b = store.save(MapEntry(name="Foo_2"))
            self.assertNotEqual(a, b)
            again = MapStore(tmp)
            self.assertEqual(sorted(again.names()), ["Foo 2", "Foo_2"])
            self.assertEqual(store.save(again.get("Foo 2")), a)   # stable


if __name__ == "__main__":
    unittest.main()
