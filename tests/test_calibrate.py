import unittest

from picobot.bot.calibrate import TraceRecorder, _trace_to_steps


def _walk(points, x0, x1, y, n=12):
    """Simulated walk: straight line samples."""
    for i in range(n):
        points.append((x0 + (x1 - x0) * i / (n - 1), y))


def _climb(points, y0, y1, x, n=10):
    for i in range(n):
        points.append((x, y0 + (y1 - y0) * i / (n - 1)))


class TraceToStepsTests(unittest.TestCase):
    def test_straight_walk(self):
        pts = []
        _walk(pts, 20, 150, 60)
        steps = _trace_to_steps(pts, (200, 150))
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].kind, "walk_to")
        self.assertAlmostEqual(steps[0].x, 0.75, places=2)

    def test_walk_then_climb_then_walk(self):
        pts = []
        _walk(pts, 20, 100, 100)
        _climb(pts, 100, 40, 100)
        _walk(pts, 100, 170, 40)
        steps = _trace_to_steps(pts, (200, 150))
        kinds = [s.kind for s in steps]
        self.assertEqual(kinds, ["walk_to", "climb", "walk_to"])
        climb = steps[1]
        self.assertEqual(climb.direction, "up")
        self.assertAlmostEqual(climb.x, 0.5, places=2)
        self.assertAlmostEqual(climb.until_y, 40 / 150, places=2)

    def test_down_climb(self):
        pts = []
        _climb(pts, 30, 100, 60)
        steps = _trace_to_steps(pts, (200, 150))
        self.assertEqual(steps[0].direction, "down")

    def test_noise_does_not_create_steps(self):
        pts = [(50.0, 50.0), (51.0, 50.5), (50.2, 50.8), (50.9, 50.1)]
        self.assertEqual(_trace_to_steps(pts, (200, 150)), [])


class TraceRecorderTests(unittest.TestCase):
    def _recorder(self):
        return TraceRecorder((200, 150))

    def test_marks_create_anchors_and_leg(self):
        r = self._recorder()
        r.sample((20, 100), t=0.0)
        self.assertEqual(r.mark(t=0.0), 0)
        for i in range(1, 13):
            r.sample((20 + i * 8, 100), t=i)
        self.assertEqual(r.mark(t=13.0), 1)
        entry = r.finish("m")
        self.assertEqual(len(entry.rotation.anchors), 2)
        steps = entry.rotation.legs[(0, 1)]
        self.assertEqual(steps[-1].kind, "walk_to")
        self.assertAlmostEqual(steps[-1].x, 116 / 200, places=2)

    def test_mark_near_existing_closes_loop(self):
        r = self._recorder()
        r.sample((20, 100), t=0.0)
        r.mark(t=0.0)
        for i in range(1, 10):
            r.sample((20 + i * 8, 100), t=i)
        r.mark(t=9.0)
        for i in range(10, 20):
            r.sample((100 - (i - 9) * 8, 100), t=i)
        r.sample((21, 101), t=20.0)
        idx = r.mark(t=20.0)
        self.assertEqual(idx, 0)  # deduped to anchor 0
        self.assertEqual(len(r.anchors), 2)
        entry = r.finish("m")
        self.assertIn((1, 0), entry.rotation.legs)

    def test_dwell_inferred_from_stationary_time(self):
        r = self._recorder()
        r.sample((50, 50), t=0.0)
        r.mark(t=0.0)
        for i in range(1, 10):
            r.sample((50 + i * 0.2, 50), t=i * 1.0)  # fidgeting in place
        r.sample((90, 50), t=20.0)  # leaves at t=20
        entry = r.finish("m")
        lo, hi = entry.rotation.anchors[0].dwell
        self.assertAlmostEqual(lo, 14.0, places=1)
        self.assertGreater(hi, lo)

    def test_finish_carries_minimap_region(self):
        r = self._recorder()
        r.sample((50, 50), t=0.0)
        r.mark(t=0.0)
        entry = r.finish("m", minimap_region=(8, 40, 200, 150))
        self.assertEqual(entry.minimap_region, (8, 40, 200, 150))

    def test_keys_attribute_to_previous_anchor(self):
        r = self._recorder()
        r.sample((50, 50), t=0.0)
        r.mark(t=0.0)
        r.record_key("d", t=1.0)   # summon right after arrival
        r.record_key("a", t=2.0)   # attack
        r.sample((120, 50), t=30.0)
        r.mark(t=30.0)
        entry = r.finish("m")
        self.assertEqual(entry.rotation.anchors[0].on_arrive, ("key_d", "key_a"))
        self.assertIn("key_d", entry.skills)

    def test_hotkeys_excluded_from_arrive(self):
        r = self._recorder()
        r.sample((50, 50), t=0.0)
        r.mark(t=0.0)
        r.record_key("f9", t=0.5)
        r.record_key("escape", t=1.0)
        r.record_key("s", t=1.5)
        r.sample((100, 50), t=30.0)
        r.mark(t=30.0)
        entry = r.finish("m")
        self.assertEqual(entry.rotation.anchors[0].on_arrive, ("key_s",))

    def test_mark_before_sample_raises(self):
        with self.assertRaises(RuntimeError):
            self._recorder().mark()

    def test_spammed_key_becomes_attack_skill(self):
        r = self._recorder()
        r.sample((50, 50), t=0.0)
        r.mark(t=0.0)
        for t in (1.0, 1.6, 2.2, 2.8):
            r.record_key("a", t=t)   # attack spam in the arrival window
        r.record_key("d", t=3.0)     # single press -> summon
        r.sample((120, 50), t=30.0)
        r.mark(t=30.0)
        entry = r.finish("m")
        self.assertEqual(entry.skills["key_a"].kind, "attack")
        self.assertAlmostEqual(entry.skills["key_a"].cooldown, 0.6)
        self.assertEqual(entry.skills["key_d"].kind, "summon")
        self.assertEqual(
            entry.rotation.anchors[0].on_arrive, ("key_a", "key_d")
        )

    def test_key_map_resolves_bound_and_movement_keys(self):
        from picobot.bot.skills import Skill

        r = self._recorder()
        r.sample((50, 50), t=0.0)
        r.mark(t=0.0)
        r.record_key("d", t=1.0)    # bound summon -> real skill name
        for t in (2.0, 2.5, 3.0, 3.5):
            r.record_key("a", t=t)  # bound attack -> real name, no dup skill
        for t in (4.0, 4.5, 5.0, 5.5):
            r.record_key("alt", t=t)  # movement -> dropped entirely
        r.sample((120, 50), t=30.0)
        r.mark(t=30.0)
        key_map = {
            "d": Skill("fountain", "d", 57.0, "summon"),
            "a": Skill("main", "a", 0.0, "attack"),
            "alt": Skill("fj", "alt", 0.0, "movement"),
        }
        entry = r.finish("m", key_map=key_map)
        self.assertEqual(
            entry.rotation.anchors[0].on_arrive, ("fountain", "main")
        )
        self.assertEqual(entry.skills, {})  # everything bound in config

    def test_quick_drop_becomes_down_jump(self):
        from picobot.bot.calibrate import _trace_to_steps

        # Sharp downward drop: few samples (fast fall) -> down_jump step.
        steps = _trace_to_steps(
            [(50, 50), (50, 58), (52, 70)], (200, 150))
        self.assertEqual(steps[-1].kind, "down_jump")

        # Sustained downward crawl = climbing down a rope -> climb.
        steps = _trace_to_steps(
            [(50, 50), (50, 55), (50, 60), (51, 65), (51, 70),
             (52, 75), (52, 80)], (200, 150))
        kinds = [s.kind for s in steps]
        self.assertIn("climb", kinds)
        self.assertNotIn("down_jump", kinds)


class CalibrationRunnerSnapTests(unittest.TestCase):
    """Off-platform anchor handling in CalibrationRunner (no threads —
    recorder/last_img are set directly)."""

    def _runner(self, snap_fn, pos=(50, 95)):
        from picobot.bot.calibrate import CalibrationRunner

        self.events = []
        runner = CalibrationRunner(
            frame_fn=lambda: None,
            pos_fn=lambda img: None,
            event=lambda k, m, d=None: self.events.append((k, m)),
            snap_fn=snap_fn,
        )
        runner.recorder = TraceRecorder((200, 150))
        runner.recorder.sample(pos)
        runner.last_img = object()
        runner.last_pos = pos
        return runner

    def test_off_platform_mark_warns(self):
        runner = self._runner(snap_fn=lambda img, x, y: None)
        runner.mark()
        self.assertTrue(
            any("off-platform" in m for k, m in self.events),
            self.events,
        )

    def test_on_platform_mark_stays_quiet(self):
        runner = self._runner(snap_fn=lambda img, x, y: 92)
        runner.mark()
        self.assertFalse(
            any("off-platform" in m for k, m in self.events),
            self.events,
        )

    def test_finish_warns_on_unsnappable_anchor(self):
        # No platform ink near the mark — kept as recorded, warned loudly.
        runner = self._runner(snap_fn=lambda img, x, y: None)
        runner.mark()
        entry = runner.finish("m")
        self.assertAlmostEqual(
            entry.rotation.anchors[0].y * 150, 95.0, places=1
        )
        self.assertTrue(
            any("off-platform" in m for k, m in self.events), self.events
        )

    def test_finish_snaps_anchor_onto_platform(self):
        # Anchor recorded at y=95 but nearest platform ink is y=80 —
        # the saved anchor must land on the floor.
        runner = self._runner(snap_fn=lambda img, x, y: 80)
        runner.mark()
        entry = runner.finish("m")
        self.assertAlmostEqual(
            entry.rotation.anchors[0].y * 150, 80.0, places=1
        )
        self.assertTrue(
            any("snapped" in m for k, m in self.events), self.events
        )


if __name__ == "__main__":
    unittest.main()
