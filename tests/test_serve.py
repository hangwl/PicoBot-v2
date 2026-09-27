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
from picobot.vision.minimap import (
    MinimapAnalyzer,
    fingerprint,
    structure_mask,
)


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
        import base64
        raw = base64.b64decode(data)
        self.assertTrue(raw.startswith(b"\xff\xd8"))  # JPEG SOI


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
            "walls": {"left": 20, "right": 180},
            "floor": 120,
            "platforms": [(10, 60, 100, 60)],
            "anchors": [(40, 50)],
            "player": (80, 50),
            "target": (120, 50),
            "rune": (60, 30),
        }
        _offset_meta(snap, 7, 52)
        self.assertEqual(snap["walls"], {"left": 27, "right": 187})
        self.assertEqual(snap["floor"], 172)
        self.assertEqual(snap["platforms"], [(17, 112, 107, 112)])
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

    def test_provide_frame_minimap_emits_panel_offset(self):
        # With a real title band the feed path composites the panel and
        # reports the minimap's offset inside it.
        img = np.zeros((150, 200, 3), dtype=np.uint8)
        feed = Mock()
        feed.minimap_img.return_value = img
        feed.minimap.player_pos.return_value = (100, 75)
        feed.minimap.region = (10, 75, 200, 150)
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

    def test_wall_set_at_player_x_via_feed(self):
        img = np.zeros((150, 200, 3), dtype=np.uint8)
        feed = Mock()
        feed.minimap_img.return_value = img
        feed.minimap.player_pos.return_value = (50, 40)
        feed.minimap.region = (0, 0, 200, 150)
        self.host._feed = feed
        with tempfile.TemporaryDirectory() as tmp:
            self.host.maps = MapStore(tmp)
            self.host.maps.save(MapEntry(name="m1"))
            self.assertTrue(
                self.host._handle_command("layout|wall|left|m1")
            )
            saved = MapStore(tmp).get("m1")
            self.assertEqual(saved.walls, {"left": 0.25})
            self.assertTrue(
                self.host._handle_command("layout|wall|right|m1")
            )
            saved = MapStore(tmp).get("m1")
            self.assertEqual(
                saved.walls, {"left": 0.25, "right": 0.25}
            )
            self.assertTrue(
                self.host._handle_command("layout|wall|clear|m1")
            )
            self.assertIsNone(MapStore(tmp).get("m1").walls)

    def test_wall_set_via_running_bot(self):
        bot = Mock()
        bot.player_pos.return_value = (160, 40)
        bot.minimap.region = (0, 0, 200, 150)
        self.host.bot = bot
        with tempfile.TemporaryDirectory() as tmp:
            self.host.maps = MapStore(tmp)
            self.host.maps.save(MapEntry(name="m1"))
            self.assertTrue(
                self.host._handle_command("layout|wall|right|m1")
            )
            saved = MapStore(tmp).get("m1")
            self.assertEqual(saved.walls, {"right": 0.8})

    def test_wall_set_without_player_pos_errors(self):
        feed = Mock()
        feed.minimap_img.return_value = None
        feed.minimap.region = (0, 0, 200, 150)
        self.host._feed = feed
        with tempfile.TemporaryDirectory() as tmp:
            self.host.maps = MapStore(tmp)
            self.host.maps.save(MapEntry(name="m1"))
            self.host._handle_command("layout|wall|left|m1")
            self.assertIsNone(MapStore(tmp).get("m1").walls)
        msgs = [
            e["msg"] for e in self.host.bus.history() if e["kind"] == "error"
        ]
        self.assertTrue(any("no player position" in m for m in msgs))

    def test_wall_set_verifies_map_identity_when_blank(self):
        # Blank name must still verify — a stale pin must not write
        # walls into the wrong map file.
        feed = Mock()
        feed.minimap_img.return_value = np.zeros((150, 200, 3), dtype=np.uint8)
        feed.minimap.player_pos.return_value = (50, 40)
        feed.minimap.region = (0, 0, 200, 150)
        self.host._feed = feed
        self.host._live_fingerprint = Mock(return_value="aa")
        with tempfile.TemporaryDirectory() as tmp:
            self.host.maps = MapStore(tmp)
            self.host.maps.save(MapEntry(name="m1", fingerprint="ff" * 512))
            self.host._handle_command("layout|wall|left")
            self.assertIsNone(MapStore(tmp).get("m1").walls)

    def test_floor_set_at_player_y(self):
        img = np.zeros((150, 200, 3), dtype=np.uint8)
        feed = Mock()
        feed.minimap_img.return_value = img
        feed.minimap.player_pos.return_value = (50, 120)
        feed.minimap.region = (0, 0, 200, 150)
        self.host._feed = feed
        with tempfile.TemporaryDirectory() as tmp:
            self.host.maps = MapStore(tmp)
            self.host.maps.save(MapEntry(name="m1"))
            self.assertTrue(
                self.host._handle_command("layout|wall|floor|m1")
            )
            saved = MapStore(tmp).get("m1")
            self.assertEqual(saved.walls, {"floor": 0.8})

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
            self.host.maps = MapStore(tmp)
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
            self.host.maps = MapStore(tmp)
            self.host.maps.save(MapEntry(name="m1"))
            self.host._handle_command("layout|plat|50,50,51,51|m1")
            self.assertIsNone(MapStore(tmp).get("m1").platforms)

    def test_map_meta_reports_drawn_platforms(self):
        entry = MapEntry(
            name="m1", platforms=[[0.05, 0.25, 0.5, 0.25]]
        )
        feed = Mock()
        feed.minimap.region = (0, 0, 200, 150)
        self.host._feed = feed
        self.host._resolved_map_entry = Mock(return_value=entry)
        with tempfile.TemporaryDirectory() as tmp:
            self.host.maps = MapStore(tmp)
            self.host.maps.save(entry)
            meta = self.host._map_meta()
        self.assertEqual(meta["platforms"], [(10, 38, 100, 38)])

    def _inked_img(self, w=200, h=150):
        """Minimap-like frame: platform rows in border/ink color."""
        img = np.zeros((h, w, 3), dtype=np.uint8)
        img[::20, :] = (228, 228, 228)
        return img

    def _feed_with(self, img):
        """Feed stub whose live frame is ``img``."""
        analyzer = MinimapAnalyzer(region=(0, 0, 200, 150))
        feed = Mock()
        feed.minimap = analyzer
        feed.minimap_img = Mock(return_value=img)
        self.host._feed = feed
        return analyzer

    def _fp_of(self, img, analyzer):
        c = analyzer.colors
        return fingerprint(
            img,
            ignore_colors=(c.player, c.other_player, c.rune),
            include_mask=structure_mask(img, c),
        )

    def test_maps_payload_reports_detected_and_score(self):
        img = self._inked_img()
        analyzer = self._feed_with(img)
        fp = self._fp_of(img, analyzer)
        with tempfile.TemporaryDirectory() as tmp:
            self.host.maps = MapStore(tmp)
            self.host.maps.save(MapEntry(name="m1", fingerprint=fp))
            self.host._send_maps()
        payload = json.loads(self.sent[-1][5:])
        self.assertEqual(payload["event"], "maps")
        self.assertEqual(payload["detected"], "m1")
        self.assertGreater(payload["score"], 0.9)

    def test_map_meta_reports_confidence(self):
        img = self._inked_img()
        analyzer = self._feed_with(img)
        fp = self._fp_of(img, analyzer)
        with tempfile.TemporaryDirectory() as tmp:
            self.host.maps = MapStore(tmp)
            self.host.maps.save(MapEntry(name="m1", fingerprint=fp))
            self.host._resolved_map_cached()   # refresh live evidence
            meta = self.host._map_meta()
        self.assertEqual(meta["map"], "m1")
        self.assertGreater(meta["map_conf"], 0.9)

    def test_map_meta_confidence_none_without_fingerprint(self):
        img = self._inked_img()
        self._feed_with(img)
        with tempfile.TemporaryDirectory() as tmp:
            self.host.maps = MapStore(tmp)
            self.host.maps.save(MapEntry(name="m1"))   # no fingerprint
            self.host._active_map_override = "m1"
            self.host._resolved_map_cached()
            meta = self.host._map_meta()
        self.assertEqual(meta["map"], "m1")
        self.assertIsNone(meta["map_conf"])

    def test_map_meta_walls_survive_bot_without_resolved_map(self):
        # Regression: walls vanished the moment the bot started — the
        # meta path trusted only bot._map (None until the first travel
        # leg resolves it), bypassing the live-fingerprint path the feed
        # was using.
        img = self._inked_img()
        analyzer = MinimapAnalyzer(region=(0, 0, 200, 150))
        c = analyzer.colors
        fp = fingerprint(
            img,
            ignore_colors=(c.player, c.other_player, c.rune),
            include_mask=structure_mask(img, c),
        )
        feed = Mock()
        feed.minimap = analyzer
        feed.minimap_img = Mock(return_value=img)
        self.host._feed = feed
        bot = Mock()
        bot._map = None                       # not resolved yet
        bot.minimap_frame = Mock(return_value=None)
        bot.minimap.region = (0, 0, 200, 150)
        self.host.bot = bot
        with tempfile.TemporaryDirectory() as tmp:
            self.host.maps = MapStore(tmp)
            self.host.maps.save(MapEntry(
                name="m1", fingerprint=fp, walls={"left": 0.25},
            ))
            meta = self.host._map_meta()
        self.assertEqual(meta["map"], "m1")
        self.assertEqual(meta["walls"], {"left": 50})   # 0.25 * 200

    def test_map_meta_live_fingerprint_beats_stale_pin(self):
        # A persisted active_map pin must not shadow the map actually on
        # screen — the pin is rotation scope, not identity.
        img = self._inked_img()
        analyzer = MinimapAnalyzer(region=(0, 0, 200, 150))
        c = analyzer.colors
        fp = fingerprint(
            img,
            ignore_colors=(c.player, c.other_player, c.rune),
            include_mask=structure_mask(img, c),
        )
        feed = Mock()
        feed.minimap = analyzer
        feed.minimap_img = Mock(return_value=img)
        self.host._feed = feed
        self.host._active_map_override = "oldmap"
        with tempfile.TemporaryDirectory() as tmp:
            self.host.maps = MapStore(tmp)
            self.host.maps.save(MapEntry(name="oldmap", fingerprint="ff" * 512))
            self.host.maps.save(MapEntry(
                name="m1", fingerprint=fp, walls={"right": 0.8},
            ))
            meta = self.host._map_meta()
        self.assertEqual(meta["map"], "m1")
        self.assertEqual(meta["walls"], {"right": 160})  # 0.8 * 200

    def test_platform_overlay_draws_stored_segments(self):
        # Drawn platforms ride the frame meta and annotate() renders them.
        img = np.zeros((150, 200, 3), dtype=np.uint8)
        feed = Mock()
        feed.minimap_img.return_value = img
        feed.minimap.player_pos.return_value = None
        feed.minimap.region = (0, 0, 200, 150)
        self.host._feed = feed
        self.host._active_map_override = "m1"
        with tempfile.TemporaryDirectory() as tmp:
            self.host.maps = MapStore(tmp)
            self.host.maps.save(MapEntry(
                name="m1", platforms=[[0.05, 0.4, 0.5, 0.4]]
            ))
            snap = self.host._provide_frame("minimap")
        self.assertEqual(snap["platforms"], [(10, 60, 100, 60)])
        out = annotate(img, snap)
        self.assertTrue((out[60, 50] != img[60, 50]).any())   # line drawn
        self.assertTrue((out[70, 50] == img[70, 50]).all())   # rest clean

    def test_wall_floor_zones_are_rectangular(self):
        # Zones shade the forbidden region, not just a dashed line:
        # left wall at x=50 blocks [0,50); right at 160 blocks [160,w);
        # floor at y=120 blocks [120,h).
        img = np.zeros((150, 200, 3), dtype=np.uint8)
        img[:] = (10, 10, 10)
        out = annotate(img, {
            "walls": {"left": 50, "right": 160},
            "floor": 120,
        })
        self.assertTrue((out[60, 20] != img[60, 20]).any())  # in left zone
        self.assertTrue((out[60, 180] != img[60, 180]).any())
        self.assertTrue((out[140, 100] != img[140, 100]).any())
        # Wall line itself is the zone border (solid red).
        self.assertTrue((out[60, 49] == (60, 60, 255)).all())
        self.assertTrue((out[60, 160] == (60, 60, 255)).all())
        self.assertTrue((out[120, 100] == (60, 60, 255)).all())
        # Walkable middle stays clean.
        self.assertTrue((out[60, 100] == img[60, 100]).all())
        self.assertTrue((out[60, 55] == img[60, 55]).all())

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
        self.assertTrue(self.host._map_dirty)

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
        self.host._handle_command(
            'movekeys|set|{"wall_zone_px":20}')
        self.assertEqual(self.host.bot_config.wall_zone_px, 20)
        self.assertEqual(self.host.config.bot["wall_zone_px"], 20)

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
