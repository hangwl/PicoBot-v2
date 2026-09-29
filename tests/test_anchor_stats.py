import unittest

from picobot.bot.anchor_stats import AnchorStats


class AnchorStatsTests(unittest.TestCase):
    def test_counts_visits_misses_and_skips_by_reason(self):
        clock = [100.0]
        seen = []
        st = AnchorStats(on_change=seen.append, clock=lambda: clock[0])
        st.visit("m", "a0")
        clock[0] = 110.0
        st.visit("m", "a0")
        st.miss("m", "a1")
        st.skip("m", "a1", "no route")
        st.skip("m", "a1", "no route")
        rows = {r["name"]: r for r in st.rows("m", ["a0", "a1", "a2"])}
        self.assertEqual((rows["a0"]["visits"], rows["a0"]["last"]), (2, 110.0))
        self.assertEqual(rows["a1"]["misses"], 1)
        self.assertEqual(rows["a1"]["skips"], {"no route": 2})
        self.assertEqual(rows["a2"]["visits"], 0)          # listed, never seen
        self.assertEqual(seen, ["m"] * 5)

    def test_reset_and_no_map_is_ignored(self):
        st = AnchorStats()
        st.visit(None, "a0")
        st.visit("m", "a0")
        st.reset("m")
        self.assertEqual(st.rows("m", ["a0"])[0]["visits"], 0)
        self.assertEqual(st.rows(None, ["a0"]), [])


if __name__ == "__main__":
    unittest.main()
