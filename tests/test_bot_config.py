import unittest

from picobot.bot.config import BotConfig
from picobot.bot.timing import human_delay, human_hold, jittered


class BotConfigTests(unittest.TestCase):
    def test_defaults(self):
        cfg = BotConfig()
        self.assertFalse(hasattr(cfg, "enable_random_wander"))
        self.assertTrue(cfg.stop_when_rune_appears)
        self.assertFalse(cfg.pause_on_lie_detector)
        self.assertEqual(cfg.attack_keys, ["a"])
        self.assertIsNone(cfg.minimap_region)

    def test_from_dict_overrides(self):
        cfg = BotConfig.from_dict({
            "attack_keys": ["a", "s"],
            "stop_when_players_appear": False,
            "minimap_region": [10, 20, 100, 80],
            "skill_gap_seconds": [0.2, 0.4],
        })
        self.assertEqual(cfg.attack_keys, ["a", "s"])
        self.assertFalse(cfg.stop_when_players_appear)
        self.assertEqual(cfg.minimap_region, (10, 20, 100, 80))
        self.assertEqual(cfg.skill_gap_seconds, (0.2, 0.4))

    def test_class_profile_selects_kit(self):
        cfg = BotConfig.from_dict({"class": {
            "active": "mage",
            "profiles": {
                "mage": {"travel": "teleport", "air_attacks": False,
                         "teleport_key": "shift",
                         "teleport_cooldown": 0.8},
                "hero": {"travel": "flash"},
            },
        }})
        self.assertEqual(cfg.class_active, "mage")
        self.assertEqual(cfg.class_travel, "teleport")
        self.assertFalse(cfg.air_attacks)
        self.assertEqual(cfg.teleport_key, "shift")
        self.assertEqual(cfg.teleport_cooldown, 0.8)
        self.assertEqual(cfg.class_profiles["hero"], {"travel": "flash"})

    def test_teleport_without_key_falls_back_to_flash(self):
        cfg = BotConfig.from_dict({"class": {
            "active": "mage",
            "profiles": {"mage": {"travel": "teleport"}},
        }})
        self.assertEqual(cfg.class_travel, "flash")

    def test_from_dict_none_gives_defaults(self):
        self.assertEqual(BotConfig.from_dict(None), BotConfig())

    def test_bad_region_rejected(self):
        with self.assertRaises(ValueError):
            BotConfig.from_dict({"minimap_region": [1, 2, 3]})

    def test_skills_synthesised_from_legacy_keys(self):
        cfg = BotConfig.from_dict({
            "attack_keys": ["a", "s"],
            "buff_keys": ["f"],
            "buff_interval_seconds": 90,
        })
        self.assertIn("attack_s", cfg.skills)
        self.assertEqual(cfg.skills["buff_f"].cooldown, 90.0)

    def test_explicit_skills_map(self):
        cfg = BotConfig.from_dict({
            "skills": {"fountain": {"key": "d", "cooldown": 57,
                                    "kind": "summon"}}
        })
        self.assertEqual(list(cfg.skills), ["fountain"])
        self.assertEqual(cfg.skills["fountain"].cooldown, 57.0)

    def test_rotation_and_map_options(self):
        cfg = BotConfig.from_dict({
            "rotation": {
                "style": "pingpong",
                "anchors": [{"pos": [0.2, 0.5]}, {"pos": [0.8, 0.5]}],
            },
            "travel_style": "flash",
            "flash_jump": {"enabled": True, "key": "alt"},
            "maps_dir": "my_maps",
            "active_map": "farm_1",
            "auto_select_map": False,
            "map_match_threshold": 12.5,
        })
        self.assertEqual(cfg.rotation.style, "pingpong")
        self.assertEqual(len(cfg.rotation.anchors), 2)
        self.assertEqual(cfg.travel_style, "flash")
        self.assertEqual(cfg.flash_jump_key, "alt")
        self.assertEqual(cfg.maps_dir, "my_maps")
        self.assertEqual(cfg.active_map, "farm_1")
        self.assertFalse(cfg.auto_select_map)
        self.assertFalse(hasattr(cfg, "map_match_threshold"))  # legacy key ignored


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

    def test_human_between_is_clamped_lognormal(self):
        from picobot.bot.timing import human_between

        samples = [human_between(0.17, 0.11, 0.26) for _ in range(500)]
        self.assertTrue(all(0.11 <= v <= 0.26 for v in samples))
        self.assertGreater(len({round(v, 3) for v in samples}), 50)
        above = sum(v > 0.17 for v in samples)
        self.assertTrue(150 < above < 350)       # centred near the mean

    def test_jittered_stays_in_band(self):
        for _ in range(100):
            self.assertGreaterEqual(jittered(1.0), 0.85)
            self.assertLessEqual(jittered(1.0), 1.15)


if __name__ == "__main__":
    unittest.main()
