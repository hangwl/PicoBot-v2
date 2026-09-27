import random
import time
import unittest
from types import SimpleNamespace

from picobot.bot.navgraph import NavGraph
from picobot.bot.navigator import Navigator
from picobot.bot.reach import Reach, ReachModel

FLOOR = (0, 100, 200, 100)
MID = (40, 84, 120, 84)
TOP = (60, 66, 100, 66)
SIDE = (135, 84, 190, 84)


def reach(**over):
    base = {
        "jump": Reach(8, 4), "flash": Reach(25, 4), "double_flash": Reach(40, 4),
        "up_flash": Reach(6, 20), "up_side_flash": Reach(25, 16),
        "rope_lift": Reach(3, 30),
    }
    base.update(over)
    return ReachModel(base, explore=1.0)


class SimBot:
    """Physics stand-in: every airborne move is (sideways distance, max
    rise); the character lands on the highest platform it can reach at
    the landing column, else falls. Walks are instant; holding a
    direction walks 4px per tick."""

    def __init__(self, plats, pos, *, flash=25, double=40, up=20, side=25,
                 rope=30, rope_cd=0.0, fizzle=0):
        self.g = NavGraph(plats, reach(rope_lift=Reach(3, rope if rope else 0)))
        self.pos = pos
        self.flash, self.double, self.up, self.side, self.rope = flash, double, up, side, rope
        self.rope_cd = rope_cd
        self.fizzle = fizzle             # first N airborne moves go nowhere
        self.held = None
        self.moves = []
        self.log_lines = []
        self.slept = 0.0
        self.config = SimpleNamespace(nav_threshold_px=4, jump_key="space")
        self.viz = {}
        self.minimap = SimpleNamespace(player_pos=lambda img: self.pos)
        self.downs, self.ups = [], []
        self.hid = SimpleNamespace(
            key_down=lambda k: (self.downs.append(k), self._down(k)) and None,
            key_up=lambda k: (self.ups.append(k), self._up(k)) and None,
            press=self._press,
        )

    # lifecycle / perception
    def minimap_frame(self):
        return object()

    def should_continue(self):
        return True

    def is_window_focused(self):
        return True

    def unsafe_reason(self):
        return None

    def log(self, m):
        self.log_lines.append(m)

    def sleep(self, dt):
        self.slept += dt
        self.rope_cd = max(0.0, self.rope_cd - dt)
        if self.held:
            self._air(4 if self.held == "right" else -4, 0)
        return False

    # moves
    def move_to_point(self, x, y, threshold=4, style="walk", flat=False):
        p = self.g.platforms[self.g.locate(*self.pos)]
        self.pos = (min(p.x1, max(p.x0, x)), self.pos[1])
        return True

    def rope_lift_remaining(self):
        return self.rope_cd if self.rope else float("inf")

    def rope_lift(self):
        if self.rope_cd > 0:
            return False
        self.moves.append("rope_lift")
        self._air(0, self.rope)
        self.rope_cd = 3.0
        return True

    def _up_flash(self, direction=None):
        self.moves.append("up_flash")
        self._air((6 if direction == "right" else -6) if direction else 0, self.up)

    def _up_side_flash(self, direction):
        self.moves.append("up_side_flash")
        self._air(self.side if direction == "right" else -self.side, self.up * 0.8)

    def _flash_hop(self):
        self.moves.append("flash")
        self._air(self._sign() * self.flash, 4)

    def _double_flash(self):
        self.moves.append("double_flash")
        self._air(self._sign() * self.double, 4)

    def down_jump(self):
        i = self.g.below(self.pos[0], self.pos[1])
        if i is not None:
            self.pos = (self.pos[0], self.g.platforms[i].y_at(self.pos[0]))

    def _press(self, key, hold=None):
        if key == "space" and self.held:
            self._air(self._sign() * 8, 4)
        return True

    def _sign(self):
        return 1 if self.held == "right" else -1

    def _down(self, k):
        self.held = k

    def _up(self, k):
        self.held = None

    def _air(self, dx, rise):
        if self.fizzle and rise > 4:
            self.fizzle -= 1
            return
        x, y = self.pos[0] + dx, self.pos[1]
        best = None
        for p in self.g.platforms:
            if p.spans(x) and p.y_at(x) >= y - rise - 0.01:
                if best is None or p.y_at(x) < best:
                    best = p.y_at(x)
        self.pos = (x, best if best is not None else 150)


class NavigatorTests(unittest.TestCase):
    def _nav(self, bot):
        return Navigator(bot, bot.g, rng=random.Random(0))

    def test_climbs_with_up_flashes(self):
        bot = SimBot([FLOOR, MID, TOP], (10, 100), rope=0)
        self.assertTrue(self._nav(bot).go((80, 66)))
        self.assertEqual(bot.pos, (80, 66))
        self.assertEqual(bot.moves, ["up_flash", "up_flash"])

    def test_rope_lift_preferred_when_ready(self):
        bot = SimBot([FLOOR, MID], (60, 100))
        self.assertTrue(self._nav(bot).go((80, 84)))
        self.assertEqual(bot.moves, ["rope_lift"])

    def test_descends(self):
        bot = SimBot([FLOOR, MID, TOP], (80, 66))
        self.assertTrue(self._nav(bot).go((10, 100)))
        self.assertEqual(bot.pos[1], 100)

    def test_flash_and_double_flash_gaps(self):
        bot = SimBot([MID, SIDE], (60, 84))
        self.assertTrue(self._nav(bot).go((170, 84)))
        self.assertEqual(bot.moves, ["flash"])
        bot = SimBot([MID, (150, 84, 190, 84)], (60, 84))
        self.assertTrue(self._nav(bot).go((170, 84)))
        self.assertEqual(bot.moves, ["double_flash"])

    def test_up_side_flash(self):
        bot = SimBot([(0, 100, 100, 100), (115, 86, 160, 86)], (50, 100))
        self.assertTrue(self._nav(bot).go((140, 86)))
        self.assertEqual(bot.moves, ["up_side_flash"])

    def test_cooling_rope_lift_is_not_waited_for(self):
        bot = SimBot([FLOOR, MID], (80, 100), rope_cd=0.5)
        self.assertTrue(self._nav(bot).go((80, 84)))
        self.assertEqual(bot.moves, ["up_flash"])

    def test_long_rope_cooldown_excludes_rope_lift(self):
        bot = SimBot([FLOOR, (60, 70, 100, 70)], (80, 100), rope_cd=10.0)
        self.assertEqual(self._nav(bot).step((80, 70)), "noroute")
        self.assertEqual(bot.moves, [])

    def test_step_is_one_move_and_replans_after_a_miss(self):
        bot = SimBot([FLOOR, MID, TOP], (80, 100), fizzle=1, rope=0)
        nav = self._nav(bot)
        self.assertEqual(nav.step((80, 66)), "failed")     # fizzled up-flash
        self.assertEqual(bot.pos[1], 100)
        self.assertEqual(nav.step((80, 66)), "moved")      # retried from floor
        self.assertEqual(nav.step((80, 66)), "arrived")

    def test_tiny_leading_walk_does_not_stall(self):
        # Standing 2px from the up-flash takeoff (MID overlap midpoint 80):
        # the route's first leg is a walk already within tolerance. Each
        # step must still make progress, not re-plan the same no-op.
        bot = SimBot([FLOOR, MID], (78, 100), rope=0)
        nav = self._nav(bot)
        statuses = [nav.step((80, 84)) for _ in range(3)]
        self.assertIn("arrived", statuses)
        self.assertEqual(bot.moves, ["up_flash"])

    def test_landing_outcomes_teach_reach(self):
        # The real flash carries 30px; the model starts at 25 — the first
        # observed flash grows the proven envelope.
        bot = SimBot([MID, SIDE], (60, 84), flash=30)
        self.assertTrue(self._nav(bot).go((170, 84)))
        self.assertGreater(bot.g.reach.get("flash").dx, 25)

    def test_short_real_reach_shrinks_model_and_gives_up(self):
        bot = SimBot([FLOOR, MID], (60, 100), up=5, rope=0)
        self.assertFalse(self._nav(bot).go((80, 84), max_failures=2))
        self.assertLess(bot.g.reach.get("up_flash").rise, 20)
        self.assertTrue(any("giving up" in l for l in bot.log_lines))

    def test_settle_tolerates_dot_jitter(self):
        bot = SimBot([FLOOR], (50, 100))
        flip = [0]

        def jittery(_img):
            flip[0] ^= 1
            return (50 + flip[0], 100)

        bot.minimap.player_pos = jittery
        t0 = time.monotonic()
        self._nav(bot)._settle()
        self.assertLess(time.monotonic() - t0, 0.5)   # not the 0.7s timeout

    def test_landing_tolerance_off_drawn_row(self):
        bot = SimBot([FLOOR, (60, 70, 100, 70)], (10, 100))
        nav = self._nav(bot)
        top = bot.g.locate(80, 70)
        self.assertIsNone(bot.g.locate(80, 79))        # 9px off: snap fails
        self.assertTrue(nav._on_platform((80, 79), top))
        self.assertFalse(nav._on_platform((80, 100), top))

    def test_gap_jump_releases_when_the_flash_fizzles(self):
        bot = SimBot([MID, SIDE], (60, 84), flash=0)
        t0 = time.monotonic()
        self.assertFalse(self._nav(bot).go((170, 84), max_failures=1, max_steps=3))
        self.assertLess(time.monotonic() - t0, 8)      # failed fast
        self.assertIn("right", bot.ups)                # direction released

    def test_rope_chain_replans_when_lift_cools(self):
        bot = SimBot([FLOOR, MID, TOP], (10, 100))
        self.assertTrue(self._nav(bot).go((80, 66)))
        # Rise #2 is planned while the lift cools → up-flash instead.
        self.assertEqual(bot.moves, ["rope_lift", "up_flash"])

    def test_route_published_for_dashboard(self):
        bot = SimBot([FLOOR, MID], (10, 100), rope=0)
        self._nav(bot).step((80, 84))
        self.assertTrue(any(k == "up_flash" for k, *_ in bot.viz["route"]))


if __name__ == "__main__":
    unittest.main()
