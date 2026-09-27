import json
import tempfile
import unittest
from pathlib import Path

from picobot.bot.config import BotConfig
from picobot.bot.reach import Reach, ReachModel, base_reach


def _model(**kw):
    base = {"flash": Reach(20.0, 4.0), "up_flash": Reach(6.0, 14.0),
            "up_side_flash": Reach(16.0, 11.0)}
    return ReachModel(base, **kw)


class ReachModelTests(unittest.TestCase):
    def test_defaults_from_config(self):
        r = base_reach(BotConfig())
        self.assertEqual(r["flash"].dx, 20.0)
        self.assertEqual(r["up_flash"].rise, 14.0)
        self.assertEqual(r["rope_lift"].rise, 20.0)

    def test_fits_proven_exploratory_and_out(self):
        m = _model()
        self.assertTrue(m.fits("flash", 18, 0))
        self.assertFalse(m.fits("flash", 22, 0))       # within 1.15x → explore
        self.assertIsNone(m.fits("flash", 30, 0))

    def test_success_grows_to_observation(self):
        m = _model()
        m.observe("flash", planned=(22, 0), observed=(26, 0), ok=True)
        self.assertEqual(m.get("flash").dx, 26)
        m.observe("up_flash", planned=(0, 13), observed=(1, 17), ok=True)
        self.assertEqual(m.get("up_flash").rise, 17)
        self.assertEqual(m.get("up_flash").dx, 6.0)    # drift not learned

    def test_failure_inside_envelope_shrinks(self):
        m = _model()
        m.observe("flash", planned=(18, 0), observed=(10, -5), ok=False)
        self.assertAlmostEqual(m.get("flash").dx, 18 * 0.95)

    def test_exploratory_failure_sets_ceiling_only(self):
        m = _model()
        m.observe("flash", planned=(22, 0), observed=(12, -8), ok=False)
        self.assertEqual(m.get("flash").dx, 20.0)       # proven reach kept
        self.assertIsNone(m.fits("flash", 22, 0))       # no retry at 22
        self.assertFalse(m.fits("flash", 21, 0))        # just under still ok

    def test_shrink_floor_is_half_base(self):
        m = _model()
        for _ in range(20):
            e = m.get("up_flash").rise
            m.observe("up_flash", planned=(0, e), observed=(0, 0), ok=False)
        self.assertGreaterEqual(m.get("up_flash").rise, 7.0)

    def test_persistence_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reach.json"
            m = _model(path=path)
            m.observe("flash", planned=(22, 0), observed=(27, 0), ok=True)
            m.observe("up_flash", planned=(0, 16), observed=(0, 2), ok=False)
            m.save(force=True)
            data = json.loads(path.read_text())
            self.assertIsNone(data["ceiling"]["up_flash"]["dx"])   # inf → null
            again = _model(path=path)
            self.assertEqual(again.get("flash").dx, 27)
            self.assertEqual(again.snapshot(), m.snapshot())

    def test_version_changes_only_on_estimate_change(self):
        m = _model()
        v = m.version
        m.observe("flash", planned=(10, 0), observed=(12, 0), ok=True)
        self.assertEqual(m.version, v)                   # 12 < 20 proven
        m.observe("flash", planned=(10, 0), observed=(24, 0), ok=True)
        self.assertEqual(m.version, v + 1)


if __name__ == "__main__":
    unittest.main()
