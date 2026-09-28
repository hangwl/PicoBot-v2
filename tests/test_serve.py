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

    def test_level_defaults_from_kind(self):
        bus = EventBus()
        bus.subscribe(lambda e: None)
        ev = bus.emit("hid", "key|a|press")
        self.assertEqual(ev["level"], "debug")
        for kind, expected in (
            ("log", "info"), ("nav", "info"), ("notify", "warn"),
            ("safety", "warn"), ("error", "error"),
        ):
            self.assertEqual(bus.emit(kind, "m")["level"], expected)

    def test_level_explicit_and_history(self):
        bus = EventBus()
        ev = bus.emit("nav", "careful", level="warn")
        self.assertEqual(ev["level"], "warn")
        # An unknown level falls back to the kind default.
        self.assertEqual(bus.emit("log", "m", level="nope")["level"], "info")
        self.assertTrue(all("level" in it for it in bus.history()))


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
        self.assertTrue(data.startswith(b"\xff\xd8"))  # JPEG SOI

    def test_frame_pack_roundtrip(self):
        from picobot.remote.streamer import pack_frame, unpack_frame

        meta = {"event": "frame", "map": "WLOH", "ox": 3}
        data = pack_frame(meta, b"\xff\xd8jpeg")
        self.assertTrue(data.startswith(b"PBF1"))
        self.assertEqual(unpack_frame(data), (meta, b"\xff\xd8jpeg"))
        with self.assertRaises(ValueError):
            unpack_frame(b"nope")


class PanelAssemblyTests(unittest.TestCase):
    @staticmethod
    def _band(title_right=180):
        # 160x400 window band: two sparse text lines (glyph-like 8px
        # runs with gaps — a solid block would read as the divider),
        # solid divider row at y=70 spanning 250px.
        band = np.zeros((160, 400, 3), dtype=np.uint8)
        for y0, x1 in ((30, min(150, title_right)), (45, title_right)):
            for x in range(50, x1, 14):
                band[y0 : y0 + 10, x : x + 8] = 255
        band[70, :250] = 255
        return band

    def test_panel_extends_to_title_and_separates_map(self):
        from picobot.remote.streamer import assemble_panel

        band = self._band()
        map_img = np.full((100, 200, 3), 40, dtype=np.uint8)
        map_img[10, 5] = (1, 2, 3)
        # Map frame below the divider, narrower + right-shifted vs band.
        comp, dx, dy = assemble_panel(band, map_img, (10, 75, 200, 100))
        # y0 = title top - pad (23); map at dy=52; the right edge
        # reaches the divider's end (250+pad) — past both the map's
        # 210 and the detected text's end, so a faded title tail
        # can't be clipped mid-glyph.
        self.assertEqual(comp.shape, (152, 246, 3))
        self.assertEqual((dx, dy), (0, 52))
        self.assertTrue((comp[62, 5] == (1, 2, 3)).all())  # map px moved
        # Separator line (cyan) at the divider row (70-23=47).
        self.assertTrue((comp[47, 100] == (255, 200, 40)).all())
        self.assertTrue((comp[46, 100] != (255, 200, 40)).any())
        # Title glyph pixel (window x=52,y=35) shows above the map.
        self.assertTrue((comp[12, 42] == 255).all())

    def test_panel_extends_right_for_long_title(self):
        from picobot.remote.streamer import assemble_panel

        # Long name reaching x=320 — past the 200px map frame's right
        # edge; the panel view must grow to cover it.
        band = self._band(title_right=320)
        band[70, :] = 0
        band[70, :350] = 255          # divider is panel-wide
        map_img = np.full((100, 200, 3), 40, dtype=np.uint8)
        comp, dx, dy = assemble_panel(band, map_img, (10, 75, 200, 100))
        self.assertGreater(comp.shape[1], 210)

    def test_panel_falls_back_to_map_when_no_title(self):
        from picobot.remote.streamer import assemble_panel

        map_img = np.full((100, 200, 3), 40, dtype=np.uint8)
        comp, dx, dy = assemble_panel(None, map_img, (10, 75, 200, 100))
        self.assertEqual((dx, dy), (0, 0))
        # Interior is the map unchanged; only the border is annotated.
        self.assertTrue((comp[2:-2, 2:-2] == map_img[2:-2, 2:-2]).all())

    def test_offset_meta_shifts_region_coords(self):
        from picobot.serve import _offset_meta

        snap = {
            "platforms": [(10, 60, 100, 60)],
            "anchors": [(40, 50)],
            "player": (80, 50),
            "target": (120, 50),
            "rune": (60, 30),
        }
        snap["ropes"] = [(10, 60, 12, 20)]
        _offset_meta(snap, 7, 52)
        self.assertEqual(snap["platforms"], [(17, 112, 107, 112)])
        self.assertEqual(snap["ropes"], [(17, 112, 19, 72)])
        self.assertEqual(snap["anchors"], [(47, 102)])
        self.assertEqual(snap["player"], (87, 102))
        self.assertEqual(snap["target"], (127, 102))
        self.assertEqual(snap["rune"], (67, 82))


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
            "bot|", "map|", "measure|", "dash|", "host|",
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
        self.host.identity.threaded = False
        self.sent = []
        self.host.remote.broadcast = self.sent.append

    def tearDown(self):
        self.save_patch.stop()

    def _use_store(self, tmp, pin=None):
        store = MapStore(tmp)
        self.host.maps = self.host.identity.store = store
        self.host.identity.pin = pin
        self.host.identity._recompute()
        return store

    def _read_title(self, title):
        ident = self.host.identity
        ident._reader = Mock(read=Mock(return_value=title))
        ident.retry_s = 0.0
        ident.request("test")
        for _ in range(ident.max_reads):
            ident.pump(lambda: object())

    def test_provide_frame_minimap_emits_panel_offset(self):
        # With a real title band the feed path composites the panel and
        # reports the minimap's offset inside it.
        img = np.zeros((150, 200, 3), dtype=np.uint8)
        feed = Mock()
        feed.minimap_img.return_value = img
        feed.minimap.player_pos.return_value = (100, 75)
        feed.minimap.region = (10, 75, 200, 150)
        feed.name_region.return_value = (0, 0, 300, 225)
        feed.name_img.return_value = PanelAssemblyTests._band()
        self.host._feed = feed
        snap = self.host._provide_frame("minimap")
        self.assertIn("ox", snap)
        self.assertEqual(snap["ox"], 0)
        self.assertGreater(snap["oy"], 0)
        self.assertEqual(snap["player"], (100, 75 + snap["oy"]))

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
        with tempfile.TemporaryDirectory() as tmp:
            self._use_store(tmp)
            self.host.maps.save(MapEntry(name="m1"))
            self.assertTrue(self.host._handle_command("layout|save|m1"))
            saved = MapStore(tmp).get("m1")
            self.assertEqual(saved.minimap_region, (8, 40, 200, 150))

    def test_layout_save_named_creates_stub(self):
        feed = Mock()
        feed.minimap.region = (8, 40, 200, 150)
        self.host._feed = feed
        with tempfile.TemporaryDirectory() as tmp:
            self._use_store(tmp)
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
        with tempfile.TemporaryDirectory() as tmp:
            self._use_store(tmp, pin="m1")
            self.host.maps.save(MapEntry(name="m1"))
            self.host.identity.refresh()
            self.host._handle_command("layout|save")
            self.assertIsNone(MapStore(tmp).get("m1").minimap_region)
        msgs = [
            e["msg"] for e in self.host.bus.history() if e["kind"] == "error"
        ]
        self.assertTrue(any("isn't verified" in m for m in msgs))

    def test_wall_set_verifies_map_identity_when_blank(self):
        # Blank name must still verify — a stale pin must not write
        # walls into the wrong map file.
        feed = Mock()
        feed.minimap_img.return_value = np.zeros((150, 200, 3), dtype=np.uint8)
        feed.minimap.player_pos.return_value = (50, 40)
        feed.minimap.region = (0, 0, 200, 150)
        self.host._feed = feed
        with tempfile.TemporaryDirectory() as tmp:
            self._use_store(tmp)
            self.host.maps.save(MapEntry(name="m1"))
            self.host._handle_command("layout|wall|left")
            self.assertIsNone(MapStore(tmp).get("m1").walls)

    def test_floor_side_is_rejected(self):
        # The floor boundary is gone: down-jumps only exist toward drawn
        # platforms below, so the graph needs no bottom line.
        with tempfile.TemporaryDirectory() as tmp:
            self._use_store(tmp)
            self.host.maps.save(MapEntry(name="m1"))
            self.assertFalse(
                self.host._handle_command("layout|wall|floor|m1")
            )
            self.assertIsNone(MapStore(tmp).get("m1").walls)

    def test_class_use_applies_profile_and_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._use_store(tmp)
            self.host.bot_config.class_profiles = {
                "mage": {"travel": "teleport", "air_attacks": False,
                         "teleport_key": "shift"},
            }
            self.host._class_use("mage")
            cfg = self.host.bot_config
            self.assertEqual(cfg.class_active, "mage")
            self.assertEqual(cfg.class_travel, "teleport")
            self.assertFalse(cfg.air_attacks)
            self.assertEqual(cfg.teleport_key, "shift")
            self.assertEqual(
                (self.host.config.bot or {}).get("class", {}).get("active"),
                "mage",
            )
            msgs = [e["msg"] for e in self.host.bus.history()
                    if e["kind"] == "bot"]
            self.assertTrue(any("class: mage" in m for m in msgs))

    def test_profile_skills_applied_on_class_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._use_store(tmp)
            self.host.bot_config.class_profiles = {
                "mage": {"travel": "teleport", "teleport_key": "shift",
                         "skills": {"blink": {"key": "shift",
                                              "kind": "movement"}}},
            }
            self.host._class_use("mage")
            self.assertEqual(
                [s.key for s in self.host.bot_config.skills.values()],
                ["shift"],
            )

    def test_skills_panel_edits_the_active_profile_kit(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._use_store(tmp)
            self.host.bot_config.class_profiles = {
                "mage": {"travel": "teleport", "teleport_key": "shift"},
            }
            self.host._class_use("mage")
            self.host._handle_command(
                'skills|set|{"name":"main","key":"a","kind":"attack"}')
            profile = self.host.bot_config.class_profiles["mage"]
            self.assertIn("main", profile["skills"])     # into the profile
            self.assertEqual(
                self.host.bot_config.skills["main"].key, "a")
            saved = (self.host.config.bot or {}).get("class", {}) \
                .get("profiles", {}).get("mage", {}).get("skills", {})
            self.assertIn("main", saved)                # persisted

    def test_class_add_creates_persists_and_applies(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._use_store(tmp)
            self.host._handle_command(
                'class|add|mage|{"travel":"teleport","air_attacks":false,'
                '"teleport_key":"shift"}')
            cfg = self.host.bot_config
            self.assertEqual(cfg.class_profiles["mage"],
                             {"travel": "teleport", "air_attacks": False,
                              "teleport_key": "shift"})
            self.assertEqual(cfg.class_active, "mage")
            self.assertEqual(cfg.teleport_key, "shift")
            self.assertEqual(
                (self.host.config.bot or {}).get("class", {})
                .get("profiles", {}).get("mage", {}).get("travel"),
                "teleport",
            )

    def test_class_add_rejects_duplicates_and_bad_json(self):
        self.host.bot_config.class_profiles = {"mage": {}}
        self.host._handle_command("class|add|mage|{}")
        self.host._handle_command("class|add|other|not-json")
        self.assertNotIn("other", self.host.bot_config.class_profiles)
        msgs = [
            e["msg"] for e in self.host.bus.history() if e["kind"] == "error"
        ]
        self.assertTrue(any("already exists" in m for m in msgs))
        self.assertTrue(any("bad class spec" in m for m in msgs))

    def test_class_use_unknown_profile_errors(self):
        self.host._class_use("nope")
        msgs = [
            e["msg"] for e in self.host.bus.history() if e["kind"] == "error"
        ]
        self.assertTrue(any("no class profile" in m for m in msgs))

    def test_patrol_policy_set_persists(self):
        self.host._patrol_policy_set("greedy")
        self.assertEqual(self.host.bot_config.patrol_policy, "greedy")
        self.host._patrol_policy_set(temp="0.7")
        self.assertEqual(self.host.bot_config.patrol_weight_temp, 0.7)
        self.assertEqual(
            (self.host.config.bot or {}).get("patrol_policy"), "greedy")

    def test_patrol_command_removed(self):
        # Patrol is the only multi-anchor mode now — the toggle is gone.
        self.assertFalse(
            self.host._handle_command("layout|patrol|on|m1")
        )

    def test_platform_draw_undo_clear(self):
        img = np.zeros((150, 200, 3), dtype=np.uint8)
        feed = Mock()
        feed.minimap_img.return_value = img
        self.host._feed = feed
        with tempfile.TemporaryDirectory() as tmp:
            self._use_store(tmp)
            self.host.maps.save(MapEntry(name="m1"))
            self.assertTrue(
                self.host._handle_command("layout|plat|10,40,100,40|m1")
            )
            saved = MapStore(tmp).get("m1")
            # Stored normalized: x over w=200, y over h=150.
            self.assertEqual(
                saved.platforms, [[0.05, round(40 / 150, 4), 0.5, round(40 / 150, 4)]]
            )
            self.host._handle_command("layout|plat|20,60,150,60|m1")
            self.assertEqual(len(MapStore(tmp).get("m1").platforms), 2)
            self.host._handle_command("layout|plat|undo|m1")
            self.assertEqual(len(MapStore(tmp).get("m1").platforms), 1)
            self.host._handle_command("layout|plat|clear|m1")
            self.assertIsNone(MapStore(tmp).get("m1").platforms)

    def test_platform_draw_ignores_accidental_click(self):
        img = np.zeros((150, 200, 3), dtype=np.uint8)
        feed = Mock()
        feed.minimap_img.return_value = img
        self.host._feed = feed
        with tempfile.TemporaryDirectory() as tmp:
            self._use_store(tmp)
            self.host.maps.save(MapEntry(name="m1"))
            self.host._handle_command("layout|plat|50,50,51,51|m1")
            self.assertIsNone(MapStore(tmp).get("m1").platforms)

    def test_map_meta_reports_graph_platforms(self):
        # The overlay shows the graph's clipped platforms — wall/floor
        # zones eat platform ends and the user must see it.
        entry = MapEntry(
            name="m1", platforms=[[0.05, 0.25, 0.5, 0.25]],
        )
        feed = Mock()
        feed.minimap.region = (0, 0, 200, 150)
        self.host._feed = feed
        self.host._resolved_entry = Mock(return_value=entry)
        with tempfile.TemporaryDirectory() as tmp:
            self._use_store(tmp)
            self.host.maps.save(entry)
            meta = self.host._map_meta()
        self.assertEqual(meta["platforms"], [(10.0, 37.5, 100.0, 37.5)])

    def test_map_meta_reports_ropes(self):
        entry = MapEntry(name="m1", platforms=[[0.0, 0.5, 1.0, 0.5]],
                         ropes=[[0.49, 2/3, 0.51, 4/15]])
        feed = Mock()
        feed.minimap.region = (0, 0, 200, 150)
        self.host._feed = feed
        self.host._resolved_entry = Mock(return_value=entry)
        with tempfile.TemporaryDirectory() as tmp:
            self._use_store(tmp)
            self.host.maps.save(entry)
            meta = self.host._map_meta()
        self.assertEqual(meta["ropes"], [(98, 100, 102, 40)])

    def _region_feed(self):
        feed = Mock()
        feed.minimap.region = (0, 0, 200, 150)
        self.host._feed = feed
        return feed

    def test_maps_payload_reports_detected_title_and_score(self):
        self._region_feed()
        with tempfile.TemporaryDirectory() as tmp:
            self._use_store(tmp).save(
                MapEntry(name="m1", map_name="Chu Chu Island Chu Chu Village")
            )
            self._read_title("Chu Chu Island Chu Chu Village")
            self.host._send_maps()
        payload = json.loads(self.sent[-1][5:])
        self.assertEqual(payload["event"], "maps")
        self.assertEqual(payload["detected"], "m1")
        self.assertEqual(payload["via"], "ocr")
        self.assertEqual(payload["title"], "Chu Chu Island Chu Chu Village")
        self.assertEqual(payload["score"], 1.0)

    def test_map_meta_reports_title_confidence(self):
        self._region_feed()
        with tempfile.TemporaryDirectory() as tmp:
            self._use_store(tmp).save(MapEntry(name="m1", map_name="Arcana Cave"))
            self._read_title("Arcana Cave")
            meta = self.host._map_meta()
        self.assertEqual(meta["map"], "m1")
        self.assertEqual(meta["map_via"], "ocr")
        self.assertEqual(meta["map_conf"], 1.0)

    def test_map_meta_pin_only_has_no_confidence(self):
        self._region_feed()
        with tempfile.TemporaryDirectory() as tmp:
            self._use_store(tmp, pin="m1").save(MapEntry(name="m1"))
            self.host.identity.refresh()
            meta = self.host._map_meta()
        self.assertEqual(meta["map"], "m1")
        self.assertEqual(meta["map_via"], "pin")
        self.assertIsNone(meta["map_conf"])

    def test_map_meta_title_beats_stale_pin(self):
        self._region_feed()
        with tempfile.TemporaryDirectory() as tmp:
            store = self._use_store(tmp, pin="oldmap")
            store.save(MapEntry(name="oldmap", map_name="Kerning Square"))
            store.save(MapEntry(name="m1", map_name="Arcana Cave"))
            self._read_title("Arcana Cave")
            meta = self.host._map_meta()
        self.assertEqual(meta["map"], "m1")

    def _nav_setup(self, tmp, player=(20, 100)):
        feed = self._region_feed()
        feed.minimap_img.return_value = np.zeros((150, 200, 3), np.uint8)
        feed.minimap.player_pos.return_value = player
        self._use_store(tmp, pin="m1").save(MapEntry(name="m1", platforms=[
            [0.0, 100 / 150, 1.0, 100 / 150],          # floor y=100
            [0.2, 84 / 150, 0.6, 84 / 150],            # ledge 16px up
        ]))
        self.host.identity.refresh()

    def test_nav_preview_plans_route_into_meta(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._nav_setup(tmp)
            self.assertTrue(self.host._handle_command("nav|preview|80,84"))
            meta = self.host._map_meta()
        kinds = [leg[0] for leg in meta["nav_route"]]
        self.assertTrue({"up_flash", "rope_lift"} & set(kinds))
        self.assertTrue(any(e[0] == "down_jump" for e in meta["nav_edges"]))
        evts = [e["msg"] for e in self.host.bus.history() if e["kind"] == "nav"]
        self.assertTrue(any("route:" in m for m in evts))

    def test_nav_show_off_hides_overlay(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._nav_setup(tmp)
            self.host._handle_command("nav|preview|80,84")
            self.host._handle_command("nav|show|off")
            meta = self.host._map_meta()
        self.assertIsNone(meta["nav_edges"])
        self.assertIsNone(meta["nav_route"])

    def test_nav_preview_errors_without_platforms(self):
        self._region_feed()
        self.host._handle_command("nav|preview|10,10")
        errs = [e["msg"] for e in self.host.bus.history() if e["kind"] == "error"]
        self.assertTrue(any("drawn platforms" in m for m in errs))

    def test_offset_meta_shifts_nav_overlay(self):
        from picobot.serve import _offset_meta

        snap = {"nav_route": [("walk", 1, 2, 3, 4)], "nav_edges": None}
        _offset_meta(snap, 10, 20)
        self.assertEqual(snap["nav_route"], [("walk", 11, 22, 13, 24)])

    def test_anchor_place_snaps_and_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._nav_setup(tmp)
            self.host._handle_command("layout|anchor|20,95|m1")    # near floor
            self.host._handle_command("layout|anchor|80,80|m1")    # near ledge
            self.host._handle_command("layout|anchor|190,20|m1")   # mid-air
            rot = MapStore(tmp).get("m1").rotation
            self.assertEqual([a.name for a in rot.anchors], ["a0", "a1", "a2"])
            self.assertEqual(rot.anchors[0].y, round(96 / 150, 4))   # floats 4px
            self.assertEqual(rot.anchors[1].y, round(80 / 150, 4))
            self.assertEqual(rot.anchors[2].y, round(20 / 150, 4))   # left as-is
            self.host._handle_command("layout|anchor|del|78,84|m1")
            names = [a.name for a in MapStore(tmp).get("m1").rotation.anchors]
            self.assertEqual(names, ["a0", "a2"])
            self.host._handle_command("layout|anchor|20,95|m1")
            self.assertEqual(MapStore(tmp).get("m1").rotation.anchors[-1].name, "a1")
            self.host._handle_command("layout|anchor|undo|m1")
            self.assertEqual(len(MapStore(tmp).get("m1").rotation.anchors), 2)
            self.host._handle_command("layout|anchor|clear|m1")
            self.assertEqual(MapStore(tmp).get("m1").rotation.anchors, [])
        msgs = [e["msg"] for e in self.host.bus.history() if e["kind"] == "map"]
        self.assertTrue(any("no drawn platform under it" in m for m in msgs))

    def test_layout_save_backfills_title(self):
        feed = self._region_feed()
        feed.minimap.region = (8, 40, 200, 150)
        with tempfile.TemporaryDirectory() as tmp:
            self._use_store(tmp).save(MapEntry(name="WLOH"))
            self._read_title("Lake of Oblivion Weathered Land of Happiness")
            self.host._handle_command("layout|save|WLOH")
            saved = MapStore(tmp).get("WLOH")
        self.assertEqual(
            saved.map_name, "Lake of Oblivion Weathered Land of Happiness"
        )
        self.assertEqual(self.host.identity.current.name, "WLOH")

    def test_platform_overlay_draws_stored_segments(self):
        # Drawn platforms ride the frame meta and annotate() renders them.
        img = np.zeros((150, 200, 3), dtype=np.uint8)
        feed = Mock()
        feed.minimap_img.return_value = img
        feed.minimap.player_pos.return_value = None
        feed.minimap.region = (0, 0, 200, 150)
        feed.name_region.return_value = None
        self.host._feed = feed
        with tempfile.TemporaryDirectory() as tmp:
            self._use_store(tmp, pin="m1")
            self.host.maps.save(MapEntry(
                name="m1", platforms=[[0.05, 0.4, 0.5, 0.4]]
            ))
            self.host.identity.refresh()
            snap = self.host._provide_frame("minimap")
        self.assertEqual(snap["platforms"], [(10.0, 60.0, 100.0, 60.0)])
        out = annotate(img, snap)
        self.assertTrue((out[60, 50] != img[60, 50]).any())   # line drawn
        self.assertTrue((out[70, 50] == img[70, 50]).all())   # rest clean

    def test_layout_save_blank_uses_title_verified_map(self):
        feed = Mock()
        feed.minimap.region = (8, 40, 200, 150)
        self.host._feed = feed
        with tempfile.TemporaryDirectory() as tmp:
            self._use_store(tmp).save(MapEntry(name="m1", map_name="Arcana Cave"))
            self._read_title("Arcana Cave")
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
        with tempfile.TemporaryDirectory() as tmp:
            self._use_store(tmp)
            self.host._handle_command("layout|save")
        msgs = [
            e["msg"] for e in self.host.bus.history() if e["kind"] == "error"
        ]
        self.assertTrue(any("isn't verified" in m for m in msgs))

    def test_layout_clear_removes_stored(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._use_store(tmp)
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
        feed.name_region.return_value = None
        self.host._feed = feed
        with tempfile.TemporaryDirectory() as tmp:
            self._use_store(tmp, pin="m1")
            self.host.maps.save(MapEntry(name="m1"))  # no anchors
            self.host.identity.refresh()
            snap = self.host._provide_frame("minimap")
            self.assertEqual(snap["map"], "m1")
            self.assertTrue(snap["no_rotation"])
            entry = MapStore(tmp).get("m1")
            entry.rotation.anchors = [
                Anchor(name="a", x=0.5, y=0.5)
            ]
            self.host.maps.save(entry)
            self.host.maps.reload()
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
        self.host._handle_command("layout|region|garbage|5,8,300,40")
        feed.minimap.set_region.assert_not_called()
        self.save_mock.assert_not_called()

    def test_layout_region_title_saves_name_region(self):
        feed = Mock()
        self.host._feed = feed
        self.host._handle_command("layout|region|title|5,8,300,40")
        self.assertEqual(self.host.bot_config.minimap_name_region,
                         (5, 8, 300, 40))
        self.assertEqual(self.host.config.bot["minimap_name_region"],
                         [5, 8, 300, 40])
        self.save_mock.assert_called()
        self.assertTrue(self.host.identity.pending)

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

    def test_measure_start_requires_stopped_bot(self):
        self.host.bot = Mock()
        self.host._measure_start()
        errs = [e["msg"] for e in self.host.bus.history() if e["kind"] == "error"]
        self.assertTrue(any("stop the bot" in m for m in errs))
        self.assertIsNone(self.host.measurer)

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
