import json
import unittest
from unittest.mock import Mock, patch

import numpy as np

from picobot.config import AppConfig
from picobot.events import EventBus
from picobot.remote.control import (
    DASHBOARD_PREFIXES,
    RemoteCallbacks,
    RemoteControlServer,
)
from picobot.remote.streamer import annotate, encode_jpeg
from picobot.serve import BotHost


class EventBusTests(unittest.TestCase):
    def test_emit_reaches_subscribers(self):
        bus = EventBus()
        got = []
        bus.subscribe(got.append)
        bus.emit("fsm", "GRIND", {"x": 1})
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["kind"], "fsm")
        self.assertEqual(got[0]["msg"], "GRIND")
        self.assertEqual(got[0]["data"], {"x": 1})
        self.assertIn("t", got[0])

    def test_history_retained_for_late_joiners(self):
        bus = EventBus(history=5)
        for i in range(8):
            bus.emit("log", f"m{i}")
        items = bus.history()
        self.assertEqual(len(items), 5)
        self.assertEqual(items[-1]["msg"], "m7")

    def test_bad_subscriber_does_not_break_emit(self):
        bus = EventBus()
        bus.subscribe(lambda e: 1 / 0)
        bus.subscribe(lambda e: None)
        bus.emit("log", "ok")  # no raise


class AnnotateTests(unittest.TestCase):
    def test_draws_markers(self):
        img = np.zeros((100, 160, 3), dtype=np.uint8)
        out = annotate(img, {
            "anchors": [(40, 50)],
            "player": (80, 50),
            "target": (120, 50),
            "hazard": None,
        })
        # anchor (blue 255,128,0), player (green), target (red)
        self.assertTrue((out[50, 37] == (255, 128, 0)).any() or
                        (out[47, 40] == (255, 128, 0)).any())
        self.assertTrue((out[46, 80] == (0, 255, 0)).all())
        self.assertTrue((out[50, 116] == (0, 0, 255)).all())
        # original untouched
        self.assertFalse(img.any())

    def test_encode_jpeg_roundtrip(self):
        img = np.full((40, 60, 3), (10, 200, 90), dtype=np.uint8)
        data = encode_jpeg(img, quality=60)
        import base64
        raw = base64.b64decode(data)
        self.assertTrue(raw.startswith(b"\xff\xd8"))  # JPEG SOI


def _callbacks(**overrides) -> RemoteCallbacks:
    kw = dict(
        schedule=lambda fn: fn(),
        log=lambda m: None,
        set_status=lambda m: None,
        set_ws_port=lambda p: None,
        start_macro=lambda: None,
        stop_macro=lambda: None,
        is_macro_playing=lambda: False,
        broadcast=lambda m: None,
        get_macro_base_path=lambda: "",
        on_remote_playlist_selected=lambda p: None,
    )
    kw.update(overrides)
    return RemoteCallbacks(**kw)


class ConnectSerialTests(unittest.TestCase):
    def _server(self) -> RemoteControlServer:
        return RemoteControlServer(
            "", 0, _callbacks(), serial_manager=Mock(is_open=False)
        )

    def test_swaps_manager_on_success(self):
        srv = self._server()
        with patch("picobot.remote.control.SerialManager") as sm:
            mgr = sm.return_value
            mgr.is_open = True
            self.assertTrue(srv.connect_serial("COM5"))
        sm.assert_called_once_with("COM5")
        mgr.open.assert_called_once_with()
        mgr.register_line_callback.assert_called_once_with(srv._on_serial_line)
        self.assertIs(srv.serial_manager, mgr)
        self.assertEqual(srv.serial_port_name, "COM5")

    def test_failure_keeps_old_manager(self):
        srv = self._server()
        old = srv.serial_manager
        with patch("picobot.remote.control.SerialManager") as sm:
            sm.return_value.open.side_effect = OSError("busy")
            self.assertFalse(srv.connect_serial("COM5"))
        self.assertIs(srv.serial_manager, old)
        self.assertEqual(srv.serial_port_name, "")

    def test_same_port_short_circuits(self):
        srv = self._server()
        srv.serial_port_name = "COM5"
        srv.serial_manager.is_open = True
        with patch("picobot.remote.control.SerialManager") as sm:
            self.assertTrue(srv.connect_serial("COM5"))
        sm.assert_not_called()

    def test_empty_port_rejected(self):
        srv = self._server()
        self.assertFalse(srv.connect_serial("  "))

    def test_dashboard_prefixes_cover_all_commands(self):
        for prefix in (
            "bot|", "map|", "cal|", "dash|", "host|", "events|", "config|"
        ):
            self.assertIn(prefix, DASHBOARD_PREFIXES)


class HostCommandTests(unittest.TestCase):
    def setUp(self):
        self.save_patch = patch("picobot.serve.save_config")
        self.save_mock = self.save_patch.start()
        self.host = BotHost(None, "OldWin", config=AppConfig())
        self.sent = []
        self.host.remote.broadcast = self.sent.append

    def tearDown(self):
        self.save_patch.stop()

    def test_window_command_updates_and_persists(self):
        self.assertTrue(self.host._handle_command("host|window|NewWin"))
        self.assertEqual(self.host.window_title, "NewWin")
        self.assertEqual(self.host.config.default_target_window, "NewWin")
        self.save_mock.assert_called()

    def test_window_command_clears_feed(self):
        self.host._feed = Mock()
        self.host._handle_command("host|window|OtherWin")
        self.assertIsNone(self.host._feed)

    def test_serial_command_connects_and_persists(self):
        with patch.object(
            self.host.remote, "connect_serial", return_value=True
        ) as cs:
            self.assertTrue(self.host._handle_command("host|serial|COM7"))
        cs.assert_called_once_with("COM7")
        self.assertEqual(self.host.serial_port, "COM7")
        self.assertEqual(self.host.config.serial_port, "COM7")
        self.save_mock.assert_called()

    def test_serial_failure_keeps_state(self):
        with patch.object(
            self.host.remote, "connect_serial", return_value=False
        ):
            self.host._handle_command("host|serial|COM7")
        self.assertIsNone(self.host.serial_port)
        self.save_mock.assert_not_called()

    def test_host_state_payload(self):
        self.host._handle_command("host|state")
        self.assertTrue(self.sent)
        payload = json.loads(self.sent[-1][len("dash|"):])
        self.assertEqual(payload["event"], "host")
        self.assertEqual(payload["window"], "OldWin")
        self.assertIsNone(payload["serial"])
        self.assertIn("ports", payload)
        self.assertIn("windows", payload)
        self.assertIn("serial_open", payload)

    def test_serial_auto_probes_then_connects(self):
        done = []
        with patch(
            "picobot.transport.discover_data_port", return_value="COM9"
        ), patch.object(
            self.host, "_finish_serial_connect",
            side_effect=lambda p: done.append(p),
        ):
            self.assertTrue(self.host._handle_command("host|serial|auto"))
        # probe runs on a daemon thread; wait briefly for it
        import threading

        for t in threading.enumerate():
            if t.name == "PortProbe":
                t.join(timeout=3)
        self.assertEqual(done, ["COM9"])


if __name__ == "__main__":
    unittest.main()
