import json
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np

from picobot.bot.maps import MapEntry, MapStore
from picobot.bot.rotation import Anchor
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
            "bot|", "map|", "cal|", "dash|", "host|",
            "events|", "config|", "layout|", "skills|", "movekeys|",
        ):
            self.assertIn(prefix, DASHBOARD_PREFIXES)


class SmartBotSignatureTests(unittest.TestCase):
    def test_accepts_event_bus_kwarg(self):
        """serve._bot_entry passes event_bus=; SmartBot must forward it."""
        import inspect

        from picobot.bot.smart_bot import SmartBot

        self.assertIn(
            "event_bus", inspect.signature(SmartBot.__init__).parameters
        )


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

    def test_layout_save_writes_named_map(self):
        feed = Mock()
        feed.minimap.region = (8, 40, 200, 150)
        self.host._feed = feed
        self.host._live_fingerprint = Mock(return_value="aa")
        with tempfile.TemporaryDirectory() as tmp:
            self.host.maps = MapStore(tmp)
            self.host.maps.save(MapEntry(name="m1"))
            self.assertTrue(self.host._handle_command("layout|save|m1"))
            saved = MapStore(tmp).get("m1")
            self.assertEqual(saved.minimap_region, (8, 40, 200, 150))
            self.assertEqual(saved.fingerprint, "aa")

    def test_layout_save_named_creates_stub(self):
        feed = Mock()
        feed.minimap.region = (8, 40, 200, 150)
        self.host._feed = feed
        self.host._live_fingerprint = Mock(return_value="aa")
        with tempfile.TemporaryDirectory() as tmp:
            self.host.maps = MapStore(tmp)
            self.assertTrue(self.host._handle_command("layout|save|newmap"))
            saved = MapStore(tmp).get("newmap")
            self.assertIsNotNone(saved)
            self.assertEqual(saved.minimap_region, (8, 40, 200, 150))

    def test_layout_save_ignores_stale_active_map_pin(self):
        # The reported bug: a persisted active_map overrode identity and
        # wrote a new map's layout into the previous map's file. The pin
        # must not count as evidence for a layout write.
        feed = Mock()
        feed.minimap.region = (8, 40, 200, 150)
        self.host._feed = feed
        self.host._live_fingerprint = Mock(return_value="aa")
        self.host._active_map_override = "m1"
        with tempfile.TemporaryDirectory() as tmp:
            self.host.maps = MapStore(tmp)
            # m1 has a fingerprint that does NOT match the live frame.
            self.host.maps.save(MapEntry(name="m1", fingerprint="ff" * 512))
            self.host._handle_command("layout|save")
            self.assertIsNone(MapStore(tmp).get("m1").minimap_region)
        msgs = [
            e["msg"] for e in self.host.bus.history() if e["kind"] == "error"
        ]
        self.assertTrue(any("no map verified" in m for m in msgs))

    def test_layout_save_verifies_fingerprint_match(self):
        # Auto path succeeds when the stored fingerprint matches live.
        feed = Mock()
        feed.minimap.region = (8, 40, 200, 150)
        self.host._feed = feed
        self.host._live_fingerprint = Mock(return_value="aa")
        with tempfile.TemporaryDirectory() as tmp:
            self.host.maps = MapStore(tmp)
            self.host.maps.save(MapEntry(name="m1", fingerprint="aa"))
            self.assertTrue(self.host._handle_command("layout|save"))
            self.assertEqual(
                MapStore(tmp).get("m1").minimap_region, (8, 40, 200, 150)
            )

    def test_layout_save_without_region_errors(self):
        feed = Mock()
        feed.minimap.region = None
        self.host._feed = feed
        self.host._handle_command("layout|save")
        kinds = [e["kind"] for e in self.host.bus.history()]
        self.assertIn("error", kinds)

    def test_layout_save_unverified_gives_clear_error(self):
        feed = Mock()
        feed.minimap.region = (8, 40, 200, 150)
        feed.minimap_img.return_value = None
        self.host._feed = feed
        self.host._active_map_override = None
        with tempfile.TemporaryDirectory() as tmp:
            self.host.maps = MapStore(tmp)
            self.host._handle_command("layout|save")
        msgs = [
            e["msg"] for e in self.host.bus.history() if e["kind"] == "error"
        ]
        self.assertTrue(any("can't verify" in m for m in msgs))

    def test_layout_clear_removes_stored(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.host.maps = MapStore(tmp)
            self.host.maps.save(
                MapEntry(name="m1", minimap_region=(1, 2, 3, 4))
            )
            self.host._handle_command("layout|clear|m1")
            self.assertIsNone(MapStore(tmp).get("m1").minimap_region)

    def test_idle_frame_reports_map_and_no_rotation(self):
        img = np.zeros((30, 40, 3), dtype=np.uint8)
        feed = Mock()
        feed.minimap_img.return_value = img
        feed.minimap.player_pos.return_value = None
        feed.minimap.region = (0, 0, 40, 30)
        self.host._feed = feed
        self.host._active_map_override = "m1"
        with tempfile.TemporaryDirectory() as tmp:
            self.host.maps = MapStore(tmp)
            self.host.maps.save(MapEntry(name="m1"))  # no anchors
            snap = self.host._provide_frame("minimap")
            self.assertEqual(snap["map"], "m1")
            self.assertTrue(snap["no_rotation"])
            entry = MapStore(tmp).get("m1")
            entry.rotation.anchors = [
                Anchor(name="a", x=0.5, y=0.5)
            ]
            self.host.maps.save(entry)
            self.host.maps.reload()
            self.host._map_res_ts = 0.0  # expire the resolution cache
            snap = self.host._provide_frame("minimap")
            self.assertFalse(snap["no_rotation"])

    def test_layout_reset_drops_region(self):
        feed = Mock()
        self.host._feed = feed
        self.assertTrue(self.host._handle_command("layout|reset"))
        feed.minimap.reset_region.assert_called_once()

    def test_layout_region_minimap_sets_and_persists(self):
        feed = Mock()
        self.host._feed = feed
        self.assertTrue(
            self.host._handle_command("layout|region|minimap|10,20,100,80")
        )
        feed.minimap.set_region.assert_called_once_with(
            (10, 20, 100, 80), explicit=True
        )
        self.assertEqual(
            self.host.bot_config.minimap_region, (10, 20, 100, 80)
        )
        self.assertEqual(
            self.host.config.bot["minimap_region"], [10, 20, 100, 80]
        )
        self.save_mock.assert_called()

    def test_layout_region_rejects_bad_payloads(self):
        feed = Mock()
        self.host._feed = feed
        self.host._handle_command("layout|region|minimap|a,b,c,d")
        self.host._handle_command("layout|region|minimap|1,2,3,4")  # too small
        self.host._handle_command("layout|region|title|5,8,300,40")
        feed.minimap.set_region.assert_not_called()
        self.save_mock.assert_not_called()

    def test_skills_set_persists_and_broadcasts(self):
        self.assertTrue(self.host._handle_command(
            'skills|set|{"name":"main","key":"a","kind":"attack",'
            '"cooldown":0}'))
        s = self.host.bot_config.skills["main"]
        self.assertEqual((s.key, s.kind, s.cooldown), ("a", "attack", 0.0))
        self.assertEqual(
            self.host.config.bot["skills"]["main"],
            {"key": "a", "kind": "attack"},
        )
        self.save_mock.assert_called()
        skills_msgs = [
            json.loads(m[len("dash|"):]) for m in self.sent
            if '"event": "skills"' in m
        ]
        self.assertTrue(skills_msgs)
        self.assertIn("main", skills_msgs[-1]["skills"])

    def test_skills_set_rejects_bad_spec(self):
        self.host._handle_command('skills|set|{"name":"x"}')  # no key
        self.assertNotIn("x", self.host.bot_config.skills)
        kinds = [e["kind"] for e in self.host.bus.history()]
        self.assertIn("error", kinds)

    def test_skills_del(self):
        from picobot.bot.skills import Skill

        self.host.bot_config.skills["main"] = Skill("main", "a")
        self.assertTrue(self.host._handle_command("skills|del|main"))
        self.assertNotIn("main", self.host.bot_config.skills)
        self.assertNotIn("main", self.host.config.bot["skills"])
        self.host._handle_command("skills|del|main")
        kinds = [e["kind"] for e in self.host.bus.history()]
        self.assertIn("error", kinds)

    def test_movekeys_set_persists(self):
        self.assertTrue(self.host._handle_command(
            'movekeys|set|{"jump_key":"space","up_jump_skill_key":"alt",'
            '"flash_jump_key":"","flash_jump_enabled":true}'))
        cfg = self.host.bot_config
        self.assertEqual(cfg.jump_key, "space")
        self.assertEqual(cfg.up_jump_skill_key, "alt")
        self.assertIsNone(cfg.flash_jump_key)
        self.assertTrue(cfg.flash_jump_enabled)
        bot_cfg = self.host.config.bot
        self.assertEqual(bot_cfg["jump_key"], "space")
        self.assertEqual(bot_cfg["up_jump_skill_key"], "alt")
        self.assertIsNone(bot_cfg["flash_jump"]["key"])
        self.save_mock.assert_called()

    def test_movekeys_set_nav_radius_and_weave(self):
        self.assertTrue(self.host._handle_command(
            'movekeys|set|{"nav_threshold_px":6,"dwell_weave":false}'))
        self.assertEqual(self.host.bot_config.nav_threshold_px, 6)
        self.assertFalse(self.host.bot_config.dwell_weave)
        self.assertEqual(self.host.config.bot["nav_threshold_px"], 6)
        self.assertFalse(self.host.config.bot["dwell_weave"])
        self.host._handle_command(
            'movekeys|set|{"nav_threshold_px":99}')  # clamped
        self.assertEqual(self.host.bot_config.nav_threshold_px, 15)

    def test_movekeys_set_blank_rope_restores_combo(self):
        self.host.bot_config.up_jump_skill_key = "alt"
        self.host._handle_command(
            'movekeys|set|{"up_jump_skill_key":""}')
        self.assertIsNone(self.host.bot_config.up_jump_skill_key)
        self.assertIsNone(self.host.config.bot["up_jump_skill_key"])

    def test_fps_command_updates_streamer_and_persists(self):
        self.assertTrue(self.host._handle_command("dash|fps|15"))
        self.assertAlmostEqual(self.host.streamer.interval, 1.0 / 15.0)
        self.assertEqual(self.host.config.view_fps, 15.0)
        self.save_mock.assert_called()
        cfg_msgs = [
            json.loads(m[len("dash|"):]) for m in self.sent
            if m.startswith("dash|")
        ]
        self.assertIn(
            15.0,
            [m.get("config", {}).get("view_fps") for m in cfg_msgs],
        )

    def test_fps_command_clamps(self):
        self.host._handle_command("dash|fps|999")
        self.assertEqual(self.host.config.view_fps, 30.0)
        self.host._handle_command("dash|fps|bogus")
        kinds = [e["kind"] for e in self.host.bus.history()]
        self.assertIn("error", kinds)

    def test_skills_commit_updates_running_bot_live(self):
        from picobot.bot.skills import Skill, SkillBook

        bot = Mock()
        bot._map = None
        bot.skills = SkillBook({"old": Skill("old", "z")})
        self.host.bot = bot
        self.host._handle_command(
            'skills|set|{"name":"main","key":"a","kind":"attack"}')
        self.assertIn("main", bot.skills.skills)
        self.assertNotIn("old", bot.skills.skills)

    def test_bot_start_shares_feed_analyzer(self):
        feed = Mock()
        feed.minimap = Mock()
        self.host._feed = feed
        with patch("picobot.bot.SmartBot") as sb, \
             patch("picobot.bot.HidController"):
            self.host._bot_entry()
        self.assertEqual(sb.call_args.kwargs["minimap"], feed.minimap)

    def test_layout_source_reports_provenance(self):
        feed = Mock()
        feed.minimap.region = (0, 0, 100, 100)
        feed.minimap.region_source = "stored"
        self.host._feed = feed
        self.assertEqual(self.host._layout_source(), "stored")

    def test_cal_finish_blank_name_uses_resolved_map(self):
        cal = Mock()
        cal.finish = Mock(side_effect=lambda n, **kw: MapEntry(name=n))
        self.host.calibrator = cal
        feed = Mock()
        feed.minimap_img.return_value = None
        feed.minimap.region = (0, 0, 100, 100)
        self.host._feed = feed
        self.host._active_map_override = "detected_map"
        with tempfile.TemporaryDirectory() as tmp:
            self.host.maps = MapStore(tmp)
            self.host.maps.save(MapEntry(name="detected_map"))
            self.host._cal_finish("")
            self.assertEqual(MapStore(tmp).names(), ["detected_map"])
        self.assertEqual(cal.finish.call_args[0][0], "detected_map")

    def test_cal_finish_explicit_name_wins(self):
        cal = Mock()
        cal.finish = Mock(side_effect=lambda n, **kw: MapEntry(name=n))
        self.host.calibrator = cal
        self.host._get_feed = Mock(return_value=None)
        with tempfile.TemporaryDirectory() as tmp:
            self.host.maps = MapStore(tmp)
            self.host._cal_finish("my_map")
            self.assertEqual(MapStore(tmp).names(), ["my_map"])
        self.assertEqual(cal.finish.call_args[0][0], "my_map")

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
