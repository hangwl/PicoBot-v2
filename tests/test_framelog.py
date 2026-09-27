import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from picobot.vision import framelog
from picobot.vision.minimap import MinimapAnalyzer
from picobot.vision.transition import TransitionDetector


def _lit(w=200, h=150):
    return np.full((h, w, 3), 90, dtype=np.uint8)


def _black(w=200, h=150):
    return np.zeros((h, w, 3), dtype=np.uint8)


def _events(root: Path):
    return sorted(p for p in root.iterdir() if p.is_dir())


class FrameRecorderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.rec = framelog.configure(self.root, pre=3, post=2)
        self.t = 0.0

    def tearDown(self):
        framelog.configure(None)
        self.tmp.cleanup()

    def _analyzer(self):
        a = MinimapAnalyzer(transition=TransitionDetector(clock=lambda: self.t))
        a._region = (0, 0, 200, 150)
        a._region_source = "auto"
        return a

    def _feed(self, a, frames, ctx=None):
        out = []
        for f in frames:
            self.t += 0.1
            out.append(a.note_frame(f, context=ctx))
        return out

    def test_disabled_by_default_writes_nothing(self):
        framelog.configure(None)
        a = self._analyzer()
        self._feed(a, [_lit()] * 3 + [_black()] * 8 + [_lit()] * 12)
        self.assertEqual(_events(self.root), [])

    def test_transfer_saves_episode(self):
        a = self._analyzer()
        calls = []

        def ctx():
            calls.append(1)
            return {"window": np.full((40, 60, 3), 7, np.uint8)}

        out = self._feed(a, [_lit()] * 4 + [_black()] * 8 + [_lit()] * 12, ctx)
        self.assertEqual(out.count(True), 1)
        self.rec.flush()
        (ev,) = _events(self.root)
        self.assertTrue(ev.name.endswith("_transition"))
        meta = json.loads((ev / "meta.json").read_text())
        self.assertEqual(meta["outcome"], "arrived")
        self.assertEqual(meta["region_source"], "auto")
        self.assertTrue(meta["frames"][0]["pre"])
        self.assertIn("arrived", [f["event"] for f in meta["frames"]])
        self.assertTrue((ev / "window_arrived.png").exists())
        self.assertTrue((ev / "000.png").exists())
        self.assertEqual(len(calls), 1)

    def test_dark_blip_is_not_saved(self):
        a = self._analyzer()
        self._feed(a, [_lit()] * 3 + [_black()] + [_lit()] * 6)
        self.rec.flush()
        self.assertEqual(_events(self.root), [])

    def test_snapshot_and_pruning(self):
        rec = framelog.configure(self.root, max_events=2)
        for i in range(4):
            rec.snapshot(f"ocr{i}", {"band": np.zeros((5, 5, 3), np.uint8)}, text="x")
            rec.flush()
        evs = _events(self.root)
        self.assertEqual(len(evs), 2)
        self.assertTrue(evs[-1].name.endswith("_ocr3"))
        self.assertTrue((evs[-1] / "band.png").exists())


if __name__ == "__main__":
    unittest.main()
