import time
import unittest

from picobot.bot.flight import Flight, FlightRecorder


def arc(y0=100, rise=20.0, peak_t=0.25, air=0.6, x=50, lead=0.1, rate=60,
        tail=0.2, glitch=None, marks=None):
    """A synthetic flight: stand, jump at ``lead`` s, peak, land on y0."""
    samples = []
    n = int((lead + air + tail) * rate)
    for i in range(n):
        t = i / rate
        a = t - lead
        if a <= 0 or a >= air:
            h = 0.0
        elif a <= peak_t:
            h = rise * (1 - (1 - a / peak_t) ** 2)
        else:
            h = rise * (1 - ((a - peak_t) / (air - peak_t)) ** 2)
        samples.append((t, x, int(round(y0 - h))))
    if glitch is not None:
        t, _, _ = samples[glitch]
        samples[glitch] = (t, x, y0 - int(rise) - 15)
    return Flight(samples, marks or {"jump": lead})


class FlightTests(unittest.TestCase):
    def test_peak_rise_and_time_after_the_jump(self):
        t, rise = arc(rise=20, peak_t=0.25).peak()
        self.assertEqual(rise, 20)
        self.assertAlmostEqual(t, 0.25, delta=0.05)

    def test_single_stray_sample_is_not_the_peak(self):
        f = arc(rise=20, peak_t=0.25, glitch=15)
        self.assertEqual(f.peak()[1], 20)

    def test_landing_back_on_the_start_row(self):
        t, rise = arc(rise=20, air=0.6).landing()
        self.assertEqual(rise, 0)
        self.assertAlmostEqual(t, 0.6, delta=0.05)

    def test_no_landing_while_still_falling(self):
        f = arc(rise=20, air=0.6, tail=0.0)
        f.samples = f.samples[: int(0.55 * 60)]
        self.assertIsNone(f.landing())

    def test_start_row_is_the_standing_median(self):
        self.assertEqual(arc(y0=87).start_y(), 87)

    def test_gap_between_marks(self):
        f = arc(marks={"jump": 0.1, "rejump": 0.3})
        self.assertAlmostEqual(f.gap(), 0.2)
        self.assertIsNone(arc().gap())

    def test_empty_flight(self):
        f = Flight([])
        self.assertIsNone(f.peak())
        self.assertIsNone(f.landing())


class FlightRecorderTests(unittest.TestCase):
    def test_samples_on_its_own_thread_with_marks(self):
        ys = iter(range(100, 0, -1))
        rec = FlightRecorder(lambda: (5, next(ys, 1)), period=0.005)
        rec.start()
        time.sleep(0.03)
        rec.mark("jump")
        time.sleep(0.03)
        f = rec.stop()
        self.assertGreater(len(f.samples), 3)
        self.assertIn("jump", f.marks)
        times = [t for t, _, _ in f.samples]
        self.assertEqual(times, sorted(times))

    def test_capture_errors_and_misses_are_skipped(self):
        calls = []

        def capture():
            calls.append(1)
            if len(calls) % 2:
                raise RuntimeError("grab failed")
            return None

        rec = FlightRecorder(capture, period=0.005)
        rec.start()
        time.sleep(0.03)
        f = rec.stop()
        self.assertEqual(f.samples, [])
        self.assertGreater(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
