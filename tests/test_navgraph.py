import random
import unittest

from picobot.bot.maps import MapEntry
from picobot.bot.navgraph import NavGraph, graph_for

# Tiers (minimap px): floor, a mid platform 16px up, a top platform 18px
# above that, and a side ledge across a 20px gap from the mid platform.
FLOOR = (0, 100, 200, 100)
MID = (40, 84, 120, 84)
TOP = (60, 66, 100, 66)
SIDE = (140, 84, 190, 84)


def kinds(legs):
    return [leg.kind for leg in legs]


class NavGraphTests(unittest.TestCase):
    def test_climbs_tier_by_tier(self):
        g = NavGraph([FLOOR, MID, TOP])
        legs = g.route((10, 100), (80, 66))
        self.assertEqual([k for k in kinds(legs) if k != "walk"], ["up_jump", "up_jump"])
        self.assertEqual((legs[-1].x1, legs[-1].y1), (80, 66))
        self.assertEqual((legs[0].x0, legs[0].y0), (10, 100))

    def test_descends_without_climbing(self):
        g = NavGraph([FLOOR, MID, TOP])
        legs = g.route((80, 66), (10, 100))
        self.assertNotIn("up_jump", kinds(legs))
        self.assertEqual((legs[-1].x1, legs[-1].y1), (10, 100))

    def test_flash_across_gap(self):
        g = NavGraph([FLOOR, MID, SIDE])
        self.assertIn("flash", kinds(g.route((60, 84), (170, 84))))

    def test_gap_too_wide_goes_around(self):
        g = NavGraph([FLOOR, MID, SIDE], gap_px=15)
        ks = kinds(g.route((60, 84), (170, 84)))
        self.assertNotIn("flash", ks)
        self.assertIn("up_jump", ks)                     # back up from the floor

    def test_too_high_is_unreachable(self):
        g = NavGraph([FLOOR, (60, 40, 100, 40)])          # 60px above the floor
        self.assertIsNone(g.route((80, 100), (80, 40)))

    def test_off_platform_points_have_no_route(self):
        g = NavGraph([FLOOR, MID])
        self.assertIsNone(g.route((80, 30), (10, 100)))   # mid-air start
        self.assertIsNone(g.route((10, 100), (300, 100)))  # beyond any platform

    def test_same_platform_is_one_walk(self):
        g = NavGraph([FLOOR, MID])
        legs = g.route((10, 100), (190, 100))
        self.assertEqual(kinds(legs), ["walk"])

    def test_down_jump_lands_on_next_platform_only(self):
        g = NavGraph([FLOOR, MID, TOP])
        for leg in g.transfer_legs():
            if leg.kind == "down_jump" and leg.y0 == 66:
                self.assertEqual(leg.y1, 84)              # not through to the floor

    def test_jitter_varies_between_equal_routes(self):
        # Two mirror-image mid platforms both reach the top one.
        g = NavGraph([FLOOR, (20, 84, 60, 84), (140, 84, 180, 84),
                      (40, 68, 160, 68)])
        via = set()
        for seed in range(40):
            legs = g.route((100, 100), (100, 68), jitter=0.3,
                           rng=random.Random(seed))
            first_up = next(l for l in legs if l.kind == "up_jump")
            via.add(first_up.x0 < 100)
        self.assertEqual(via, {True, False})

    def test_graph_for_scales_normalized_platforms(self):
        entry = MapEntry(name="m", platforms=[[0.0, 0.5, 1.0, 0.5]])
        g = graph_for(entry, (7, 68, 200, 100))
        self.assertEqual(len(g.platforms), 1)
        self.assertEqual((g.platforms[0].x1, g.platforms[0].y0), (200, 50))
        self.assertIsNone(graph_for(MapEntry(name="m"), (0, 0, 10, 10)))


if __name__ == "__main__":
    unittest.main()
