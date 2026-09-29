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
        bot.config.patrol_policy = "greedy"      # deterministic order
        p = Patrol(bot, rng=random.Random(0))
        for _ in range(6):
            p.tick()
        self.assertIn("up_flash", bot.moves)
        self.assertGreaterEqual(sum(l.startswith("Checkpoint:") for l in bot.log_lines), 2)

    def test_weighted_policy_gives_far_anchors_a_real_draw(self):
        # P(next) ∝ 1/cost: a far anchor (cost 10 vs 1) still gets picked
        # on a high roulette draw instead of being neglected.
        bot = PatrolBot([FLOOR], (10, 100), [(20, 100), (180, 100)])
        p = Patrol(bot)
        p.rng = Mock()
        p.rng.random.return_value = 0.95
        self.assertEqual(p._pick_next({0: 1.0, 1: 10.0}, [0, 1]), 1)
        p.rng.random.return_value = 0.1
        self.assertEqual(p._pick_next({0: 1.0, 1: 10.0}, [0, 1]), 0)

    def test_greedy_policy_picks_cheapest(self):
        bot = PatrolBot([FLOOR], (10, 100), [(20, 100), (180, 100)])
        bot.config.patrol_policy = "greedy"
        p = Patrol(bot, rng=random.Random(0))
        self.assertEqual(p._pick_next({0: 1.0, 1: 10.0}, [0, 1]), 0)

    def test_anchor_stats_record_visits_misses_and_skips(self):
        from picobot.bot.anchor_stats import AnchorStats
        from picobot.bot.maps import MapEntry

        bot = PatrolBot([FLOOR, MID], (60, 100), [(20, 100), (80, 84)], up=5, rope=0)
        bot._map = MapEntry(name="m")
        bot._current_map_entry = lambda: bot._map
        bot.anchor_stats = AnchorStats()
        p = Patrol(bot, rng=random.Random(2))
        for _ in range(12):
            p.tick()
        rows = {r["name"]: r for r in bot.anchor_stats.rows("m", ["a0", "a1"])}
        self.assertGreater(rows["a0"]["visits"], 0)
        self.assertGreater(rows["a1"]["misses"], 0)                 # can't reach MID
        self.assertIn("unreachable after retries", rows["a1"]["skips"])

    def test_status_tracks_target_next_and_arrivals(self):
        bot = PatrolBot([FLOOR], (10, 100), [(20, 100), (100, 100), (180, 100)])
        bot.config.patrol_policy = "greedy"
        p = Patrol(bot, rng=random.Random(0))
        p.tick()
        st = bot.viz["patrol"]
        self.assertEqual(st["target"], "a0")
        self.assertEqual(st["next"][:2], ["a1", "a2"])
        self.assertEqual((st["misses"], st["arrived"], st["halted"]), (0, 0, False))
        for _ in range(8):
            p.tick()
        st = bot.viz["patrol"]
        self.assertGreaterEqual(st["arrived"], 2)
        self.assertIn(st["target"], {"a0", "a1", "a2"})

    def test_status_counts_misses_and_shows_a_halt(self):
        bot = PatrolBot([FLOOR, MID], (60, 100), [(20, 100), (80, 84)], up=5, rope=0)
        p = Patrol(bot, rng=random.Random(0))
        from picobot.bot.navgraph import Leg

        p.plan = [(1, [Leg("up_flash", 60, 100, 80, 84, 1.0)], 0)]
        p.tick()
        st = bot.viz["patrol"]
        self.assertEqual((st["target"], st["misses"]), ("a1", 1))
        self.assertIn(st["move"], {"walk", "up_flash"})      # re-routed leg
        halted = PatrolBot([FLOOR, (60, 40, 100, 40)], (10, 100), [(80, 40)])
        q = Patrol(halted, rng=random.Random(0))
        q.tick()
        self.assertTrue(halted.viz["patrol"]["halted"])
        self.assertIsNone(halted.viz["patrol"]["target"])

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
        self.assertEqual((bot.weaves, bot.moves), (0, []))

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

    def test_persistent_blind_dot_never_learns_or_leaps(self):
        # A missing dot may be UI over the minimap or a map load — no
        # guessing: no rope learned, no jump pressed.
        bot = PatrolBot([FLOOR], (60, 100), [(20, 100), (180, 100)])
        bot.rope_exit = Mock()
        bot.probe_rope = Mock()
        bot._blind_wait = Mock()
        bot.minimap.player_pos = Mock(return_value=None)
        p = Patrol(bot)
        for _ in range(5):
            p.tick()
        bot.rope_exit.assert_not_called()
        bot.probe_rope.assert_not_called()
        self.assertEqual(bot._blind_wait.call_count, 5)

    def _rope_bot(self, tmp, pos=(60, 60)):
        from picobot.bot.maps import MapEntry, MapStore

        bot = PatrolBot([FLOOR, (40, 40, 160, 40)], pos,
                        [(20, 100), (180, 100)])
        bot._map = MapEntry(name="m")
        bot._current_map_entry = lambda: bot._map
        bot.maps = MapStore(tmp)
        bot.maps.save(bot._map)
        bot.rope_exit = Mock()
        bot._blind_wait = Mock()
        return bot

    def _stuck_tick(self, bot, pos):
        p = Patrol(bot)
        p._stuck_since = time.monotonic() - 3.0
        p._stuck_pos = pos
        p.tick()

    def test_confirmed_hang_learns_a_rope(self):
        # Stable + off-graph for >2s, and holding Down slides the
        # character: a rope. Recorded up to the platform above, then leap.
        import tempfile

        from picobot.bot.maps import MapStore

        with tempfile.TemporaryDirectory() as tmp:
            bot = self._rope_bot(tmp)
            bot.probe_rope = Mock(return_value=True)
            self._stuck_tick(bot, (60, 60))
            bot.rope_exit.assert_called_once()
            saved = MapStore(tmp).get("m")
            self.assertEqual(len(saved.ropes), 1)
            self.assertEqual(saved.ropes[0][0], 0.3)      # stuck x
            self.assertEqual(saved.ropes[0][3], round(40 / 150, 4))
        self.assertTrue(any("Learned a rope" in l for l in bot.log_lines))

    def test_undrawn_ground_is_not_learned_as_a_rope(self):
        import tempfile

        from picobot.bot.maps import MapStore

        with tempfile.TemporaryDirectory() as tmp:
            bot = self._rope_bot(tmp)
            bot.probe_rope = Mock(return_value=False)
            self._stuck_tick(bot, (60, 60))
            self.assertFalse(MapStore(tmp).get("m").ropes)
            bot.rope_exit.assert_called_once()           # hop back
        self.assertTrue(any("not a rope" in l for l in bot.log_lines))

    def test_unreadable_probe_does_nothing(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            bot = self._rope_bot(tmp)
            bot.probe_rope = Mock(return_value=None)
            self._stuck_tick(bot, (60, 60))
            bot.rope_exit.assert_not_called()

    def test_hangs_on_one_rope_extend_it(self):
        import tempfile

        from picobot.bot.maps import MapStore

        with tempfile.TemporaryDirectory() as tmp:
            bot = self._rope_bot(tmp)
            bot.probe_rope = Mock(return_value=True)
            self._stuck_tick(bot, (60, 60))
            bot.pos = (61, 75)                         # lower on the same rope
            self._stuck_tick(bot, (61, 75))
            ropes = MapStore(tmp).get("m").ropes
            self.assertEqual(len(ropes), 1)
            self.assertEqual(ropes[0][1], round(75 / 150, 4))   # bottom grew
            self.assertEqual(ropes[0][3], round(40 / 150, 4))   # top kept

    def test_learned_rope_keeps_its_bottom_off_the_platform_below(self):
        import tempfile

        from picobot.bot.maps import MapStore

        with tempfile.TemporaryDirectory() as tmp:
            bot = self._rope_bot(tmp, pos=(61, 70))
            # A rope saved earlier that runs all the way onto FLOOR (y=100).
            bot._map.ropes = [[0.3, round(100 / 150, 4), 0.3, round(40 / 150, 4)]]
            bot.maps.save(bot._map)
            bot.probe_rope = Mock(return_value=True)
            self._stuck_tick(bot, (61, 70))                # a new hang extends it
            ropes = MapStore(tmp).get("m").ropes
            self.assertEqual(len(ropes), 1)
            self.assertEqual(ropes[0][1], round(95 / 150, 4))   # 100 - 5px gap

    def test_exit_leaps_toward_a_landable_platform(self):
        # Rope at x=60, y=60: a platform above to the left (unreachable
        # by a leap) and one below to the right.
        above_left = (20, 30, 50, 30)
        below_right = (70, 80, 150, 80)
        bot = PatrolBot([above_left, below_right], (60, 60),
                        [(20, 100), (180, 100)])
        p = Patrol(bot)
        self.assertEqual(p._exit_direction(bot.g, (60, 60)), "right")

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
