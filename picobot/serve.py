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
        bot_config,
        identity=None,
        on_event=None,
    ) -> None:
        from .vision.game_window import GameWindow
        from .vision.minimap import MinimapAnalyzer
        from .vision.screen import ScreenGrabber

        self.window = GameWindow(window_title)
        self.screen = ScreenGrabber()
        self.config = bot_config
        self.identity = identity
        self.minimap = MinimapAnalyzer(
            colors=bot_config.minimap_colors,
            region=bot_config.minimap_region,
            marker_inset=bot_config.marker_inset_px,
        )
        self._on_event = on_event

    def minimap_img(self):
        """Minimap capture; also drives blackout detection and title
        reads while the bot isn't running."""
        if self.minimap.region is None:
            full = self.window_img()
            if full is None or self.minimap.locate(full) is None:
                return None
        x, y, w, h = self.minimap.region
        img = self.screen.capture(
            (self.window.client_left + x, self.window.client_top + y, w, h)
        )
        if img is not None and self.minimap.note_frame(
            img, context=lambda: {"window": self.window_img()}
        ):
            if self._on_event:
                self._on_event("vision", "arrived on a new map — re-detecting minimap")
            if self.identity is not None:
                self.identity.request("arrival", clear=True)
        elif self.minimap.edge_lost and self.minimap.relocate(self.window_img()):
            if self._on_event:
                self._on_event(
                    "vision", f"minimap panel moved: {list(self.minimap.region)}"
                )
            if self.identity is not None and self.identity.current.title is None:
                self.identity.request("panel moved")
        if self.identity is not None and not self.minimap.loading:
            self.identity.pump(self.name_img)
        return img

    def window_img(self):
        l, t, r, b = self.window.client_rect()
        return self.screen.capture((l, t, r - l, b - t))

    def name_region(self):
        """Client-area rect of the title band, or None."""
        from .vision.mapname import name_strip_region

        if self.config.minimap_name_region:
            return self.config.minimap_name_region
        if self.minimap.region is None:
            return None
        return name_strip_region(self.minimap.region, self.config.name_scan_px)

    def name_img(self):
        region = self.name_region()
        if region is None:
            return None
        x, y, w, h = region
        return self.screen.capture(
            (self.window.client_left + x, self.window.client_top + y, w, h)
        )

    def close(self) -> None:
        self.screen.close()


def _offset_meta(snap: dict, dx: int, dy: int) -> None:
    """Shift region-space overlay coords by (dx, dy) — used when the
    frame image is the panel composite (title zone above the map) so
    platforms/walls/anchors still land on the minimap."""
    if not dx and not dy:
        return
    walls = snap.get("walls")
    if walls:
        snap["walls"] = {k: v + dx for k, v in walls.items()}
    if snap.get("floor") is not None:
        snap["floor"] += dy
    if snap.get("platforms"):
        snap["platforms"] = [
            (a + dx, b + dy, c + dx, d + dy)
            for a, b, c, d in snap["platforms"]
        ]
    if snap.get("anchors"):
        snap["anchors"] = [(x + dx, y + dy) for x, y in snap["anchors"]]
    for key in ("player", "target", "rune"):
        pos = snap.get(key)
        if pos:
            snap[key] = (pos[0] + dx, pos[1] + dy)


class BotHost:
    """Wires transport, bot, streamer, and calibration together."""

    def __init__(
        self,
        serial_port: Optional[str],
        window_title: str,
        config: Optional[AppConfig] = None,
    ) -> None:
        from .bot import BotConfig
        from .bot.calibrate import CalibrationRunner
        from .bot.identity import MapIdentity
        from .bot.maps import MapStore

        self.config = config or load_config()
        self.bot_config = BotConfig.from_dict(getattr(self.config, "bot", None))
        self.window_title = window_title
        self.serial_port = serial_port
        self.bus = EventBus()
        self.telegram = TelegramHandler(self.config.bot_token, self.config.chat_id)
        self.maps = MapStore(self.bot_config.maps_dir)
        self._feed: Optional[_VisionFeed] = None
        self._feed_lock = threading.Lock()
        self.identity = MapIdentity(
            self.maps,
            pin=self.bot_config.active_map,
            ocr_enabled=self.bot_config.name_ocr,
            on_event=lambda k, m: self.bus.emit(k, m),
        )
        self._maps_sent_version = -1

        self.bot = None
        self.bot_thread: Optional[threading.Thread] = None
        self.calibrator: Optional[CalibrationRunner] = None

        callbacks = RemoteCallbacks(
            schedule=lambda fn: fn(),
            # TX:/RX: wire chatter rides at debug with the hid relays.
            log=lambda m: self.bus.emit(
                "remote", m,
                level="debug" if m.startswith(("TX:", "RX:")) else "info",
            ),
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
            self._provide_frame,
            self.remote.broadcast,
            interval=1.0 / max(1.0, float(self.config.view_fps)),
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
        from .vision import framelog

        framelog.configure_from(
            self.bot_config,
            on_saved=lambda p: self.bus.emit(
                "vision", f"frames captured: {p}", level="debug"
            ),
        )
        self.identity.request("startup")
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

            # Share the feed's analyzer (region + provenance + transition
            # state) and the identity, so bot start re-detects nothing.
            feed = self._get_feed()
            self.bot = SmartBot(
                HidController(send),
                self.window_title,
                self.bot_config,
                minimap=feed.minimap if feed is not None else None,
                identity=self.identity,
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
                        self.bot_config,
                        identity=self.identity,
                        on_event=lambda k, m: self.bus.emit(k, m),
                    )
                except Exception as exc:
                    logger.warning("vision feed unavailable: %s", exc)
                    return None
            return self._feed

    def _layout_source(self):
        """Provenance of the live minimap region: explicit/stored/auto."""
        bot = self.bot
        mm = bot.minimap if bot is not None else (
            self._feed.minimap if self._feed is not None else None
        )
        if mm is None or mm.region is None:
            return None
        return mm.region_source

    def _resolved_entry(self):
        """The identity's resolved map (fresh store copy), or None."""
        return self.identity.entry()

    def _sync_maps_payload(self) -> None:
        """Broadcast the maps payload whenever identity changed."""
        if self.identity.version != self._maps_sent_version:
            self._send_maps()

    def _map_meta(self) -> dict:
        """Frame metadata: resolved map, title evidence, overlays, and
        whether a rotation can run."""
        self._sync_maps_payload()
        bot = self.bot
        entry = self._resolved_entry()
        res = self.identity.current
        rot = (
            entry.rotation
            if entry is not None
            else (bot.effective_rotation() if bot else self.bot_config.rotation)
        )
        region = None
        if bot is not None:
            region = bot.minimap.region
        if region is None:
            feed = self._get_feed()
            region = feed.minimap.region if feed is not None else None
        w, h = (region[2], region[3]) if region else (0, 0)

        def px(v, span):
            v = float(v)
            return int(round(v * span)) if 0.0 <= v <= 1.0 else int(round(v))

        walls = floor = anchors = platforms = None
        if entry is not None and entry.platforms and w and h:
            platforms = [
                (
                    int(round(s[0] * w)), int(round(s[1] * h)),
                    int(round(s[2] * w)), int(round(s[3] * h)),
                )
                for s in entry.platforms
            ]
        if entry is not None and entry.walls and w:
            walls = {
                side: px(v, w)
                for side, v in (
                    ("left", entry.walls.get("left")),
                    ("right", entry.walls.get("right")),
                )
                if isinstance(v, (int, float))
            } or None
            fy = entry.walls.get("floor")
            if h and isinstance(fy, (int, float)):
                floor = px(fy, h)
        if entry is not None and entry.rotation.anchors and w and h:
            anchors = [(px(a.x, w), px(a.y, h)) for a in entry.rotation.anchors]
        conf = (
            res.score
            if entry is not None and res.title_map == entry.name else None
        )
        return {
            "map": entry.name if entry else None,
            "map_via": res.via,
            "map_conf": conf,
            "map_title": res.title,
            "no_rotation": not bool(rot.anchors),
            "walls": walls,
            "floor": floor,
            "anchors": anchors,
            "platforms": platforms,
        }

    def _layout_target(self, name: str):
        """Which map file a layout write applies to, or (None, error).

        Explicit ``name`` = the user asserts the identity (a stub file is
        created if needed). Blank = the map must be verified by its OCR'd
        title — a pin alone is not evidence of what's on screen.
        """
        if name:
            entry = self.maps.get(name)
            if entry is None:
                from .bot.maps import MapEntry

                entry = MapEntry(name=name)
            return entry, None
        res = self.identity.current
        entry = self._resolved_entry()
        if entry is not None and res.via == "ocr":
            return entry, None
        return None, (
            "current map isn't verified by its title — type the map name "
            "to save anyway"
        )

    def _save_entry(self, entry) -> None:
        self.maps.save(entry)
        self.maps.reload()
        self.identity.refresh()
        self._send_maps()

    def _layout_save(self, name: str = "") -> None:
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
        entry, err = self._layout_target(name.strip())
        if entry is None:
            self.bus.emit("error", err)
            return
        entry.minimap_region = tuple(int(v) for v in region)
        res = self.identity.current
        if not entry.map_name and res.title and res.title_map in (None, entry.name):
            entry.map_name = res.title
        self._save_entry(entry)
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

        Manual rects are treated as trusted (arrival won't drop them),
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
        if which == "title":
            # Hand-drawn title band — wins over auto detection entirely.
            self.bot_config.minimap_name_region = rect
            bot_cfg = getattr(self.config, "bot", None) or {}
            bot_cfg["minimap_name_region"] = list(rect)
            self.config.bot = bot_cfg
            save_config(self.config)
            self.identity.request("title region")
            self.bus.emit("vision", f"title region set: {list(rect)}")
            return
        if which != "minimap":
            return
        for mm in self._analyzers():
            mm.set_region(rect, explicit=True)
        self.bot_config.minimap_region = rect
        bot_cfg = getattr(self.config, "bot", None) or {}
        bot_cfg["minimap_region"] = list(rect)
        self.config.bot = bot_cfg
        save_config(self.config)
        self.bus.emit("vision", f"minimap region set: {list(rect)}")

    def _layout_clear(self, name: str = "") -> None:
        entry, err = self._layout_target(name.strip())
        if entry is None:
            self.bus.emit("error", err)
            return
        if not entry.minimap_region:
            self.bus.emit("error", f"{entry.name} has no stored layout")
            return
        entry.minimap_region = None
        self._save_entry(entry)
        self.bus.emit("map", f"layout cleared for {entry.name}")

    def _layout_set_wall(self, msg: str) -> None:
        """layout|wall|left|right|floor|clear[|<name>] — map boundaries.

        ``left``/``right`` pin a wall at the player's current minimap x
        (inside it the weave faces inward); ``floor`` pins the player's
        y as the map's bottom — no downward movement is attempted there.
        ``clear`` removes them all. Unlike the global ``wall_zone_px``
        edge margins, these work on maps whose play area doesn't span
        the minimap edge-to-edge.
        """
        parts = msg.split("|", 3)
        side = parts[2] if len(parts) > 2 else ""
        name = parts[3].strip() if len(parts) > 3 else ""
        if side not in ("left", "right", "floor", "clear"):
            return
        entry, err = self._layout_target(name)
        if entry is None:
            self.bus.emit("error", err)
            return
        if side == "clear":
            entry.walls = None
            self._save_entry(entry)
            self.bus.emit("map", f"walls cleared for {entry.name}")
            return
        pos = region = None
        bot = self.bot
        if bot is not None:
            img = bot.viz.get("img")
            pos = bot.minimap.player_pos(img) if img is not None else None
            region = bot.minimap.region
        feed = self._get_feed()
        if pos is None and feed is not None:
            img = feed.minimap_img()
            pos = feed.minimap.player_pos(img) if img is not None else None
        if not region and feed is not None:
            region = feed.minimap.region
        if pos is None or not region:
            self.bus.emit(
                "error", "no player position — can't place a wall"
            )
            return
        span = region[3] if side == "floor" else region[2]
        if not span:
            self.bus.emit("error", "no minimap region — can't place a wall")
            return
        walls = dict(entry.walls or {})
        v = pos[1] if side == "floor" else pos[0]
        if side == "floor" and entry.platforms:
            # Snap onto the drawn platform under the player — the boundary
            # should sit on the floor line, not hover above it.
            from .vision.minimap import platform_row_at

            snapped = platform_row_at(
                self._platforms_px(entry), pos[0], pos[1], max_snap=10
            )
            if isinstance(snapped, int):
                v = snapped
        walls[side] = round(max(0.0, min(1.0, v / span)), 4)
        entry.walls = walls
        self._save_entry(entry)
        axis = "y" if side == "floor" else "x"
        self.bus.emit(
            "map", f"{side} wall set at {axis}={v} for {entry.name}"
        )

    def _layout_platform(self, msg: str) -> None:
        """layout|plat|x0,y0,x1,y1|undo|clear[|<name>] — drawn platforms.

        Each drag on the Minimap view appends a segment (minimap px here,
        stored normalized). Platforms are hand-drawn — the authoritative
        walkable geometry for anchor snapping, weave bounds and floor
        placement, since auto-detecting translucent minimap lines proved
        too fragile.
        """
        parts = msg.split("|", 3)
        if len(parts) < 3:
            return
        payload, name = parts[2], (parts[3].strip() if len(parts) > 3 else "")
        entry, err = self._layout_target(name)
        if entry is None:
            self.bus.emit("error", err)
            return
        plats = list(entry.platforms or [])
        if payload == "undo":
            if not plats:
                self.bus.emit("map", f"{entry.name}: no platforms to undo")
                return
            plats.pop()
            entry.platforms = plats or None
            self._save_entry(entry)
            self.bus.emit(
                "map",
                f"{entry.name}: undid platform ({len(plats)} left)",
            )
            return
        if payload == "clear":
            entry.platforms = None
            self._save_entry(entry)
            self.bus.emit("map", f"platforms cleared for {entry.name}")
            return
        try:
            seg = [float(v) for v in payload.split(",")]
        except (TypeError, ValueError):
            return
        if len(seg) != 4 or (seg[0] - seg[2]) ** 2 + (seg[1] - seg[3]) ** 2 < 16:
            return  # degenerate / accidental click — ignore
        img = None
        bot = self.bot
        if bot is not None:
            img = bot.viz.get("img")
        feed = self._get_feed()
        if img is None and feed is not None:
            img = feed.minimap_img()
        if img is None:
            self.bus.emit("error", "no minimap frame — can't draw platforms")
            return
        h, w = img.shape[:2]
        if not all(0 <= v for v in seg) or seg[0] > w or seg[2] > w \
                or seg[1] > h or seg[3] > h:
            self.bus.emit("error", "platform drag is off the minimap")
            return
        plats.append([
            round(seg[0] / w, 4), round(seg[1] / h, 4),
            round(seg[2] / w, 4), round(seg[3] / h, 4),
        ])
        entry.platforms = plats
        self._save_entry(entry)
        self.bus.emit("map", f"{entry.name}: platform {len(plats)} drawn")

    def _platforms_px(self, entry) -> list:
        """A map's drawn platform segments converted to minimap px."""
        if not entry.platforms:
            return []
        region = None
        if self.bot is not None:
            region = self.bot.minimap.region
        if not region:
            feed = self._get_feed()
            region = feed.minimap.region if feed is not None else None
        if not region:
            return []
        w, h = region[2], region[3]
        return [
            (s[0] * w, s[1] * h, s[2] * w, s[3] * h) for s in entry.platforms
        ]

    def _provide_frame(self, mode: str):
        bot = self.bot
        if mode == "title":
            # Verification view: the segmented title band with the
            # detected text lines boxed — exactly what OCR sees.
            from .remote.streamer import annotate_title

            img = None
            if bot is not None:
                try:
                    img = bot.name_img()
                except Exception:
                    img = None
            if img is None:
                feed = self._get_feed()
                if feed is not None:
                    img = feed.name_img()
            if img is None:
                return {"state": "IDLE"}
            meta = self._map_meta()
            meta.pop("walls", None)
            meta.pop("floor", None)
            meta.pop("platforms", None)
            meta.pop("anchors", None)
            return {
                "img": annotate_title(img),
                "layout": self._layout_source(),
                **meta,
            }
        if mode == "window":
            meta = self._map_meta()
            meta.pop("walls", None)   # minimap-relative — meaningless here
            meta.pop("floor", None)
            meta.pop("platforms", None)
            if bot is not None:
                img = bot._window_capture()
                if img is None:
                    return None
                return {"img": img, "layout": self._layout_source(), **meta}
            feed = self._get_feed()
            if feed is None:
                return None
            return {
                "img": feed.window_img(),
                "layout": self._layout_source(),
                **meta,
            }
        band = band_rect = None
        region = None
        if bot is not None:
            snap = bot.viz_snapshot() or {}
            snap["layout"] = self._layout_source()
            meta = self._map_meta()
            if not meta.get("anchors"):
                # The bot's own viz anchors win; meta fills them only
                # when the bot hasn't resolved the map yet.
                meta.pop("anchors", None)
            snap.update(meta)
            region = bot.minimap.region
            try:
                band_rect = bot.name_region()
                band = bot.name_img()
            except Exception:
                band = None
        else:
            feed = self._get_feed()
            if feed is None:
                return None
            img = feed.minimap_img()
            if img is None:
                return {"state": "IDLE"}
            snap = {
                "img": img,
                "player": feed.minimap.player_pos(img),
                "state": "IDLE",
                "layout": self._layout_source(),
                **self._map_meta(),
            }
            region = feed.minimap.region
            band_rect = feed.name_region()
            band = feed.name_img()
        # The minimap view is the whole located panel: title strip on
        # top, map below, separated at the panel divider. Its right edge
        # reaches the title text's end so long names aren't clipped.
        img = snap.get("img")
        if img is not None and region is not None:
            from .remote.streamer import assemble_panel

            band_xy = tuple(band_rect[:2]) if band_rect else (0, 0)
            comp, dx, dy = assemble_panel(
                band, img, region, band_xy=band_xy
            )
            snap["img"] = comp
            snap["ox"], snap["oy"] = dx, dy
            _offset_meta(snap, dx, dy)
        return snap

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
        elif msg.startswith("dash|fps|"):
            self._fps_set(msg.split("|", 2)[2])
        elif msg == "layout|save" or msg.startswith("layout|save|"):
            self._layout_save(msg.split("|", 2)[2] if msg.count("|") > 1 else "")
        elif msg == "layout|clear" or msg.startswith("layout|clear|"):
            self._layout_clear(msg.split("|", 2)[2] if msg.count("|") > 1 else "")
        elif msg.startswith("layout|wall|"):
            self._layout_set_wall(msg)
        elif msg.startswith("layout|plat|"):
            self._layout_platform(msg)
        elif msg == "layout|reset":
            self._layout_reset()
        elif msg.startswith("layout|region|"):
            self._layout_set_region(msg)
        elif msg == "skills|list":
            self._send_skills()
        elif msg.startswith("skills|set|"):
            self._skills_set(msg.split("|", 2)[2])
        elif msg.startswith("skills|del|"):
            self._skills_del(msg.split("|", 2)[2])
        elif msg.startswith("movekeys|set|"):
            self._movekeys_set(msg.split("|", 2)[2])
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
        self._maps_sent_version = self.identity.version
        res = self.identity.current
        payload = {
            "event": "maps",
            "maps": self.maps.names(),
            "active": self.identity.pin,
            "detected": res.name,
            "via": res.via,
            "title": res.title,
            "score": res.score if res.title_map else None,
            "reading": self.identity.pending,
        }
        self.remote.broadcast("dash|" + json.dumps(payload))

    def _set_map(self, name: str) -> None:
        pin = name or None
        self.bot_config.active_map = pin
        self.bot_config.auto_select_map = not bool(name)
        if self.bot is not None:
            self.bot.config.active_map = pin
            self.bot.config.auto_select_map = not bool(name)
        self.identity.set_pin(pin)
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
        data["view_fps"] = self.config.view_fps
        payload = {"event": "config", "config": data}
        self.remote.broadcast("dash|" + json.dumps(payload))

    def _fps_set(self, payload: str) -> None:
        """dash|fps|<float> — live stream rate, clamped 1–30 fps."""
        try:
            fps = float(payload)
        except ValueError:
            self.bus.emit("error", f"invalid fps: {payload!r}")
            return
        fps = min(30.0, max(1.0, fps))
        self.streamer.interval = 1.0 / fps
        self.config.view_fps = fps
        save_config(self.config)
        self.bus.emit("host", f"view fps: {fps:g}")
        self._send_config()

    # -- Skills editor -------------------------------------------------------------
    def _send_skills(self) -> None:
        payload = {
            "event": "skills",
            "skills": {
                n: s.to_dict() for n, s in self.bot_config.skills.items()
            },
        }
        self.remote.broadcast("dash|" + json.dumps(payload))

    def _skills_set(self, payload: str) -> None:
        from .bot.skills import Skill

        try:
            spec = json.loads(payload)
            skill = Skill.from_dict(str(spec["name"]), spec)
        except (ValueError, KeyError, TypeError,
                json.JSONDecodeError) as exc:
            self.bus.emit("error", f"invalid skill spec: {exc}")
            return
        self.bot_config.skills[skill.name] = skill
        self._skills_commit()
        self.bus.emit(
            "skill", f"skill saved: {skill.name} ({skill.kind}, {skill.key})"
        )

    def _skills_del(self, name: str) -> None:
        if not self.bot_config.skills.pop(name, None):
            self.bus.emit("error", f"no such skill: {name}")
            return
        self._skills_commit()
        self.bus.emit("skill", f"skill removed: {name}")

    def _skills_commit(self) -> None:
        """Persist skills to config.json and update a running bot live."""
        bot_cfg = getattr(self.config, "bot", None) or {}
        # Materialize the full effective set so a legacy attack_keys config
        # doesn't lose its synthesized skills on the first UI edit.
        bot_cfg["skills"] = {
            n: s.to_dict() for n, s in self.bot_config.skills.items()
        }
        self.config.bot = bot_cfg
        save_config(self.config)
        bot = self.bot
        if bot is not None:
            merged = dict(self.bot_config.skills)
            if bot._map is not None:
                merged.update(bot._map.skills)
            # Mutate in place so cooldown timestamps survive the edit.
            bot.skills.skills.clear()
            bot.skills.skills.update(merged)
        self._send_skills()

    def _movekeys_set(self, payload: str) -> None:
        """movekeys|set|{json} — movement keybinds (jump/rope-lift/flash)."""
        try:
            spec = json.loads(payload)
        except json.JSONDecodeError:
            return
        cfg = self.bot_config
        changed = {}
        if "jump_key" in spec:
            cfg.jump_key = str(spec["jump_key"] or "space").strip().lower()
            changed["jump_key"] = cfg.jump_key
        if "up_jump_skill_key" in spec:
            v = spec["up_jump_skill_key"]
            cfg.up_jump_skill_key = str(v).strip().lower() if v else None
            changed["up_jump_skill_key"] = cfg.up_jump_skill_key
        if "flash_jump_key" in spec:
            v = spec["flash_jump_key"]
            cfg.flash_jump_key = str(v).strip().lower() if v else None
            changed["flash_jump"] = {"key": cfg.flash_jump_key}
        if "flash_jump_enabled" in spec:
            cfg.flash_jump_enabled = bool(spec["flash_jump_enabled"])
            changed.setdefault("flash_jump", {})[
                "enabled"] = cfg.flash_jump_enabled
        if "nav_threshold_px" in spec and spec["nav_threshold_px"] is not None:
            cfg.nav_threshold_px = min(15, max(2, int(spec["nav_threshold_px"])))
            changed["nav_threshold_px"] = cfg.nav_threshold_px
        if "wall_zone_px" in spec and spec["wall_zone_px"] is not None:
            cfg.wall_zone_px = min(60, max(0, int(spec["wall_zone_px"])))
            changed["wall_zone_px"] = cfg.wall_zone_px
        if "dwell_weave" in spec:
            cfg.dwell_weave = bool(spec["dwell_weave"])
            changed["dwell_weave"] = cfg.dwell_weave
        if not changed:
            return
        # bot.config is the same BotConfig — live fields, nothing else to do.
        bot_cfg = getattr(self.config, "bot", None) or {}
        if "jump_key" in changed:
            bot_cfg["jump_key"] = cfg.jump_key
        if "up_jump_skill_key" in changed:
            bot_cfg["up_jump_skill_key"] = cfg.up_jump_skill_key
        if "flash_jump" in changed:
            fj = dict(bot_cfg.get("flash_jump") or {})
            fj.update(changed["flash_jump"])
            bot_cfg["flash_jump"] = fj
        for k in ("nav_threshold_px", "wall_zone_px", "dwell_weave"):
            if k in changed:
                bot_cfg[k] = changed[k]
        self.config.bot = bot_cfg
        save_config(self.config)
        self.bus.emit(
            "bot",
            "movement keys: " + ", ".join(
                f"{k}={v}" for k, v in changed.items()
            ),
        )
        self._send_config()

    # -- Calibration -------------------------------------------------------------
    def _cal_start(self) -> None:
        feed = self._get_feed()
        if feed is None:
            self.bus.emit("error", "calibration needs the game window")
            return
        from .bot.calibrate import CalibrationRunner

        def _snap(img, x, y):
            # Snap onto the map's drawn platforms. ``y`` back means "no
            # geometry here to verify against"; None means "off a drawn
            # platform" — the runner warns on the latter.
            from .vision.minimap import platform_covered, platform_row_at

            entry = self._resolved_entry()
            segs = self._platforms_px(entry) if entry is not None else []
            if not segs or not platform_covered(segs, x):
                return y
            return platform_row_at(segs, x, y)

        self.calibrator = CalibrationRunner(
            feed.minimap_img,
            feed.minimap.player_pos,
            event=lambda k, m, d=None: self.bus.emit(k, m, d),
            snap_fn=_snap,
        )
        img = feed.minimap_img()
        wh = (img.shape[1], img.shape[0]) if img is not None else (200, 150)
        self.calibrator.start(wh)

    def _cal_finish(self, name: str) -> None:
        if not self.calibrator:
            return
        res = self.identity.current
        if not name:
            resolved = self._resolved_entry()
            name = resolved.name if resolved is not None else "unnamed"
            self.bus.emit("cal", f"auto-named map: {name}")
        feed = self._get_feed()
        map_name = res.title if res.title_map in (None, name) else None
        if self.bot_config.name_ocr and not map_name:
            self.bus.emit(
                "notify",
                "map title not read yet — saved without map_name; title "
                "matching will skip this map until a save captures it "
                "(check the Title view)",
            )
        try:
            entry = self.calibrator.finish(
                name,
                minimap_region=feed.minimap.region if feed else None,
                key_map={
                    s.key: s for s in self.bot_config.skills.values()
                },
                # Saving re-records anchors only — walls/platforms/
                # title must survive from the existing file.
                existing=self.maps.get(name),
                map_name=map_name,
            )
            path = self.maps.save(entry)
            self.maps.reload()
            self.identity.refresh()
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
