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
        "rope_lift": Reach(3, 30), "teleport": Reach(25, 12),
    }
    base.update(over)
    return ReachModel(base, explore=explore)


def kinds(legs):
    return [l.kind for l in legs if l.kind != "walk"]


class UpFlashOvershootTests(unittest.TestCase):
    """Limina 2-6's left stack: an up flash peaking at 26px from the y64
    tier clears the y51 tier *and* the y41 one (23px up) where they
    overlap, so it comes down on y41 — never plan it onto y51 there."""

    PLATS = [(31, 82, 140, 82), (31, 64, 66, 64), (32, 51, 71, 51), (31, 41, 55, 41)]

    def _graph(self):
        return NavGraph(self.PLATS, reach(up_flash=Reach(6, 26), rope_lift=Reach(3, 0)))

    def _up_edges(self, g, frm_row, to_row):
        return [l for l in g.transfer_legs()
                if l.kind == "up_flash" and round(l.y0) == frm_row and round(l.y1) == to_row]

    def test_targets_the_highest_platform_the_peak_clears(self):
        g = self._graph()
        to51 = self._up_edges(g, 64, 51)
        self.assertTrue(to51)                         # beyond the top tier's end
        self.assertTrue(all(l.x0 > 55 for l in to51))
        self.assertTrue(self._up_edges(g, 64, 41))    # where y41 is overhead

    def test_route_to_the_middle_tier_goes_around_the_top_one(self):
        g = self._graph()
        legs = g.route((45, 64), (60, 51))
        ups = [l for l in legs if l.kind == "up_flash"]
        self.assertEqual(len(ups), 1)
        self.assertGreater(ups[0].x0, 55)


class NavGraphTests(unittest.TestCase):
    def test_climbs_tier_by_tier_with_up_flash(self):
        no_rope = reach(rope_lift=Reach(0, 0))
        legs = NavGraph([FLOOR, MID, TOP], no_rope).route((10, 100), (80, 66))
        self.assertEqual(kinds(legs), ["up_flash", "up_flash"])
        self.assertEqual((legs[-1].x1, legs[-1].y1), (80, 66))

    def test_rope_lift_grabs_highest_platform_in_range(self):
        # FLOOR → TOP directly (38px, highest within the 90px grab range);
        # no rope edge stops at MID, and the 100px platform is out of range.
        g = NavGraph([FLOOR, MID, TOP, (60, 0, 100, 0)],
                     reach(rope_lift=Reach(3, 90)))
        floor_rope = [l for l in g.transfer_legs()
                      if l.kind == "rope_lift" and l.y0 == 100]
        # Highest platform per column: TOP where it overhangs, MID elsewhere,
        # never the platform 100px up (beyond the grab range).
        self.assertEqual({l.y1 for l in floor_rope}, {84.0, 66.0})
        self.assertEqual(kinds(g.route((10, 100), (80, 66))), ["rope_lift"])
        self.assertEqual(
            kinds(g.route((10, 100), (80, 66), exclude=("rope_lift",))),
            ["up_flash", "up_flash"],
        )

    def test_rope_lift_preferred_whenever_available(self):
        g = NavGraph([FLOOR, MID], reach())
        self.assertEqual(kinds(g.route((10, 100), (80, 84))), ["rope_lift"])
        self.assertEqual(
            kinds(g.route((10, 100), (80, 84), exclude=("rope_lift",))),
            ["up_flash"],
        )

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
                      (40, 68, 160, 68)], reach(rope_lift=Reach(0, 0)))
        via = set()
        for seed in range(40):
            legs = g.route((100, 100), (100, 68), jitter=0.3, rng=random.Random(seed))
            first_up = next(l for l in legs if l.kind == "up_flash")
            via.add(first_up.x0 < 100)
        self.assertEqual(via, {True, False})

    def test_teleport_edges_exist_only_for_teleport_kits(self):
        plats = [FLOOR, MID, (140, 84, 190, 84)]
        mage = NavGraph(plats, reach(teleport=Reach(30, 12)),
                        allow_flash=False, allow_teleport=True)
        kinds_seen = {l.kind for l in mage.transfer_legs()}
        self.assertIn("teleport", kinds_seen)
        self.assertNotIn("flash", kinds_seen)
        self.assertNotIn("up_flash", kinds_seen)
        hero = NavGraph(plats, reach(), allow_flash=True, allow_teleport=False)
        self.assertNotIn("teleport", {l.kind for l in hero.transfer_legs()})

    def test_drawn_rope_links_two_platforms(self):
        # A rope from the floor (y=100) to a platform 60px up — beyond
        # any flash, reachable only by climbing.
        tall = (80, 40, 120, 40)
        g = NavGraph([FLOOR, tall], reach(),
                     ropes=[(98, 100, 102, 40)])
        self.assertEqual(
            kinds(g.route((50, 100), (100, 40))), ["climb_up"])
        # Descending may prefer a drop — the climb edge just has to exist.
        self.assertTrue(
            any(l.kind == "climb_down" for l in g.transfer_legs()))

    def test_rope_climbs_cost_a_penalty(self):
        # Ropes are a last resort: the penalty pushes climb edges behind
        # any reasonable non-rope alternative.
        plats = [FLOOR, MID]
        g = NavGraph(plats, reach(), ropes=[(80, 100, 82, 84)],
                     rope_penalty=5.0)
        rope_cost = next(
            l.cost for l in g.transfer_legs() if l.kind == "climb_up")
        self.assertGreaterEqual(rope_cost, 5.0)

    def test_hanging_rope_boards_by_jump_grab(self):
        # The rope hangs from the tall platform; its bottom end is 20px
        # above the floor — reachable by a jump-grab, not by standing.
        g = NavGraph([FLOOR, (80, 40, 120, 40)], reach(),
                     ropes=[(98, 80, 102, 40)])
        ups = [l for l in g.transfer_legs() if l.kind == "climb_up"]
        self.assertTrue(ups)
        self.assertEqual({l.y0 for l in ups}, {100.0})   # from the floor
        self.assertTrue(
            any(l.kind == "climb_down" for l in g.transfer_legs()))

    def test_rope_out_of_jump_grab_reach_has_no_boards(self):
        # Bottom end 30px above the only platform — beyond jump-grab reach.
        g = NavGraph([FLOOR, (80, 40, 120, 40)], reach(),
                     ropes=[(98, 70, 102, 40)])
        self.assertFalse(any(l.kind == "climb_up" for l in g.transfer_legs()))
        self.assertIsNone(g.route((50, 100), (100, 40)))

    def test_rope_grabs_are_moving_hops_from_beside_the_rope(self):
        g = NavGraph([FLOOR, (80, 40, 120, 40)], reach(),
                     ropes=[(100, 80, 100, 40)])
        ups = [l for l in g.transfer_legs() if l.kind == "climb_up"]
        self.assertTrue(ups)
        for l in ups:
            self.assertEqual(abs(l.x1 - l.x0), 6.0)     # never straight up

    def test_rope_between_stacked_platforms_keeps_a_bottom_gap(self):
        # Rope drawn all the way down onto the lower platform (y=100):
        # its usable bottom stops 5px above it, so boarding is a grab.
        low, high = FLOOR, (80, 40, 120, 40)
        g = NavGraph([low, high], reach(), ropes=[(100, 100, 100, 40)])
        downs = [l for l in g.transfer_legs() if l.kind == "climb_down"]
        self.assertTrue(downs)                            # still lands below
        ups = [l for l in g.transfer_legs() if l.kind == "climb_up"]
        self.assertTrue(ups and all(l.y0 == 100.0 for l in ups))

    def test_narrow_platform_falls_back_to_a_straight_grab(self):
        ledge = (98, 100, 104, 100)                       # 6px wide
        g = NavGraph([ledge, (80, 40, 120, 40)], reach(),
                     ropes=[(101, 90, 101, 40)])
        ups = [l for l in g.transfer_legs() if l.kind == "climb_up"]
        self.assertEqual([abs(l.x1 - l.x0) for l in ups], [0.0])

    def test_rope_past_a_platform_end_needs_the_flash_kit(self):
        ledge = (20, 100, 85, 100)                        # ends 15px short
        plats = [ledge, (80, 40, 120, 40)]
        rope = [(100, 85, 100, 40)]
        flash = NavGraph(plats, reach(), ropes=rope, allow_flash=True)
        walk = NavGraph(plats, reach(), ropes=rope, allow_flash=False)
        self.assertTrue(any(l.kind == "climb_up" for l in flash.transfer_legs()))
        self.assertFalse(any(l.kind == "climb_up" for l in walk.transfer_legs()))

    def test_exit_direction_prefers_a_landable_platform(self):
        g = NavGraph([(20, 30, 50, 30), (70, 80, 150, 80)], reach())
        self.assertEqual(g.exit_direction(60, 60), "right")

    def test_rope_needs_platforms_at_both_ends(self):
        g = NavGraph([FLOOR], reach(), ropes=[(98, 100, 102, 40)])
        self.assertEqual(g.transfer_legs(), [])      # nothing to climb to

    def test_graph_for_scales_normalized_platforms(self):
        entry = MapEntry(name="m", platforms=[[0.0, 0.5, 1.0, 0.5]])
        g = graph_for(entry, (7, 68, 200, 100), reach())
        self.assertEqual((g.platforms[0].x1, g.platforms[0].y0), (200, 50))
        self.assertIsNone(graph_for(MapEntry(name="m"), (0, 0, 10, 10), reach()))


if __name__ == "__main__":
    unittest.main()
