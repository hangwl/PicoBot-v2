import time
import unittest
from unittest import mock

from picobot.bot.config import BotConfig
from picobot.bot.machine import Machine
from picobot.bot.states.base import POP
from picobot.bot.states.grind import Grind
from picobot.bot.states.pause import Pause
from picobot.bot.states.wander import Wander


class FakeHid:
    def __init__(self):
        self.releases = 0

    def release_all(self):
        self.releases += 1


class FakeBot:
    """Minimal bot double satisfying what states/machine touch."""

    def __init__(self):
        self.config = BotConfig(stationary_seconds=0.0)
        self.hid = FakeHid()
        self._continue = True
        self._focused = True
        self._rune = False
        self._players = False
        self.grind_calls = 0
        self.wander_calls = 0
        self.notifications = []

    # BotBase-compatible surface
    def should_continue(self):
        return self._continue

    def is_window_focused(self):
        return self._focused

    def rune_present(self):
        return self._rune

    def other_players_present(self):
        return self._players

    def check_lie_detector(self):
        return None

    def sleep(self, _duration):
        return not self._continue

    def log(self, _msg):
        pass

    def notify(self, msg):
        self.notifications.append(msg)

    def grind_once(self):
        self.grind_calls += 1
        self._continue = False  # one shot

    def random_wander(self):
        self.wander_calls += 1
        self._continue = False


class MachineTests(unittest.TestCase):
    def test_runs_grind_then_stops_cleanly(self):
        bot = FakeBot()
        bot.config.enable_random_wander = False  # stay in GRIND
        machine = Machine(bot)
        machine.run()
        self.assertEqual(bot.grind_calls, 1)
        self.assertGreaterEqual(bot.hid.releases, 1)

    def test_unfocused_window_pauses_and_resumes(self):
        bot = FakeBot()
        bot.config.stationary_seconds = 999  # stay in GRIND when focused
        bot.grind_once = lambda: None  # don't end the run early
        machine = Machine(bot)
        machine.current_state = Grind(bot)
        machine.current_state.enter()

        bot._focused = False
        self.assertTrue(machine.switch())
        self.assertIsInstance(machine.current_state, Pause)
        self.assertEqual(len(machine.stack), 1)

        # while paused, POPs only when safe again
        machine.current_state.execute()
        self.assertFalse(machine.switch())
        bot._focused = True
        self.assertTrue(machine.switch())
        self.assertIsInstance(machine.current_state, Grind)
        self.assertEqual(len(machine.stack), 0)

    def test_rune_pauses_when_enabled(self):
        bot = FakeBot()
        bot.config.stationary_seconds = 999
        bot._rune = True
        machine = Machine(bot)
        machine.current_state = Grind(bot)
        machine.current_state.enter()
        self.assertTrue(machine.switch())
        self.assertIsInstance(machine.current_state, Pause)
        self.assertTrue(bot.notifications)

    def test_rune_ignored_when_toggle_off(self):
        bot = FakeBot()
        bot.config.stationary_seconds = 999
        bot.config.stop_when_rune_appears = False
        bot._rune = True
        machine = Machine(bot)
        machine.current_state = Grind(bot)
        machine.current_state.enter()
        self.assertFalse(machine.switch())
        self.assertIsInstance(machine.current_state, Grind)

    def test_grind_transitions_to_wander_after_timer(self):
        bot = FakeBot()
        bot.config.stationary_seconds = 0.0
        machine = Machine(bot)
        machine.current_state = Grind(bot)
        machine.current_state.enter()
        with mock.patch(
            "picobot.bot.states.grind.time.time",
            return_value=time.time() + 10,
        ):
            self.assertTrue(machine.switch())
        self.assertIsInstance(machine.current_state, Wander)

    def test_grind_stays_when_wander_disabled(self):
        bot = FakeBot()
        bot.config.stationary_seconds = 0.0
        bot.config.enable_random_wander = False
        machine = Machine(bot)
        machine.current_state = Grind(bot)
        machine.current_state.enter()
        self.assertFalse(machine.switch())

    def test_lie_detector_seam_pauses(self):
        bot = FakeBot()
        bot.config.stationary_seconds = 999
        bot.config.pause_on_lie_detector = True
        bot.check_lie_detector = lambda: True
        machine = Machine(bot)
        machine.current_state = Grind(bot)
        machine.current_state.enter()
        machine.switch()
        self.assertIsInstance(machine.current_state, Pause)


if __name__ == "__main__":
    unittest.main()
