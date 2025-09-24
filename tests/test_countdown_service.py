import unittest
from unittest import mock

from picobot.countdown import CountdownService


class CountdownServiceTests(unittest.TestCase):
    def test_countdown_completes_and_reports_status(self) -> None:
        service = CountdownService()
        on_tick_calls = []
        on_status_calls = []
        on_complete_calls = []

        with mock.patch("picobot.countdown.time.sleep"):
            service.start(
                seconds=2,
                on_tick=on_tick_calls.append,
                on_status=on_status_calls.append,
                on_complete=on_complete_calls.append,
            )
            service.wait()

        self.assertEqual(on_tick_calls, [2, 1])
        self.assertEqual(on_complete_calls, [True])
        self.assertEqual(
            on_status_calls, ["Countdown: Sending notification...", "Countdown: Completed!"]
        )

    def test_stop_aborts_countdown(self) -> None:
        service = CountdownService()
        on_tick_calls = []
        on_complete_calls = []

        def on_tick(remaining: int) -> None:
            on_tick_calls.append(remaining)
            if remaining == 1:
                service.stop()

        with mock.patch("picobot.countdown.time.sleep"):
            service.start(
                seconds=2,
                on_tick=on_tick,
                on_complete=on_complete_calls.append,
            )
            service.wait()

        self.assertEqual(on_tick_calls, [2, 1])
        self.assertEqual(on_complete_calls, [False])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()