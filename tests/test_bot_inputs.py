import unittest

from picobot.bot.inputs import HidController


class FakeSender:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    def __call__(self, payload: str) -> bool:
        self.sent.append(payload)
        return not self.fail


def _noop_sleep(_seconds):
    return None


class KeySpacingTests(unittest.TestCase):
    def _hid(self, gap=0.03):
        clock = [0.0]
        slept = []

        def sleep(s):
            slept.append(round(s, 4))
            clock[0] += s

        hid = HidController(FakeSender(), sleep=sleep, gap=lambda: gap,
                            clock=lambda: clock[0])
        return hid, clock, slept

    def test_back_to_back_events_are_staggered(self):
        hid, _, slept = self._hid()
        hid.key_down("left")
        hid.key_down("space")          # would land at the same instant
        self.assertEqual(slept, [0.03])

    def test_time_already_passed_counts_toward_the_gap(self):
        hid, clock, slept = self._hid()
        hid.key_down("left")
        clock[0] += 0.02               # e.g. the serial round-trip
        hid.key_down("space")
        self.assertEqual(slept, [0.01])
        clock[0] += 0.5                # a deliberate re-press sleep
        hid.key_up("space")
        self.assertEqual(slept, [0.01])

    def test_releases_are_staggered_too(self):
        hid, _, slept = self._hid()
        hid.key_down("left")
        hid.key_down("up")
        hid.release_all()
        self.assertEqual(len(slept), 3)

    def test_no_gap_when_disabled(self):
        hid = HidController(FakeSender(), sleep=_noop_sleep, gap=None)
        hid.key_down("left")
        hid.key_down("space")
        self.assertEqual(hid.held_keys, frozenset({"left", "space"}))


class HidControllerTests(unittest.TestCase):
    def test_press_emits_down_then_up(self):
        sender = FakeSender()
        hid = HidController(sender, sleep=_noop_sleep)
        self.assertTrue(hid.press("a"))
        self.assertEqual(sender.sent, ["hid|key|down|a", "hid|key|up|a"])
        self.assertEqual(hid.held_keys, frozenset())

    def test_hold_tracking_and_release_all(self):
        sender = FakeSender()
        hid = HidController(sender, sleep=_noop_sleep)
        hid.key_down("left")
        hid.key_down("shift")
        self.assertEqual(hid.held_keys, frozenset({"left", "shift"}))
        hid.release_all()
        self.assertEqual(hid.held_keys, frozenset())
        self.assertIn("hid|key|up|left", sender.sent)
        self.assertIn("hid|key|up|shift", sender.sent)

    def test_key_up_clears_tracking(self):
        sender = FakeSender()
        hid = HidController(sender, sleep=_noop_sleep)
        hid.key_down("left")
        hid.key_up("left")
        self.assertEqual(hid.held_keys, frozenset())

    def test_failed_key_down_not_tracked(self):
        sender = FakeSender(fail=True)
        hid = HidController(sender, sleep=_noop_sleep)
        self.assertFalse(hid.key_down("a"))
        self.assertEqual(hid.held_keys, frozenset())

    def test_mouse_and_move_payloads(self):
        sender = FakeSender()
        hid = HidController(sender, sleep=_noop_sleep)
        hid.move(5, -3)
        hid.scroll(1)
        hid.click("right")
        self.assertEqual(
            sender.sent,
            [
                "hid|move|5|-3",
                "hid|scroll|0|1",
                "hid|mouse|down|right",
                "hid|mouse|up|right",
            ],
        )

    def test_release_all_is_idempotent(self):
        sender = FakeSender()
        hid = HidController(sender, sleep=_noop_sleep)
        hid.release_all()  # nothing held — must not raise
        hid.key_down("a")
        hid.release_all()
        hid.release_all()
        self.assertEqual(hid.held_keys, frozenset())


if __name__ == "__main__":
    unittest.main()
