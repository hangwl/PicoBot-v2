import random
import unittest

from picobot.bot.maps import MapEntry
from picobot.bot.navgraph import NavGraph, graph_for
from picobot.bot.reach import Reach, ReachModel

FLOOR = (0, 100, 200, 100)
MID = (40, 84, 120, 84)       # 16 above the floor
TOP = (60, 66, 100, 66)       # 18 above MID
SIDE = (135, 84, 190, 84)     # 15px gap right of MID (22px takeoff→landing)


def reach(explore=1.0, **over):
    base = {
        "jump": Reach(8, 4), "flash": Reach(25, 4), "double_flash": Reach(40, 4),
        "up_flash": Reach(6, 20), "up_side_flash": Reach(25, 16),
        "rope_lift": Reach(3, 30),
    }
    base.update(over)
    return ReachModel(base, explore=explore)


def kinds(legs):
    return [l.kind for l in legs if l.kind != "walk"]


class NavGraphTests(unittest.TestCase):
    def test_climbs_tier_by_tier_with_up_flash(self):
        legs = NavGraph([FLOOR, MID, TOP], reach()).route((10, 100), (80, 66))
        self.assertEqual(kinds(legs), ["up_flash", "up_flash"])
        self.assertEqual((legs[-1].x1, legs[-1].y1), (80, 66))

    def test_tall_rise_needs_rope_lift(self):
        high = (60, 70, 100, 70)                      # 30 above the floor
        g = NavGraph([FLOOR, high], reach())
        self.assertEqual(kinds(g.route((80, 100), (80, 70))), ["rope_lift"])
        self.assertIsNone(g.route((80, 100), (80, 70), exclude=("rope_lift",)))

    def test_up_side_flash_goes_up_and_over(self):
        ledge = (115, 86, 160, 86)                    # 15 gap right, 14 up
        g = NavGraph([(0, 100, 100, 100), ledge], reach())
        self.assertEqual(kinds(g.route((50, 100), (140, 86))), ["up_side_flash"])

    def test_double_flash_for_wide_gap(self):
        wide = (150, 84, 190, 84)                     # 30px gap: > flash 25
        g = NavGraph([MID, wide], reach())
        self.assertEqual(kinds(g.route((60, 84), (170, 84))), ["double_flash"])

    def test_single_flash_preferred_when_it_fits(self):
        g = NavGraph([MID, SIDE], reach())
        self.assertEqual(kinds(g.route((60, 84), (170, 84))), ["flash"])

    def test_exploration_edge_costs_more_and_ceiling_removes_it(self):
        far = (140, 84, 190, 84)                      # needs 27 > flash 25
        m = reach(explore=1.15, double_flash=Reach(0, 0))
        g = NavGraph([MID, far], m)
        legs = g.route((60, 84), (170, 84))
        self.assertEqual(kinds(legs), ["flash"])
        jump = next(l for l in legs if l.kind == "flash")
        self.assertGreater(jump.cost, 0.8)            # exploration penalty
        m.observe("flash", planned=(jump.x1 - jump.x0, 0), observed=(10, -16), ok=False)
        self.assertIsNone(NavGraph([MID, far], m).route((60, 84), (170, 84)))

    def test_descends_without_climbing(self):
        legs = NavGraph([FLOOR, MID, TOP], reach()).route((80, 66), (10, 100))
        self.assertFalse({"up_flash", "rope_lift"} & set(kinds(legs)))
        self.assertEqual((legs[-1].x1, legs[-1].y1), (10, 100))

    def test_down_jump_lands_on_next_platform_only(self):
        g = NavGraph([FLOOR, MID, TOP], reach())
        for leg in g.transfer_legs():
            if leg.kind == "down_jump" and leg.y0 == 66:
                self.assertEqual(leg.y1, 84)

    def test_unreachable_and_off_platform(self):
        g = NavGraph([FLOOR, (60, 40, 100, 40)], reach())   # 60 up: nothing reaches
        self.assertIsNone(g.route((80, 100), (80, 40)))
        self.assertEqual(g.route_cost((80, 100), (80, 40)), float("inf"))
        self.assertIsNone(g.route((80, 30), (10, 100)))       # mid-air start

    def test_same_platform_is_one_walk(self):
        legs = NavGraph([FLOOR, MID], reach()).route((10, 100), (190, 100))
        self.assertEqual([l.kind for l in legs], ["walk"])

    def test_jitter_varies_between_equal_routes(self):
        g = NavGraph([FLOOR, (20, 84, 60, 84), (140, 84, 180, 84),
                      (40, 68, 160, 68)], reach())
        via = set()
        for seed in range(40):
            legs = g.route((100, 100), (100, 68), jitter=0.3, rng=random.Random(seed))
            first_up = next(l for l in legs if l.kind == "up_flash")
            via.add(first_up.x0 < 100)
        self.assertEqual(via, {True, False})

    def test_graph_for_scales_normalized_platforms(self):
        entry = MapEntry(name="m", platforms=[[0.0, 0.5, 1.0, 0.5]])
        g = graph_for(entry, (7, 68, 200, 100), reach())
        self.assertEqual((g.platforms[0].x1, g.platforms[0].y0), (200, 50))
        self.assertIsNone(graph_for(MapEntry(name="m"), (0, 0, 10, 10), reach()))


if __name__ == "__main__":
    unittest.main()
