import threading
import time
import unittest
from unittest.mock import Mock

import numpy as np

from picobot.bot.config import BotConfig
from picobot.bot.monitor import MapMonitor
from picobot.vision.minimap import MinimapAnalyzer
from picobot.vision.transition import TransitionDetector

_BORDER = (228, 228, 228)


def _window(x, y, w, h, fill=50):
    img = np.full((768, 1366, 3), fill, np.uint8)
    img[y, x + 4 : x + w - 3] = _BORDER
    img[y + 1, x + 2 : x + w - 1] = _BORDER
    img[y + h, x + 4 : x + w - 3] = _BORDER
    img[y + h - 1, x + 2 : x + w - 1] = _BORDER
    img[y + 4 : y + h - 3, x] = _BORDER
    img[y + 2 : y + h - 1, x + 1] = _BORDER
    img[y + 4 : y + h - 3, x + w] = _BORDER
    img[y + 2 : y + h - 1, x + w - 1] = _BORDER
    return img


class _Screen:
    """Serves crops of whatever ``self.img`` currently shows."""

    def __init__(self, img):
        self.img = img
        self.calls = []

    def capture(self, rect):
        x, y, w, h = rect
        self.calls.append(rect)
        return self.img[y : y + h, x : x + w]

    def close(self):
        pass


class MapMonitorTests(unittest.TestCase):
    def setUp(self):
        self.t = 0.0
        clock = lambda: self.t
        self.analyzer = MinimapAnalyzer(transition=TransitionDetector(clock=clock))
        self.identity = Mock()
        self.identity.current.title = "x"
        self.events = []
        window = Mock(client_left=0, client_top=0, client_rect=lambda: (0, 0, 1366, 768))
        self.mon = MapMonitor(
            window, self.analyzer, self.identity, config=BotConfig(),
            on_event=lambda k, m: self.events.append(m), clock=clock,
        )
        self.happy = _window(7, 68, 216, 90)
        self.rage = _window(7, 68, 185, 82)
        self.screen = _Screen(self.happy)

    def _run(self, img, seconds, step=0.05):
        self.screen.img = img
        for _ in range(int(round(seconds / step))):
            self.t += step
            self.mon.tick(self.screen)

    def test_locates_panel_and_pumps_titles(self):
        self._run(self.happy, 0.2)
        self.assertEqual(self.analyzer.region, (7, 68, 216, 90))
        self.identity.pump.assert_called()

    def test_short_blackout_is_caught_at_20hz(self):
        # The shortest captured transfer: ~0.43s of black.
        self._run(self.happy, 0.5)
        self._run(np.zeros_like(self.happy), 0.45)
        self._run(self.rage, 1.5)
        self.identity.request.assert_called_once_with("arrival", clear=True)
        self.assertEqual(self.analyzer.region, (7, 68, 185, 82))
        self.assertIn("map transfer — loading screen", self.events)
        self.assertEqual(
            sum("arrived on a new map" in e for e in self.events), 1
        )

    def test_no_title_reads_while_loading(self):
        self._run(self.happy, 0.5)
        self.identity.pump.reset_mock()
        self._run(np.zeros_like(self.happy), 0.4)
        self.assertLessEqual(self.identity.pump.call_count, 3)

    def test_locate_is_rate_limited_when_panel_missing(self):
        blank = np.full((768, 1366, 3), 50, np.uint8)
        self._run(blank, 1.0)
        full = [c for c in self.screen.calls if c[2] == 1366]
        self.assertLessEqual(len(full), 5)

    def test_panel_toggle_relocates_without_transfer(self):
        self._run(self.happy, 0.5)
        self._run(_window(7, 28, 170, 82), 1.5)
        self.assertEqual(self.analyzer.region, (7, 28, 170, 82))
        self.identity.request.assert_not_called()   # title already known

    def test_thread_runs_and_stops(self):
        screen = _Screen(self.happy)
        mon = MapMonitor(
            self.mon.window, MinimapAnalyzer(), None,
            grabber_factory=lambda: screen, period=0.01,
        )
        mon.start()
        deadline = time.time() + 2
        while mon.analyzer.region is None and time.time() < deadline:
            time.sleep(0.01)
        mon.stop()
        self.assertEqual(mon.analyzer.region, (7, 68, 216, 90))
        self.assertFalse(mon.running)
        self.assertNotIn("MapMonitor", [t.name for t in threading.enumerate()])


if __name__ == "__main__":
    unittest.main()
