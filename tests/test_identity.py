import tempfile
import threading
import time
import unittest

from picobot.bot.identity import MapIdentity
from picobot.bot.maps import MapEntry, MapStore

_LAKE = "Lake of Oblivion Weathered Land of "


class _Reader:
    def __init__(self, *texts):
        self.texts = list(texts)
        self.calls = 0

    def read(self, band):
        self.calls += 1
        return self.texts.pop(0) if len(self.texts) > 1 else self.texts[0]


class MapIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = MapStore(self.tmp.name)
        for sfx in ("Happiness", "Rage"):
            self.store.save(MapEntry(name=sfx.lower(), map_name=_LAKE + sfx))
        self.events = []

    def tearDown(self):
        self.tmp.cleanup()

    def _ident(self, reader, **kw):
        kw.setdefault("threaded", False)
        kw.setdefault("retry_s", 0.0)
        return MapIdentity(
            self.store, reader,
            on_event=lambda k, m: self.events.append(m), **kw
        )

    def _pump(self, ident, n=5):
        for _ in range(n):
            ident.pump(lambda: object())

    def test_strong_single_read_resolves(self):
        ident = self._ident(_Reader(_LAKE + "Rage"))
        ident.request("startup")
        self._pump(ident, 1)
        self.assertEqual(ident.current.name, "rage")
        self.assertEqual(ident.current.via, "ocr")
        self.assertFalse(ident.pending)

    def test_clipped_title_needs_two_agreeing_reads(self):
        reader = _Reader(_LAKE + "Happir")
        ident = self._ident(reader)
        ident.request("startup")
        self._pump(ident, 1)
        self.assertIsNone(ident.current.name)
        self.assertTrue(ident.pending)
        self._pump(ident, 1)
        self.assertEqual(ident.current.name, "happiness")
        self.assertEqual(reader.calls, 2)

    def test_unknown_title_recorded_without_map(self):
        ident = self._ident(_Reader("Chu Chu Island Chu Chu Village"))
        ident.request("startup")
        self._pump(ident)
        self.assertIsNone(ident.current.name)
        self.assertEqual(ident.current.title, "Chu Chu Island Chu Chu Village")

    def test_unreadable_gives_up_after_max_reads(self):
        reader = _Reader(None)
        ident = self._ident(reader, max_reads=3)
        ident.request("startup")
        self._pump(ident, 10)
        self.assertEqual(reader.calls, 3)
        self.assertFalse(ident.pending)
        self.assertIn("map title unreadable", self.events)

    def test_arrival_clears_stale_title(self):
        ident = self._ident(_Reader(_LAKE + "Rage"))
        ident.request("startup")
        self._pump(ident, 1)
        ident.request("arrival", clear=True)
        self.assertIsNone(ident.current.name)
        self.assertIsNone(ident.current.title)

    def test_pin_stands_until_title_names_another_map(self):
        ident = self._ident(_Reader(_LAKE + "Rage"), pin="happiness")
        self.assertEqual((ident.current.name, ident.current.via), ("happiness", "pin"))
        ident.request("startup")
        self._pump(ident, 1)
        self.assertEqual(ident.current.name, "rage")
        self.assertTrue(any("overrides pin" in e for e in self.events))

    def test_pin_confirmed_by_title(self):
        ident = self._ident(_Reader(_LAKE + "Rage"), pin="rage")
        ident.request("startup")
        self._pump(ident, 1)
        self.assertEqual((ident.current.name, ident.current.via), ("rage", "ocr"))

    def test_unknown_title_keeps_pin(self):
        ident = self._ident(_Reader("Chu Chu Island Chu Chu Village"), pin="rage")
        ident.request("startup")
        self._pump(ident)
        self.assertEqual((ident.current.name, ident.current.via), ("rage", "pin"))

    def test_stale_read_from_previous_request_is_discarded(self):
        ident = self._ident(_Reader(_LAKE + "Rage"))
        ident.request("startup")
        with ident._lock:
            ident._busy = True
            gen = ident._gen
        ident.request("arrival", clear=True)       # supersedes the read
        ident._process(object(), gen)
        self.assertIsNone(ident.current.name)
        self.assertFalse(ident._busy)

    def test_no_reads_without_request_or_when_disabled(self):
        reader = _Reader(_LAKE + "Rage")
        ident = self._ident(reader)
        self._pump(ident)
        ident2 = self._ident(reader, ocr_enabled=False)
        ident2.request("startup")
        self._pump(ident2)
        self.assertEqual(reader.calls, 0)

    def test_version_bumps_only_on_change(self):
        ident = self._ident(_Reader(_LAKE + "Rage"))
        v0 = ident.version
        ident.request("startup")
        self._pump(ident, 1)
        v1 = ident.version
        ident.request("again")
        self._pump(ident, 1)
        self.assertGreater(v1, v0)
        self.assertEqual(ident.version, v1)

    def test_refresh_after_store_change(self):
        ident = self._ident(_Reader("Chu Chu Island Chu Chu Village"))
        ident.request("startup")
        self._pump(ident)
        self.store.save(MapEntry(name="chu", map_name="Chu Chu Island Chu Chu Village"))
        ident.refresh()
        self.assertEqual(ident.current.name, "chu")

    def test_worker_thread_runs_ocr_off_the_caller(self):
        seen = []

        class Slow:
            def read(self, band):
                seen.append(threading.current_thread().name)
                return _LAKE + "Rage"

        ident = MapIdentity(self.store, Slow(), threaded=True)
        ident.request("startup")
        ident.pump(lambda: object())
        deadline = time.time() + 2
        while ident.current.name is None and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(ident.current.name, "rage")
        self.assertEqual(seen, ["TitleOCR"])


if __name__ == "__main__":
    unittest.main()
