import unittest
from types import SimpleNamespace

from picobot.bot.measure import MoveMeasurer
from picobot.bot.navgraph import NavGraph
from picobot.bot.reach import Reach, ReachModel

FLOOR = (0, 100, 200, 100)
LEDGE = (40, 78, 160, 78)          # 22px above the floor
TOP = (60, 56, 140, 56)            # 22px above the ledge


class MeasureBot:
    """Duck-typed SmartBot: primitives move the character by the move's
    carry, landing on the highest platform at/below the apex."""

    def __init__(self, plats, pos):
        self.g = NavGraph(plats, ReachModel({
            "jump": Reach(10, 4), "flash": Reach(30, 4),
            "double_flash": Reach(48, 4), "up_flash": Reach(6, 12),
            "up_side_flash": Reach(30, 20), "rope_lift": Reach(3, 15),
            "teleport": Reach(25, 12),
        }))
        self.pos = pos
        self.carry = {"flash": 37, "double_flash": 45, "jump": 10,
                      "up_flash": 22, "rope_lift": 22, "teleport": 44}
        self.tp_wait = [0.0]
        self.slept = []
        self.moves = []
        self.focus = True
        self.held = None
        self.config = SimpleNamespace(nav_threshold_px=4, jump_key="space",
                                      class_travel="flash",
                                      flash_jump_enabled=True,
                                      teleport_key=None)
        self.reach = self.g.reach
        self.minimap = SimpleNamespace(player_pos=lambda img: self.pos)
        self.hid = SimpleNamespace(
            key_down=lambda k: setattr(self, "held", k),
            key_up=lambda k: setattr(self, "held", None),
            press=self._press,
        )

    # SmartBot surface the measurer touches
    def _sync_map(self):
        pass

    def _nav_graph(self):
        return self.g if self.g.platforms else None

    def _platform_segments_px(self):
        return [(p.x0, p.y0, p.x1, p.y1) for p in self.g.platforms]

    def is_window_focused(self):
        return self.focus

    def sleep(self, dt):
        self.slept.append(dt)
        return False

    def teleport_remaining(self):
        if not self.config.teleport_key:
            return float("inf")
        return self.tp_wait.pop(0) if self.tp_wait else 0.0

    def teleport(self, direction=None):
        self.moves.append(f"teleport:{direction}")
        if direction == "up":
            self._air(0, 22)
        else:
            self._air(self.carry["teleport"] * (1 if direction == "right" else -1), 0)
        return True

    def minimap_frame(self):
        return object()

    def _flash_hop(self):
        self.moves.append("flash")
        self._air(self.carry["flash"] * self._sign(), 2)

    def _double_flash(self):
        self._air(self.carry["double_flash"] * self._sign(), 2)

    def _up_flash(self, direction):
        self.moves.append("up_flash")
        self._air(0, self.carry["up_flash"])

    def rope_lift(self):
        self.moves.append("rope_lift")
        self._air(0, self.carry["rope_lift"])
        return True

    def _press(self, k, h=None):
        if k == self.config.jump_key and self.held:
            self._air(8 * self._sign(), 4)
        return True

    def _sign(self):
        return 1 if self.held == "right" else -1

    def _air(self, dx, rise):
        x, y = self.pos[0] + dx, self.pos[1] - rise
        best = None
        for p in self.g.platforms:
            if p.spans(x) and p.y_at(x) >= y - 1:
                if best is None or p.y_at(x) < best:
                    best = p.y_at(x)
        self.pos = (x, best if best is not None else 150)

    _last_kind = "move"


class MoveMeasurerTests(unittest.TestCase):
    def _run(self, bot):
        got = []
        m = MoveMeasurer(bot, on_event=lambda k, msg: got.append((k, msg)))
        m._loop()
        return m, got

    def test_measured_moves_grow_reach(self):
        bot = MeasureBot([FLOOR, LEDGE, TOP], (30, 100))
        self._run(bot)
        self.assertEqual(bot.reach.get("flash").dx, 37)     # grew from 30
        self.assertEqual(bot.reach.get("up_flash").rise, 22)  # grew from 12
        self.assertEqual(bot.reach.get("rope_lift").rise, 22)  # grew from 15
        self.assertIn("up_flash", bot.moves)
        self.assertIn("rope_lift", bot.moves)

    def test_vertical_moves_skipped_without_platform_above(self):
        bot = MeasureBot([FLOOR], (30, 100))
        self._run(bot)
        self.assertNotIn("up_flash", bot.moves)
        self.assertNotIn("rope_lift", bot.moves)
        self.assertEqual(bot.reach.get("flash").dx, 37)     # horizontal still ran

    def test_horizontal_skipped_without_room(self):
        bot = MeasureBot([(0, 100, 20, 100)], (10, 100))
        self._run(bot)
        self.assertNotIn("flash", bot.moves)

    def test_unfocused_stops_measurement(self):
        bot = MeasureBot([FLOOR], (30, 100))
        bot.focus = False
        _, got = self._run(bot)
        self.assertTrue(any("stopped" in m for _, m in got))

    def test_teleport_class_measures_teleport_not_flashes(self):
        bot = MeasureBot([FLOOR, LEDGE, TOP], (30, 100))
        bot.config.class_travel = "teleport"
        bot.config.teleport_key = "shift"
        self._run(bot)
        self.assertNotIn("flash", bot.moves)
        self.assertNotIn("up_flash", bot.moves)
        self.assertIn("teleport:right", bot.moves)
        self.assertIn("teleport:up", bot.moves)
        tp = bot.reach.get("teleport")
        self.assertEqual((tp.dx, tp.rise), (44, 22))   # both grew from 25/12

    def test_teleport_waits_out_the_cooldown(self):
        bot = MeasureBot([FLOOR], (30, 100))
        bot.config.class_travel = "teleport"
        bot.config.teleport_key = "shift"
        bot.tp_wait = [0.8]
        self._run(bot)
        self.assertTrue(any(abs(d - 0.85) < 1e-9 for d in bot.slept))
        self.assertIn("teleport:right", bot.moves)

    def test_walk_class_measures_only_jump_and_rope_lift(self):
        bot = MeasureBot([FLOOR, LEDGE], (30, 100))
        bot.config.class_travel = "walk"
        self._run(bot)
        self.assertEqual(
            {m.split(":")[0] for m in bot.moves}, {"rope_lift"})
        self.assertEqual(bot.reach.get("jump").dx, 8)     # measured: lowered
        self.assertEqual(bot.reach.get("flash").dx, 30)   # untouched

    def test_flash_disabled_skips_flash_moves(self):
        bot = MeasureBot([FLOOR], (30, 100))
        bot.config.flash_jump_enabled = False
        self._run(bot)
        self.assertNotIn("flash", bot.moves)

    def test_results_and_status_are_published(self):
        bot = MeasureBot([FLOOR], (30, 100))
        seen = []
        m = MoveMeasurer(bot, on_status=seen.append)
        m._loop()
        final = seen[-1]
        self.assertFalse(final["running"])
        self.assertEqual(final["results"]["flash"], {"dx": 37})
        self.assertIn("no platform above",
                      final["results"]["rope_lift"]["skipped"])
        self.assertIn("flash", bot.reach.measured)
        self.assertNotIn("rope_lift", bot.reach.measured)

    def test_stopped_run_keeps_finished_moves(self):
        bot = MeasureBot([FLOOR], (30, 100))
        m = MoveMeasurer(bot)
        real = m._measure

        def measure(move):
            if move == "double_flash":
                m._stop.set()            # stop arrives during the 2nd move
            return real(move)

        m._measure = measure
        m._loop()
        self.assertIn("flash", bot.reach.measured)
        self.assertNotIn("double_flash", bot.reach.measured)
        self.assertEqual(bot.reach.get("flash").dx, 37)

    def test_fall_off_a_ledge_is_reported(self):
        bot = MeasureBot([(0, 100, 60, 100), (0, 130, 200, 130)], (20, 100))
        bot.carry["flash"] = 50                        # overshoots the ledge
        _, got = self._run(bot)
        self.assertTrue(any("fell off a ledge" in msg for _, msg in got))

    def test_no_platforms_warns(self):
        bot = MeasureBot([], (30, 100))
        _, got = self._run(bot)
        self.assertTrue(any("draw the platforms" in m for _, m in got))


def _peak_for(delay):
    """Fake physics: the later the re-press (to 0.25s), the higher."""
    if delay is None:
        return 30.0
    return 12.0 + 40.0 * min(delay, 0.25) - 20.0 * max(0.0, delay - 0.3)


class FlightBot(MeasureBot):
    """Records the up-flash delay; the fake recorder turns it into an arc."""

    def __init__(self, plats, pos):
        super().__init__(plats, pos)
        self.delays = []
        self.jumps = 0

    def _up_flash(self, direction, delay=None, mark=None):
        self.moves.append("up_flash")
        self.delays.append(delay)
        if mark:
            mark("jump")
            mark("rejump")
        self._air(0, self.carry["up_flash"])

    def _press(self, k, h=None):
        if k == self.config.jump_key and not self.held:
            self.jumps += 1
            self.delays.append("jump")
        return super()._press(k, h)


class FakeRecorder:
    def __init__(self, bot):
        self.bot = bot
        self.marks = {}

    def start(self):
        self.n = len(self.bot.delays)

    def mark(self, name):
        self.marks[name] = 0.1 if name == "jump" else 0.1 + 0.2

    def stop(self):
        from tests.test_flight import arc

        d = self.bot.delays[-1] if len(self.bot.delays) > self.n else None
        if d is None and len(self.bot.delays) == self.n:
            return arc(rise=0.0)
        rise = _peak_for(None if d == "jump" else d)
        return arc(rise=rise, marks=dict(self.marks) or {"jump": 0.1})


class UpFlashPeakTests(unittest.TestCase):
    def _measurer(self, bot, **kw):
        got = []
        m = MoveMeasurer(bot, on_event=lambda k, msg: got.append((k, msg)),
                         recorder_factory=lambda: FakeRecorder(bot), **kw)
        return m, got

    def test_up_flash_rise_is_the_recorded_peak_not_the_ledge(self):
        bot = FlightBot([FLOOR, LEDGE, TOP], (30, 100))
        m, _ = self._measurer(bot)
        m._loop()
        # The ledge above is 22px up; the patrol-timed arc peaks at 30px.
        self.assertEqual(bot.reach.get("up_flash").rise, 30)
        self.assertIn("up_flash", bot.reach.measured)

    def test_up_flash_needs_no_platform_above(self):
        bot = FlightBot([FLOOR], (30, 100))
        m, _ = self._measurer(bot)
        m._loop()
        self.assertIn("up_flash", bot.moves)
        self.assertEqual(m.results["up_flash"], {"rise": 30})

    def test_sweep_records_every_delay_and_keeps_the_envelope(self):
        bot = FlightBot([FLOOR], (30, 100))
        before = bot.reach.get("up_flash").rise
        m, got = self._measurer(bot)
        m.mode = "up_flash_profile"
        m._loop()
        prof = bot.reach.profiles["up_flash"]["rows"]
        self.assertEqual([r["delay"] for r in prof],
                         [None, *MoveMeasurer.PROFILE_DELAYS])
        self.assertTrue(all(r["n"] == MoveMeasurer.PROFILE_REPS for r in prof))
        by = {r["delay"]: r for r in prof}
        self.assertEqual(by[0.08]["rise"], 15)
        self.assertEqual(by[0.25]["rise"], 22)
        self.assertEqual(by[None]["rise"], 30)          # plain-jump baseline
        self.assertAlmostEqual(by[0.2]["gap"], 0.2)
        self.assertEqual(bot.reach.get("up_flash").rise, before)
        self.assertNotIn("up_flash", bot.reach.measured)
        self.assertTrue(any("sweep saved" in msg for _, msg in got))

    def test_sweep_refuses_a_low_ceiling(self):
        bot = FlightBot([FLOOR, LEDGE], (60, 100))
        m, got = self._measurer(bot)
        m.mode = "up_flash_profile"
        m._loop()
        self.assertEqual(bot.moves, [])
        self.assertTrue(any("22px overhead" in msg for _, msg in got))

    def test_sweep_stops_when_it_lands_higher(self):
        high = (0, 50, 200, 50)                     # 50px up: allowed
        bot = FlightBot([FLOOR, high], (30, 100))
        bot.carry["up_flash"] = 60                  # ...but reached
        m, got = self._measurer(bot)
        m.mode = "up_flash_profile"
        m._loop()
        self.assertTrue(any("landed 50px higher" in msg for _, msg in got))
        self.assertLessEqual(len(bot.delays), 2)

    def test_sweep_is_for_flash_classes(self):
        bot = FlightBot([FLOOR], (30, 100))
        bot.config.class_travel = "teleport"
        m, got = self._measurer(bot)
        m.mode = "up_flash_profile"
        m._loop()
        self.assertEqual(bot.moves, [])

    def test_status_carries_profiles(self):
        bot = FlightBot([FLOOR], (30, 100))
        seen = []
        m = MoveMeasurer(bot, on_status=seen.append,
                         recorder_factory=lambda: FakeRecorder(bot))
        m.mode = "up_flash_profile"
        m._loop()
        self.assertEqual(seen[-1]["mode"], "up_flash_profile")
        self.assertIn("up_flash", seen[-1]["profiles"])
        self.assertTrue(seen[-1]["profile"])


if __name__ == "__main__":
    unittest.main()
