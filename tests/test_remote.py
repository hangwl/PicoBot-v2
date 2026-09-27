import asyncio
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from picobot.remote.control import RemoteCallbacks, RemoteControlServer
from picobot.remote.http import EmbeddedHTTPServer


def _callbacks(**kw):
    base = dict(
        schedule=lambda fn: fn(), log=lambda m: None, set_status=lambda m: None,
        set_ws_port=lambda p: None, start_macro=lambda: None,
        stop_macro=lambda: None, is_macro_playing=lambda: False,
        broadcast=lambda m: None, get_macro_base_path=lambda: "",
        on_remote_playlist_selected=lambda p: None,
    )
    base.update(kw)
    return RemoteCallbacks(**base)


class _Loop:
    """An asyncio loop on a background thread, like the WS bridge's."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=2)


class _Client:
    def __init__(self, gate=None):
        self.sent = []
        self.gate = gate

    async def send(self, data):
        if self.gate is not None:
            while not self.gate.is_set():
                await asyncio.sleep(0.01)
        self.sent.append(data)


class CommandWorkerTests(unittest.TestCase):
    def _server(self, **cb):
        srv = RemoteControlServer("", 0, _callbacks(**cb), serial_manager=Mock())
        srv._job_thread = threading.Thread(target=srv._job_loop, daemon=True)
        srv._job_thread.start()
        return srv

    def test_slow_command_does_not_block_the_event_loop(self):
        release = threading.Event()
        done = []

        def handle(msg):
            release.wait(2)
            done.append(msg)
            return True

        srv = self._server(handle_command=handle)
        t0 = time.monotonic()
        asyncio.run(srv._handle_ws_message(Mock(), "map|list"))
        self.assertLess(time.monotonic() - t0, 0.5)
        self.assertEqual(done, [])
        release.set()
        srv._jobs.put(None)
        srv._job_thread.join(2)
        self.assertEqual(done, ["map|list"])

    def test_commands_run_in_order_including_bot_lifecycle(self):
        order = []
        srv = self._server(
            handle_command=lambda m: order.append(m) or True,
            start_bot=lambda: order.append("START"),
            stop_bot=lambda: order.append("STOP"),
        )
        for m in ("map|list", "bot|start", "layout|save", "bot|stop"):
            asyncio.run(srv._handle_ws_message(Mock(), m))
        srv._jobs.put(None)
        srv._job_thread.join(2)
        self.assertEqual(order, ["map|list", "START", "layout|save", "STOP"])

    def test_inline_when_not_started(self):
        seen = []
        srv = RemoteControlServer(
            "", 0, _callbacks(handle_command=lambda m: seen.append(m) or True),
            serial_manager=Mock(),
        )
        asyncio.run(srv._handle_ws_message(Mock(), "skills|list"))
        self.assertEqual(seen, ["skills|list"])


class FrameBroadcastTests(unittest.TestCase):
    def setUp(self):
        self.loop = _Loop()
        self.srv = RemoteControlServer("", 0, _callbacks(), serial_manager=Mock())
        self.srv.bridge = Mock(_loop=self.loop.loop)

    def tearDown(self):
        self.loop.close()

    def _subscribe(self, client):
        asyncio.run(self.srv._handle_ws_message(client, "dash|subscribe|frames"))

    def test_only_subscribers_get_frames(self):
        sub, other = _Client(), _Client()
        self.srv.clients |= {sub, other}
        self._subscribe(sub)
        self.srv.broadcast_frame(b"PBF1frame")
        time.sleep(0.1)
        self.assertEqual(sub.sent, [b"PBF1frame"])
        self.assertEqual(other.sent, [])

    def test_slow_client_drops_frames_instead_of_queueing(self):
        gate = threading.Event()
        slow = _Client(gate)
        self._subscribe(slow)
        for i in range(5):
            self.srv.broadcast_frame(b"f%d" % i)
        gate.set()
        time.sleep(0.2)
        self.assertEqual(slow.sent, [b"f0"])
        self.srv.broadcast_frame(b"f5")
        time.sleep(0.1)
        self.assertEqual(slow.sent, [b"f0", b"f5"])

    def test_disconnect_unsubscribes(self):
        c = _Client()
        self._subscribe(c)
        self.srv._on_ws_client_disconnected(c)
        self.assertNotIn(c, self.srv.frame_clients)


class HttpTemplateTests(unittest.TestCase):
    def test_port_and_scheme_substituted(self):
        with TemporaryDirectory() as tmp:
            page = Path(tmp) / "index.html"
            page.write_text("REPLACE_WS_SCHEME://h:REPLACE_WS_PORT")
            srv = EmbeddedHTTPServer(lambda: 9000, 0, search_paths=[page],
                                     ws_scheme="wss")
            self.assertEqual(srv._read_index(9000), "wss://h:9000")


if __name__ == "__main__":
    unittest.main()
