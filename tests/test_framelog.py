import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from picobot.vision import framelog
from picobot.vision.minimap import MinimapAnalyzer

_BORDER = (228, 228, 228)


def _hlines(w=200, h=150):
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[::15, :] = _BORDER
    return img


def _vlines(w=200, h=150, off=0):
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:, off::15] = _BORDER
    return img


def _events(root: Path):
    return sorted(p for p in root.iterdir() if p.is_dir())


class FrameRecorderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.rec = framelog.configure(self.root, pre=3, post=2)

    def tearDown(self):
        framelog.configure(None)
        self.tmp.cleanup()

    def _analyzer(self):
        a = MinimapAnalyzer()
        a._region = (0, 0, 200, 150)
        a._region_source = "auto"
        return a

    def test_disabled_by_default_writes_nothing(self):
        framelog.configure(None)
        a = self._analyzer()
        for img in (_hlines(), _vlines(), _vlines(), _vlines()):
            a.note_frame(img)
        self.assertEqual(_events(self.root), [])

    def test_confirmed_change_saves_episode(self):
        a = self._analyzer()
        ctx_calls = []

        def ctx():
            ctx_calls.append(1)
            return {"window": np.full((40, 60, 3), 7, np.uint8)}

        frames = [_hlines(), _hlines()] + [_vlines()] * 3 + [_vlines()] * 3
        results = [a.note_frame(f, context=ctx) for f in frames]
        self.assertEqual(results.count(True), 1)
        self.rec.flush()
        (ev,) = _events(self.root)
        self.assertTrue(ev.name.endswith("_watchdog_confirmed"))
        meta = json.loads((ev / "meta.json").read_text())
        self.assertEqual(meta["outcome"], "confirmed")
        self.assertEqual(meta["region_source"], "auto")
        states = [f["state"] for f in meta["frames"]]
        self.assertIn("confirm", states)
        self.assertTrue(meta["frames"][0]["pre"])
        for name in ("baseline.png", "window_onset.png", "window_confirm.png",
                     "000.png", "000_mask.png"):
            self.assertTrue((ev / name).exists(), name)
        self.assertEqual(len(ctx_calls), 2)

    def test_recovered_overlay_is_saved_single_blip_is_not(self):
        a = self._analyzer()
        a.note_frame(_hlines())
        a.note_frame(_vlines())            # one-frame blip
        for _ in range(3):
            a.note_frame(_hlines())
        self.rec.flush()
        self.assertEqual(_events(self.root), [])

        a.note_frame(_vlines())
        a.note_frame(_vlines())            # 2-frame overlay, then gone
        for _ in range(3):
            a.note_frame(_hlines())
        self.rec.flush()
        (ev,) = _events(self.root)
        self.assertTrue(ev.name.endswith("_watchdog_recovered"))

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
