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
