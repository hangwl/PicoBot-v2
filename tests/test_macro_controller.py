import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from picobot.playback import MacroController, build_playlist, parse_macro_file


class MacroControllerPlaybackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app_stub = SimpleNamespace(
            is_playing=True,
            status_text=SimpleNamespace(set=lambda value: None),
            keys_currently_down=set(),
            remote_server=None,
            root=SimpleNamespace(after=lambda *_args, **_kwargs: None),
            port_menu={},
            selected_port=SimpleNamespace(get=lambda: "", set=lambda value: None),
            window_menu={},
            selected_window=SimpleNamespace(get=lambda: "", set=lambda value: None),
            log_remote=lambda *args, **kwargs: None,
        )
        self.controller = MacroController(self.app_stub)

    def test_parse_macro_file_parses_events(self) -> None:
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as handle:
            handle.write("0.0 down a\n0.1 up a\n")
            path = handle.name
        try:
            events = parse_macro_file(path)
        finally:
            os.unlink(path)
        self.assertIsNotNone(events)
        assert events is not None
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["type"], "down")
        self.assertEqual(events[1]["key"], "a")

    def test_parse_macro_file_missing_returns_none(self) -> None:
        self.assertIsNone(parse_macro_file("nonexistent_file.txt"))

    def test_build_playlist_prioritises_start_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            for name in ["START_intro.txt", "macro_one.txt", "START_alpha.txt", "macro_two.txt"]:
                (Path(tmpdir) / name).write_text("", encoding="utf-8")
            with mock.patch(
                "picobot.playback.macro_controller.random.shuffle",
                side_effect=lambda seq: None,
            ):
                playlist = build_playlist(tmpdir)
        start_segment = playlist[:2]
        self.assertTrue(all(name.startswith("START_") for name in start_segment))
        self.assertCountEqual(start_segment, ["START_intro.txt", "START_alpha.txt"])
        self.assertCountEqual(playlist[2:], ["macro_one.txt", "macro_two.txt"])

    def test_interruptible_sleep_returns_false_when_stopped(self) -> None:
        time_values = iter([0.0, 0.0, 0.01, 0.02, 0.03])

        def fake_time() -> float:
            return next(time_values)

        def fake_sleep(_seconds: float) -> None:
            self.app_stub.is_playing = False

        with mock.patch(
            "picobot.playback.macro_controller.time.time",
            side_effect=fake_time,
        ), mock.patch(
            "picobot.playback.macro_controller.time.sleep",
            side_effect=fake_sleep,
        ):
            result = self.controller.interruptible_sleep(0.05)
        self.assertFalse(result)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
