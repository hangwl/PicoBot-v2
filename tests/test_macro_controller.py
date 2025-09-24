import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from picobot.playback import MacroController, build_playlist, parse_macro_file


class DummyPortService:
    def list_ports(self):
        return []

    def guess_data_port(self):
        return None

    def discover_data_port(self, exclude_port=None):
        return None

    def build_selection(self, current=None, force_auto=False):
        return SimpleNamespace(ports=[], selected=None, auto_selected=False)


class DummyWindowService:
    def list_titles(self):
        return []

    def activate(self, _title: str) -> bool:
        return True

    def get_active_title(self):
        return ""

    def build_selection(self, current=None):
        return SimpleNamespace(titles=[], selected=None)



class MacroControllerPlaybackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.port_service = DummyPortService()
        self.window_service = DummyWindowService()
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
        self.controller = MacroController(
            self.app_stub,
            port_service=self.port_service,
            window_service=self.window_service,
        )

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

    def test_build_port_selection_delegates_to_service(self) -> None:
        expected = SimpleNamespace(ports=['COM3'], selected='COM3', auto_selected=True)

        def fake_build_selection(current, *, force_auto=False):
            self.assertEqual(current, 'COM3')
            self.assertTrue(force_auto)
            return expected

        self.port_service.build_selection = fake_build_selection  # type: ignore[attr-defined]
        result = self.controller.build_port_selection('COM3', force_auto=True)
        self.assertIs(result, expected)

    def test_build_window_selection_delegates_to_service(self) -> None:
        expected = SimpleNamespace(titles=['One', 'Two'], selected='Two')

        def fake_build_selection(current):
            self.assertEqual(current, 'Two')
            return expected

        self.window_service.build_selection = fake_build_selection  # type: ignore[attr-defined]
        result = self.controller.build_window_selection('Two')
        self.assertIs(result, expected)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
