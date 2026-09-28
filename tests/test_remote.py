import asyncio
import threading
import time
import unittest

import numpy as np
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from picobot.remote.control import RemoteCallbacks, RemoteControlServer
from picobot.remote.http import EmbeddedHTTPServer


def _callbacks(**kw):
    base = dict(
        schedule=lambda fn: fn(), log=lambda m: None, set_status=lambda m: None,
        set_ws_port=lambda p: None,
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
        self.closed = None

    async def close(self, code=1000, reason=""):
        self.closed = (code, reason)

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

    def test_stuck_frame_send_closes_the_client(self):
        stuck = _Client(threading.Event())          # never reads
        self._subscribe(stuck)
        self.srv.FRAME_STALL_S = 0.05
        self.srv.broadcast_frame(b"f0")
        time.sleep(0.1)
        self.srv.broadcast_frame(b"f1")
        time.sleep(0.1)
        self.assertEqual(stuck.closed[0], 1011)
        self.assertNotIn(stuck, self.srv.frame_clients)

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


class HeldKeyTests(unittest.TestCase):
    def test_keys_held_by_a_dropped_client_are_released(self):
        srv = RemoteControlServer("", 0, _callbacks(), serial_manager=Mock())
        c = Mock()
        for m in ("key|down|left", "key|down|alt", "key|up|alt"):
            asyncio.run(srv._handle_ws_message(c, m))
        while not srv.cmd_queue.empty():
            srv.cmd_queue.get_nowait()
        srv._on_ws_client_disconnected(c)
        sent = [srv.cmd_queue.get_nowait()[0] for _ in range(srv.cmd_queue.qsize())]
        self.assertEqual(sent, ["hid|key|up|left"])

    def test_bot_query_replies_with_dash_event(self):
        srv = RemoteControlServer(
            "", 0, _callbacks(is_bot_running=lambda: True),
            serial_manager=Mock(),
        )
        c = _Client()
        asyncio.run(srv._handle_ws_message(c, "bot|query"))
        self.assertEqual(c.sent, ['dash|{"event": "bot", "running": true}'])

    def test_has_frame_clients(self):
        srv = RemoteControlServer("", 0, _callbacks(), serial_manager=Mock())
        self.assertFalse(srv.has_frame_clients())
        asyncio.run(srv._handle_ws_message(Mock(), "dash|subscribe|frames"))
        self.assertTrue(srv.has_frame_clients())


class HttpStaticTests(unittest.TestCase):
    def _dist(self, tmp):
        dist = Path(tmp) / "dist"
        (dist / "assets").mkdir(parents=True)
        (dist / "index.html").write_text(
            '<meta name="pb-ws-port" content="REPLACE_WS_PORT">'
        )
        (dist / "favicon.svg").write_text("<svg/>")
        (dist / "assets" / "app.js").write_text("js")
        (Path(tmp) / "secret.txt").write_text("no")
        return dist

    def test_root_files_assets_and_spa_fallback(self):
        with TemporaryDirectory() as tmp:
            port = [8765]
            srv = EmbeddedHTTPServer(lambda: port[0], 0, static_dir=self._dist(tmp))
            self.assertEqual(srv._serve_static("/favicon.svg")[1], b"<svg/>")
            self.assertEqual(srv._serve_static("/assets/app.js")[1], b"js")
            port[0] = 8766          # WS fell back to the next port
            ctype, body = srv._serve_static("/some/route")
            self.assertTrue(ctype.startswith("text/html"))
            self.assertIn(b'content="8766"', body)
            ctype, body = srv._serve_static("/../secret.txt")
            self.assertNotIn(b"no", body)


class StreamerIdleTests(unittest.TestCase):
    def test_no_capture_without_viewers(self):
        from picobot.remote.streamer import FrameStreamer

        provider = Mock(return_value={})
        st = FrameStreamer(provider, Mock(), interval=0.01, active=lambda: False)
        st.start()
        time.sleep(0.1)
        st.stop()
        provider.assert_not_called()


class StreamerWatchdogTests(unittest.TestCase):
    def _run(self, st, seconds=0.3):
        st.start()
        time.sleep(seconds)
        st.stop()

    def test_persistent_failure_reports_a_stall(self):
        from picobot.remote.streamer import FrameStreamer

        stalls = []
        st = FrameStreamer(Mock(side_effect=RuntimeError("window gone")), Mock(),
                           interval=0.01, on_stall=stalls.append,
                           stall_after=0.05)
        self._run(st)
        self.assertTrue(stalls)
        self.assertIn("window gone", stalls[0])

    def test_no_capture_counts_as_failure_but_no_image_does_not(self):
        from picobot.remote.streamer import FrameStreamer

        stalls = []
        st = FrameStreamer(Mock(return_value={"state": "IDLE"}), Mock(),
                           interval=0.01, on_stall=stalls.append,
                           stall_after=0.05)
        self._run(st)
        self.assertEqual(stalls, [])               # panel not located: normal
        st = FrameStreamer(Mock(return_value=None), Mock(), interval=0.01,
                           on_stall=stalls.append, stall_after=0.05)
        self._run(st)
        self.assertTrue(stalls)

    def test_nothing_kills_the_thread(self):
        from picobot.remote.streamer import FrameStreamer

        calls = {"n": 0}

        def active():
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("boom")
            return True

        send = Mock()
        img = np.zeros((4, 4, 3), dtype=np.uint8)
        st = FrameStreamer(Mock(return_value={"img": img}), send,
                           interval=0.01, active=active)
        self._run(st, 0.2)
        send.assert_called()                       # recovered after raising


if __name__ == "__main__":
    unittest.main()
