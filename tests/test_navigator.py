import random
import unittest
from types import SimpleNamespace

from picobot.bot.navgraph import NavGraph
from picobot.bot.navigator import Navigator

FLOOR = (0, 100, 200, 100)
MID = (40, 84, 120, 84)
TOP = (60, 66, 100, 66)
SIDE = (140, 84, 190, 84)


class SimBot:
    """Tiny physics stand-in for SmartBot's movement primitives.

    Walks are instant; up/down jumps move between adjacent platforms;
    holding a direction walks 4px per 50ms tick and falls off edges; a
    jump press while holding carries ``hop`` px (flash ``flash`` px).
    """

    def __init__(self, plats, pos, *, up_px=20, hop=8, flash=25,
                 fail_up=0, cooldowns=0):
        self.g = NavGraph(plats)
        self.pos = pos
        self.up_px, self.hop, self.flash = up_px, hop, flash
        self.fail_up = fail_up          # first N up-jumps fizzle
        self.cooldowns = cooldowns      # first N up-jump calls are "on cooldown"
        self.held = None
        self.log_lines = []
        self.config = SimpleNamespace(nav_threshold_px=4, jump_key="space")
        self.viz = {}
        self.minimap = SimpleNamespace(player_pos=lambda img: self.pos)
        self.hid = SimpleNamespace(
            key_down=self._down, key_up=self._up, press=self._press
        )

    # perception / lifecycle
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

    def event(self, *a):
        pass

    def sleep(self, dt):
        if self.held:
            x = self.pos[0] + (4 if self.held == "right" else -4)
            self._place(x, self.pos[1])
        return False

    # movement
    def move_to_point(self, x, y, threshold=4, style="walk"):
        p = self.g.platforms[self.g.locate(*self.pos)]
        self.pos = (min(p.x1, max(p.x0, x)), self.pos[1])
        return True

    def up_jump(self):
        if self.cooldowns:
            self.cooldowns -= 1
            return False
        if self.fail_up:
            self.fail_up -= 1
            return True
        i = self.g.above(self.pos[0], self.pos[1])
        if i is not None:
            y = self.g.platforms[i].y_at(self.pos[0])
            if self.pos[1] - y <= self.up_px:
                self.pos = (self.pos[0], y)
        return True

    def down_jump(self):
        i = self.g.below(self.pos[0], self.pos[1])
        if i is not None:
            self.pos = (self.pos[0], self.g.platforms[i].y_at(self.pos[0]))

    def _flash_hop(self):
        self._carry(self.flash)

    def _press(self, key, hold=None):
        if key == "space" and self.held:
            self._carry(self.hop)
        return True

    def _carry(self, dist):
        sign = 1 if self.held == "right" else -1
        self._place(self.pos[0] + sign * dist, self.pos[1] - 4)

    def _down(self, k):
        self.held = k

    def _up(self, k):
        self.held = None

    def _place(self, x, y):
        """Land on the highest platform at/below y (small rise allowed)."""
        best = None
        for p in self.g.platforms:
            if p.spans(x) and p.y_at(x) >= y - 1:
                if best is None or p.y_at(x) < best:
                    best = p.y_at(x)
        self.pos = (x, best if best is not None else 150)


class NavigatorTests(unittest.TestCase):
    def _go(self, bot, goal, **kw):
        return Navigator(bot, bot.g, rng=random.Random(0), **kw).go(goal)

    def test_climbs_to_top(self):
        bot = SimBot([FLOOR, MID, TOP], (10, 100))
        self.assertTrue(self._go(bot, (80, 66)))
        self.assertEqual(bot.pos, (80, 66))

    def test_descends(self):
        bot = SimBot([FLOOR, MID, TOP], (80, 66))
        self.assertTrue(self._go(bot, (10, 100)))
        self.assertEqual(bot.pos[1], 100)

    def test_flash_across_gap(self):
        bot = SimBot([FLOOR, MID, SIDE], (60, 84))
        self.assertTrue(self._go(bot, (170, 84)))
        self.assertEqual(bot.pos[1], 84)
        self.assertEqual(bot.pos[0], 170)

    def test_fizzled_up_jump_replans(self):
        bot = SimBot([FLOOR, MID, TOP], (10, 100), fail_up=1)
        self.assertTrue(self._go(bot, (80, 66)))
        self.assertTrue(any("replanned" in l for l in bot.log_lines))

    def test_rides_out_up_jump_cooldown(self):
        bot = SimBot([FLOOR, MID], (60, 100), cooldowns=2)
        self.assertTrue(self._go(bot, (80, 84)))

    def test_gives_up_when_physics_disagrees_with_graph(self):
        # The graph believes up-jumps reach 20px; the character can't.
        bot = SimBot([FLOOR, MID], (60, 100), up_px=5)
        self.assertFalse(self._go(bot, (80, 84), max_replans=2))
        self.assertTrue(any("giving up" in l for l in bot.log_lines))

    def test_unroutable_goal_fails_fast(self):
        bot = SimBot([FLOOR], (10, 100))
        self.assertFalse(self._go(bot, (80, 30)))
        self.assertTrue(any("no route" in l for l in bot.log_lines))

    def test_route_published_for_dashboard(self):
        bot = SimBot([FLOOR, MID], (10, 100))
        self._go(bot, (80, 84))
        self.assertTrue(any(k == "up_jump" for k, *_ in bot.viz["route"]))


if __name__ == "__main__":
    unittest.main()
