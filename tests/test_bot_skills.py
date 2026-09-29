import unittest

from picobot.bot.skills import Skill, SkillBook


class SkillParseTests(unittest.TestCase):
    def test_defaults(self):
        s = Skill.from_dict("main", {"key": "a"})
        self.assertEqual(s.cooldown, 0.0)
        self.assertEqual(s.kind, "attack")
        self.assertIsNone(s.hold)

    def test_full_spec(self):
        s = Skill.from_dict("fountain", {
            "key": "d", "cooldown": 57, "kind": "summon",
            "wait_on_arrival": 4, "hold": 0.5,
        })
        self.assertEqual(s.cooldown, 57.0)
        self.assertEqual(s.kind, "summon")
        self.assertEqual(s.wait_on_arrival, 4.0)
        self.assertEqual(s.hold, 0.5)

    def test_bad_kind_and_missing_key_rejected(self):
        with self.assertRaises(ValueError):
            Skill.from_dict("x", {"key": "a", "kind": "ultimate"})
        with self.assertRaises(ValueError):
            Skill.from_dict("x", {"cooldown": 5})

    def test_to_dict_round_trip(self):
        spec = {"key": "d", "cooldown": 57, "kind": "summon",
                "wait_on_arrival": 4}
        s = Skill.from_dict("f", spec)
        out = s.to_dict()
        self.assertEqual(out["key"], "d")
        self.assertEqual(out["cooldown"], 57)
        s2 = Skill.from_dict("f", out)
        self.assertEqual(s2.cooldown, 57.0)


class SkillBookTests(unittest.TestCase):
    def test_zero_cooldown_always_ready(self):
        book = SkillBook({"a": Skill("a", "a", 0.0)})
        self.assertTrue(book.ready("a"))
        book.mark_used("a")
        self.assertTrue(book.ready("a"))

    def test_cooldown_counts_down(self):
        book = SkillBook({"s": Skill("s", "s", 60.0)})
        self.assertTrue(book.ready("s"))
        book.mark_used("s", now=100.0)
        self.assertFalse(book.ready("s", now=110.0))
        self.assertAlmostEqual(book.remaining("s", now=110.0), 50.0)
        self.assertTrue(book.ready("s", now=161.0))

    def test_unknown_skill_not_ready(self):
        book = SkillBook()
        self.assertFalse(book.ready("nope"))

    def test_ready_attacks_and_due_buffs(self):
        book = SkillBook({
            "spam": Skill("spam", "a", 0.0, "attack"),
            "burst": Skill("burst", "s", 30.0, "attack"),
            "holy": Skill("holy", "f", 120.0, "buff"),
            "fountain": Skill("fountain", "d", 57.0, "summon"),
        })
        self.assertEqual(
            {s.name for s in book.ready_attacks()}, {"spam", "burst"}
        )
        self.assertEqual([s.name for s in book.due_buffs()], ["holy"])
        book.mark_used("burst", now=0.0)
        self.assertEqual(
            [s.name for s in book.ready_attacks(now=1.0)], ["spam"]
        )

    def test_from_config_explicit_skills(self):
        book = SkillBook.from_config({
            "skills": {"main": {"key": "a"}, "f": {"key": "d", "kind": "summon"}}
        })
        self.assertIn("main", book)
        self.assertEqual(book.get("f").kind, "summon")
        # explicit map replaces legacy lists entirely
        self.assertNotIn("attack_a", book)

    def test_from_config_legacy_keys(self):
        book = SkillBook.from_config({
            "attack_keys": ["a", "s"],
            "buff_keys": ["f"],
            "buff_interval_seconds": 90,
        })
        self.assertIn("attack_a", book)
        self.assertIn("attack_s", book)
        buff = book.get("buff_f")
        self.assertEqual(buff.kind, "buff")
        self.assertEqual(buff.cooldown, 90.0)

    def test_from_config_empty_defaults_to_a(self):
        book = SkillBook.from_config({})
        self.assertIn("attack_a", book)

    def test_overlay(self):
        book = SkillBook({"a": Skill("a", "a")})
        book.overlay({"b": Skill("b", "b", 5.0)})
        self.assertIn("b", book)


class ChargeTests(unittest.TestCase):
    def test_one_charge_behaves_like_a_plain_cooldown(self):
        book = SkillBook({"s": Skill("s", "s", 10.0)})
        book.mark_used("s", now=100.0)
        self.assertFalse(book.ready("s", now=105.0))
        self.assertAlmostEqual(book.remaining("s", now=105.0), 5.0)
        self.assertTrue(book.ready("s", now=110.0))

    def test_charges_return_one_per_cooldown(self):
        book = SkillBook({"s": Skill("s", "s", 10.0, "summon", charges=2)})
        book.mark_used("s", now=100.0)
        book.mark_used("s", now=101.0)
        self.assertEqual(book.charges("s", now=101.0), 0)
        self.assertEqual(book.charges("s", now=110.5), 1)   # first back at 110
        self.assertEqual(book.charges("s", now=119.0), 1)
        self.assertEqual(book.charges("s", now=120.0), 2)   # second at 120
        self.assertEqual(book.charges("s", now=500.0), 2)   # never above max

    def test_use_while_recharging_keeps_the_timer(self):
        book = SkillBook({"s": Skill("s", "s", 10.0, "summon", charges=2)})
        book.mark_used("s", now=100.0)                      # timer -> 110
        book.mark_used("s", now=105.0)                      # still -> 110
        self.assertEqual(book.charges("s", now=110.0), 1)

    def test_charges_and_duration_round_trip(self):
        s = Skill.from_dict("f", {"key": "d", "kind": "summon", "cooldown": 57,
                                  "charges": 2, "duration": 60})
        self.assertEqual((s.charges, s.duration, s.uptime), (2, 60.0, 60.0))
        again = Skill.from_dict("f", s.to_dict())
        self.assertEqual((again.charges, again.duration), (2, 60.0))
        plain = Skill.from_dict("g", {"key": "g", "kind": "summon", "cooldown": 30})
        self.assertEqual(plain.uptime, 30.0)                # until its cooldown


class SummonTrackerTests(unittest.TestCase):
    def test_expiry_frees_the_anchor_and_reset_clears(self):
        from picobot.bot.summons import SummonTracker

        t = SummonTracker()
        f = Skill("f", "d", 57, "summon", duration=60)
        t.place(f, "a0", now=0.0)
        self.assertFalse(t.anchor_free("a0", now=59.0))
        self.assertTrue(t.anchor_free("a0", now=60.5))
        t.place(f, "a1", now=100.0)
        t.reset()
        self.assertEqual(t.active(now=101.0), [])


if __name__ == "__main__":
    unittest.main()
