"""Remote control server implementation for PicoBot."""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
from dataclasses import dataclass
from typing import Callable, Optional

try:
    import websockets
except Exception:
    websockets = None

from ..transport import SerialManager

__all__ = [
    "AsyncWebsocketBridge",
    "RemoteCallbacks",
    "RemoteControlServer",
]


@dataclass
class RemoteCallbacks:
    """Functions invoked by the remote server to interact with the UI layer."""

    schedule: Callable[[Callable[[], None]], None]
    log: Callable[[str], None]
    set_status: Callable[[str], None]
    set_ws_port: Callable[[int], None]
    start_macro: Callable[[], None]
    stop_macro: Callable[[], None]
    is_macro_playing: Callable[[], bool]
    broadcast: Callable[[str], None]


class AsyncWebsocketBridge:
    """Owns the asyncio loop for the remote WebSocket interface."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        message_handler: Callable,
        on_port_bound: Optional[Callable[[int], None]] = None,
        on_client_connected: Optional[Callable[[object], None]] = None,
        on_client_disconnected: Optional[Callable[[object], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
        max_attempts: int = 10,
    ) -> None:
        self.host = host
        self.base_port = int(port)
        self.message_handler = message_handler
        self.on_port_bound = on_port_bound
        self.on_client_connected = on_client_connected
        self.on_client_disconnected = on_client_disconnected
        self.on_error = on_error
        self.max_attempts = max_attempts
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._server = None
        self.port = int(port)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f"AsyncWebsocketBridge[{self.base_port}]",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        loop = self._loop
        if loop and loop.is_running():
            try:
                loop.call_soon_threadsafe(lambda: None)
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        self._loop = None
        self._server = None

    def _run(self) -> None:
        if websockets is None:
            if self.on_error:
                try:
                    self.on_error(RuntimeError("websockets package is not available"))
                except Exception:
                    pass
            return
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._start_server())
            if self._server is None:
                return
            loop.run_until_complete(self._wait_for_stop())
        except Exception as exc:
            if self.on_error:
                try:
                    self.on_error(exc)
                except Exception:
                    pass
        finally:
            try:
                if self._server:
                    self._server.close()
                    loop.run_until_complete(self._server.wait_closed())
            except Exception:
                pass
            try:
                if loop.is_running():
                    loop.stop()
            except Exception:
                pass
            loop.close()
            self._loop = None
            self._server = None

    async def _start_server(self) -> None:
        last_error: Optional[Exception] = None
        for offset in range(self.max_attempts):
            port = self.base_port + offset
            try:
                self._server = await websockets.serve(
                    self._handle_client,
                    self.host,
                    port,
                    ping_interval=20,
                    ping_timeout=20,
                )
                self.port = port
                if self.on_port_bound:
                    try:
                        self.on_port_bound(port)
                    except Exception:
                        pass
                return
            except OSError as exc:
                last_error = exc
                await asyncio.sleep(0.3)
        if last_error and self.on_error:
            try:
                self.on_error(last_error)
            except Exception:
                pass
    async def _wait_for_stop(self) -> None:
        while not self._stop_event.is_set():
            await asyncio.sleep(0.1)

    async def _handle_client(self, websocket, path: str | None = None) -> None:
        try:
            if self.on_client_connected:
                try:
                    self.on_client_connected(websocket)
                except Exception:
                    pass
            while not self._stop_event.is_set():
                try:
                    message = await asyncio.wait_for(websocket.recv(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                if message is None:
                    break
                try:
                    await self.message_handler(websocket, message)
                except Exception as exc:
                    logging.getLogger(__name__).debug(
                        "WS handler error: %s", exc, exc_info=True
                    )
        except (
            asyncio.CancelledError,
            getattr(websockets, "exceptions", object()).ConnectionClosedOK,
        ):
            pass
        except getattr(websockets, "exceptions", object()).ConnectionClosedError:
            pass
        finally:
            if self.on_client_disconnected:
                try:
                    self.on_client_disconnected(websocket)
                except Exception:
                    pass


class RemoteControlServer:
    """Runs a WebSocket server in background threads and relays commands to Pico."""

    def __init__(
        self,
        serial_port_name: str,
        ws_port: int,
        callbacks: RemoteCallbacks,
        *,
        serial_manager: Optional[SerialManager] = None,
    ) -> None:
        self.serial_port_name = serial_port_name
        self.ws_port = ws_port
        self.callbacks = callbacks
        self.serial_manager = serial_manager or SerialManager(serial_port_name)
        self.cmd_queue: "queue.Queue[tuple[str, bool, Optional[queue.Queue[bool]], Optional[float]]]" = queue.Queue()
        self.stop_event = threading.Event()
        self.writer_thread: Optional[threading.Thread] = None
        self.bridge: Optional[AsyncWebsocketBridge] = None
        self.clients: set = set()
        self.clients_lock = threading.Lock()

    # -- Lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if self.writer_thread and self.writer_thread.is_alive():
            return
        try:
            self.serial_manager.open()
        except Exception as exc:
            logging.error(
                "Remote server failed to open serial on %s: %s",
                self.serial_port_name,
                exc,
            )
            self._set_status("Remote: Serial error")
            return
        self.serial_manager.register_line_callback(self._on_serial_line)
        self.stop_event.clear()
        self.cmd_queue = queue.Queue()
        self.writer_thread = threading.Thread(target=self._writer_loop, daemon=True)
        self.writer_thread.start()
        self.bridge = AsyncWebsocketBridge(
            host="0.0.0.0",
            port=self.ws_port,
            message_handler=self._handle_ws_message,
            on_port_bound=self._on_ws_port_bound,
            on_client_connected=self._on_ws_client_connected,
            on_client_disconnected=self._on_ws_client_disconnected,
            on_error=self._on_ws_error,
        )
        self.bridge.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.bridge:
            self.bridge.stop()
        self.bridge = None
        if self.writer_thread and self.writer_thread.is_alive():
            self.writer_thread.join(timeout=1.5)
        self.writer_thread = None
        self.serial_manager.unregister_line_callback(self._on_serial_line)
        self.serial_manager.close()
        with self.clients_lock:
            self.clients.clear()

    # -- Serial bridge -----------------------------------------------------
    def _writer_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                cmd, wait_ack, response_queue, timeout_s = self.cmd_queue.get(
                    timeout=0.1
                )
            except queue.Empty:
                continue
            result = False
            try:
                self._log(f"TX: {cmd.strip()}")
                result = self.serial_manager.send_payload(
                    cmd,
                    wait_ack=wait_ack,
                    timeout=timeout_s or 1.5,
                )
            except Exception as exc:
                self._log(f"ERR: serial write {exc}")
            finally:
                if response_queue is not None:
                    try:
                        response_queue.put_nowait(result)
                    except queue.Full:
                        pass

    def _on_serial_line(self, line: str) -> None:
        if line == "ACK":
            self._log("RX: ACK")
        elif line == "PICO_READY":
            self._log("RX: PICO_READY")
        else:
            self._log(f"RX: {line}")

    # -- Client helpers ----------------------------------------------------
    def enqueue_hid_payload(
        self,
        payload: str,
        wait_ack: bool = False,
        timeout: float = 1.5,
    ) -> bool:
        message = (payload or "").strip()
        if not message:
            return False
        if message.startswith("hid|"):
            cmd = message
        else:
            parts = message.split("|")
            if len(parts) >= 3 and parts[0] in ("key", "mouse", "scroll"):
                cmd = f"hid|{message}"
            else:
                cmd = message
        if wait_ack:
            response_queue: "queue.Queue[bool]" = queue.Queue(maxsize=1)
            self.cmd_queue.put((cmd, True, response_queue, timeout))
            try:
                return response_queue.get(timeout=timeout)
            except queue.Empty:
                return False
        self.cmd_queue.put((cmd, False, None, timeout))
        return True

    def wait_for_ready(self, timeout: float = 12.0) -> bool:
        """Block until a recent PICO_READY has been seen.

        Strategy: send a handshake probe immediately to prompt a READY, then
        wait a short, capped interval (1–2s) for the response. If that fails
        and we still have time budget, try one more quick attempt.
        """
        try:
            total_budget = max(0.5, timeout)
            # Always poke the Pico first
            try:
                self.serial_manager.send_payload("hello|handshake", wait_ack=False, timeout=0.2)
            except Exception:
                pass
            if self.serial_manager.wait_for_ready(timeout=min(2.0, total_budget)):
                return True
            # Optional second poke if budget allows
            remaining = total_budget - min(2.0, total_budget)
            if remaining > 0.4:
                try:
                    self.serial_manager.send_payload("hello|handshake", wait_ack=False, timeout=0.2)
                except Exception:
                    pass
                return self.serial_manager.wait_for_ready(timeout=min(1.5, remaining))
            return False
        except Exception:
            return False

    def send_hid(
        self,
        event_type: str,
        key: str,
        wait_ack: bool = True,
        timeout: float = 1.5,
    ) -> bool:
        payload = f"{event_type}|{key}"
        return self.enqueue_hid_payload(payload, wait_ack=wait_ack, timeout=timeout)

    def broadcast(self, message: str) -> None:
        """Send a message to all connected WebSocket clients."""
        msg = (message or "").strip()
        if not msg:
            return
        with self.clients_lock:
            for client in list(self.clients):
                try:
                    asyncio.run_coroutine_threadsafe(
                        client.send(msg), self.bridge._loop
                    )
                except Exception:
                    # Client may have disconnected, will be cleaned up later
                    pass

    # -- WebSocket callbacks -----------------------------------------------
    async def _handle_ws_message(self, websocket, message: str) -> None:
        msg = (message or "").strip()
        if not msg:
            return
        if msg.startswith("macro|"):
            action = msg.split("|", 1)[1] if "|" in msg else ""
            if action == "start":
                self._schedule(self.callbacks.start_macro)
            elif action == "stop":
                self._schedule(self.callbacks.stop_macro)
            elif action == "query":
                try:
                    playing = bool(self.callbacks.is_macro_playing())
                except Exception:
                    playing = False
                try:
                    await websocket.send("macro|playing" if playing else "macro|stopped")
                except Exception:
                    pass
            else:
                self._log(f"WS: unknown macro action '{action}'")
            return
        self.enqueue_hid_payload(msg)

    def _on_ws_port_bound(self, port: int) -> None:
        self.ws_port = port
        self._schedule(lambda: self.callbacks.set_ws_port(port))

    def _on_ws_client_connected(self, websocket) -> None:
        with self.clients_lock:
            self.clients.add(websocket)
        peer = getattr(websocket, "remote_address", None)
        self._log(f"WS: client connected {peer}")
        self._set_status(f"Remote: Connected (ws://0.0.0.0:{self.ws_port})")

    def _on_ws_client_disconnected(self, websocket) -> None:
        with self.clients_lock:
            self.clients.discard(websocket)
        self._log("WS: client disconnected")
        self._set_status(f"Remote: Listening (ws://0.0.0.0:{self.ws_port})")

    def _on_ws_error(self, error: Exception) -> None:
        logging.error("WebSocket bridge error: %s", error)
        self._set_status("Remote: WS start error")

    # -- Callback helpers --------------------------------------------------
    def _schedule(self, func: Callable[[], None]) -> None:
        try:
            self.callbacks.schedule(func)
        except Exception:
            try:
                func()
            except Exception:
                logging.getLogger(__name__).debug(
                    "Remote callback error", exc_info=True
                )

    def _log(self, message: str) -> None:
        self._schedule(lambda: self._safe_invoke(self.callbacks.log, message))

    def _set_status(self, message: str) -> None:
        self._schedule(lambda: self._safe_invoke(self.callbacks.set_status, message))

    @staticmethod
    def _safe_invoke(func: Callable, *args) -> None:
        try:
            func(*args)
        except Exception:
            logging.getLogger(__name__).debug("Remote callback error", exc_info=True)
