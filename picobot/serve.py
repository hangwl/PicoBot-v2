"""Headless PicoBot host: serial transport + smart bot + web dashboard.

``python -m picobot.serve --port COM3 --window "Eluna (x64)"``

Owns the WebSocket/HTTP remote server, the SmartBot lifecycle, the frame
streamer feeding the dashboard, and move measurement. There is no
desktop UI — the dashboard *is* the UI (locally or over Tailscale).
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import ssl
import threading
import time
from pathlib import Path
from typing import Optional

from .bot.skills import Skill
from .config import AppConfig, load_config, save_config
from .events import EventBus
from .messaging import TelegramHandler
from .remote import EmbeddedHTTPServer, RemoteCallbacks, RemoteControlServer
from .remote.streamer import FrameStreamer
from .settings import configure_logging

logger = logging.getLogger(__name__)


class _VisionFeed:
    """Window/minimap captures for previews and layout
    commands, plus the :class:`MapMonitor` thread that does map-change
    detection and title reads. Lazily created — pygetwindow/mss only
    exist on the host machine.
    """

    def __init__(
        self,
        window_title: str,
        bot_config,
        identity=None,
        on_event=None,
    ) -> None:
        from .bot.monitor import MapMonitor
        from .vision.game_window import GameWindow
        from .vision.minimap import MinimapAnalyzer
        from .vision.screen import ScreenGrabber

        self.window = GameWindow(window_title)
        self.screen = ScreenGrabber()
        self.config = bot_config
        self.minimap = MinimapAnalyzer(
            colors=bot_config.minimap_colors,
            region=bot_config.minimap_region,
            marker_inset=bot_config.marker_inset_px,
        )
        self.monitor = MapMonitor(
            self.window, self.minimap, identity,
            config=bot_config, on_event=on_event,
        )
        self.monitor.start()

    def _capture(self, rect):
        if rect is None:
            return None
        x, y, w, h = rect
        return self.screen.capture(
            (self.window.client_left + x, self.window.client_top + y, w, h)
        )

    def minimap_img(self):
        """Minimap capture, or None until the monitor locates the panel."""
        return self._capture(self.minimap.region)

    def window_img(self):
        l, t, r, b = self.window.client_rect()
        return self.screen.capture((l, t, r - l, b - t))

    def name_region(self):
        """Client-area rect of the title band, or None."""
        return self.monitor.name_region()

    def name_img(self):
        return self._capture(self.name_region())

    def close(self) -> None:
        self.monitor.stop()
        self.screen.close()


def _kit_summary(cfg) -> str:
    """One-line class-kit summary for logs and the status strip."""
    travel = cfg.class_travel
    extra = f" ({cfg.teleport_key})" if travel == "teleport" else ""
    attacks = "air attacks" if cfg.air_attacks else "attacks on landing"
    return f"{travel}{extra}, {attacks}"


# BotConfig fields a class profile may override (see BotConfig._apply_class).
_CLASS_FIELDS = (
    "class_travel", "air_attacks", "teleport_key", "teleport_cooldown",
    "jump_key", "up_jump_skill_key", "flash_jump_key", "flash_jump_enabled",
    "skills",
)

# Frame-meta keys holding minimap-px coordinates.
_MINIMAP_OVERLAYS = (
    "platforms", "ropes", "anchors", "nav_edges", "nav_plan", "nav_route",
    "player", "player_box", "target", "rune",
)


def _offset_meta(snap: dict, dx: int, dy: int) -> None:
    """Shift region-space overlay coords by (dx, dy) — used when the
    frame image is the panel composite (title zone above the map) so
    platforms/anchors still land on the minimap."""
    if not dx and not dy:
        return
    for key in ("platforms", "ropes"):
        if snap.get(key):
            snap[key] = [
                (a + dx, b + dy, c + dx, d + dy)
                for a, b, c, d in snap[key]
            ]
    if snap.get("anchors"):
        snap["anchors"] = [(x + dx, y + dy) for x, y in snap["anchors"]]
    for key in ("nav_edges", "nav_plan", "nav_route"):
        if snap.get(key):
            snap[key] = [
                (k, a + dx, b + dy, c + dx, d + dy)
                for k, a, b, c, d in snap[key]
            ]
    for key in ("player", "target", "rune"):
        pos = snap.get(key)
        if pos:
            snap[key] = (pos[0] + dx, pos[1] + dy)
    box = snap.get("player_box")
    if box:
        snap["player_box"] = (box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy)


class BotHost:
    """Wires transport, bot, streamer, and measurement together."""

    def __init__(
        self,
        serial_port: Optional[str],
        window_title: str,
        config: Optional[AppConfig] = None,
        *,
        debug_frames: bool = False,
    ) -> None:
        from .bot import BotConfig
        from .bot.identity import MapIdentity
        from .bot.maps import MapStore

        self.config = config or load_config()
        self.bot_config = BotConfig.from_dict(getattr(self.config, "bot", None))
        self.window_title = window_title
        self.serial_port = serial_port
        self.debug_frames = debug_frames
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
        self._nav_show = False
        self._nav_preview = None      # (legs, expires_at)
        from .bot.navgraph import GraphCache
        from .bot.reach import ReachModel, base_reach

        self._nav_cache = GraphCache()
        self.reach = ReachModel(
            base_reach(self.bot_config), path=self.bot_config.nav_reach_file
        )

        self.bot = None
        self.bot_thread: Optional[threading.Thread] = None
        self._bot_stop = threading.Event()
        self.measurer = None
        from .bot.platform_fit import PlatformFit

        # Where the feet settle on each drawn platform (diagnostic only).
        self.platfit = PlatformFit(on_change=self._platfit_changed)
        self._platfit_sent = 0.0
        # (map, field) -> snapshots of the segment list before each edit.
        self._layout_undo: dict = {}

        callbacks = RemoteCallbacks(
            schedule=lambda fn: fn(),
            # TX:/RX: wire chatter rides at debug with the hid relays.
            log=lambda m: self.bus.emit(
                "remote", m,
                level="debug" if m.startswith(("TX:", "RX:")) else "info",
            ),
            set_status=lambda m: self.bus.emit("status", m),
            set_ws_port=lambda p: self.bus.emit("status", f"ws port: {p}"),
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
            self.remote.broadcast_frame,
            interval=1.0 / max(1.0, float(self.config.view_fps)),
            active=self.remote.has_frame_clients,
            on_stall=self._stream_stalled,
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
            self.debug_frames,
            on_saved=lambda p: self.bus.emit(
                "vision", f"frames captured: {p}", level="debug"
            ),
        )
        if self.debug_frames:
            self.bus.emit(
                "status", f"debug frames → {self.bot_config.debug_capture_dir}"
            )
        self.identity.request("startup")
        self.remote.start()
        self.http = EmbeddedHTTPServer(
            lambda: self.remote.ws_port, self.config.http_port,
            static_dir=(
                Path(__file__).resolve().parent.parent / "web" / "dist"
            ),
            ws_scheme="wss" if self.remote.ssl_context else "ws",
            # Every page request lands in the event log (debug), so a phone
            # that "won't load" shows whether its request ever arrived.
            on_log=lambda m, level: self.bus.emit("http", m, level=level),
        )
        self.http.start()
        self.streamer.start()
        urls = self.http.urls()
        self.bus.emit("status", "dashboard: " + ", ".join(urls))
        logger.info("BotHost running (ws :%s, http :%s%s)",
                    self.remote.ws_port, self.http.http_port,
                    "" if self.http.dual_stack else ", IPv4 only")
        for url in urls:
            logger.info("dashboard: %s   (health check: add /health)", url)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        self.stop_bot()
        self.reach.save(force=True)
        if self.measurer:
            self.measurer.stop()
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
        if self.measurer and self.measurer.running():
            self.bus.emit("error", "stop measuring before starting the bot")
            return
        self._bot_stop.clear()
        self.bot_thread = threading.Thread(
            target=self._bot_entry, name="SmartBot", daemon=True
        )
        self.bot_thread.start()

    def stop_bot(self) -> None:
        # Set first: a stop that lands while the bot is still being
        # constructed must not be lost (_bot_entry checks it).
        self._bot_stop.set()
        bot = self.bot
        if bot is not None:
            bot.stop()
        if self.bot_thread and self.bot_thread.is_alive():
            self.bot_thread.join(timeout=4)

    def _send_bot_state(self, running: bool) -> None:
        self.remote.broadcast(
            "dash|" + json.dumps({"event": "bot", "running": running})
        )

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
                monitor=feed.monitor if feed is not None else None,
                identity=self.identity,
                reach=self.reach,
                notify_callback=self.telegram.send_message,
                event_bus=bus,
            )
            self.bot.platfit = self.platfit
            if self._bot_stop.is_set():
                return
            self.bus.emit("bot", "started")
            self._send_bot_state(True)
            self.bot.start()
        except Exception as exc:
            logger.error("Smart bot failed: %s", exc)
            self.bus.emit("error", f"smart bot failed: {exc}")
        finally:
            self.bot = None
            self.bus.emit("bot", "stopped")
            self._send_bot_state(False)

    # -- Frames & events -------------------------------------------------------
    def _forward_event(self, event: dict) -> None:
        payload = {"event": "evt", **event}
        self.remote.broadcast("dash|" + json.dumps(payload))

    def _drop_feed(self) -> None:
        """Close the vision feed; the next _get_feed builds a fresh one."""
        with self._feed_lock:
            if self._feed is not None:
                try:
                    self._feed.close()
                except Exception:
                    pass
                self._feed = None

    def _stream_stalled(self, why: str) -> None:
        """Captures have failed for seconds straight (streamer thread).
        Rebuild the feed — new window lookup, capture handles, monitor —
        unless a bot or measurement is running on it."""
        busy = self.is_bot_running() or (
            self.measurer is not None and self.measurer.running()
        )
        now = time.monotonic()
        if now - getattr(self, "_stall_warned", -1e9) >= 30.0:
            self._stall_warned = now
            self.bus.emit(
                "warn",
                f"live view: capture failing ({why})"
                + ("" if busy else " — rebuilding the vision feed"),
                level="warn",
            )
        if not busy:
            self._drop_feed()

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

        anchors = platforms = ropes = None
        if entry is not None and entry.rotation.anchors and w and h:
            anchors = [(px(a.x, w), px(a.y, h)) for a in entry.rotation.anchors]
        if entry is not None and entry.ropes and w and h:
            ropes = [
                (round(s[0] * w), round(s[1] * h),
                 round(s[2] * w), round(s[3] * h))
                for s in entry.ropes
            ]
        conf = (
            res.score
            if entry is not None and res.title_map == entry.name else None
        )
        nav_edges = nav_route = None
        graph = self._nav_graph(entry, region)
        if graph is not None:
            platforms = [(p.x0, p.y0, p.x1, p.y1) for p in graph.platforms]
            if self._nav_show:
                nav_edges = [
                    (l.kind, l.x0, l.y0, l.x1, l.y1) for l in graph.transfer_legs()
                ]
            prev = self._nav_preview
            if prev is not None and time.monotonic() < prev[1]:
                nav_route = [(l.kind, l.x0, l.y0, l.x1, l.y1) for l in prev[0]]
        nav_plan = None
        if bot is not None and bot.viz.get("route"):
            nav_route = list(bot.viz["route"])
        if bot is not None and bot.viz.get("plan"):
            nav_plan = list(bot.viz["plan"])
        return {
            "nav_edges": nav_edges,
            "nav_plan": nav_plan,
            "nav_route": nav_route,
            "platforms": platforms,
            "map": entry.name if entry else None,
            "map_via": res.via,
            "map_conf": conf,
            "map_title": res.title,
            "no_rotation": not bool(rot.anchors),
            "anchors": anchors,
            "ropes": ropes,
        }

    def _nav_graph(self, entry, region):
        cfg = self.bot_config
        return self._nav_cache.get(
            entry, region, self.reach, cfg.rope_penalty,
            allow_flash=cfg.class_travel == "flash" and cfg.flash_jump_enabled,
            allow_teleport=cfg.class_travel == "teleport"
            and bool(cfg.teleport_key),
        )

    def _nav_command(self, msg: str) -> None:
        """nav|show|on|off, nav|preview|x,y (minimap px) — route preview
        from the player to a clicked point."""
        parts = msg.split("|")
        if len(parts) < 3:
            return
        if parts[1] == "show":
            self._nav_show = parts[2] == "on"
            if not self._nav_show:
                self._nav_preview = None
            return
        if parts[1] != "preview":
            return
        try:
            gx, gy = (float(v) for v in parts[2].split(","))
        except ValueError:
            return
        self._nav_show = True
        entry = self._resolved_entry()
        pos = region = None
        bot = self.bot
        if bot is not None:
            img = bot.viz.get("img")
            region = bot.minimap.region
            pos = bot.minimap.player_pos(img) if img is not None else None
        feed = self._get_feed()
        if pos is None and feed is not None:
            img = feed.minimap_img()
            region = region or feed.minimap.region
            pos = feed.minimap.player_pos(img) if img is not None else None
        graph = self._nav_graph(entry, region)
        if graph is None:
            self.bus.emit("error", "route preview needs a resolved map with drawn platforms")
            return
        if pos is None:
            self.bus.emit("error", "route preview: no player position")
            return
        legs = graph.route(pos, (gx, gy))
        if legs is None:
            self.bus.emit(
                "nav",
                f"no route from {pos} to ({gx:.0f}, {gy:.0f}) — off the drawn "
                "platforms, or beyond jump reach",
            )
            self._nav_preview = None
            return
        self._nav_preview = (legs, time.monotonic() + 20.0)
        self.bus.emit(
            "nav", "route: " + " → ".join(l.kind for l in legs)
        )

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


    def _minimap_frame_img(self):
        """Latest minimap image: the running bot's, else a feed capture."""
        img = self.bot.viz.get("img") if self.bot is not None else None
        feed = self._get_feed()
        if img is None and feed is not None:
            img = feed.minimap_img()
        return img

    def _layout_anchor(self, msg: str) -> None:
        """layout|anchor|x,y | del|x,y | undo | clear [|<name>] — anchors
        placed by clicking the minimap (px here, stored normalized). A
        click snaps onto the drawn platform under it."""
        from .bot.rotation import Anchor
        from .vision.minimap import platform_row_at

        parts = msg.split("|")
        if len(parts) < 3:
            return
        op = parts[2]
        if op == "del":
            coords, rest = (parts[3] if len(parts) > 3 else ""), parts[4:]
        else:
            coords, rest = op, parts[3:]
        name = rest[0].strip() if rest else ""
        entry, err = self._layout_target(name)
        if entry is None:
            self.bus.emit("error", err)
            return
        rot = entry.rotation
        if op in ("undo", "clear"):
            if not rot.anchors:
                self.bus.emit("map", f"{entry.name}: no anchors")
                return
            for i in (range(len(rot.anchors) - 1, -1, -1) if op == "clear"
                      else [len(rot.anchors) - 1]):
                rot.remove_anchor(i)
            self._save_entry(entry)
            self.bus.emit("map", f"{entry.name}: {len(rot.anchors)} anchor(s) left")
            return
        try:
            x, y = (float(v) for v in coords.split(","))
        except ValueError:
            return
        img = self._minimap_frame_img()
        if img is None:
            self.bus.emit("error", "no minimap frame — can't place anchors")
            return
        h, w = img.shape[:2]
        if not (0 <= x <= w and 0 <= y <= h):
            self.bus.emit("error", "anchor click is off the minimap")
            return
        if op == "del":
            if not rot.anchors:
                return
            i = min(range(len(rot.anchors)), key=lambda k: (
                (rot.anchors[k].x * w - x) ** 2 + (rot.anchors[k].y * h - y) ** 2
            ))
            removed = rot.anchors[i].name
            rot.remove_anchor(i)
            self._save_entry(entry)
            self.bus.emit("map", f"{entry.name}: removed anchor {removed}")
            return
        snapped = platform_row_at(self._platforms_px(entry), x, y, max_snap=12)
        if snapped is not None:
            # Float above the line like the player icon does.
            y = snapped - self.bot_config.anchor_float_px
        taken = {a.name for a in rot.anchors}
        n = 0
        while f"a{n}" in taken:
            n += 1
        rot.anchors.append(Anchor(f"a{n}", round(x / w, 4), round(y / h, 4)))
        self._save_entry(entry)
        note = "" if snapped is not None else " (no drawn platform under it)"
        self.bus.emit("map", f"{entry.name}: anchor a{n} placed{note}")

    def _layout_segments(self, msg: str, field: str, label: str) -> None:
        """layout|<field>|x0,y0,x1,y1|undo|clear[|<name>] — drawn platform
        or rope segments (minimap px here, stored normalized).
        """
        parts = msg.split("|", 3)
        if len(parts) < 3:
            return
        payload, name = parts[2], (parts[3].strip() if len(parts) > 3 else "")
        entry, err = self._layout_target(name)
        if entry is None:
            self.bus.emit("error", err)
            return
        segs = list(getattr(entry, field) or [])
        stack = self._layout_undo.setdefault((entry.name, field), [])
        if payload == "undo":
            if stack:
                segs, moved = stack.pop()    # before the last edit (merges too)
                self._restore_anchors(entry, moved)
            elif segs:
                segs.pop()
            else:
                self.bus.emit("map", f"{entry.name}: no {label} to undo")
                return
            setattr(entry, field, segs or None)
            self._save_entry(entry)
            self.bus.emit(
                "map",
                f"{entry.name}: undid {label} ({len(segs)} left)",
            )
            return
        if payload == "clear":
            stack.append((segs, {}))
            setattr(entry, field, None)
            self._save_entry(entry)
            self.bus.emit("map", f"{label} cleared for {entry.name}")
            return
        if payload == "tidy":
            size = self._minimap_size()
            if size is None:
                self.bus.emit("error", f"no minimap frame — can't tidy {label}")
                return
            tidied = self._tidy(segs, *size)
            if self._same_lines(tidied, segs):
                # A right-to-left drag rewritten left-to-right is the same
                # line — not an edit (no undo step, fit samples kept).
                self.bus.emit(
                    "map",
                    f"{entry.name}: {label}s already tidy — nothing to level "
                    "or merge (tidy doesn't move lines to the feet)",
                )
                return
            moved = self._resnap_anchors(entry, segs, tidied, *size)
            stack.append((segs, moved))
            setattr(entry, field, tidied or None)
            self._save_entry(entry)
            self.bus.emit(
                "map",
                f"{entry.name}: tidied {label}s ({len(segs)} → {len(tidied)})"
                + (f", moved {len(moved)} anchor(s) with their lines"
                   if moved else ""),
            )
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
            self.bus.emit("error", f"no minimap frame — can't draw {label}")
            return
        h, w = img.shape[:2]
        if not all(0 <= v for v in seg) or seg[0] > w or seg[2] > w \
                or seg[1] > h or seg[3] > h:
            self.bus.emit("error", f"{label} drag is off the minimap")
            return
        before = list(segs)
        segs.append([
            round(seg[0] / w, 4), round(seg[1] / h, 4),
            round(seg[2] / w, 4), round(seg[3] / h, 4),
        ])
        moved = {}
        if field == "platforms":
            # Hand drags are never level: straighten, merge same-row overlaps.
            segs = self._tidy(segs, w, h)
            moved = self._resnap_anchors(entry, before, segs, w, h)
        stack.append((before, moved))
        del stack[:-50]
        setattr(entry, field, segs)
        self._save_entry(entry)
        self.bus.emit("map", f"{entry.name}: {label} {len(segs)} drawn")

    @staticmethod
    def _restore_anchors(entry, moved: dict) -> None:
        """Undo re-snaps: put back anchors an edit moved — only while they
        are still where it left them (a deleted-and-replaced anchor can
        reuse the name)."""
        for a in entry.rotation.anchors:
            m = moved.get(a.name)
            if m is not None and (a.x, a.y) == m[1]:
                a.x, a.y = m[0]

    @staticmethod
    def _resnap_anchors(entry, old_segs, new_segs, w: int, h: int,
                        follow_px: float = 4.0) -> dict:
        """Move anchors with the platform they stand on when its line
        moves (levelled, merged, redrawn). Each keeps its x and its own
        float above the line. Returns ``{name: (before, after)}``."""
        def rows(segs):
            out = []
            for s in segs or []:
                x0, y0, x1, y1 = s[0] * w, s[1] * h, s[2] * w, s[3] * h
                if x1 < x0:
                    x0, y0, x1, y1 = x1, y1, x0, y0
                out.append((x0, y0, x1, y1))
            return out

        def row_at(p, x):
            x0, y0, x1, y1 = p
            t = 0.0 if x1 == x0 else min(1.0, max(0.0, (x - x0) / (x1 - x0)))
            return y0 + t * (y1 - y0)

        old, new = rows(old_segs), rows(new_segs)
        moved = {}
        for a in entry.rotation.anchors:
            ax, ay = a.x * w, a.y * h
            # The platform it stands on — the planner's rule: up to 8px
            # above the line, 2px below.
            best = None
            for p in old:
                if not p[0] - 3 <= ax <= p[2] + 3:
                    continue
                d = row_at(p, ax) - ay
                if -2 <= d <= 8 and (best is None or d < best[0]):
                    best = (d, row_at(p, ax))
            if best is None:
                continue
            float_px, old_row = best
            cands = [
                row_at(p, ax) for p in new
                if p[0] - 3 <= ax <= p[2] + 3
                and abs(row_at(p, ax) - old_row) <= follow_px
            ]
            if not cands:
                continue
            new_row = min(cands, key=lambda r: abs(r - old_row))
            if abs(new_row - old_row) < 0.05:
                continue
            before = (a.x, a.y)
            a.y = round((new_row - float_px) / h, 4)
            moved[a.name] = (before, (a.x, a.y))
        return moved

    @staticmethod
    def _same_lines(a, b) -> bool:
        def norm(segs):
            out = []
            for s in segs or []:
                x0, y0, x1, y1 = (round(float(v), 4) for v in s)
                out.append((x0, y0, x1, y1) if x0 <= x1 else (x1, y1, x0, y0))
            return sorted(out)
        return norm(a) == norm(b)

    @staticmethod
    def _tidy(segs, w: int, h: int) -> list:
        """Straighten + merge normalized segments (thresholds are px)."""
        from .bot.platform_fit import tidy_segments

        px = [(s[0] * w, s[1] * h, s[2] * w, s[3] * h) for s in segs]
        return [
            [round(x0 / w, 4), round(y0 / h, 4), round(x1 / w, 4), round(y1 / h, 4)]
            for x0, y0, x1, y1 in tidy_segments(px)
        ]

    def _minimap_size(self):
        img = self._minimap_frame_img()
        if img is None:
            return None
        h, w = img.shape[:2]
        return w, h

    def _layout_platform(self, msg: str) -> None:
        if msg.startswith("layout|plat|feet|"):
            self._platform_to_feet(msg)
            return
        self._layout_segments(msg, "platforms", "platform")

    def _layout_rope(self, msg: str) -> None:
        """layout|rope|del|<x0,y0,x1,y1>[|<name>] removes one learned rope
        (its stored line); layout|rope|clear|undo[|<name>] as for
        platforms."""
        if not msg.startswith("layout|rope|del|"):
            self._layout_segments(msg, "ropes", "rope")
            return
        parts = msg.split("|")
        if len(parts) < 4:
            return
        name = parts[4].strip() if len(parts) > 4 else ""
        try:
            want = self.platfit.key([float(v) for v in parts[3].split(",")])
        except (TypeError, ValueError):
            return
        entry, err = self._layout_target(name)
        if entry is None:
            self.bus.emit("error", err)
            return
        ropes = list(entry.ropes or [])
        idx = next((i for i, r in enumerate(ropes) if self.platfit.key(r) == want), None)
        if idx is None:
            self.bus.emit("error", "that rope changed — reopen the map page and try again")
            return
        stack = self._layout_undo.setdefault((entry.name, "ropes"), [])
        stack.append((list(ropes), {}))       # a copy: ropes is edited below
        del stack[:-50]
        gone = ropes.pop(idx)
        entry.ropes = ropes or None
        self._save_entry(entry)
        region = self._live_region()
        where = (f" at x {gone[0] * region[2]:.0f}" if region else "")
        self.bus.emit(
            "map",
            f"{entry.name}: removed the rope{where} ({len(ropes)} left) — "
            "it is re-learned if the bot hangs there again",
        )

    def _platform_to_feet(self, msg: str) -> None:
        """layout|plat|feet|<x0,y0,x1,y1>[|<name>] — move one drawn line by
        the platform-fit offset, onto where the feet settle. Its anchors
        follow; undoable like any platform edit."""
        parts = msg.split("|")
        if len(parts) < 4:
            return
        name = parts[4].strip() if len(parts) > 4 else ""
        try:
            want = self.platfit.key([float(v) for v in parts[3].split(",")])
        except (TypeError, ValueError):
            return
        entry, err = self._layout_target(name)
        if entry is None:
            self.bus.emit("error", err)
            return
        segs = list(entry.platforms or [])
        idx = next((i for i, s in enumerate(segs) if self.platfit.key(s) == want), None)
        if idx is None:
            self.bus.emit("error", "that platform changed — reopen the map page and try again")
            return
        dy = self.platfit.offset(entry.name, segs[idx])
        if dy is None:
            self.bus.emit("error", "not enough samples on that platform yet")
            return
        if abs(dy) < 0.5:
            self.bus.emit("map", f"{entry.name}: that platform already sits at the feet")
            return
        size = self._minimap_size()
        if size is None:
            self.bus.emit("error", "no minimap frame — can't move the platform")
            return
        w, h = size
        old = segs[idx]
        new = [old[0], round(old[1] + dy / h, 4), old[2], round(old[3] + dy / h, 4)]
        moved_to = list(segs)
        moved_to[idx] = new
        stack = self._layout_undo.setdefault((entry.name, "platforms"), [])
        moved = self._resnap_anchors(entry, segs, moved_to, w, h,
                                     follow_px=abs(dy) + 1.0)
        stack.append((segs, moved))
        del stack[:-50]
        entry.platforms = moved_to
        self._save_entry(entry)
        self.platfit.move(entry.name, old, new, dy)
        row = (old[1] + old[3]) / 2 * h
        self.bus.emit(
            "map",
            f"{entry.name}: moved the platform at y {row:.0f} "
            f"{'down' if dy > 0 else 'up'} {abs(dy):.1f}px onto the feet"
            + (f"; {len(moved)} anchor(s) followed" if moved else ""),
        )
        self._send_maps()


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

    @staticmethod
    def _bot_status(bot) -> dict:
        """FSM state + hazard for views that don't carry the bot's viz
        snapshot — a rune must stay visible while it's solved in Window
        view."""
        if bot is None:
            return {"state": "IDLE"}
        return {"state": bot.viz.get("state"), "hazard": bot.viz.get("hazard")}

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
            for key in _MINIMAP_OVERLAYS:
                meta.pop(key, None)
            return {
                "img": annotate_title(img),
                "layout": self._layout_source(),
                **meta,
                **self._bot_status(bot),
            }
        if mode == "window":
            meta = self._map_meta()
            feed = self._get_feed() if bot is None else None
            if bot is not None:
                img, region = bot._window_capture(), bot.minimap.region
            elif feed is not None:
                img, region = feed.window_img(), feed.minimap.region
            else:
                return None
            if img is None:
                return None
            # Overlays are minimap-relative; the window image is the whole
            # client area, so move them to where the minimap sits in it.
            if region:
                _offset_meta(meta, int(region[0]), int(region[1]))
            else:
                for key in _MINIMAP_OVERLAYS:
                    meta.pop(key, None)
            return {
                "img": img, "layout": self._layout_source(), **meta,
                **self._bot_status(bot),
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
        analyzer = bot.minimap if bot is not None else feed.minimap
        if snap.get("player"):
            snap["player_box"] = analyzer.player_box(tuple(snap["player"]))
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
        elif msg == "class|list":
            self._send_class()
        elif msg.startswith("class|use|"):
            self._class_use(msg.split("|", 2)[2].strip())
        elif msg.startswith("class|add|"):
            parts = msg.split("|", 3)
            self._class_add(parts[2], parts[3] if len(parts) > 3 else "")
        elif msg.startswith("patrol|policy|"):
            self._patrol_policy_set(msg.split("|", 2)[2])
        elif msg.startswith("patrol|temp|"):
            self._patrol_policy_set(temp=msg.split("|", 2)[2])
        elif msg.startswith("map|set|"):
            self._set_map(msg.split("|", 2)[2])
        elif msg == "measure|start":
            self._measure_start()
        elif msg == "measure|stop":
            if self.measurer:
                self.measurer.stop()
        elif msg == "measure|status":
            self._send_measure()
        elif msg.startswith("dash|view|"):
            self.streamer.set_mode(msg.split("|", 2)[2])
        elif msg.startswith("dash|fps|"):
            self._fps_set(msg.split("|", 2)[2])
        elif msg == "layout|save" or msg.startswith("layout|save|"):
            self._layout_save(msg.split("|", 2)[2] if msg.count("|") > 1 else "")
        elif msg == "layout|clear" or msg.startswith("layout|clear|"):
            self._layout_clear(msg.split("|", 2)[2] if msg.count("|") > 1 else "")
        elif msg.startswith("layout|anchor|"):
            self._layout_anchor(msg)
        elif msg.startswith("layout|rope|"):
            self._layout_rope(msg)
        elif msg.startswith("layout|plat|"):
            self._layout_platform(msg)
        elif msg == "layout|reset":
            self._layout_reset()
        elif msg.startswith("nav|"):
            self._nav_command(msg)
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
        entry = self._resolved_entry()
        payload = {
            "event": "maps",
            "maps": self.maps.names(),
            "active": self.identity.pin,
            "detected": res.name,
            "via": res.via,
            "title": res.title,
            "score": res.score if res.title_map else None,
            "reading": self.identity.pending,
            # Setup-checklist state for the resolved map.
            "platforms_n": len(entry.platforms or []) if entry else 0,
            "anchors_n": len(entry.rotation.anchors) if entry else 0,
            "platform_fit": self.platfit.summary(
                entry.name if entry else None,
                entry.platforms if entry else None,
                self._live_region(),
            ),
            "ropes": self._rope_rows(entry),
        }
        self.remote.broadcast("dash|" + json.dumps(payload))

    def _rope_rows(self, entry) -> list:
        """Learned ropes for the Map page: column and span (minimap px)
        plus the stored line that names each in commands."""
        region = self._live_region()
        if entry is None or not entry.ropes or not region:
            return []
        w, h = region[2], region[3]
        rows = []
        for r in entry.ropes:
            rows.append({
                "key": ",".join(f"{v:g}" for v in self.platfit.key(r)),
                "x": round((r[0] + r[2]) / 2 * w),
                "top": round(min(r[1], r[3]) * h),
                "bottom": round(max(r[1], r[3]) * h),
            })
        rows.sort(key=lambda d: (d["x"], d["top"]))
        return rows

    def _live_region(self):
        bot = self.bot
        if bot is not None and bot.minimap.region:
            return bot.minimap.region
        feed = self._feed
        return feed.minimap.region if feed is not None else None

    def _platfit_changed(self, _map_name: str) -> None:
        """New standing sample (bot thread): refresh the dashboard, at
        most every few seconds."""
        now = time.monotonic()
        if now - self._platfit_sent >= 5.0:
            self._platfit_sent = now
            self._send_maps()

    def _send_class(self) -> None:
        """Class profiles + patrol policy for the dashboard selectors."""
        from .bot.measure import MoveMeasurer

        cfg = self.bot_config
        plan = MoveMeasurer.plan_for(cfg)
        self.remote.broadcast("dash|" + json.dumps({
            "event": "class",
            "active": cfg.class_active,
            "profiles": {
                name: {
                    "travel": p.get("travel", "flash"),
                    "air_attacks": bool(p.get("air_attacks", True)),
                    "teleport_key": p.get("teleport_key"),
                }
                for name, p in (cfg.class_profiles or {}).items()
            },
            "policy": cfg.patrol_policy,
            "temp": cfg.patrol_weight_temp,
            # Moves of this class's measurement plan that have been
            # measured (not "grew past the guess" — a measurement may
            # also lower it).
            "measure_plan": list(plan),
            "measured_moves": [m for m in plan if m in self.reach.measured],
            "measured": sum(1 for m in plan if m in self.reach.measured),
        }))

    def _class_add(self, name: str, spec_json: str) -> None:
        """class|add|<name>|{travel,air_attacks,teleport_key,…} — create a
        profile from the dashboard and apply it live."""
        name = name.strip()
        cfg = self.bot_config
        if not name:
            self.bus.emit("error", "class profile needs a name")
            return
        if name in (cfg.class_profiles or {}):
            self.bus.emit("error", f"class profile {name!r} already exists")
            return
        try:
            raw = json.loads(spec_json) if spec_json else {}
        except ValueError:
            self.bus.emit("error", "bad class spec")
            return
        spec = {
            k: v for k, v in (raw or {}).items()
            if k in ("travel", "air_attacks", "teleport_key",
                     "teleport_cooldown", "skills")
        }
        cfg.class_profiles = dict(cfg.class_profiles or {})
        cfg.class_profiles[name] = spec
        bot_cfg = getattr(self.config, "bot", None) or {}
        cls = dict(bot_cfg.get("class") or {})
        cls["profiles"] = cfg.class_profiles
        bot_cfg["class"] = cls
        self.config.bot = bot_cfg
        save_config(self.config)
        self.bus.emit("bot", f"class profile {name} created")
        self._class_use(name)

    def _class_use(self, name: str) -> None:
        """class|use|<name> — apply a class profile live (stops the bot
        first: the kit decides which moves exist)."""
        cfg = self.bot_config
        if name not in (cfg.class_profiles or {}):
            self.bus.emit("error", f"no class profile named {name!r}")
            return
        self.stop_bot()
        # A measurement runs on this same config and records into the old
        # profile's reach file — never let it continue under a new kit.
        if self.measurer and self.measurer.running():
            self.measurer.stop()
            self.bus.emit("measure", "measurement stopped — class switched")
        # Start from the global (profile-free) values so nothing the
        # previous profile set survives into one that doesn't set it.
        from .bot import BotConfig

        base = BotConfig.from_dict(
            {**(getattr(self.config, "bot", None) or {}), "class": None}
        )
        for field in _CLASS_FIELDS:
            setattr(cfg, field, getattr(base, field))
        cfg.class_active = name
        cfg._apply_class({"active": name, "profiles": cfg.class_profiles},
                         cfg)
        # Reach is per character: swap to this profile's learned envelopes.
        from .bot.reach import ReachModel, base_reach

        self.reach = ReachModel(base_reach(cfg), path=cfg.reach_path())
        bot_cfg = getattr(self.config, "bot", None) or {}
        cls = dict(bot_cfg.get("class") or {})
        cls["active"] = name
        cls["profiles"] = cfg.class_profiles
        bot_cfg["class"] = cls
        self.config.bot = bot_cfg
        save_config(self.config)
        self.bus.emit("bot", f"class: {name} ({_kit_summary(cfg)})")
        # The kit owns the skills and move keys: every client re-reads them.
        self._send_class()
        self._send_skills()
        self._send_config()
        self._send_measure()    # the plan follows the kit

    def _patrol_policy_set(self, policy: str = "", temp=None) -> None:
        cfg = self.bot_config
        if policy in ("weighted", "greedy"):
            cfg.patrol_policy = policy
        if temp is not None:
            try:
                t = float(temp)
            except (TypeError, ValueError):
                t = math.nan
            if not math.isfinite(t):
                self.bus.emit("error", f"invalid patrol temperature: {temp!r}")
                return
            cfg.patrol_weight_temp = max(0.05, t)
        bot_cfg = getattr(self.config, "bot", None) or {}
        bot_cfg["patrol_policy"] = cfg.patrol_policy
        bot_cfg["patrol_weight_temp"] = cfg.patrol_weight_temp
        self.config.bot = bot_cfg
        save_config(self.config)
        self.bus.emit(
            "bot",
            f"patrol policy: {cfg.patrol_policy} "
            f"(temp {cfg.patrol_weight_temp})",
        )
        self._send_class()

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

            # Windows COM ports are exclusive: the port we already hold
            # can't be re-opened by the probe, so skip it explicitly.
            current = (
                self.serial_port
                if self.serial_port and self.remote.serial_manager.is_open
                else None
            )

            def probe() -> None:
                from .transport import discover_data_port

                found = discover_data_port(exclude_port=current)
                if found:
                    self._finish_serial_connect(found)
                elif current:
                    self.bus.emit(
                        "host", f"no other Pico port found — staying on {current}"
                    )
                    self._send_host_state()
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
        if self.is_bot_running() or (self.measurer and self.measurer.running()):
            # The running bot shares the feed's monitor; closing it
            # underneath would blind the bot.
            self.bus.emit("error", "stop the bot before switching windows")
            self._send_host_state()
            return
        self.window_title = title
        self.config.default_target_window = title
        save_config(self.config)
        self._drop_feed()
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
    def _skills_target(self):
        """The skill book the Skills panel edits: the active profile's own
        kit when one is active (seeded from the global book on first
        edit), else the global book. Only call it to write."""
        cfg = self.bot_config
        profile = (cfg.class_profiles or {}).get(cfg.class_active)
        if profile is None:
            return cfg.skills, "global"
        if not isinstance(profile.get("skills"), dict):
            # First edit under this profile: seed it with the effective
            # global book so the character starts from what's configured.
            profile["skills"] = {
                n: s.to_dict() for n, s in cfg.skills.items()
            }
        return profile["skills"], cfg.class_active

    def _send_skills(self) -> None:
        """The book the bot uses. Read-only: a profile inheriting the
        global book is shown as such, not seeded (that happens on the
        first edit)."""
        cfg = self.bot_config
        profile = (cfg.class_profiles or {}).get(cfg.class_active)
        own = profile is not None and isinstance(profile.get("skills"), dict)
        payload = {
            "event": "skills",
            "source": cfg.class_active if profile is not None else "global",
            "inherited": profile is not None and not own,
            "skills": {n: s.to_dict() for n, s in cfg.skills.items()},
        }
        self.remote.broadcast("dash|" + json.dumps(payload))

    def _skills_set(self, payload: str) -> None:
        try:
            spec = json.loads(payload)
            skill = Skill.from_dict(str(spec["name"]), spec)
        except (ValueError, KeyError, TypeError,
                json.JSONDecodeError) as exc:
            self.bus.emit("error", f"invalid skill spec: {exc}")
            return
        target, _ = self._skills_target()
        if target is self.bot_config.skills:
            self.bot_config.skills[skill.name] = skill
        else:
            target[skill.name] = skill.to_dict()
            self.bot_config.skills[skill.name] = skill   # live book too
        self._skills_commit()
        self.bus.emit(
            "skill", f"skill saved: {skill.name} ({skill.kind}, {skill.key})"
        )

    def _skills_del(self, name: str) -> None:
        target, _ = self._skills_target()
        gone = (
            self.bot_config.skills.pop(name, None)
            if target is self.bot_config.skills
            else (target.pop(name, None),
                  self.bot_config.skills.pop(name, None))[0]
        )
        if not gone:
            self.bus.emit("error", f"no such skill: {name}")
            return
        self._skills_commit()
        self.bus.emit("skill", f"skill removed: {name}")

    def _skills_commit(self) -> None:
        """Persist skills to config.json and update a running bot live."""
        bot_cfg = getattr(self.config, "bot", None) or {}
        profile = (self.bot_config.class_profiles or {}).get(
            self.bot_config.class_active)
        if profile is not None:
            # The edit landed in the active profile's own kit — persist
            # only the class block; the global book stays untouched.
            cls = dict(bot_cfg.get("class") or {})
            cls["profiles"] = self.bot_config.class_profiles
            bot_cfg["class"] = cls
        else:
            # Materialize the full effective set so a legacy attack_keys
            # config doesn't lose its synthesized skills on the first edit.
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
        if not changed:
            return
        # bot.config is the same BotConfig — live fields, nothing else to do.
        bot_cfg = getattr(self.config, "bot", None) or {}
        profile = (cfg.class_profiles or {}).get(cfg.class_active)
        if profile is not None:
            # A profile is active: the movement keys belong to its kit —
            # mirror the edits into the profile and persist the class block.
            for k in ("jump_key", "up_jump_skill_key"):
                if k in changed:
                    profile[k] = changed[k]
            if "flash_jump" in changed:
                fj = dict(profile.get("flash_jump") or {})
                fj.update(changed["flash_jump"])
                profile["flash_jump"] = fj
            cls = dict(bot_cfg.get("class") or {})
            cls["profiles"] = cfg.class_profiles
            bot_cfg["class"] = cls
        else:
            if "jump_key" in changed:
                bot_cfg["jump_key"] = cfg.jump_key
            if "up_jump_skill_key" in changed:
                bot_cfg["up_jump_skill_key"] = cfg.up_jump_skill_key
            if "flash_jump" in changed:
                fj = dict(bot_cfg.get("flash_jump") or {})
                fj.update(changed["flash_jump"])
                bot_cfg["flash_jump"] = fj
        if "nav_threshold_px" in changed:
            bot_cfg["nav_threshold_px"] = cfg.nav_threshold_px
        self.config.bot = bot_cfg
        save_config(self.config)
        self.bus.emit(
            "bot",
            "movement keys: " + ", ".join(
                f"{k}={v}" for k, v in changed.items()
            ),
        )
        self._send_config()

    # -- Move measurement -------------------------------------------------------
    def _measure_start(self) -> None:
        if self.bot is not None or (self.bot_thread and self.bot_thread.is_alive()):
            self.bus.emit("error", "stop the bot before measuring moves")
            return
        if self.measurer and self.measurer.running():
            self.bus.emit("measure", "already measuring")
            self._send_measure()
            return
        feed = self._get_feed()
        if feed is None:
            self.bus.emit("error", "measuring needs the game window")
            return
        from .bot import HidController, SmartBot
        from .bot.measure import MoveMeasurer

        def send(payload: str) -> bool:
            self.bus.emit("hid", payload)
            return self.remote.enqueue_hid_payload(
                payload, wait_ack=True, timeout=1.5
            )

        bot = SmartBot(
            HidController(send),
            self.window_title,
            self.bot_config,
            minimap=feed.minimap,
            monitor=feed.monitor,
            identity=self.identity,
            reach=self.reach,
            notify_callback=self.telegram.send_message,
            event_bus=self.bus,
        )
        bot.platfit = self.platfit
        def on_status(status: dict) -> None:
            self._send_measure(status)
            if not status["running"]:
                self._send_class()   # the measured-moves count changed

        self.measurer = MoveMeasurer(
            bot, on_event=lambda k, m: self.bus.emit(k, m), on_status=on_status,
        )
        self.measurer.start()

    def _send_measure(self, status: Optional[dict] = None) -> None:
        """measure event: {running, move, plan, results}."""
        if status is None:
            from .bot.measure import MoveMeasurer

            status = (
                self.measurer.status() if self.measurer is not None else {
                    "running": False, "move": None,
                    "plan": list(MoveMeasurer.plan_for(self.bot_config)),
                    "results": {},
                }
            )
        self.remote.broadcast(
            "dash|" + json.dumps({"event": "measure", **status})
        )

def main() -> None:
    parser = argparse.ArgumentParser(prog="picobot.serve")
    parser.add_argument("--port", default=None, help="Pico DATA COM port")
    parser.add_argument("--window", default=None, help="game window title")
    parser.add_argument("--ws", type=int, default=None, help="WS port")
    parser.add_argument("--http", type=int, default=None, help="HTTP port")
    parser.add_argument(
        "--debug-frames", action="store_true",
        help="save detection debug captures to debug_capture_dir",
    )
    args = parser.parse_args()

    # config.json, maps/, nav_reach*.json and debug/ are relative paths:
    # anchor them to the project folder so launching from a shortcut or
    # another directory doesn't start from a blank config.
    root = Path(__file__).resolve().parent.parent
    if (root / "pyproject.toml").exists():
        os.chdir(root)

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
    BotHost(port, window_title, config, debug_frames=args.debug_frames).run()


if __name__ == "__main__":
    main()
