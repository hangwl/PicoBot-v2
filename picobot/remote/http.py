"""Embedded HTTP server for the PicoBot dashboard page."""

from __future__ import annotations

import errno
import ipaddress
import json
import logging
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Tuple

__all__ = ["EmbeddedHTTPServer", "local_addresses"]

Log = Callable[[str, str], None]          # (message, level)


class _ExclusiveServer(ThreadingHTTPServer):
    """Own the port outright. HTTPServer sets SO_REUSEADDR, which on
    Windows lets a second process bind a port already in use — a stale
    host could then keep answering (or hanging) some of the connections."""

    daemon_threads = True
    allow_reuse_address = False

    def server_bind(self) -> None:
        excl = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if excl is not None:
            try:
                self.socket.setsockopt(socket.SOL_SOCKET, excl, 1)
            except OSError:
                pass
        super().server_bind()


class _DualStackServer(_ExclusiveServer):
    """IPv6 socket that also accepts IPv4: Tailscale names resolve to both
    a 100.x and an fd7a:: address, and an IPv6-first phone must not wait
    out a timeout before falling back."""

    address_family = socket.AF_INET6

    def server_bind(self) -> None:
        try:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        except (AttributeError, OSError):
            pass
        super().server_bind()


class _IPv4Server(_ExclusiveServer):
    pass


def _in_use(exc: OSError) -> bool:
    return (exc.errno == errno.EADDRINUSE
            or getattr(exc, "winerror", None) == 10048)


def local_addresses() -> List[Tuple[str, str]]:
    """This machine's reachable IPv4 addresses as ``(ip, kind)`` — kind is
    ``tailscale`` (100.64.0.0/10) or ``lan``."""
    found = []
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        infos = []
    tailnet = ipaddress.ip_network("100.64.0.0/10")
    for info in infos:
        ip = info[4][0]
        addr = ipaddress.ip_address(ip)
        if addr.is_loopback or addr.is_link_local or ip in (i for i, _ in found):
            continue
        found.append((ip, "tailscale" if addr in tailnet else "lan"))
    found.sort(key=lambda t: (t[1] != "tailscale", t[0]))
    return found


class EmbeddedHTTPServer:
    """Serve the built dashboard (``web/dist``) with the live WS port
    filled into ``index.html``; ``/health`` answers with a small JSON
    status so a phone can tell "host unreachable" from "page broken"."""

    MAX_ATTEMPTS = 5
    CONN_TIMEOUT = 30.0

    def __init__(
        self,
        ws_port_provider: Callable[[], int],
        http_port: int,
        *,
        search_paths: Iterable[Path] | None = None,
        ws_scheme: str = "ws",
        static_dir: Path | None = None,
        on_log: Optional[Log] = None,
    ) -> None:
        self._ws_port_provider = ws_port_provider
        self.ws_scheme = ws_scheme
        self.http_port = http_port
        self.on_log = on_log
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
        self.dual_stack = False

    def _log(self, message: str, level: str = "debug") -> None:
        if self.on_log is not None:
            try:
                self.on_log(message, level)
            except Exception:
                pass

    def start(self) -> bool:
        """Bind the configured port, or the next free one; True when up."""
        if self.thread and self.thread.is_alive():
            return True
        handler = self._build_handler()
        last: Optional[Exception] = None
        for offset in range(self.MAX_ATTEMPTS):
            port = self.http_port + offset if self.http_port else 0
            try:
                try:
                    self.httpd = _DualStackServer(("::", port), handler)
                    self.dual_stack = True
                except OSError as exc:
                    if _in_use(exc):
                        raise                           # try the next port
                    self.httpd = _IPv4Server(("0.0.0.0", port), handler)
                    self.dual_stack = False
            except OSError as exc:
                last = exc
                continue
            self.http_port = self.httpd.server_address[1]
            self.thread = threading.Thread(
                target=self.httpd.serve_forever, name="HTTP", daemon=True)
            self.thread.start()
            if offset:
                self._log(f"http: port {port - offset} busy — serving on {port}", "warn")
            return True
        logging.error("Failed to start HTTP server on ports %s-%s: %s",
                      self.http_port, self.http_port + self.MAX_ATTEMPTS - 1, last)
        self._log(f"http: could not start ({last})", "error")
        self.httpd = None
        self.thread = None
        return False

    def stop(self) -> None:
        try:
            if self.httpd:
                self.httpd.shutdown()
                self.httpd.server_close()
        except Exception:
            pass
        self.httpd = None
        self.thread = None

    def urls(self) -> List[str]:
        """Addresses to open the dashboard at, Tailscale first."""
        return [
            f"http://{ip}:{self.http_port} ({kind})"
            for ip, kind in local_addresses()
        ] or [f"http://localhost:{self.http_port}"]

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

    def _health(self) -> bytes:
        dist = self.static_dir is not None and (self.static_dir / "index.html").exists()
        return json.dumps({
            "ok": True,
            "ws_port": self._resolve_ws_port(),
            "dashboard_built": dist,
            "time": round(time.time(), 3),
        }).encode("utf-8")

    def _respond(self, path: str) -> Tuple[int, str, bytes, bool]:
        """(status, content type, body, no-cache) for a GET of ``path``."""
        if path == "/health":
            return 200, "application/json", self._health(), True
        if self.static_dir is not None:
            served = self._serve_static(path)
            if served is not None:
                ctype, data = served
                # Re-fetch after a rebuild; assets are hashed.
                return 200, ctype, data, ctype.startswith("text/html")
        if path != "/":
            return 404, "text/plain; charset=utf-8", b"Not Found", False
        page = self._read_index(self._resolve_ws_port())
        return 200, "text/html; charset=utf-8", page.encode("utf-8"), True

    def _build_handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            # A stalled phone connection must not pin a thread forever.
            timeout = server.CONN_TIMEOUT

            @property
            def peer(self) -> str:
                ip = str(self.client_address[0])
                return ip[7:] if ip.startswith("::ffff:") else ip

            def do_GET(self):
                path = (getattr(self, "path", "/") or "/").split("?")[0]
                try:
                    status, ctype, body, no_cache = server._respond(path)
                except Exception as exc:
                    server._log(f"http: {self.peer} GET {path} failed: {exc!r}", "warn")
                    status, ctype, body, no_cache = (
                        500, "text/plain; charset=utf-8", b"Server error", True)
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", ctype)
                    # A cut-off transfer must not look like a complete page.
                    self.send_header("Content-Length", str(len(body)))
                    if no_cache:
                        self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    self.wfile.write(body)
                except OSError as exc:
                    server._log(
                        f"http: {self.peer} GET {path} — connection "
                        f"lost while sending ({exc.__class__.__name__})", "warn")

            def log_message(self, format, *args):
                return      # replaced by log_request / log_error below

            def log_request(self, code="-", size="-"):
                server._log(f"http: {self.peer} "
                            f"{getattr(self, 'requestline', '')} → {code}")

            def log_error(self, format, *args):
                server._log(f"http: {self.peer} {format % args}", "warn")

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
