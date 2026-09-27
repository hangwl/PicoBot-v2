import threading
import unittest

import numpy as np

from picobot.vision.screen import ScreenGrabber


class _FakeMss:
    def __init__(self, made):
        self.thread = threading.current_thread().name
        self.closed = False
        made.append(self)

    def grab(self, mon):
        if threading.current_thread().name != self.thread:
            raise RuntimeError("mss used from a foreign thread")
        return np.zeros((mon["height"], mon["width"], 4), np.uint8)

    def close(self):
        self.closed = True


class ScreenGrabberTests(unittest.TestCase):
    def test_each_thread_gets_its_own_instance(self):
        made = []
        grab = ScreenGrabber(factory=lambda: _FakeMss(made))
        results = {}

        def worker(name):
            results[name] = grab.capture((0, 0, 4, 3))

        threads = [threading.Thread(target=worker, args=(n,), name=n) for n in "ab"]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        main = grab.capture((0, 0, 4, 3))
        self.assertEqual(len(made), 3)
        for img in (*results.values(), main):
            self.assertEqual(img.shape, (3, 4, 3))       # BGRA -> BGR
        grab.capture((0, 0, 4, 3))
        self.assertEqual(len(made), 3)                   # reused per thread

    def test_close_releases_all_and_stops_capturing(self):
        made = []
        grab = ScreenGrabber(factory=lambda: _FakeMss(made))
        grab.capture((0, 0, 2, 2))
        grab.close()
        self.assertTrue(all(m.closed for m in made))
        self.assertIsNone(grab.capture((0, 0, 2, 2)))

    def test_empty_region(self):
        grab = ScreenGrabber(factory=lambda: _FakeMss([]))
        self.assertIsNone(grab.capture((0, 0, 0, 5)))


if __name__ == "__main__":
    unittest.main()
