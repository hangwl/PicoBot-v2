import unittest

import numpy as np

from picobot.vision.transition import TransitionDetector, is_dark


class IsDarkTests(unittest.TestCase):
    def test_black_and_near_black(self):
        self.assertTrue(is_dark(np.zeros((50, 50, 3), np.uint8)))
        img = np.zeros((50, 50, 3), np.uint8)
        img[0, 0] = 255                       # a stray bright pixel
        self.assertTrue(is_dark(img))

    def test_dark_scene_with_ui_is_not_dark(self):
        img = np.full((90, 216, 3), 15, np.uint8)
        img[0:2, :] = 228                     # the panel's white frame
        img[:, 0:2] = 228
        self.assertFalse(is_dark(img))

    def test_none_and_empty(self):
        self.assertFalse(is_dark(None))
        self.assertFalse(is_dark(np.zeros((0, 0, 3), np.uint8)))


class DetectorTests(unittest.TestCase):
    def setUp(self):
        self.t = 0.0
        self.d = TransitionDetector(clock=lambda: self.t)
        self.black = np.zeros((20, 20, 3), np.uint8)
        self.lit = np.full((20, 20, 3), 90, np.uint8)

    def _note(self, img, dt=0.1):
        self.t += dt
        return self.d.note(img)

    def test_full_cycle_events(self):
        events = [self._note(self.black) for _ in range(5)]
        self.assertIn("loading", events)
        events = [self._note(self.lit) for _ in range(8)]
        self.assertEqual(events.count("arrived"), 1)
        self.assertEqual(self.d.state, "normal")

    def test_redark_while_settling_restarts_settle(self):
        for _ in range(5):
            self._note(self.black)
        self._note(self.lit)
        self.assertEqual(self.d.state, "settling")
        self._note(self.black)
        self.assertEqual(self.d.state, "dark")
        events = [self._note(self.lit) for _ in range(8)]
        self.assertEqual(events.count("arrived"), 1)

    def test_none_frames_ignored(self):
        self.assertIsNone(self.d.note(None))
        self.assertEqual(self.d.state, "normal")


if __name__ == "__main__":
    unittest.main()
