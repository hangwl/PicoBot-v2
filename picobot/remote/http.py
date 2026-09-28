"""Embedded HTTP server for the PicoBot remote controller page."""

from __future__ import annotations

import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Iterable, List

__all__ = ["EmbeddedHTTPServer"]


class EmbeddedHTTPServer:
    """Serve a simple controller page that proxies WebSocket interactions."""

    def __init__(
        self,
        ws_port_provider: Callable[[], int],
        http_port: int,
        *,
        search_paths: Iterable[Path] | None = None,
        ws_scheme: str = "ws",
        static_dir: Path | None = None,
    ) -> None:
        self._ws_port_provider = ws_port_provider
        self.ws_scheme = ws_scheme
        self.http_port = http_port
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        base_dir = Path(__file__).resolve().parent
        picobot_dir = base_dir.parent
        root_dir = picobot_dir.parent
        default_paths: List[Path] = [
            root_dir / "index.html",
            picobot_dir / "index.html",
            base_dir / "index.html",
        ]
        self._search_paths = list(search_paths) if search_paths else default_paths
        # Built dashboard app (web/dist); without a build, a page saying
        # how to make one.
        self.static_dir = Path(static_dir).resolve() if static_dir else None

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        ws_port = self._resolve_ws_port()
        handler = self._build_handler(ws_port)
        try:
            self.httpd = ThreadingHTTPServer(("0.0.0.0", self.http_port), handler)
            self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
            self.thread.start()
        except Exception as exc:
            logging.error(
                "Failed to start HTTP server on port %s: %s",
                self.http_port,
                exc,
            )
            self.httpd = None
            self.thread = None

    def stop(self) -> None:
        try:
            if self.httpd:
                self.httpd.shutdown()
                self.httpd.server_close()
        except Exception:
            pass
        self.httpd = None
        self.thread = None

    def _resolve_ws_port(self) -> int:
        try:
            value = int(self._ws_port_provider())
            return value if value > 0 else 8765
        except Exception:
            return 8765

    _MIME = {
        ".html": "text/html; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".json": "application/json",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".ico": "image/x-icon",
        ".woff2": "font/woff2",
        ".webmanifest": "application/manifest+json",
    }

    def _build_handler(self, ws_port: int):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                try:
                    path = (getattr(self, "path", "/") or "/").split("?")[0]
                    if server.static_dir is not None:
                        served = server._serve_static(path)
                        if served is not None:
                            ctype, data = served
                            self.send_response(200)
                            self.send_header("Content-Type", ctype)
                            if ctype.startswith("text/html"):
                                # Re-fetch after a rebuild; assets are hashed.
                                self.send_header("Cache-Control", "no-cache")
                            self.end_headers()
                            self.wfile.write(data)
                            return
                    if path != "/":
                        self.send_response(404)
                        self.send_header("Content-Type", "text/plain; charset=utf-8")
                        self.end_headers()
                        self.wfile.write(b"Not Found")
                        return
                    content = server._read_index(ws_port)
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(content.encode("utf-8"))
                except Exception:
                    pass

            def log_message(self, format, *args):
                return

        return Handler

    def _serve_static(self, path: str):
        """Serve the built app from web/dist: files that exist under it
        (hashed bundle, favicon), else index.html with the live WS port
        filled in. None when dist is absent."""
        if self.static_dir is None:
            return None
        index = self.static_dir / "index.html"
        if not index.exists():
            return None
        rel = path.lstrip("/")
        file = (self.static_dir / rel).resolve() if rel else index
        if file == index or not (
            file.is_relative_to(self.static_dir) and file.is_file()
        ):
            # '/', '/index.html' and unknown routes (SPA fallback).
            try:
                html = index.read_text(encoding="utf-8")
            except OSError:
                return None
            html = html.replace(
                "REPLACE_WS_PORT", str(self._resolve_ws_port())
            ).replace("REPLACE_WS_SCHEME", self.ws_scheme)
            return ("text/html; charset=utf-8", html.encode("utf-8"))
        try:
            data = file.read_bytes()
        except OSError:
            return None
        return (self._MIME.get(file.suffix, "application/octet-stream"), data)

    def _read_index(self, ws_port: int) -> str:
        fallback = (
            "<html><body style='background:#121212;color:#eee;font-family:sans-serif'>"
            "<h3 style='margin:16px'>Dashboard not built</h3>"
            "<p style='margin:16px'>Run <code>npm install</code> then "
            "<code>npm run build</code> in the <code>web</code> folder, "
            "then reload this page.</p>"
            "</body></html>"
        )
        for path in self._search_paths:
            try:
                if path.exists():
                    content = path.read_text(encoding="utf-8")
                    return content.replace(
                        "REPLACE_WS_PORT", str(ws_port)
                    ).replace("REPLACE_WS_SCHEME", self.ws_scheme)
            except Exception:
                continue
        return fallback
