import random
import time
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
        self.config = SimpleNamespace(
            nav_threshold_px=4, jump_key="space", walk_band_px=8,
            wall_pad_px=3.0)
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
        return self.g if self.g.platforms else None

    def _current_map_entry(self):
        return None

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
                        [(20, 100), (80, 66), (180, 100)], rope=0)
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
        for _ in range(60):
            p.tick()
        arrivals = [l.split(": ")[1] for l in bot.log_lines
                    if l.startswith("Checkpoint:")]
        self.assertGreaterEqual(len(arrivals), 5)          # looped more than once
        self.assertEqual(set(arrivals), {"a0", "a1", "a2"})
        self.assertEqual(bot.attacks, 0)                   # never parked
        self.assertEqual(bot.weaves, 0)                    # no linger
        self.assertEqual(bot._patrol_tick.call_count, 0)

    def test_no_linger_moves_on_immediately(self):
        bot = PatrolBot([FLOOR], (10, 100), [(20, 100), (180, 100)])
        p = Patrol(bot, rng=random.Random(0))
        for _ in range(12):
            p.tick()
        self.assertGreaterEqual(sum(l.startswith("Checkpoint:") for l in bot.log_lines), 3)
        self.assertEqual(bot.weaves, 0)            # no pause at anchors at all

    def test_unreachable_anchor_is_skipped_not_stuck_on(self):
        # a1 floats 60px above everything — no move reaches it.
        bot = PatrolBot([FLOOR, (60, 40, 100, 40)], (10, 100),
                        [(20, 100), (80, 40), (180, 100)])
        p = Patrol(bot, rng=random.Random(0))
        for _ in range(12):
            p.tick()
        self.assertNotIn(1, [i for i, _, _ in p.plan])
        self.assertTrue(any("no route from here" in l for l in bot.log_lines))
        self.assertIn(1, bot._ckpt_ban)                # banned, not retried

    def test_nothing_plannable_halts_all_actions(self):
        bot = PatrolBot([FLOOR, (60, 40, 100, 40)], (10, 100), [(80, 40), (90, 40)])
        p = Patrol(bot, rng=random.Random(0))
        for _ in range(10):
            p.tick()
        self.assertEqual((bot.weaves, bot.moves), (0, []))   # halted, not weaving
        self.assertGreaterEqual(bot.slept, 2.0)              # taking a break
        self.assertEqual(sum("taking a break" in l for l in bot.log_lines), 1)

    def test_continues_after_first_arrival_near_takeoff(self):
        # Regression: arriving 2px from the next up-flash takeoff stalled
        # the bot (no-op walk leg re-planned forever).
        bot = PatrolBot([FLOOR, MID], (10, 100), [(78, 100), (80, 84)], rope=0)
        p = Patrol(bot, rng=random.Random(0))
        for _ in range(6):
            p.tick()
        self.assertIn("up_flash", bot.moves)
        self.assertGreaterEqual(sum(l.startswith("Checkpoint:") for l in bot.log_lines), 2)

    def test_repeated_misses_ban_the_anchor(self):
        bot = PatrolBot([FLOOR, MID], (60, 100), [(20, 100), (80, 84)], up=5, rope=0)
        p = Patrol(bot, rng=random.Random(0))
        from picobot.bot.navgraph import Leg

        p.plan = [(1, [Leg("up_flash", 60, 100, 80, 84, 1.0)], 0)]
        for _ in range(4):
            p.tick()
        self.assertIn(1, bot._ckpt_ban)

    def test_blind_tick_neither_attacks_nor_moves(self):
        bot = PatrolBot([FLOOR], (10, 100), [(20, 100), (180, 100)])
        bot.pos = None
        bot._blind_wait = Mock()
        Patrol(bot).tick()
        bot._blind_wait.assert_called_once()
        self.assertEqual((bot.attacks, bot.moves), (0, []))

    def test_no_platforms_falls_back_to_straight_line_patrol(self):
        bot = PatrolBot([], (10, 40), [(20, 100), (180, 100)])
        Patrol(bot).tick()
        bot._patrol_tick.assert_called_once()

    def test_player_off_graph_waits_instead_of_legacy_cascade(self):
        # Platforms drawn, player mid-air: waiting — the legacy patrol
        # would plan routes from an off-graph start and ban every anchor.
        bot = PatrolBot([FLOOR], (10, 40), [(20, 100), (180, 100)])
        bot._blind_wait = Mock()
        Patrol(bot).tick()
        bot._blind_wait.assert_called_once()
        bot._patrol_tick.assert_not_called()
        self.assertTrue(any("not on any drawn platform" in l for l in bot.log_lines))

    def test_persistent_blind_dot_leaps_off_the_rope(self):
        # The dot can stay invisible while hanging on a rope — after 2.5s
        # blind, leap off toward the last known position's nearest platform.
        bot = PatrolBot([FLOOR], (60, 100), [(20, 100), (180, 100)])
        bot.rope_exit = Mock()
        bot._blind_wait = Mock()
        p = Patrol(bot)
        p._blind_since = time.monotonic() - 3.0     # blind window elapsed
        bot.minimap.player_pos = Mock(return_value=None)
        p._last_pos = (60, 100)                     # last known position
        p.tick()
        bot.rope_exit.assert_called_once()
        self.assertEqual(bot.attacks, 0)

    def test_stable_off_graph_position_learns_a_rope(self):
        # Hanging on an (undrawn) game rope: stable + off-graph for >2s
        # records a rope segment up to the platform above, persisted into
        # the map file, and leaps off.
        import tempfile

        from picobot.bot.maps import MapEntry, MapStore

        bot = PatrolBot([FLOOR, (40, 40, 160, 40)], (60, 60),
                        [(20, 100), (180, 100)])
        bot._map = MapEntry(name="m")
        bot._current_map_entry = lambda: bot._map
        with tempfile.TemporaryDirectory() as tmp:
            bot.maps = MapStore(tmp)
            bot.maps.save(bot._map)
            bot.rope_exit = Mock()
            bot._blind_wait = Mock()
            p = Patrol(bot)
            p._stuck_since = time.monotonic() - 3.0
            p._stuck_pos = (60, 60)
            p.tick()
            bot.rope_exit.assert_called_once()
            saved = MapStore(tmp).get("m")
            self.assertEqual(len(saved.ropes), 1)
            self.assertEqual(saved.ropes[0][0], 0.3)      # stuck x
            self.assertEqual(saved.ropes[0][3], round(40 / 150, 4))
        self.assertTrue(any("Learned a rope" in l for l in bot.log_lines))

    def test_anchor_off_platform_is_banned_with_message(self):
        bot = PatrolBot([FLOOR], (10, 100), [(20, 100), (95, 20)])
        p = Patrol(bot, rng=random.Random(0))
        p.tick()
        self.assertIn(1, bot._ckpt_ban)
        self.assertTrue(any("not on a drawn platform" in l for l in bot.log_lines))

    def test_reachable_anchors_still_patrolled_past_a_bad_one(self):
        bot = PatrolBot([FLOOR, (60, 40, 100, 40)], (10, 100),
                        [(20, 100), (80, 40), (180, 100)])
        p = Patrol(bot, rng=random.Random(0))
        for _ in range(14):
            p.tick()
        names = {l.split(": ")[1] for l in bot.log_lines
                 if l.startswith("Checkpoint:")}
        self.assertEqual(names, {"a0", "a2"})           # a1 banned, rest flow

    def test_next_loop_is_planned_before_the_current_ends(self):
        bot = PatrolBot([FLOOR, MID, TOP], (10, 100),
                        [(20, 100), (80, 66), (180, 100)], rope=0)
        p = Patrol(bot, rng=random.Random(2))
        next_loop_at = completion_at = None
        for i in range(40):
            p.tick()
            if next_loop_at is None and any(
                l.startswith("Next loop planned:") for l in bot.log_lines
            ):
                next_loop_at = i
            if completion_at is None and sum(
                l.startswith("Checkpoint:") for l in bot.log_lines
            ) >= 3:
                completion_at = i                        # first loop done
        self.assertIsNotNone(next_loop_at)
        self.assertLess(next_loop_at, completion_at)     # planned before the loop ends
        arrivals = sum(l.startswith("Checkpoint:") for l in bot.log_lines)
        self.assertGreaterEqual(arrivals, 5)            # loops keep flowing
        self.assertNotIn("taking a break", " ".join(bot.log_lines))

    def test_failed_leg_splices_a_reroute_without_dropping_the_anchor(self):
        bot = PatrolBot([FLOOR, MID], (60, 100), [(20, 100), (80, 84)],
                        up=5, rope=0)
        from picobot.bot.navgraph import Leg

        p = Patrol(bot, rng=random.Random(0))
        p.plan = [(1, [Leg("up_flash", 60, 100, 80, 84, 1.0)], 0)]
        for _ in range(3):
            p.tick()
        self.assertNotIn(1, bot._ckpt_ban)              # only 2 misses so far
        self.assertEqual(sum("missed a landing" in l for l in bot.log_lines), 2)
        p.tick()
        self.assertIn(1, bot._ckpt_ban)                 # 3rd miss bans

    def test_recorded_leg_wins(self):
        from picobot.bot.navgraph import Leg

        bot = PatrolBot([FLOOR, MID], (20, 100), [(20, 100), (80, 84)])
        bot.rot.legs[(0, 1)] = ["climb"]
        p = Patrol(bot, rng=random.Random(0))
        p.plan = [(1, None, 0)]
        p.tick()
        bot._run_leg.assert_called_once_with(["climb"])


if __name__ == "__main__":
    unittest.main()
