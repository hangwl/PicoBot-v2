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
        # anchor (blue 255,128,0), player (green), target (red). Feet
        # points: the 6x6 dot sits above them (x-3..x+2, y-5..y), framed
        # with a 1px margin — the frame runs y-6..y+1, x-4..x+3.
        self.assertTrue((out[44, 40] == (255, 128, 0)).all())      # anchor top
        self.assertTrue((out[51, 40] == (255, 128, 0)).all())      # anchor bottom
        self.assertTrue((out[44, 80] == (0, 255, 0)).all())        # player top
        self.assertTrue((out[51, 80] == (0, 255, 0)).all())        # just below feet
        self.assertFalse((out[52, 80] == (0, 255, 0)).all())       # not 4px below
        self.assertTrue((out[48, 76] == (0, 255, 0)).all())        # left side
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
        # Never read or write the project's real nav_reach.json.
        from picobot.bot.reach import ReachModel, base_reach

        self.host.reach = ReachModel(base_reach(self.host.bot_config))
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
        feed.minimap.player_box.return_value = (97, 70, 102, 75)
        feed.minimap.region = (10, 75, 200, 150)
        feed.name_region.return_value = (0, 0, 300, 225)
        feed.name_img.return_value = PanelAssemblyTests._band()
        self.host._feed = feed
        snap = self.host._provide_frame("minimap")
        self.assertIn("ox", snap)
        self.assertEqual(snap["ox"], 0)
        self.assertGreater(snap["oy"], 0)
        self.assertEqual(snap["player"], (100, 75 + snap["oy"]))

    def test_provide_frame_player_box_is_offset_with_the_panel(self):
        from picobot.vision.minimap import MinimapAnalyzer

        img = np.zeros((150, 200, 3), dtype=np.uint8)
        feed = Mock()
        feed.minimap_img.return_value = img
        feed.minimap.player_pos.return_value = (100, 75)
        feed.minimap.player_box = MinimapAnalyzer().player_box
        feed.minimap.region = (10, 75, 200, 150)
        feed.name_region.return_value = (0, 0, 300, 225)
        feed.name_img.return_value = PanelAssemblyTests._band()
        self.host._feed = feed
        snap = self.host._provide_frame("minimap")
        oy = snap["oy"]
        self.assertEqual(snap["player_box"], (97, 70 + oy, 102, 75 + oy))

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

    def test_startup_loads_the_active_profile_reach_file(self):
        cfg = AppConfig()
        cfg.bot = {"class": {"active": "mage",
                             "profiles": {"mage": {"travel": "teleport"}}}}
        with patch("picobot.bot.reach.ReachModel.load"):
            host = BotHost(None, "OldWin", config=cfg)
        self.assertEqual(host.reach.path.name, "nav_reach_mage.json")

    def test_class_use_swaps_to_the_profile_reach_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._use_store(tmp)
            self.host.bot_config.class_profiles = {
                "mage": {"travel": "teleport", "teleport_key": "shift"},
                "hero": {"travel": "flash"},
            }
            self.host._class_use("mage")
            self.assertTrue(
                self.host.reach.path.name.endswith("nav_reach_mage.json"))
            # Each profile measures and learns independently.
            self.host._class_use("hero")
            self.assertTrue(
                self.host.reach.path.name.endswith("nav_reach_hero.json"))

    def test_profile_movekeys_applied_on_class_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._use_store(tmp)
            self.host.bot_config.class_profiles = {
                "mage": {"travel": "teleport", "teleport_key": "shift",
                         "jump_key": "space", "up_jump_skill_key": "alt",
                         "flash_jump": {"key": None, "enabled": False}},
            }
            self.host._class_use("mage")
            cfg = self.host.bot_config
            self.assertEqual(
                (cfg.jump_key, cfg.up_jump_skill_key,
                 cfg.flash_jump_enabled), ("space", "alt", False))
            # A movekeys edit lands in the profile's kit and persists.
            self.host._handle_command(
                'movekeys|set|{"up_jump_skill_key":"ctrl"}')
            self.assertEqual(cfg.up_jump_skill_key, "ctrl")
            saved = (self.host.config.bot or {}).get("class", {}) \
                .get("profiles", {}).get("mage", {})
            self.assertEqual(saved.get("up_jump_skill_key"), "ctrl")

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

    def _plat_host(self, tmp):
        img = np.zeros((150, 200, 3), dtype=np.uint8)
        feed = Mock()
        feed.minimap_img.return_value = img
        feed.minimap.region = (0, 0, 200, 150)
        self.host._feed = feed
        self._use_store(tmp)
        self.host.maps.save(MapEntry(name="m1"))

    def _plats_px(self, tmp):
        return [[round(v * (200 if i % 2 == 0 else 150), 1) for i, v in enumerate(s)]
                for s in MapStore(tmp).get("m1").platforms or []]

    def test_wobbly_drag_is_saved_level(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._plat_host(tmp)
            self.host._handle_command("layout|plat|10,40,100,42|m1")
            (x0, y0, x1, y1), = self._plats_px(tmp)
            self.assertEqual(y0, y1)
            self.assertAlmostEqual(y0, 41.0, delta=0.1)

    def test_overlapping_drags_merge_and_undo_restores(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._plat_host(tmp)
            self.host._handle_command("layout|plat|10,40,100,40|m1")
            self.host._handle_command("layout|plat|90,41,160,41|m1")
            merged = self._plats_px(tmp)
            self.assertEqual(len(merged), 1)
            self.assertAlmostEqual(merged[0][0], 10, delta=0.5)
            self.assertAlmostEqual(merged[0][2], 160, delta=0.5)
            self.host._handle_command("layout|plat|undo|m1")
            back = self._plats_px(tmp)
            self.assertEqual(len(back), 1)                 # the first drag, intact
            self.assertAlmostEqual(back[0][2], 100, delta=0.5)

    def test_tidy_command_cleans_existing_drawings(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._plat_host(tmp)
            entry = MapStore(tmp).get("m1")
            entry.platforms = [[0.05, 0.3, 0.3, 0.31], [0.25, 0.3067, 0.6, 0.3]]
            self.host.maps.save(entry)
            self.host.maps.reload()
            self.host._handle_command("layout|plat|tidy|m1")
            self.assertEqual(len(MapStore(tmp).get("m1").platforms), 1)
            self.host._handle_command("layout|plat|undo|m1")
            self.assertEqual(len(MapStore(tmp).get("m1").platforms), 2)

    def _with_anchors(self, tmp, platforms, anchors):
        entry = MapStore(tmp).get("m1")
        entry.platforms = platforms
        entry.rotation.anchors = [Anchor(n, x / 200, y / 150) for n, x, y in anchors]
        self.host.maps.save(entry)
        self.host.maps.reload()

    def _anchor_px(self, tmp):
        return {a.name: (round(a.x * 200, 1), round(a.y * 150, 1))
                for a in MapStore(tmp).get("m1").rotation.anchors}

    def test_tidy_moves_anchors_with_their_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._plat_host(tmp)
            # Wobbly line: y 40 at x=20 → y 44 at x=180 (levels at 42).
            # a0 floats 5px above it at x=20; a1 stands on another platform.
            self._with_anchors(
                tmp,
                [[0.1, 40 / 150, 0.9, 44 / 150], [0.1, 100 / 150, 0.9, 100 / 150]],
                [("a0", 20, 35), ("a1", 100, 96)],
            )
            self.host._handle_command("layout|plat|tidy|m1")
            got = self._anchor_px(tmp)
            self.assertAlmostEqual(got["a0"][1], 37.0, delta=0.2)   # 42 - 5
            self.assertEqual(got["a0"][0], 20.0)                    # x kept
            self.assertEqual(got["a1"], (100.0, 96.0))               # untouched
            self.host._handle_command("layout|plat|undo|m1")
            self.assertAlmostEqual(self._anchor_px(tmp)["a0"][1], 35.0, delta=0.2)

    def test_undo_leaves_a_recreated_anchor_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._plat_host(tmp)
            self._with_anchors(tmp, [[0.1, 40 / 150, 0.9, 44 / 150]],
                               [("a0", 20, 35)])
            self.host._handle_command("layout|plat|tidy|m1")       # moves a0
            # a0 deleted and a new a0 placed elsewhere (names are reused).
            entry = MapStore(tmp).get("m1")
            entry.rotation.anchors = [Anchor("a0", 150 / 200, 90 / 150)]
            self.host.maps.save(entry)
            self.host.maps.reload()
            self.host._handle_command("layout|plat|undo|m1")
            self.assertEqual(self._anchor_px(tmp)["a0"], (150.0, 90.0))

    def test_merging_a_drag_moves_anchors_on_the_merged_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._plat_host(tmp)
            self._with_anchors(tmp, [[0.05, 40 / 150, 0.5, 40 / 150]],
                               [("a0", 50, 36)])
            # Overlapping drag 1px lower and longer: merged row ~40.5.
            self.host._handle_command("layout|plat|90,41,190,41|m1")
            (x0, y0, x1, y1), = self._plats_px(tmp)
            a0 = self._anchor_px(tmp)["a0"]
            self.assertAlmostEqual(a0[1], y0 - 4, delta=0.2)          # float kept
            self.host._handle_command("layout|plat|undo|m1")
            self.assertAlmostEqual(self._anchor_px(tmp)["a0"][1], 36.0, delta=0.2)

    def test_direction_only_difference_is_already_tidy(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._plat_host(tmp)
            entry = MapStore(tmp).get("m1")
            entry.platforms = [[0.5, 0.3, 0.1, 0.3]]        # drawn right-to-left
            self.host.maps.save(entry)
            self.host.maps.reload()
            self.save_mock.reset_mock()
            self.host._handle_command("layout|plat|tidy|m1")
            self.assertEqual(MapStore(tmp).get("m1").platforms, [[0.5, 0.3, 0.1, 0.3]])
            self.assertFalse(self.host._layout_undo.get(("m1", "platforms")))
            self.assertTrue(any("already tidy" in m for m in self.sent))

    def _feet_samples(self, seg, residuals, name="m1"):
        key = self.host.platfit.key(seg)
        self.host.platfit._samples.setdefault(name, {})[key] = [
            (50.0 + i, r) for i, r in enumerate(residuals)]

    def test_move_to_feet_shifts_the_line_and_its_anchors(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._plat_host(tmp)
            seg = [0.1, 40 / 150, 0.9, 40 / 150]
            self._with_anchors(tmp, [seg], [("a0", 60, 36)])
            self._feet_samples(seg, [6, 6, 7, 6, 5, 6])       # feet 6px below
            key = self.host.platfit.key(seg)
            self.host._handle_command(
                "layout|plat|feet|" + ",".join(f"{v:g}" for v in key) + "|m1")
            (x0, y0, x1, y1), = self._plats_px(tmp)
            self.assertAlmostEqual(y0, 46.0, delta=0.1)
            self.assertAlmostEqual(y1, 46.0, delta=0.1)
            # Beyond the usual 4px follow limit, the anchor still follows.
            self.assertAlmostEqual(self._anchor_px(tmp)["a0"][1], 42.0, delta=0.2)
            # Samples carried over: the moved line now reads as fitting.
            got = self.host.platfit.summary(
                "m1", MapStore(tmp).get("m1").platforms, (0, 0, 200, 150))
            self.assertAlmostEqual(got[0]["offset"], 0.0, delta=0.1)
            self.assertTrue(any("onto the feet" in m for m in self.sent))
            self.host._handle_command("layout|plat|undo|m1")
            self.assertAlmostEqual(self._plats_px(tmp)[0][1], 40.0, delta=0.1)
            self.assertAlmostEqual(self._anchor_px(tmp)["a0"][1], 36.0, delta=0.2)

    def _stand(self, *positions):
        self.host.FEET_READ_GAP = 0.0
        seq = list(positions) or [None]
        self.host._feed.minimap.player_pos = Mock(
            side_effect=lambda img: seq.pop(0) if len(seq) > 1 else seq[0])

    def test_align_here_moves_the_line_under_the_feet(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._plat_host(tmp)
            seg = [0.1, 40 / 150, 0.9, 40 / 150]
            other = [0.1, 60 / 150, 0.9, 60 / 150]
            self._with_anchors(tmp, [seg, other], [("a0", 60, 36)])
            self._stand((80, 47), (80, 47), (81, 47))   # 7px below y40
            self.host._handle_command("layout|plat|here|m1")
            rows = sorted(p[1] for p in self._plats_px(tmp))
            self.assertEqual(rows, [47.0, 60.0])       # nearest line only
            self.assertAlmostEqual(self._anchor_px(tmp)["a0"][1], 43.0, delta=0.2)
            self.assertTrue(any("onto your feet" in m for m in self.sent))
            self.host._handle_command("layout|plat|undo|m1")
            self.assertEqual(sorted(p[1] for p in self._plats_px(tmp)), [40.0, 60.0])

    def test_align_here_refuses_while_moving_or_far_from_a_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._plat_host(tmp)
            seg = [0.1, 40 / 150, 0.9, 40 / 150]
            self._with_anchors(tmp, [seg], [])
            self._stand((80, 44), (80, 40), (80, 36))    # airborne
            self.host._handle_command("layout|plat|here|m1")
            self._stand((80, 70))                        # 30px below
            self.host._handle_command("layout|plat|here|m1")
            self._stand((190, 40))                       # past its end
            self.host._handle_command("layout|plat|here|m1")
            self.assertEqual(self._plats_px(tmp)[0][1], 40.0)
            msgs = " ".join(self.sent)
            self.assertIn("steady position", msgs)
            self.assertIn("no drawn platform within 10px", msgs)

    def test_align_here_on_a_fitting_line_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._plat_host(tmp)
            seg = [0.1, 40 / 150, 0.9, 40 / 150]
            self._with_anchors(tmp, [seg], [])
            self._stand((80, 40))
            self.host._handle_command("layout|plat|here|m1")
            self.assertFalse(self.host._layout_undo.get(("m1", "platforms")))
            self.assertTrue(any("already sits at your feet" in m for m in self.sent))

    def test_move_to_feet_refuses_without_enough_samples_or_a_stale_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._plat_host(tmp)
            seg = [0.1, 40 / 150, 0.9, 40 / 150]
            self._with_anchors(tmp, [seg], [])
            self._feet_samples(seg, [6, 6])                    # too few
            key = ",".join(f"{v:g}" for v in self.host.platfit.key(seg))
            self.host._handle_command(f"layout|plat|feet|{key}|m1")
            self.host._handle_command("layout|plat|feet|0.2,0.5,0.3,0.5|m1")
            self.assertAlmostEqual(self._plats_px(tmp)[0][1], 40.0, delta=0.1)
            msgs = " ".join(self.sent)
            self.assertIn("not enough samples", msgs)
            self.assertIn("platform changed", msgs)

    def test_move_to_feet_leaves_a_fitting_line_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._plat_host(tmp)
            seg = [0.1, 40 / 150, 0.9, 40 / 150]
            self._with_anchors(tmp, [seg], [])
            self._feet_samples(seg, [0, 0, 1, 0, 0])
            key = ",".join(f"{v:g}" for v in self.host.platfit.key(seg))
            self.host._handle_command(f"layout|plat|feet|{key}|m1")
            self.assertIn("already sits at the feet", " ".join(self.sent))

    def _with_ropes(self, tmp, ropes):
        entry = MapStore(tmp).get("m1")
        entry.ropes = ropes
        self.host.maps.save(entry)
        self.host.maps.reload()

    def test_remove_one_learned_rope_and_undo(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._plat_host(tmp)
            a, b = [0.3, 0.6, 0.3, 0.3], [0.7, 0.8, 0.7, 0.5]
            self._with_ropes(tmp, [a, b])
            key = ",".join(f"{v:g}" for v in self.host.platfit.key(a))
            self.host._handle_command(f"layout|rope|del|{key}|m1")
            self.assertEqual(MapStore(tmp).get("m1").ropes, [b])
            self.assertTrue(any("removed the rope" in m for m in self.sent))
            self.host._handle_command("layout|rope|undo|m1")
            self.assertEqual(MapStore(tmp).get("m1").ropes, [a, b])

    def test_remove_rope_refuses_a_stale_key_and_clear_removes_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._plat_host(tmp)
            self._with_ropes(tmp, [[0.3, 0.6, 0.3, 0.3]])
            self.host._handle_command("layout|rope|del|0.9,0.9,0.9,0.1|m1")
            self.assertEqual(len(MapStore(tmp).get("m1").ropes), 1)
            self.assertIn("rope changed", " ".join(self.sent))
            self.host._handle_command("layout|rope|clear|m1")
            self.assertIsNone(MapStore(tmp).get("m1").ropes)

    def test_maps_event_lists_learned_ropes(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._plat_host(tmp)
            self._with_ropes(tmp, [[0.3, 0.6, 0.3, 0.3]])
            with patch.object(self.host, "_resolved_entry",
                              return_value=MapStore(tmp).get("m1")):
                self.host._send_maps()
            ropes = [json.loads(m[5:]) for m in self.sent
                     if '"event": "maps"' in m][-1]["ropes"]
            self.assertEqual(ropes, [{"key": "0.3,0.6,0.3,0.3", "x": 60,
                                      "top": 45, "bottom": 90}])

    def _title_host(self, tmp, title="Identisk Tisk Food Storehouse Entrance"):
        store = self._use_store(tmp)
        store.save(MapEntry(name="room", map_name=title))
        store.save(MapEntry(name="entr"))
        store.reload()
        self.host.identity._current = type(self.host.identity.current)(
            name=None, via=None, title=title)
        return store

    def test_record_title_from_screen(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._title_host(tmp)
            entry = MapStore(tmp).get("room")
            entry.map_name = None
            self.host.maps.save(entry)
            self.host.maps.reload()
            self.host._handle_command("map|title|record|entr")
            self.assertEqual(MapStore(tmp).get("entr").map_name,
                             "Identisk Tisk Food Storehouse Entrance")

    def test_record_title_refuses_one_recorded_elsewhere_and_clear(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._title_host(tmp)
            self.host._handle_command("map|title|record|entr")
            self.assertIsNone(MapStore(tmp).get("entr").map_name)
            self.assertIn("already recorded for room", " ".join(self.sent))
            self.host._handle_command("map|title|clear|room")
            self.assertIsNone(MapStore(tmp).get("room").map_name)
            # Saving re-resolves identity from its last real read; this
            # test has none, so put the on-screen title back.
            self.host.identity._current = type(self.host.identity.current)(
                title="Identisk Tisk Food Storehouse Entrance")
            self.host._handle_command("map|title|record|entr")
            self.assertEqual(MapStore(tmp).get("entr").map_name,
                             "Identisk Tisk Food Storehouse Entrance")

    def test_maps_event_carries_the_recorded_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._title_host(tmp)
            with patch.object(self.host, "_resolved_entry",
                              return_value=store.get("room")):
                self.host._send_maps()
            last = [json.loads(m[5:]) for m in self.sent if '"event": "maps"' in m][-1]
            self.assertEqual(last["recorded_title"],
                             "Identisk Tisk Food Storehouse Entrance")

    def test_maps_event_carries_anchor_stats_and_reset_clears(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._plat_host(tmp)
            self._with_anchors(tmp, [], [("a0", 20, 40), ("a1", 80, 40)])
            self.host.anchor_stats.visit("m1", "a0")
            self.host.anchor_stats.skip("m1", "a1", "no route")
            entry = MapStore(tmp).get("m1")
            with patch.object(self.host, "_resolved_entry", return_value=entry):
                self.host._send_maps()
                rows = [json.loads(m[5:]) for m in self.sent
                        if '"event": "maps"' in m][-1]["anchor_stats"]
                self.assertEqual([r["name"] for r in rows], ["a0", "a1"])
                self.assertEqual(rows[0]["visits"], 1)
                self.assertEqual(rows[1]["skips"], {"no route": 1})
                self.host._handle_command("map|stats|reset")
                rows = [json.loads(m[5:]) for m in self.sent
                        if '"event": "maps"' in m][-1]["anchor_stats"]
                self.assertEqual(rows[1]["skips"], {})

    def test_maps_event_carries_platform_fit(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._plat_host(tmp)
            self.host._handle_command("layout|plat|10,40,100,40|m1")
            with patch.object(self.host, "_resolved_entry",
                              return_value=MapStore(tmp).get("m1")):
                self.host._send_maps()
            fit = [json.loads(m[5:]) for m in self.sent
                   if '"event": "maps"' in m][-1]["platform_fit"]
            self.assertEqual(len(fit), 1)
            self.assertEqual(fit[0]["n"], 0)

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

    def test_movekeys_set_nav_radius(self):
        self.assertTrue(self.host._handle_command(
            'movekeys|set|{"nav_threshold_px":6}'))
        self.assertEqual(self.host.bot_config.nav_threshold_px, 6)
        self.assertEqual(self.host.config.bot["nav_threshold_px"], 6)
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

    # -- lifecycle races, per-view overlays, profile isolation ----------------
    def test_stop_during_bot_construction_prevents_start(self):
        self.host._bot_stop.set()
        with patch("picobot.bot.SmartBot") as sb, \
             patch("picobot.bot.HidController"):
            self.host._bot_entry()
        sb.return_value.start.assert_not_called()
        states = [json.loads(m[5:]) for m in self.sent
                  if '"event": "bot"' in m]
        self.assertEqual(states[-1], {"event": "bot", "running": False})

    def test_bot_entry_broadcasts_running_state(self):
        self.host._bot_stop.clear()
        with patch("picobot.bot.SmartBot"), patch("picobot.bot.HidController"):
            self.host._bot_entry()
        states = [json.loads(m[5:])["running"] for m in self.sent
                  if '"event": "bot"' in m]
        self.assertEqual(states, [True, False])

    def test_start_bot_refused_while_measuring(self):
        self.host.serial_port = "COM6"
        self.host.remote.serial_manager = Mock(is_open=True)
        self.host.measurer = Mock(running=Mock(return_value=True))
        with patch("threading.Thread") as th:
            self.host.start_bot()
        th.assert_not_called()
        errs = [e["msg"] for e in self.host.bus.history() if e["kind"] == "error"]
        self.assertTrue(any("measuring" in m for m in errs))

    def test_window_switch_refused_while_bot_runs(self):
        feed = Mock()
        self.host._feed = feed
        self.host.bot_thread = Mock(is_alive=Mock(return_value=True))
        self.host._handle_command("host|window|OtherWin")
        self.assertEqual(self.host.window_title, "OldWin")
        self.assertIs(self.host._feed, feed)
        feed.close.assert_not_called()

    def test_window_frame_moves_overlays_onto_the_minimap(self):
        feed = Mock()
        feed.window_img.return_value = np.zeros((600, 800, 3), dtype=np.uint8)
        feed.minimap.region = (10, 70, 200, 100)
        self.host._feed = feed
        with patch.object(self.host, "_map_meta", return_value={
            "platforms": [(0, 50, 100, 50)], "anchors": [(5, 40)],
            "map": "m1",
        }):
            snap = self.host._provide_frame("window")
        self.assertEqual(snap["platforms"], [(10, 120, 110, 120)])
        self.assertEqual(snap["anchors"], [(15, 110)])
        self.assertEqual(snap["state"], "IDLE")

    def test_window_frame_carries_bot_hazard(self):
        bot = Mock()
        bot._window_capture.return_value = np.zeros((600, 800, 3), dtype=np.uint8)
        bot.minimap.region = (10, 70, 200, 100)
        bot.viz = {"state": "PAUSE", "hazard": "rune"}
        self.host.bot = bot
        with patch.object(self.host, "_map_meta", return_value={"map": "m1"}):
            snap = self.host._provide_frame("window")
        self.assertEqual((snap["state"], snap["hazard"]), ("PAUSE", "rune"))

    def test_title_frame_drops_minimap_overlays(self):
        feed = Mock()
        feed.name_img.return_value = PanelAssemblyTests._band()
        self.host._feed = feed
        with patch.object(self.host, "_map_meta", return_value={
            "platforms": [(0, 1, 2, 3)], "ropes": [(0, 1, 2, 3)],
            "nav_route": [("walk", 0, 1, 2, 3)], "map": "m1",
        }):
            snap = self.host._provide_frame("title")
        for key in ("platforms", "ropes", "nav_route"):
            self.assertNotIn(key, snap)
        self.assertEqual(snap["map"], "m1")

    def test_class_switch_does_not_inherit_previous_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._use_store(tmp)
            self.host.config.bot = {"jump_key": "space"}
            self.host.bot_config.class_profiles = {
                "mage": {"travel": "teleport", "teleport_key": "shift",
                         "jump_key": "c",
                         "skills": {"blink": {"key": "x", "kind": "attack"}}},
                "hero": {"travel": "flash"},
            }
            self.host._class_use("mage")
            self.host._class_use("hero")
            cfg = self.host.bot_config
            self.assertEqual(cfg.jump_key, "space")
            self.assertIsNone(cfg.teleport_key)
            self.assertNotIn("blink", cfg.skills)

    def _events(self, name):
        return [json.loads(m[5:]) for m in self.sent
                if m.startswith("dash|") and f'"event": "{name}"' in m]

    def test_class_switch_resyncs_skills_and_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._use_store(tmp)
            self.host.config.bot = {"jump_key": "space"}
            self.host.bot_config.class_profiles = {
                "mage": {"jump_key": "c",
                         "skills": {"blink": {"key": "x", "kind": "attack"}}},
                "hero": {"skills": {"slash": {"key": "a", "kind": "attack"}}},
            }
            self.host._class_use("mage")
            self.sent.clear()
            self.host._class_use("hero")
            skills = self._events("skills")[-1]
            self.assertEqual(skills["source"], "hero")
            self.assertEqual(list(skills["skills"]), ["slash"])
            self.assertEqual(self._events("config")[-1]["config"]["jump_key"],
                             "space")

    def test_emptied_profile_kit_matches_what_the_bot_uses(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._use_store(tmp)
            self.host.config.bot = {
                "skills": {"g": {"key": "g", "kind": "attack"}},
            }
            self.host.bot_config.class_profiles = {
                "hero": {"skills": {"slash": {"key": "a", "kind": "attack"}}},
                "mage": {},
            }
            self.host._class_use("hero")
            self.host._handle_command("skills|del|slash")
            self.host._class_use("mage")
            self.host._class_use("hero")        # re-apply the emptied kit
            self.assertEqual(self.host.bot_config.skills, {})
            self.assertEqual(self._events("skills")[-1]["skills"], {})

    def test_showing_an_inheriting_profile_does_not_seed_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._use_store(tmp)
            self.host.config.bot = {
                "skills": {"g": {"key": "g", "kind": "attack"}},
            }
            self.host.bot_config.class_profiles = {"hero": {"travel": "flash"}}
            self.host._class_use("hero")
            self.host._handle_command("skills|list")
            shown = self._events("skills")[-1]
            self.assertTrue(shown["inherited"])
            self.assertEqual(list(shown["skills"]), ["g"])
            self.assertNotIn("skills", self.host.bot_config.class_profiles["hero"])
            # The first edit gives the profile its own kit.
            self.host._handle_command(
                'skills|set|{"name":"p","key":"p","kind":"attack"}')
            self.assertEqual(
                sorted(self.host.bot_config.class_profiles["hero"]["skills"]),
                ["g", "p"])
            self.assertFalse(self._events("skills")[-1]["inherited"])

    def test_class_switch_stops_a_running_measurement(self):
        self.host.bot_config.class_profiles = {"hero": {"travel": "flash"}}
        measurer = Mock(running=Mock(return_value=True), status=Mock(
            return_value={"running": False, "move": None, "plan": [],
                          "results": {}}))
        self.host.measurer = measurer
        self.host._class_use("hero")
        measurer.stop.assert_called_once()

    def test_measurement_status_is_broadcast_and_refreshes_count(self):
        self.host._feed = Mock()
        with patch("picobot.bot.SmartBot"),                 patch("picobot.bot.measure.MoveMeasurer") as mm:
            mm.plan_for.return_value = ("jump",)
            self.host._measure_start()
        on_status = mm.call_args.kwargs["on_status"]
        self.sent.clear()
        on_status({"running": True, "move": "jump", "plan": ["jump"],
                   "results": {}})
        self.assertTrue(self._events("measure")[-1]["running"])
        self.assertEqual(self._events("class"), [])
        self.host.reach.measured["jump"] = 1.0
        on_status({"running": False, "move": None, "plan": ["jump"],
                   "results": {"jump": {"dx": 12.0}}})
        self.assertEqual(self._events("measure")[-1]["results"],
                         {"jump": {"dx": 12.0}})
        self.assertEqual(self._events("class")[-1]["measured"], 1)

    def test_measure_status_when_idle_lists_the_class_plan(self):
        self.host.bot_config.class_travel = "walk"
        self.host._handle_command("measure|status")
        st = self._events("measure")[-1]
        self.assertFalse(st["running"])
        self.assertEqual(st["plan"], ["jump", "rope_lift"])

    def test_measure_start_while_measuring_says_so(self):
        self.host.measurer = Mock(running=Mock(return_value=True), status=Mock(
            return_value={"running": True, "move": "jump", "plan": ["jump"],
                          "results": {}}))
        self.host._measure_start()
        self.assertTrue(any("already measuring" in m for m in self.sent))

    def test_up_flash_sweep_command_starts_profile_mode(self):
        self.host._feed = Mock()
        with patch("picobot.bot.SmartBot"),                 patch("picobot.bot.measure.MoveMeasurer") as mm:
            self.host._handle_command("measure|profile|up_flash")
        mm.return_value.start.assert_called_once_with("up_flash_profile")

    def test_idle_measure_status_carries_stored_profiles(self):
        self.host.reach.set_profile("up_flash", [{"delay": 0.2, "rise": 18}])
        self.host._handle_command("measure|status")
        st = self._events("measure")[-1]
        self.assertEqual(st["profiles"]["up_flash"]["rows"],
                         [{"delay": 0.2, "rise": 18}])

    def test_profile_edits_leave_the_global_book_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._use_store(tmp)
            self.host.config.bot = {
                "skills": {"g": {"key": "g", "kind": "attack"}},
            }
            self.host.bot_config.class_profiles = {
                "mage": {"travel": "flash"},
            }
            self.host._class_use("mage")
            self.host._handle_command(
                'skills|set|{"name":"p","key":"p","kind":"attack"}')
            self.host._handle_command('movekeys|set|{"jump_key":"c"}')
            self.assertEqual(list(self.host.config.bot["skills"]), ["g"])
            self.assertNotIn("jump_key", self.host.config.bot)
            self.assertEqual(self.host.bot_config.jump_key, "c")

    def test_patrol_temp_rejects_non_finite(self):
        before = self.host.bot_config.patrol_weight_temp
        for bad in ("inf", "nan", "abc"):
            self.host._handle_command(f"patrol|temp|{bad}")
        self.assertEqual(self.host.bot_config.patrol_weight_temp, before)

    def test_serial_auto_skips_the_port_already_open(self):
        self.host.serial_port = "COM6"
        self.host.remote.serial_manager = Mock(is_open=True)
        with patch(
            "picobot.transport.discover_data_port", return_value=None
        ) as disc:
            self.host._handle_command("host|serial|auto")
            import threading

            for t in threading.enumerate():
                if t.name == "PortProbe":
                    t.join(timeout=3)
        self.assertEqual(disc.call_args.kwargs.get("exclude_port"), "COM6")
        msgs = [e["msg"] for e in self.host.bus.history() if e["kind"] == "host"]
        self.assertTrue(any("staying on COM6" in m for m in msgs))


if __name__ == "__main__":
    unittest.main()
