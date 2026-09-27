import unittest

from picobot.bot.rotation import (
    Anchor,
    Rotation,
    Step,
    resolve_coord,
)


class ResolveCoordTests(unittest.TestCase):
    def test_normalized(self):
        self.assertEqual(resolve_coord(0.5, 200), 100)
        self.assertEqual(resolve_coord(1.0, 200), 200)

    def test_absolute_px_passthrough(self):
        self.assertEqual(resolve_coord(37, 200), 37)
        self.assertEqual(resolve_coord(150.4, 200), 150)


class StepParseTests(unittest.TestCase):
    def test_walk_to(self):
        s = Step.from_dict({"walk_to": [0.5, 0.3], "style": "flash"})
        self.assertEqual(s.kind, "walk_to")
        self.assertEqual((s.x, s.y), (0.5, 0.3))
        self.assertEqual(s.style, "flash")

    def test_climb(self):
        s = Step.from_dict({"climb": {"dir": "up", "until_y": 0.3, "x": 0.5}})
        self.assertEqual(s.kind, "climb")
        self.assertEqual(s.direction, "up")
        self.assertEqual(s.until_y, 0.3)
        self.assertEqual(s.x, 0.5)

    def test_jumps_and_wait(self):
        self.assertEqual(Step.from_dict({"up_jump": True}).kind, "up_jump")
        self.assertEqual(Step.from_dict({"down_jump": True}).kind, "down_jump")
        self.assertEqual(Step.from_dict({"wait": 1.5}).seconds, 1.5)

    def test_bad_steps_rejected(self):
        with self.assertRaises(ValueError):
            Step.from_dict({"fly": True})
        with self.assertRaises(ValueError):
            Step.from_dict({"climb": {"dir": "up"}})  # no until_y
        with self.assertRaises(ValueError):
            Step.from_dict({"climb": {"dir": "sideways", "until_y": 0.5}})
        with self.assertRaises(ValueError):
            Step.from_dict({"walk_to": [0.1, 0.2], "style": "teleport"})

    def test_step_round_trip(self):
        for spec in (
            {"walk_to": [0.5, 0.3], "style": "walk"},
            {"climb": {"dir": "down", "until_y": 0.8, "x": 0.5}},
            {"wait": 2.0},
            {"up_jump": True},
        ):
            s2 = Step.from_dict(Step.from_dict(spec).to_dict())
            self.assertEqual(s2.kind, Step.from_dict(spec).kind)


class AnchorTests(unittest.TestCase):
    def test_from_dict(self):
        a = Anchor.from_dict({
            "pos": [0.3, 0.5], "dwell": [4, 9],
            "on_arrive": ["fountain"], "face": "left",
        }, 0)
        self.assertEqual((a.x, a.y), (0.3, 0.5))
        self.assertEqual(a.dwell, (4.0, 9.0))
        self.assertEqual(a.on_arrive, ("fountain",))
        self.assertEqual(a.face, "left")

    def test_requires_pos(self):
        with self.assertRaises(ValueError):
            Anchor.from_dict({"dwell": [1, 2]})

    def test_dwell_seconds_in_range(self):
        a = Anchor("x", 0.5, 0.5, dwell=(5.0, 9.0))
        for _ in range(50):
            self.assertGreaterEqual(a.dwell_seconds(), 5.0)
            self.assertLessEqual(a.dwell_seconds(), 9.0)


class RotationTests(unittest.TestCase):
    def _rotation(self, style="loop"):
        return Rotation.from_dict({
            "style": style,
            "anchors": [{"pos": [0.1, 0.5]}, {"pos": [0.5, 0.5]},
                        {"pos": [0.9, 0.5]}],
        })

    def test_loop_wraps(self):
        rot = self._rotation("loop")
        self.assertEqual(rot.next_index(0), (1, 1))
        self.assertEqual(rot.next_index(2), (0, 1))

    def test_pingpong_reverses(self):
        rot = self._rotation("pingpong")
        self.assertEqual(rot.next_index(0, 1), (1, 1))
        idx, direction = rot.next_index(2, 1)
        self.assertEqual((idx, direction), (1, -1))
        idx, direction = rot.next_index(0, -1)
        self.assertEqual((idx, direction), (1, 1))

    def test_shuffle_never_stays(self):
        rot = self._rotation("shuffle")
        for _ in range(20):
            idx, _ = rot.next_index(1, 1)
            self.assertNotEqual(idx, 1)

    def test_default_leg_walks_to_anchor(self):
        rot = self._rotation()
        steps = rot.leg_steps(0, 2)
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].kind, "walk_to")
        self.assertEqual((steps[0].x, steps[0].y), (0.9, 0.5))

    def test_explicit_leg_steps(self):
        rot = Rotation.from_dict({
            "anchors": [{"pos": [0.1, 0.5]}, {"pos": [0.9, 0.3]}],
            "legs": [{"from": 0, "to": 1, "steps": [
                {"walk_to": [0.5, 0.5]},
                {"climb": {"dir": "up", "until_y": 0.3, "x": 0.5}},
            ]}],
        })
        steps = rot.leg_steps(0, 1)
        self.assertEqual([s.kind for s in steps], ["walk_to", "climb"])

    def test_leg_out_of_range_rejected(self):
        with self.assertRaises(ValueError):
            Rotation.from_dict({
                "anchors": [{"pos": [0.1, 0.5]}],
                "legs": [{"from": 0, "to": 3, "steps": []}],
            })

    def test_rotation_round_trip(self):
        rot = Rotation.from_dict({
            "style": "pingpong",
            "position_jitter_px": 6,
            "anchors": [{"name": "a", "pos": [0.1, 0.5],
                         "on_arrive": ["f"], "face": "left"}],
            "legs": [{"from": 0, "to": 0, "steps": [{"wait": 1}]}],
        })
        rot2 = Rotation.from_dict(rot.to_dict())
        self.assertEqual(rot2.style, "pingpong")
        self.assertEqual(rot2.position_jitter_px, 6)
        self.assertEqual(rot2.anchors[0].on_arrive, ("f",))
        self.assertIn((0, 0), rot2.legs)

    def test_empty(self):
        self.assertEqual(Rotation.from_dict(None).anchors, [])
        self.assertEqual(Rotation.from_dict({}).anchors, [])


class RemoveAnchorTests(unittest.TestCase):
    def test_legs_reindexed_and_dangling_dropped(self):
        from picobot.bot.rotation import Anchor, Rotation, Step

        rot = Rotation(
            anchors=[Anchor(f"a{i}", 0.1 * i, 0.5) for i in range(4)],
            legs={(0, 1): [Step("wait", seconds=1)],
                  (1, 2): [Step("wait", seconds=2)],
                  (2, 3): [Step("wait", seconds=3)]},
        )
        rot.remove_anchor(1)
        self.assertEqual([a.name for a in rot.anchors], ["a0", "a2", "a3"])
        self.assertEqual(list(rot.legs), [(1, 2)])
        self.assertEqual(rot.legs[(1, 2)][0].seconds, 3)


if __name__ == "__main__":
    unittest.main()
