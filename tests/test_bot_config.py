import unittest

from picobot.bot.config import BotConfig
from picobot.bot.timing import human_delay, human_hold, jittered


class BotConfigTests(unittest.TestCase):
    def test_defaults(self):
        cfg = BotConfig()
        self.assertTrue(cfg.stationary_mode)
        self.assertTrue(cfg.stop_when_rune_appears)
        self.assertFalse(cfg.pause_on_lie_detector)
        self.assertEqual(cfg.attack_keys, ["a"])
        self.assertIsNone(cfg.minimap_region)

    def test_from_dict_overrides(self):
        cfg = BotConfig.from_dict({
            "stationary_seconds": 5,
            "attack_keys": ["a", "s"],
            "stop_when_players_appear": False,
            "minimap_region": [10, 20, 100, 80],
            "skill_gap_seconds": [0.2, 0.4],
        })
        self.assertEqual(cfg.stationary_seconds, 5.0)
        self.assertEqual(cfg.attack_keys, ["a", "s"])
        self.assertFalse(cfg.stop_when_players_appear)
        self.assertEqual(cfg.minimap_region, (10, 20, 100, 80))
        self.assertEqual(cfg.skill_gap_seconds, (0.2, 0.4))

    def test_from_dict_none_gives_defaults(self):
        self.assertEqual(BotConfig.from_dict(None), BotConfig())

    def test_bad_region_rejected(self):
        with self.assertRaises(ValueError):
            BotConfig.from_dict({"minimap_region": [1, 2, 3]})


class TimingTests(unittest.TestCase):
    def test_human_delay_respects_minimum(self):
        for _ in range(50):
            self.assertGreaterEqual(human_delay(0.0, minimum=0.05), 0.05)

    def test_human_delay_mean_ballpark(self):
        samples = [human_delay(0.5) for _ in range(500)]
        mean = sum(samples) / len(samples)
        self.assertGreater(mean, 0.2)
        self.assertLess(mean, 1.2)

    def test_human_hold_range(self):
        for _ in range(200):
            value = human_hold()
            self.assertGreaterEqual(value, 0.04)
            self.assertLessEqual(value, 0.25)

    def test_jittered_stays_in_band(self):
        for _ in range(100):
            self.assertGreaterEqual(jittered(1.0), 0.85)
            self.assertLessEqual(jittered(1.0), 1.15)


if __name__ == "__main__":
    unittest.main()
