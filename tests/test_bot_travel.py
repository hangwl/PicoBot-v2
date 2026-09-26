import time
import unittest

from picobot.bot.config import BotConfig
from picobot.bot.machine import Machine
from picobot.bot.rotation import Rotation
from picobot.bot.states.grind import Grind
from picobot.bot.states.travel import Travel


class FakeHid:
    def __init__(self):
        self.releases = 0

    def release_all(self):
        self.releases += 1


class RotationFakeBot:
    """Bot double with a configured rotation for Travel/Grind interplay."""

    def __init__(self, rotation):
        self.config = BotConfig()
        self._rotation = rotation
        self.hid = FakeHid()
        self._continue = True
        self._focused = True
        self._dwell_end = 0.0
        self._travel_target = None
        self.anchor_idx = 0
        self.begin_travel_calls = 0
        self.run_travel_calls = 0
        self.dwell_ticks = 0
        self.leg_ok = True
        self.notifications = []

    def should_continue(self):
        return self._continue

    def is_window_focused(self):
        return self._focused

    def unsafe_reason(self):
        return None

    def sleep(self, _duration):
        return not self._continue

    def log(self, _msg):
        pass

    def notify(self, msg):
        self.notifications.append(msg)

    # rotation surface
    def rotation_active(self):
        return True

    def effective_rotation(self):
        return self._rotation

    def begin_dwell(self):
        self._dwell_end = time.time()  # instantly done on demand via flag

    def dwell_done(self):
        return time.time() >= self._dwell_end

    def dwell_tick(self):
        self.dwell_ticks += 1
        self._continue = False

    def begin_travel(self):
        self.begin_travel_calls += 1
        target, _ = self._rotation.next_index(self.anchor_idx, 1)
        self._travel_target = target
        return True

    def run_travel(self):
        self.run_travel_calls += 1
        if self.leg_ok:
            self.anchor_idx = self._travel_target
        return self.leg_ok


def _rotation(wander_chance=0.0):
    return Rotation.from_dict({
        "wander_chance": wander_chance,
        "anchors": [{"pos": [0.1, 0.5]}, {"pos": [0.9, 0.5]}],
    })


class TravelStateTests(unittest.TestCase):
    def test_grind_to_travel_to_grind(self):
        bot = RotationFakeBot(_rotation())
        machine = Machine(bot)
        machine.current_state = Grind(bot)
        machine.current_state.enter()

        # dwell_done is already True -> transition to TRAVEL
        self.assertTrue(machine.switch())
        self.assertIsInstance(machine.current_state, Travel)

        machine.current_state.execute()
        self.assertTrue(machine.switch())
        self.assertIsInstance(machine.current_state, Grind)
        self.assertEqual(bot.run_travel_calls, 1)

    def test_travel_without_target_returns_to_grind(self):
        bot = RotationFakeBot(_rotation())
        bot.begin_travel = lambda: False  # nothing to do
        machine = Machine(bot)
        machine.current_state = Travel(bot)
        machine.current_state.enter()
        self.assertTrue(machine.switch())
        self.assertIsInstance(machine.current_state, Grind)

    def test_failed_leg_still_resumes_grind(self):
        bot = RotationFakeBot(_rotation())
        bot.leg_ok = False
        machine = Machine(bot)
        machine.current_state = Travel(bot)
        machine.current_state.enter()
        machine.current_state.execute()
        self.assertTrue(machine.switch())
        self.assertIsInstance(machine.current_state, Grind)

    def test_grind_dwells_until_done(self):
        bot = RotationFakeBot(_rotation())
        bot._dwell_end = None
        bot.begin_dwell = lambda: setattr(
            bot, "_dwell_end", time.time() + 3600
        )
        machine = Machine(bot)
        machine.current_state = Grind(bot)
        machine.current_state.enter()
        self.assertFalse(machine.switch())
        machine.current_state.execute()
        self.assertEqual(bot.dwell_ticks, 1)


if __name__ == "__main__":
    unittest.main()
