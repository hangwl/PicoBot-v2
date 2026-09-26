"""Headless PicoBot host: serial transport + smart bot + web dashboard.

``python -m picobot.serve --port COM3 --window "Eluna (x64)"``

Owns the WebSocket/HTTP remote server, the SmartBot lifecycle, the frame
streamer feeding the dashboard, and the calibration recorder. There is no
desktop UI — the dashboard *is* the UI (locally or over Tailscale).
"""

from __future__ import annotations

import argparse
import json
import logging
import ssl
import threading
import time
from pathlib import Path
from typing import Optional

from .config import AppConfig, load_config, save_config
from .events import EventBus
from .messaging import TelegramHandler
from .remote import EmbeddedHTTPServer, RemoteCallbacks, RemoteControlServer
from .remote.streamer import FrameStreamer
from .settings import configure_logging

logger = logging.getLogger(__name__)


class _VisionFeed:
    """Window/minimap capture used for previews and calibration.

    Lazily created — pygetwindow/mss only exist on the host machine.
    """

    def __init__(
        self,
        window_title: str,
        colors,
        region,
        on_event=None,
        map_change_threshold: float = 15.0,
        name_region=None,
        name_strip_height: int = 26,
    ) -> None:
        from .vision.game_window import GameWindow
        from .vision.minimap import MinimapAnalyzer
        from .vision.screen import ScreenGrabber

        self.window = GameWindow(window_title)
        self.screen = ScreenGrabber()
        self.minimap = MinimapAnalyzer(
            colors=colors,
            region=region,
            map_change_threshold=map_change_threshold,
        )
        self._on_event = on_event
        self._name_region = name_region
        self._name_h = name_strip_height

    def minimap_img(self):
        region = self.minimap.region
        if region is None:
            l, t, r, b = self.window.client_rect()
            full = self.screen.capture((l, t, r - l, b - t))
            if full is None or self.minimap.locate(full) is None:
                return None
            region = self.minimap.region
        x, y, w, h = region
        img = self.screen.capture(
            (self.window.client_left + x, self.window.client_top + y, w, h)
        )
        if img is not None and self.minimap.note_frame(img):
            if self._on_event:
                self._on_event("vision", "map change detected — minimap relocated")
        return img

    def window_img(self):
        l, t, r, b = self.window.client_rect()
        return self.screen.capture((l, t, r - l, b - t))

    def name_region(self):
        """Window-relative (x, y, w, h) of the map-name strip, if known."""
        if self._name_region is not None:
            return self._name_region
        if self.minimap.region is not None:
            from .vision.mapname import name_strip_region

            return name_strip_region(self.minimap.region, self._name_h)
        return None

    def name_img(self):
        """BGR capture of the map-name strip (above the minimap)."""
        region = self.name_region()
        if region is None:
            return None
        x, y, w, h = region
        return self.screen.capture(
            (self.window.client_left + x, self.window.client_top + y, w, h)
        )

    def close(self) -> None:
        self.screen.close()


class BotHost:
    """Wires transport, bot, streamer, and calibration together."""

    def __init__(
        self,
        serial_port: Optional[str],
        window_title: str,
        config: Optional[AppConfig] = None,
    ) -> None:
        from .bot import BotConfig
        from .bot.maps import MapStore
        from .bot.calibrate import CalibrationRunner

        self.config = config or load_config()
        self.bot_config = BotConfig.from_dict(getattr(self.config, "bot", None))
        self.window_title = window_title
        self.serial_port = serial_port
        self.bus = EventBus()
        self.telegram = TelegramHandler(self.config.bot_token, self.config.chat_id)
        self.maps = MapStore(self.bot_config.maps_dir)
        self._feed: Optional[_VisionFeed] = None
        self._feed_lock = threading.Lock()
        self._active_map_override: Optional[str] = self.bot_config.active_map

        self.bot = None
        self.bot_thread: Optional[threading.Thread] = None
        self.calibrator: Optional[CalibrationRunner] = None
        self._name_reader = None
        self._title_cache = (0.0, None)
        self._map_res_ts = 0.0
        self._map_res = None

        callbacks = RemoteCallbacks(
            schedule=lambda fn: fn(),
            log=lambda m: self.bus.emit("remote", m),
            set_status=lambda m: self.bus.emit("status", m),
            set_ws_port=lambda p: self.bus.emit("status", f"ws port: {p}"),
            start_macro=lambda: None,
            stop_macro=lambda: None,
            is_macro_playing=lambda: False,
            broadcast=lambda m: None,
            get_macro_base_path=lambda: "",
            on_remote_playlist_selected=lambda p: None,
            start_bot=self.start_bot,
            stop_bot=self.stop_bot,
            is_bot_running=self.is_bot_running,
            handle_command=self._handle_command,
        )
        self.remote = RemoteControlServer(
            serial_port or "",
            self.config.ws_port,
            callbacks,
            ssl_context=self._ssl_context(),
            # Always degraded-tolerant: a stale remembered port must not keep
            # the dashboard from coming up.
            serial_optional=True,
        )
        self.streamer = FrameStreamer(
            self._provide_frame, self.remote.broadcast, interval=0.35
        )
        self.bus.subscribe(self._forward_event)

    # -- Lifecycle ----------------------------------------------------------
    def _ssl_context(self):
        try:
            if self.config.ws_tls:
                cert = Path(self.config.ws_certfile or "")
                key = Path(self.config.ws_keyfile or "")
                if cert.exists() and key.exists():
                    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                    ctx.load_cert_chain(certfile=str(cert), keyfile=str(key))
                    return ctx
        except Exception as exc:
            logger.error("TLS setup failed: %s", exc)
        return None

    def run(self) -> None:
        self.remote.start()
        self.http = EmbeddedHTTPServer(
            lambda: self.remote.ws_port, self.config.http_port,
            search_paths=[
                Path(__file__).resolve().parent / "remote" / "dashboard.html",
                Path(__file__).resolve().parent.parent / "index.html",
            ],
        )
        self.http.start()
        self.streamer.start()
        self._hook_keyboard()
        self.bus.emit("status", f"dashboard: http://0.0.0.0:{self.config.http_port}")
        logger.info("BotHost running (ws :%s, http :%s)",
                    self.remote.ws_port, self.config.http_port)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        self.stop_bot()
        if self.calibrator:
            self.calibrator.stop()
        self.streamer.stop()
        self.remote.stop()
        if getattr(self, "http", None):
            self.http.stop()
        if self._feed:
            self._feed.close()

    # -- Bot lifecycle --------------------------------------------------------
    def is_bot_running(self) -> bool:
        return bool(self.bot_thread and self.bot_thread.is_alive())

    def start_bot(self) -> None:
        if self.is_bot_running():
            return
        if not self.serial_port or not self.remote.serial_manager.is_open:
            self.bus.emit("error", "no serial — pick a port in the Connection panel")
            return
        self.bot_thread = threading.Thread(
            target=self._bot_entry, name="SmartBot", daemon=True
        )
        self.bot_thread.start()

    def stop_bot(self) -> None:
        if self.bot is not None:
            self.bot.stop()
        if self.bot_thread and self.bot_thread.is_alive():
            self.bot_thread.join(timeout=4)

    def _bot_entry(self) -> None:
        from .bot import HidController, SmartBot

        try:
            bus = self.bus

            def send(payload: str) -> bool:
                bus.emit("hid", payload)
                return self.remote.enqueue_hid_payload(
                    payload, wait_ack=True, timeout=1.5
                )

            self.bot = SmartBot(
                HidController(send),
                self.window_title,
                self.bot_config,
                notify_callback=self.telegram.send_message,
                event_bus=bus,
            )
            self.bus.emit("bot", "started")
            self.bot.start()
        except Exception as exc:
            logger.error("Smart bot failed: %s", exc)
            self.bus.emit("error", f"smart bot failed: {exc}")
        finally:
            self.bot = None
            self.bus.emit("bot", "stopped")

    # -- Frames & events -------------------------------------------------------
    def _forward_event(self, event: dict) -> None:
        payload = {"event": "evt", **event}
        self.remote.broadcast("dash|" + json.dumps(payload))

    def _get_feed(self) -> Optional[_VisionFeed]:
        with self._feed_lock:
            if self._feed is None:
                try:
                    self._feed = _VisionFeed(
                        self.window_title,
                        self.bot_config.minimap_colors,
                        self.bot_config.minimap_region,
                        on_event=self.bus.emit,
                        map_change_threshold=self.bot_config.map_match_threshold,
                        name_region=self.bot_config.minimap_name_region,
                        name_strip_height=self.bot_config.name_strip_height,
                    )
                except Exception as exc:
                    logger.warning("vision feed unavailable: %s", exc)
                    return None
            return self._feed

    def _reader(self):
        if self._name_reader is None:
            from .vision.mapname import MapNameReader

            self._name_reader = MapNameReader()
        return self._name_reader

    def _idle_title(self):
        """OCR'd map name for the idle feed, refreshed every ~5s."""
        now = time.time()
        ts, cached = self._title_cache
        if now - ts < 5.0:
            return cached
        name = None
        if self.bot_config.name_ocr:
            feed = self._get_feed()
            if feed is not None:
                name = self._reader().read(feed.name_img())
        self._title_cache = (now, name)
        if name and name != cached:
            self.bus.emit("vision", f"map name: {name}")
        return name

    def _layout_source(self):
        """Provenance of the live minimap region: explicit/stored/auto."""
        bot = self.bot
        mm = bot.minimap if bot is not None else (
            self._feed.minimap if self._feed is not None else None
        )
        if mm is None or mm.region is None:
            return None
        return mm.region_source

    def _resolved_map_entry(self):
        """The map this session currently believes we're on."""
        if self.bot is not None and self.bot._map is not None:
            return self.bot._map
        if self._active_map_override:
            return self.maps.get(self._active_map_override)
        entry = self.maps.match_name(self._idle_title())
        if entry is None:
            feed = self._get_feed()
            img = feed.minimap_img() if feed is not None else None
            if img is not None:
                from .vision.minimap import fingerprint

                c = feed.minimap.colors
                fp = fingerprint(
                    img,
                    ignore_colors=(c.player, c.other_player, c.rune),
                    include_colors=(c.ink or c.border,),
                )
                entry = self.maps.match(
                    fp, self.bot_config.map_match_threshold
                )
        return entry

    def _resolved_map_cached(self):
        """``_resolved_map_entry`` throttled to ~2s for per-frame use."""
        now = time.time()
        if now - self._map_res_ts < 2.0:
            return self._map_res
        try:
            self._map_res = self._resolved_map_entry()
        except Exception:
            self._map_res = None
        self._map_res_ts = now
        return self._map_res

    def _map_meta(self) -> dict:
        """Frame metadata: resolved map name + whether a rotation can run.

        ``no_rotation`` mirrors the bot's ``effective_rotation`` — a map
        entry's own rotation, else the global config fallback.
        """
        bot = self.bot
        if bot is not None:
            entry = bot._map
            rot = bot.effective_rotation()
        else:
            entry = self._resolved_map_cached()
            rot = entry.rotation if entry else self.bot_config.rotation
        return {
            "map": entry.name if entry else None,
            "no_rotation": not bool(rot.anchors),
        }

    def _layout_save(self) -> None:
        bot = self.bot
        feed = self._get_feed()
        region = None
        if bot is not None:
            region = bot.minimap.region
        if region is None and feed is not None:
            region = feed.minimap.region
        if not region:
            self.bus.emit("error", "no minimap layout detected to save")
            return
        entry = self._resolved_map_entry()
        if entry is None:
            if not self.maps.names():
                self.bus.emit(
                    "error", "no maps saved yet — Record a rotation first"
                )
            else:
                title = self._idle_title()
                hint = f" (title reads: {title})" if title else ""
                self.bus.emit(
                    "error",
                    "no map resolved — pick one in the Map dropdown" + hint,
                )
            return
        entry.minimap_region = tuple(int(v) for v in region)
        # The save is explicit confirmation that the current screen is
        # this map — backfill identity fields the entry is missing.
        if not entry.map_name:
            title = self._idle_title()
            if title:
                entry.map_name = title
                self.bus.emit("map", f"map name stored: {title}")
        if not entry.fingerprint:
            img = (
                bot.minimap_frame()
                if bot is not None
                else feed.minimap_img() if feed is not None else None
            )
            if img is not None:
                from .vision.minimap import fingerprint

                mm = bot.minimap if bot is not None else feed.minimap
                c = mm.colors
                entry.fingerprint = fingerprint(
                    img,
                    ignore_colors=(c.player, c.other_player, c.rune),
                    include_colors=(c.ink or c.border,),
                ) or entry.fingerprint
        self.maps.save(entry)
        self.bus.emit(
            "map", f"layout saved for {entry.name}: {list(entry.minimap_region)}"
        )

    def _analyzers(self):
        """Live MinimapAnalyzers: the bot's (if running) and the feed's."""
        if self.bot is not None:
            yield self.bot.minimap
        if self._feed is not None:
            yield self._feed.minimap

    def _layout_reset(self) -> None:
        """Drop the live region on all analyzers -> next frame re-detects."""
        touched = False
        for mm in self._analyzers():
            mm.reset_region()
            touched = True
        self.bus.emit(
            "vision",
            "minimap layout reset — re-detecting"
            if touched else "no minimap to reset",
        )

    def _layout_set_region(self, msg: str) -> None:
        """layout|region|minimap|x,y,w,h — hand-drawn rect from Window view.

        Manual rects are treated as trusted (watchdog won't drop them),
        persisted to config.json, and can additionally be committed to
        the map file via Save layout.
        """
        parts = msg.split("|", 3)
        if len(parts) != 4:
            return
        which, payload = parts[2], parts[3]
        try:
            rect = tuple(int(float(v)) for v in payload.split(","))
        except (TypeError, ValueError):
            return
        if len(rect) != 4 or rect[2] < 10 or rect[3] < 10:
            return
        bot_cfg = getattr(self.config, "bot", None) or {}
        if which == "minimap":
            for mm in self._analyzers():
                mm.set_region(rect, explicit=True)
            self.bot_config.minimap_region = rect
            bot_cfg["minimap_region"] = list(rect)
            self.bus.emit("vision", f"minimap region set: {list(rect)}")
        elif which == "title":
            if self._feed is not None:
                self._feed._name_region = rect
            self.bot_config.minimap_name_region = rect
            bot_cfg["minimap_name_region"] = list(rect)
            self.bus.emit("vision", f"title region set: {list(rect)}")
        else:
            return
        self.config.bot = bot_cfg
        save_config(self.config)

    def _layout_clear(self) -> None:
        entry = self._resolved_map_entry()
        if entry is None or not entry.minimap_region:
            self.bus.emit("error", "resolved map has no stored layout")
            return
        entry.minimap_region = None
        self.maps.save(entry)
        self.bus.emit("map", f"layout cleared for {entry.name}")

    def _provide_frame(self, mode: str):
        bot = self.bot
        if mode == "title":
            feed = self._get_feed()
            if feed is None:
                return None
            if feed.name_region() is None:
                feed.minimap_img()  # force locate so the region resolves
            img = feed.name_img()
            if img is None:
                return {"state": "IDLE"}
            return {
                "img": img,
                "title": self._idle_title(),
                "layout": self._layout_source(),
                **self._map_meta(),
            }
        if mode == "window":
            if bot is not None:
                img = bot._window_capture()
                if img is None:
                    return None
                return {
                    "img": img,
                    "name_rect": bot._name_region(),
                    "layout": self._layout_source(),
                    **self._map_meta(),
                }
            feed = self._get_feed()
            if feed is None:
                return None
            return {
                "img": feed.window_img(),
                "name_rect": feed.name_region(),
                "title": self._idle_title(),
                "layout": self._layout_source(),
                **self._map_meta(),
            }
        if bot is not None:
            snap = bot.viz_snapshot() or {}
            snap["layout"] = self._layout_source()
            snap.update(self._map_meta())
            return snap
        feed = self._get_feed()
        if feed is None:
            return None
        img = feed.minimap_img()
        if img is None:
            return {"state": "IDLE"}
        return {
            "img": img,
            "player": feed.minimap.player_pos(img),
            "state": "IDLE",
            "title": self._idle_title(),
            "layout": self._layout_source(),
            **self._map_meta(),
        }

    # -- Dashboard commands ------------------------------------------------------
    def _handle_command(self, msg: str) -> bool:
        if msg == "map|list":
            self._send_maps()
        elif msg.startswith("map|set|"):
            self._set_map(msg.split("|", 2)[2])
        elif msg == "cal|start":
            self._cal_start()
        elif msg == "cal|mark":
            if self.calibrator:
                self.calibrator.mark()
        elif msg.startswith("cal|finish"):
            name = msg.split("|", 2)[2] if msg.count("|") >= 2 else ""
            self._cal_finish(name)
        elif msg == "cal|cancel":
            if self.calibrator:
                self.calibrator.stop()
                self.bus.emit("cal", "recording cancelled")
        elif msg.startswith("dash|view|"):
            self.streamer.set_mode(msg.split("|", 2)[2])
        elif msg == "layout|save":
            self._layout_save()
        elif msg == "layout|clear":
            self._layout_clear()
        elif msg == "layout|reset":
            self._layout_reset()
        elif msg.startswith("layout|region|"):
            self._layout_set_region(msg)
        elif msg == "events|history":
            self._send_history()
        elif msg == "config|get":
            self._send_config()
        elif msg == "host|state":
            self._send_host_state()
        elif msg.startswith("host|serial|"):
            self._connect_serial(msg.split("|", 2)[2])
        elif msg.startswith("host|window|"):
            self._set_window(msg.split("|", 2)[2])
        else:
            return False
        return True

    def _send_maps(self) -> None:
        payload = {
            "event": "maps",
            "maps": self.maps.names(),
            "active": self._active_map_override,
        }
        self.remote.broadcast("dash|" + json.dumps(payload))

    def _set_map(self, name: str) -> None:
        self._active_map_override = name or None
        self.bot_config.active_map = self._active_map_override
        self.bot_config.auto_select_map = not bool(name)
        if self.bot is not None:
            self.bot.config.active_map = self._active_map_override
            self.bot.config.auto_select_map = not bool(name)
        self.bus.emit("map", f"active map: {name or 'auto'}")
        self._send_maps()

    def _send_history(self) -> None:
        items = [e for e in self.bus.history() if e["kind"] != "calstat"]
        payload = {"event": "history", "items": items}
        self.remote.broadcast("dash|" + json.dumps(payload))

    # -- Host connection (serial port + window) --------------------------------
    @staticmethod
    def _list_ports() -> list:
        try:
            from serial.tools import list_ports

            return [
                {"device": p.device, "desc": p.description or ""}
                for p in list_ports.comports()
            ]
        except Exception:
            return []

    @staticmethod
    def _list_windows() -> list:
        try:
            import pygetwindow as gw

            return sorted(
                {t.strip() for t in gw.getAllTitles() if t and t.strip()}
            )
        except Exception:
            return []

    def _send_host_state(self) -> None:
        payload = {
            "event": "host",
            "serial": self.serial_port,
            "serial_open": bool(self.remote.serial_manager.is_open),
            "window": self.window_title,
            "ports": self._list_ports(),
            "windows": self._list_windows(),
        }
        self.remote.broadcast("dash|" + json.dumps(payload))

    def _connect_serial(self, port: str) -> None:
        port = port.strip()
        if port == "auto":
            self.bus.emit("host", "probing serial ports…")

            def probe() -> None:
                from .transport import discover_data_port

                found = discover_data_port()
                if found:
                    self._finish_serial_connect(found)
                else:
                    self.bus.emit("error", "no Pico DATA port discovered")
                    self._send_host_state()

            threading.Thread(target=probe, name="PortProbe", daemon=True).start()
            return
        self._finish_serial_connect(port)

    def _finish_serial_connect(self, port: str) -> None:
        if self.remote.connect_serial(port):
            self.serial_port = port
            self.config.serial_port = port
            save_config(self.config)
            self.bus.emit("host", f"serial: {port}")
        else:
            self.bus.emit("error", f"serial connect failed: {port}")
        self._send_host_state()

    def _set_window(self, title: str) -> None:
        title = title.strip()
        if not title or title == self.window_title:
            return
        self.window_title = title
        self.config.default_target_window = title
        save_config(self.config)
        with self._feed_lock:
            if self._feed is not None:
                try:
                    self._feed.close()
                except Exception:
                    pass
                self._feed = None
        self.bus.emit("host", f"window: {title}")
        self._send_host_state()

    def _send_config(self) -> None:
        from dataclasses import asdict

        data = asdict(self.bot_config)
        data["skills"] = {n: s.to_dict() for n, s in self.bot_config.skills.items()}
        data["rotation"] = self.bot_config.rotation.to_dict()
        data["minimap_colors"] = asdict(self.bot_config.minimap_colors)
        payload = {"event": "config", "config": data}
        self.remote.broadcast("dash|" + json.dumps(payload))

    # -- Calibration -------------------------------------------------------------
    def _cal_start(self) -> None:
        feed = self._get_feed()
        if feed is None:
            self.bus.emit("error", "calibration needs the game window")
            return
        from .bot.calibrate import CalibrationRunner

        self.calibrator = CalibrationRunner(
            feed.minimap_img,
            feed.minimap.player_pos,
            event=lambda k, m, d=None: self.bus.emit(k, m, d),
        )
        img = feed.minimap_img()
        wh = (img.shape[1], img.shape[0]) if img is not None else (200, 150)
        self.calibrator.start(wh)

    def _cal_finish(self, name: str) -> None:
        if not self.calibrator:
            return
        if not name:
            # Blank = save onto the currently resolved map; fall back to
            # the OCR'd title, then 'unnamed'.
            resolved = self._resolved_map_entry()
            name = (
                resolved.name
                if resolved is not None
                else (self._idle_title() or "unnamed")
            )
            self.bus.emit("cal", f"auto-named map: {name}")
        feed = self._get_feed()
        fp = None
        map_name = None
        if feed is not None:
            img = feed.minimap_img()
            if img is not None:
                from .vision.minimap import fingerprint

                c = feed.minimap.colors
                fp = fingerprint(
                    img,
                    ignore_colors=(c.player, c.other_player, c.rune),
                    include_colors=(c.ink or c.border,),
                ) or None
            title_img = feed.name_img()
            if title_img is not None:
                map_name = self._reader().read(title_img)
                if map_name:
                    self.bus.emit("cal", f"map name read: {map_name}")
        try:
            entry = self.calibrator.finish(
                name,
                fingerprint=fp,
                map_name=map_name,
                minimap_region=feed.minimap.region if feed else None,
            )
            path = self.maps.save(entry)
            self.maps.reload()
            self.bus.emit("cal", f"map saved: {path}")
            self._send_maps()
        except Exception as exc:
            self.bus.emit("error", f"calibration save failed: {exc}")

    def _hook_keyboard(self) -> None:
        """Global key hook so calibration can attribute skill presses."""
        try:
            import keyboard

            def on_press(event):
                if (
                    self.calibrator
                    and self.calibrator.running
                    and event.event_type == "down"
                ):
                    # F9 marks an anchor without leaving the game window.
                    if event.name == "f9":
                        self.calibrator.mark()
                    self.calibrator.record_key(event.name)

            keyboard.hook(on_press)
        except Exception as exc:
            logger.info("keyboard hook unavailable: %s", exc)


def main() -> None:
    parser = argparse.ArgumentParser(prog="picobot.serve")
    parser.add_argument("--port", default=None, help="Pico DATA COM port")
    parser.add_argument("--window", default=None, help="game window title")
    parser.add_argument("--ws", type=int, default=None, help="WS port")
    parser.add_argument("--http", type=int, default=None, help="HTTP port")
    args = parser.parse_args()

    configure_logging()
    config = load_config()
    if args.ws:
        config.ws_port = args.ws
    if args.http:
        config.http_port = args.http
    port = args.port
    if port == "auto":
        from .transport import discover_data_port

        port = discover_data_port()
        if port is None:
            parser.exit(2, "no Pico DATA port discovered\n")
        print(f"discovered Pico DATA port: {port}")
    if port is None:
        # Fall back to the port remembered from the dashboard/last run.
        port = config.serial_port or None
    elif port != config.serial_port:
        config.serial_port = port
        save_config(config)
    window_title = args.window or config.default_target_window
    if args.window and args.window != config.default_target_window:
        config.default_target_window = args.window
        save_config(config)
    BotHost(port, window_title, config).run()


if __name__ == "__main__":
    main()
