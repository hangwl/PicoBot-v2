import random
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from picobot.bot.patrol import Patrol
from picobot.bot.rotation import Anchor, Rotation
from tests.test_navigator import FLOOR, MID, SIDE, TOP, SimBot


class PatrolBot(SimBot):
    """SimBot + the SmartBot surface Patrol uses (region 200x150)."""

    def __init__(self, plats, pos, anchors, **kw):
        super().__init__(plats, pos, **kw)
        self.rot = Rotation(anchors=[Anchor(f"a{i}", x / 200, y / 150)
                                     for i, (x, y) in enumerate(anchors)])
        self.config = SimpleNamespace(nav_threshold_px=4, jump_key="space",
                                      linger_hops=(0, 0))
        self._anchor_idx = 0
        self._ckpt_ban = {}
        self._arrive_pending = []
        self._weave_dir = self._weave_bounds = None
        self.weaves = 0
        self.attacks = 0
        self._patrol_tick = Mock()
        self._run_leg = Mock(return_value=True)

    def effective_rotation(self):
        return self.rot

    def _nav_graph(self):
        return self.g

    def _rx(self, v):
        return round(v * 200)

    def _ry(self, v):
        return round(v * 150)

    def _weave_attack(self):
        self.weaves += 1

    def _attack_once(self):
        self.attacks += 1

    def _arrival_skills(self, anchor, now):
        return []


class PatrolTests(unittest.TestCase):
    def test_plans_full_loop_and_publishes_traversal(self):
        bot = PatrolBot([FLOOR, MID, TOP], (10, 100),
                        [(20, 100), (80, 66), (180, 100)])
        p = Patrol(bot, rng=random.Random(1))
        p.tick()
        plan = next(l for l in bot.log_lines if l.startswith("Patrol plan:"))
        self.assertEqual(sorted(plan.split(": ")[1].split(" → ")), ["a0", "a1", "a2"])
        kinds = {k for k, *_ in bot.viz["plan"]}
        self.assertIn("up_flash", kinds)                   # climbs to a1 in the plan

    def test_keeps_moving_through_every_anchor_then_replans(self):
        anchors = [(20, 100), (80, 66), (170, 84)]
        bot = PatrolBot([FLOOR, MID, TOP, SIDE], (10, 100), anchors)
        p = Patrol(bot, rng=random.Random(2))
        arrivals = []
        for _ in range(60):
            before = len(p.order)
            p.tick()
            if len(p.order) < before or (before and not p.order):
                arrivals.append(bot._anchor_idx)
        self.assertGreaterEqual(len(arrivals), 5)          # looped more than once
        self.assertEqual(set(arrivals), {0, 1, 2})
        self.assertEqual(bot.attacks, 0)                   # never parked
        self.assertEqual(bot._patrol_tick.call_count, 0)

    def test_linger_is_bounded_weave_hops(self):
        bot = PatrolBot([FLOOR], (10, 100), [(20, 100), (180, 100)])
        bot.config.linger_hops = (2, 2)
        p = Patrol(bot, rng=random.Random(0))
        for _ in range(9):
            p.tick()
        arrivals = sum(l.startswith("Checkpoint:") for l in bot.log_lines)
        self.assertGreaterEqual(arrivals, 2)
        self.assertLessEqual(bot.weaves, 2 * arrivals)
        self.assertGreaterEqual(bot.weaves, 2 * (arrivals - 1))

    def test_unreachable_anchor_is_skipped_not_stuck_on(self):
        # a1 floats 60px above everything — no move reaches it.
        bot = PatrolBot([FLOOR, (60, 40, 100, 40)], (10, 100),
                        [(20, 100), (80, 40), (180, 100)])
        p = Patrol(bot, rng=random.Random(0))
        for _ in range(12):
            p.tick()
        self.assertNotIn(1, p.order)
        self.assertTrue(any("unreachable" in l for l in bot.log_lines))

    def test_nothing_reachable_weaves_without_replanning_every_tick(self):
        bot = PatrolBot([FLOOR, (60, 40, 100, 40)], (10, 100), [(80, 40), (90, 40)])
        p = Patrol(bot, rng=random.Random(0))
        for _ in range(10):
            p.tick()
        self.assertEqual(bot.weaves, 10)
        self.assertEqual(sum("unreachable" in l for l in bot.log_lines), 1)

    def test_continues_after_first_arrival_near_takeoff(self):
        # Regression: arriving 2px from the next up-flash takeoff stalled
        # the bot (no-op walk leg re-planned forever).
        bot = PatrolBot([FLOOR, MID], (10, 100), [(78, 100), (80, 84)])
        p = Patrol(bot, rng=random.Random(0))
        for _ in range(6):
            p.tick()
        self.assertIn("up_flash", bot.moves)
        self.assertGreaterEqual(sum(l.startswith("Checkpoint:") for l in bot.log_lines), 2)

    def test_repeated_misses_ban_the_anchor(self):
        bot = PatrolBot([FLOOR, MID], (60, 100), [(20, 100), (80, 84)], up=5)
        p = Patrol(bot, rng=random.Random(0))
        p.order = [1]
        for _ in range(4):
            p.tick()
        self.assertIn(1, bot._ckpt_ban)

    def test_off_graph_falls_back_to_straight_line_patrol(self):
        bot = PatrolBot([FLOOR], (10, 40), [(20, 100), (180, 100)])   # mid-air
        Patrol(bot).tick()
        bot._patrol_tick.assert_called_once()

    def test_recorded_leg_wins(self):
        bot = PatrolBot([FLOOR, MID], (20, 100), [(20, 100), (80, 84)])
        bot.rot.legs[(0, 1)] = ["climb"]
        p = Patrol(bot, rng=random.Random(0))
        p.tick()
        bot._run_leg.assert_called_once_with(["climb"])


if __name__ == "__main__":
    unittest.main()
