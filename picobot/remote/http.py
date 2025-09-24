"""Embedded HTTP server for the PicoBot remote controller page."""
from __future__ import annotations

import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
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
    ) -> None:
        self._ws_port_provider = ws_port_provider
        self.http_port = http_port
        self.httpd: HTTPServer | None = None
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

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        ws_port = self._resolve_ws_port()
        handler = self._build_handler(ws_port)
        try:
            self.httpd = HTTPServer(("0.0.0.0", self.http_port), handler)
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

    def _build_handler(self, ws_port: int):  # type: ignore[override]
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                try:
                    if getattr(self, "path", "/") != "/":
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

            def log_message(self, format, *args):  # noqa: A003
                return

        return Handler

    def _read_index(self, ws_port: int) -> str:
        fallback = (
            "<html><body style='background:#121212;color:#eee;font-family:sans-serif'>"
            "<h3 style='margin:16px'>index.html not found</h3>"
            "<p style='margin:16px'>Create <code>index.html</code> in the PicoBot folder. "
            "You can use the token <code>REPLACE_WS_PORT</code> and it will be replaced "
            "with the active WebSocket port.</p>"
            "</body></html>"
        )
        for path in self._search_paths:
            try:
                if path.exists():
                    content = path.read_text(encoding="utf-8")
                    return content.replace("REPLACE_WS_PORT", str(ws_port))
            except Exception:
                continue
        return fallback
