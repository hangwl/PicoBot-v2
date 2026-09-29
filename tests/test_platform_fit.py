import unittest

from picobot.bot.platform_fit import (
    PlatformFit,
    merge_level,
    straighten,
    tidy_segments,
)


class StraightenTests(unittest.TestCase):
    def test_near_flat_drag_is_levelled_at_its_mean(self):
        self.assertEqual(straighten((10, 50, 90, 52)), (10, 51.0, 90, 51.0))

    def test_shallow_long_drag_is_levelled(self):
        # 6px over 100px is ~3.4 degrees: a wobbly hand, not a slope.
        self.assertEqual(straighten((0, 40, 100, 46)), (0, 43.0, 100, 43.0))

    def test_real_slope_is_kept(self):
        self.assertEqual(straighten((0, 40, 40, 60)), (0, 40, 40, 60))

    def test_right_to_left_drag_is_normalized(self):
        self.assertEqual(straighten((90, 50, 10, 50)), (10, 50.0, 90, 50.0))


class MergeTests(unittest.TestCase):
    def test_overlapping_same_row_segments_merge(self):
        got = merge_level([(10, 50, 60, 50), (50, 51, 120, 51)])
        self.assertEqual(len(got), 1)
        x0, y0, x1, y1 = got[0]
        self.assertEqual((x0, x1), (10, 120))
        self.assertTrue(50 < y0 < 51 and y0 == y1)

    def test_different_rows_stay_apart(self):
        got = merge_level([(10, 50, 60, 50), (20, 56, 70, 56)])
        self.assertEqual(len(got), 2)

    def test_a_bridge_segment_merges_a_chain(self):
        got = merge_level([(0, 50, 30, 50), (60, 50, 90, 50), (28, 50, 62, 50)])
        self.assertEqual(got, [(0, 50.0, 90, 50.0)])

    def test_slopes_are_not_merged(self):
        got = tidy_segments([(0, 40, 40, 60), (10, 42, 50, 62)])
        self.assertEqual(len(got), 2)


class PlatformFitTests(unittest.TestCase):
    REGION = (0, 0, 200, 100)
    SEGS = [[0.1, 0.5, 0.6, 0.5], [0.1, 0.8, 0.9, 0.8]]   # rows 50 and 80

    def _fit(self):
        clock = [0.0]
        fit = PlatformFit(clock=lambda: clock[0])
        return fit, clock

    def _stand(self, fit, clock, pos, reads=4):
        for _ in range(reads):
            fit.observe("m", self.SEGS, self.REGION, pos)
            clock[0] += 0.15

    def test_settled_feet_give_one_sample_per_standstill(self):
        fit, clock = self._fit()
        self._stand(fit, clock, (60, 53), reads=10)        # 3px below row 50
        top = fit.summary("m", self.SEGS, self.REGION)[0]
        self.assertEqual(top["n"], 1)
        self.assertEqual(top["offset"], 3.0)

    def test_moving_dot_is_not_sampled(self):
        fit, clock = self._fit()
        for x in range(40, 80, 3):
            fit.observe("m", self.SEGS, self.REGION, (x, 50))
            clock[0] += 0.15
        self.assertEqual(fit.summary("m", self.SEGS, self.REGION)[0]["n"], 0)

    def test_offsets_report_too_high_and_too_low(self):
        fit, clock = self._fit()
        for x in (30, 60, 90):
            self._stand(fit, clock, (x, 53))               # row 50 drawn high
            self._stand(fit, clock, (x + 40, 76))          # row 80 drawn low
        top, low = fit.summary("m", self.SEGS, self.REGION)
        self.assertEqual((top["n"], top["offset"]), (3, 3.0))
        self.assertEqual((low["n"], low["offset"]), (3, -4.0))
        self.assertGreater(top["coverage"], 0.5)

    def test_ambiguous_stacked_rows_are_skipped(self):
        segs = [[0.1, 0.5, 0.6, 0.5], [0.1, 0.55, 0.6, 0.55]]  # rows 50, 55
        fit, clock = self._fit()
        for _ in range(4):
            fit.observe("m", segs, self.REGION, (60, 52))
            clock[0] += 0.15
        self.assertTrue(all(p["n"] == 0 for p in fit.summary("m", segs, self.REGION)))

    def test_far_from_any_line_is_skipped(self):
        fit, clock = self._fit()
        self._stand(fit, clock, (60, 30))
        self.assertTrue(all(p["n"] == 0 for p in fit.summary("m", self.SEGS, self.REGION)))


if __name__ == "__main__":
    unittest.main()
