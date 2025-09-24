import asyncio
import json
import logging
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import pygetwindow as gw
import serial
import serial.tools.list_ports

try:
    import websockets
except Exception:
    websockets = None
from http.server import BaseHTTPRequestHandler, HTTPServer

from .messaging import TelegramHandler
from .settings import CONFIG_FILE, configure_logging
from .transport import SerialManager
from .playback import MacroController


class AsyncWebsocketBridge:
    """Owns the asyncio loop for the remote WebSocket interface."""

    def __init__(
        self,
        host,
        port,
        *,
        message_handler,
        on_port_bound=None,
        on_client_connected=None,
        on_client_disconnected=None,
        on_error=None,
        max_attempts=10,
    ) -> None:
        self.host = host
        self.base_port = int(port)
        self.message_handler = message_handler
        self.on_port_bound = on_port_bound
        self.on_client_connected = on_client_connected
        self.on_client_disconnected = on_client_disconnected
        self.on_error = on_error
        self.max_attempts = max_attempts
        self._thread = None
        self._stop_event = threading.Event()
        self._loop = None
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
                self.on_error(RuntimeError("websockets package is not available"))
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
                self.on_error(exc)
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
        last_error = None
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
                    self.on_port_bound(port)
                return
            except OSError as exc:
                last_error = exc
                continue
            except Exception as exc:
                last_error = exc
                continue
        if self.on_error:
            self.on_error(last_error or RuntimeError("Failed to bind WebSocket server"))

    async def _wait_for_stop(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._stop_event.wait)

    async def _handle_client(self, websocket):
        if self.on_client_connected:
            try:
                self.on_client_connected(websocket)
            except Exception:
                pass
        try:
            async for message in websocket:
                if not self.message_handler:
                    continue
                try:
                    await self.message_handler(websocket, message)
                except Exception as exc:
                    logging.getLogger(__name__).debug(
                        "WS handler error: %s", exc, exc_info=True
                    )
        except (asyncio.CancelledError, websockets.exceptions.ConnectionClosedOK):
            pass
        except websockets.exceptions.ConnectionClosedError:
            pass
        finally:
            if self.on_client_disconnected:
                try:
                    self.on_client_disconnected(websocket)
                except Exception:
                    pass


class RemoteControlServer:
    """Runs a WebSocket server in background threads and relays commands to Pico."""

    def __init__(self, app, serial_port_name, ws_port):
        self.app = app
        self.serial_port_name = serial_port_name
        self.ws_port = ws_port
        self.serial_manager = SerialManager(serial_port_name)
        self.cmd_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.writer_thread = None
        self.bridge = None
        self.clients = set()
        self.clients_lock = threading.Lock()

    def start(self):
        if self.writer_thread and self.writer_thread.is_alive():
            return
        try:
            self.serial_manager.open()
        except Exception as e:
            logging.error(
                "Remote server failed to open serial on %s: %s",
                self.serial_port_name,
                e,
            )
            self.app.root.after(
                0, lambda: self.app.remote_status_var.set("Remote: Serial error")
            )
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

    def stop(self):
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

    def _writer_loop(self):
        while not self.stop_event.is_set():
            try:
                cmd, wait_ack, response_queue, timeout_s = self.cmd_queue.get(
                    timeout=0.1
                )
            except queue.Empty:
                continue
            result = False
            try:
                self.app.log_remote(f"TX: {cmd.strip()}")
                result = self.serial_manager.send_payload(
                    cmd,
                    wait_ack=wait_ack,
                    timeout=timeout_s or 1.5,
                )
            except Exception as exc:
                self.app.log_remote(f"ERR: serial write {exc}")
            finally:
                if response_queue is not None:
                    try:
                        response_queue.put_nowait(result)
                    except queue.Full:
                        pass

    def _on_serial_line(self, line: str) -> None:
        if line == "ACK":
            self.app.log_remote("RX: ACK")
        elif line == "PICO_READY":
            self.app.log_remote("RX: PICO_READY")
        else:
            self.app.log_remote(f"RX: {line}")

    def enqueue_hid_payload(
        self, payload: str, wait_ack: bool = False, timeout: float = 1.5
    ) -> bool:
        p = (payload or "").strip()
        if not p:
            return False
        if p.startswith("hid|"):
            cmd = p
        else:
            parts = p.split("|")
            if len(parts) >= 3 and parts[0] in ("key", "mouse", "scroll"):
                cmd = f"hid|{p}"
            elif len(parts) == 2 and parts[0] in ("down", "up"):
                cmd = p
            else:
                cmd = p
        if wait_ack:
            response_queue = queue.Queue(maxsize=1)
            self.cmd_queue.put((cmd, True, response_queue, timeout))
            try:
                return response_queue.get(timeout=timeout)
            except queue.Empty:
                return False
        self.cmd_queue.put((cmd, False, None, timeout))
        return True

    def send_hid(
        self, event_type: str, key: str, wait_ack: bool = True, timeout: float = 1.5
    ) -> bool:
        payload = f"{event_type}|{key}"
        return self.enqueue_hid_payload(payload, wait_ack=wait_ack, timeout=timeout)

    def wait_for_ready(self, timeout: float = 12.0) -> bool:
        return self.serial_manager.wait_for_ready(timeout)

    async def _handle_ws_message(self, websocket, message: str) -> None:
        msg = (message or "").strip()
        if not msg:
            return
        if msg.startswith("macro|"):
            action = msg.split("|", 1)[1] if "|" in msg else ""
            if action == "start":
                self.app.root.after(0, self.app.start_macro)
            elif action == "stop":
                self.app.root.after(0, self.app.stop_macro)
            else:
                self.app.log_remote(f"WS: unknown macro action '{action}'")
            return
        self.enqueue_hid_payload(msg)

    def _on_ws_port_bound(self, port: int) -> None:
        self.ws_port = port
        try:
            self.app.root.after(0, lambda: self.app.ws_port_var.set(str(port)))
            self.app.root.after(
                0,
                lambda: self.app.remote_status_var.set(
                    f"Remote: Listening (ws://0.0.0.0:{port})"
                ),
            )
        except Exception:
            pass

    def _on_ws_client_connected(self, websocket) -> None:
        with self.clients_lock:
            self.clients.add(websocket)
        peer = getattr(websocket, "remote_address", None)
        try:
            self.app.log_remote(f"WS: client connected {peer}")
        except Exception:
            pass
        self.app.root.after(
            0,
            lambda: self.app.remote_status_var.set(
                f"Remote: Connected (ws://0.0.0.0:{self.ws_port})"
            ),
        )

    def _on_ws_client_disconnected(self, websocket) -> None:
        with self.clients_lock:
            self.clients.discard(websocket)
        try:
            self.app.log_remote("WS: client disconnected")
        except Exception:
            pass
        self.app.root.after(
            0,
            lambda: self.app.remote_status_var.set(
                f"Remote: Listening (ws://0.0.0.0:{self.ws_port})"
            ),
        )

    def _on_ws_error(self, error: Exception) -> None:
        logging.error("WebSocket bridge error: %s", error)
        try:
            self.app.root.after(
                0, lambda: self.app.remote_status_var.set("Remote: WS start error")
            )
        except Exception:
            pass


class EmbeddedHTTPServer:
    """Simple embedded HTTP server to serve a dynamic controller page."""

    def __init__(self, app, http_port):
        self.app = app
        self.http_port = http_port
        self.httpd = None
        self.thread = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return

        ws_port = (
            int(self.app.ws_port_var.get())
            if str(self.app.ws_port_var.get()).isdigit()
            else 8765
        )

        # Serve external index.html from disk (with WS port token replacement)
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self_inner):  # noqa: N802
                _ = parent  # reference outer
                try:
                    if getattr(self_inner, "path", "/") != "/":
                        self_inner.send_response(404)
                        self_inner.send_header(
                            "Content-Type", "text/plain; charset=utf-8"
                        )
                        self_inner.end_headers()
                        self_inner.wfile.write(b"Not Found")
                        return
                    # Read index.html located alongside picobot.py
                    base_dir = os.path.dirname(os.path.abspath(__file__))
                    index_path = os.path.join(base_dir, "index.html")
                    try:
                        with open(index_path, "r", encoding="utf-8") as f:
                            content = f.read()
                    except Exception:
                        # Fallback to embedded minimal page if file missing
                        content = """<html><body style='background:#121212;color:#eee;font-family:sans-serif'>
                        <h3 style='margin:16px'>index.html not found</h3>
                        <p style='margin:16px'>Create <code>index.html</code> in the PicoBot folder. You can use the token <code>REPLACE_WS_PORT</code> and it will be replaced with the active WebSocket port.</p>
                        </body></html>"""
                    # Replace WS port token if present
                    try:
                        content = content.replace("REPLACE_WS_PORT", str(ws_port))
                    except Exception:
                        pass
                    self_inner.send_response(200)
                    self_inner.send_header("Content-Type", "text/html; charset=utf-8")
                    self_inner.end_headers()
                    self_inner.wfile.write(content.encode("utf-8"))
                except Exception:
                    pass

            def log_message(self_inner, format, *args):  # Suppress console logs
                return

        try:
            self.httpd = HTTPServer(("0.0.0.0", self.http_port), Handler)
            self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
            self.thread.start()
        except Exception as e:
            logging.error(f"Failed to start HTTP server on port {self.http_port}: {e}")

    def stop(self):
        try:
            if self.httpd:
                self.httpd.shutdown()
                self.httpd.server_close()
        except Exception:
            pass
        self.httpd = None
        self.thread = None


class MacroControllerApp:
    """Main application class for the PicoBot macro controller GUI."""

    def __init__(self, root):
        """Initializes the MacroControllerApp with the main window.

        Args:
            root (tk.Tk): The main Tkinter window.
        """
        self.root = root
        self.root.title("Pico Continuous Macro Controller")
        self.root.geometry("500x450")  # Increased height for new elements

        # Configure logging
        logging.basicConfig(
            level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
        )

        # --- State Variables ---
        self.is_playing = False
        self.macro_thread = None
        self.keys_currently_down = set()
        # Remote control state
        self.remote_server = None
        self.remote_status_var = tk.StringVar(value="Remote: Stopped")
        self.ws_port_var = tk.StringVar(value="8765")
        self.http_server = None
        self.http_port_var = tk.StringVar(value="8000")

        # --- Telegram Settings ---
        self.bot_token_var = tk.StringVar(value="")
        self.chat_id_var = tk.StringVar(value="")
        self.countdown_seconds_var = tk.StringVar(value="60")
        self.countdown_running = False
        self.countdown_thread = None
        self.countdown_status_var = tk.StringVar(value="Countdown: Idle")
        self.telegram = TelegramHandler("", "")

        # --- Macro Controller ---
        self.macro_controller = MacroController(self)

        # --- Pico Connection ---
        self.selected_port = tk.StringVar(root)
        self.create_pico_connection_ui()

        # --- Window Selection ---
        self.selected_window = tk.StringVar(root)
        self.create_window_selection_ui()

        # --- Macro Folder Selection ---
        self.macro_folder_path = tk.StringVar(value="No folder selected.")
        self.create_macro_folder_ui()

        # --- Telegram Notification Settings ---
        self.create_telegram_settings_ui()

        # --- Remote Control (WebSocket) ---
        self.create_remote_ui()

        # --- Options ---
        self.pin_var = tk.BooleanVar(value=True)
        self.create_options_ui()

        # --- Controls ---
        self.create_controls_ui()

        # --- Status Bars ---
        self.status_text = tk.StringVar(
            value="Status: Idle. Click START to begin. Switch windows to stop."
        )
        self.create_status_bars()

        # --- Initial Setup ---
        self.load_config()
        self.refresh_windows()  # Refresh windows after loading config

        # Make window height dynamic based on content
        self.root.update_idletasks()
        height = self.root.winfo_reqheight()
        self.root.geometry(f"500x{height}")

    def create_remote_ui(self):
        """Creates the UI elements for Remote Control via WebSocket."""
        self.remote_frame = tk.LabelFrame(
            self.root, text="5. Remote Control (WebSocket)", padx=10, pady=10
        )
        self.remote_frame.pack(padx=10, pady=10, fill="x")

        # Port entries
        tk.Label(self.remote_frame, text="WS Port:").grid(row=0, column=0, sticky="w")
        self.ws_port_entry = tk.Entry(
            self.remote_frame, textvariable=self.ws_port_var, width=8
        )
        self.ws_port_entry.grid(row=0, column=1, sticky="w")
        self.ws_port_entry.bind("<FocusOut>", self.save_config)
        tk.Label(self.remote_frame, text="HTTP Port:").grid(
            row=0, column=2, sticky="w", padx=(10, 0)
        )
        self.http_port_entry = tk.Entry(
            self.remote_frame, textvariable=self.http_port_var, width=8
        )
        self.http_port_entry.grid(row=0, column=3, sticky="w")
        self.http_port_entry.bind("<FocusOut>", self.save_config)

        # Start/Stop buttons
        self.remote_start_btn = tk.Button(
            self.remote_frame,
            text="Start Remote",
            command=self.start_remote,
            bg="#1976D2",
            fg="white",
        )
        self.remote_start_btn.grid(row=0, column=4, padx=(10, 0))
        self.remote_stop_btn = tk.Button(
            self.remote_frame,
            text="Stop Remote",
            command=self.stop_remote,
            state=tk.DISABLED,
        )
        self.remote_stop_btn.grid(row=0, column=5, padx=(5, 0))

        # Status label
        self.remote_status_label = tk.Label(
            self.remote_frame, textvariable=self.remote_status_var, anchor="w"
        )
        self.remote_status_label.grid(
            row=1, column=0, columnspan=6, sticky="ew", pady=(8, 0)
        )

        self.remote_frame.grid_columnconfigure(5, weight=1)

        # Remote log (visible TX/RX/events)
        self.remote_log = ScrolledText(
            self.remote_frame, height=10, wrap="word", state=tk.DISABLED
        )
        self.remote_log.grid(row=2, column=0, columnspan=6, sticky="nsew", pady=(8, 0))
        # Allow the log to expand vertically
        self.remote_frame.grid_rowconfigure(2, weight=1)

    def start_remote(self):
        """Starts the remote control WebSocket server."""
        if websockets is None:
            messagebox.showerror(
                "Missing Dependency",
                "The 'websockets' package is required. Install with: pip install websockets",
            )
            return
        port = self.selected_port.get()
        if not port or "No COM" in port:
            messagebox.showerror("Error", "Please select a Pico COM port first.")
            return
        try:
            ws_port = int(self.ws_port_var.get())
        except ValueError:
            messagebox.showerror("Error", "Invalid WebSocket port.")
            return
        try:
            http_port = int(self.http_port_var.get())
        except ValueError:
            messagebox.showerror("Error", "Invalid HTTP port.")
            return
        if self.remote_server:
            messagebox.showinfo("Remote", "Remote control is already running.")
            return
        # Start background remote server
        self.remote_server = RemoteControlServer(self, port, ws_port)
        self.remote_server.start()

        # Start embedded HTTP server shortly after WS so WS can pick a fallback port if needed
        def _start_http():
            try:
                if self.http_server:
                    try:
                        self.http_server.stop()
                    except Exception:
                        pass
                self.http_server = EmbeddedHTTPServer(self, http_port)
                self.http_server.start()
            finally:
                try:
                    self.remote_status_var.set(
                        f"Remote: Starting on ws://0.0.0.0:{self.ws_port_var.get()} and http://0.0.0.0:{self.http_port_var.get()}"
                    )
                except Exception:
                    pass

        # Give WS thread ~300ms to bind (and possibly change port)
        try:
            self.root.after(300, _start_http)
        except Exception:
            _start_http()
        self.remote_start_btn.config(state=tk.DISABLED)
        self.remote_stop_btn.config(state=tk.NORMAL)
        # Persist ports
        try:
            self.save_config()
        except Exception:
            pass

    def maybe_autostart_remote(self):
        """Start the Remote server automatically once a valid Pico DATA port is selected."""
        try:
            if self.remote_server is not None:
                return
            if websockets is None:
                return
            port = self.selected_port.get()
            if not port or "No COM" in port:
                return
            # Ensure WS/HTTP vars are valid ints
            try:
                int(self.ws_port_var.get())
                int(self.http_port_var.get())
            except Exception:
                return
            # Fire-and-forget auto-start
            self.start_remote()
        except Exception:
            pass

    def log_remote(self, text: str):
        """Thread-safe append to the Remote log view with auto-scroll."""
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] {text}\n"

        def _append():
            try:
                self.remote_log.configure(state=tk.NORMAL)
                self.remote_log.insert(tk.END, line)
                # Cap the buffer size to avoid unbounded growth (~5000 lines)
                max_chars = 200000
                if (
                    int(self.remote_log.index("end-1c").split(".")[0]) > 5000
                    or len(self.remote_log.get("1.0", tk.END)) > max_chars
                ):
                    self.remote_log.delete("1.0", "3.0")  # prune first couple lines
                self.remote_log.see(tk.END)
            finally:
                self.remote_log.configure(state=tk.DISABLED)

        # Ensure UI changes happen on the main thread
        try:
            self.root.after(0, _append)
        except Exception:
            pass

    def stop_remote(self):
        """Stops the remote control WebSocket server."""
        try:
            if self.remote_server:
                self.remote_server.stop()
            if self.http_server:
                self.http_server.stop()
        finally:
            self.remote_server = None
            self.http_server = None
            self.remote_status_var.set("Remote: Stopped")
            self.remote_start_btn.config(state=tk.NORMAL)
            self.remote_stop_btn.config(state=tk.DISABLED)
            try:
                self.save_config()
            except Exception:
                pass

    def create_pico_connection_ui(self):
        """Creates the UI elements for Pico COM port selection."""
        self.pico_frame = tk.LabelFrame(
            self.root, text="1. Select Pico COM Port", padx=10, pady=10
        )
        self.pico_frame.pack(padx=10, pady=10, fill="x")
        self.port_menu = ttk.Combobox(
            self.pico_frame, textvariable=self.selected_port, state="readonly"
        )
        self.port_menu.pack(side=tk.LEFT, fill="x", expand=True)
        self.refresh_ports_button = tk.Button(
            self.pico_frame, text="Refresh", command=self.macro_controller.refresh_ports
        )
        self.refresh_ports_button.pack(side=tk.RIGHT, padx=(10, 0))
        self.macro_controller.refresh_ports()
        # Try to auto-select the Pico DATA port on startup (force override any prior selection)
        self.macro_controller.auto_select_pico_port_async(force=True)

    def start_countdown_internal(self):
        """Starts the countdown timer in a separate thread."""
        try:
            seconds = int(self.countdown_seconds_var.get())
            bot_token = self.bot_token_var.get()
            chat_id = self.chat_id_var.get()
            if not bot_token or not chat_id:
                messagebox.showerror("Error", "Please enter Bot Token and Chat ID.")
                return
            self.countdown_running = True
            self.countdown_status_var.set(f"Countdown: {seconds} seconds remaining")

            # Update Telegram handler with current values
            self.telegram.bot_token = self.bot_token_var.get()
            self.telegram.chat_id = self.chat_id_var.get()

            def countdown_thread():
                completed = True
                for i in range(seconds, 0, -1):
                    if not self.countdown_running:
                        completed = False
                        break
                    if not self.is_playing:
                        completed = False
                        break
                    time.sleep(1)
                    self.root.after(
                        0,
                        lambda i=i: self.countdown_status_var.set(
                            f"Countdown: {i} seconds remaining"
                        ),
                    )
                if self.countdown_running and completed:
                    # Always update the status bar to indicate completion
                    self.root.after(
                        0,
                        lambda: self.countdown_status_var.set("Countdown: Completed!"),
                    )

                    # Send Telegram notification if configured
                    if self.bot_token_var.get() and self.chat_id_var.get():
                        self.root.after(
                            0,
                            lambda: self.countdown_status_var.set(
                                "Countdown: Sending notification..."
                            ),
                        )
                        self.telegram.send_message("Countdown timer finished!")
                        self.root.after(
                            0,
                            lambda: self.countdown_status_var.set(
                                "Countdown: Notification sent"
                            ),
                        )
                    else:
                        self.root.after(
                            0,
                            lambda: self.countdown_status_var.set(
                                "Countdown: Completed!"
                            ),
                        )

                    # Reset to idle after a short delay to show the completion message
                    self.root.after(
                        2000, lambda: self.countdown_status_var.set("Countdown: Idle")
                    )
                    if not self.is_playing:
                        self.root.after(
                            0,
                            lambda: self.start_button.config(
                                text="START", state=tk.NORMAL
                            ),
                        )
                self.countdown_running = False

            self.countdown_thread = threading.Thread(
                target=countdown_thread, daemon=True
            )
            self.countdown_thread.start()
        except ValueError:
            messagebox.showerror("Error", "Invalid countdown seconds.")
            self.countdown_running = False

    def create_window_selection_ui(self):
        """Creates the UI elements for window selection."""
        self.window_frame = tk.LabelFrame(
            self.root, text="2. Select Target Window", padx=10, pady=10
        )
        self.window_frame.pack(padx=10, pady=10, fill="x")
        self.window_menu = ttk.Combobox(
            self.window_frame, textvariable=self.selected_window, state="readonly"
        )
        self.window_menu.pack(side=tk.LEFT, fill="x", expand=True)
        self.window_menu.bind(
            "<<ComboboxSelected>>", self.save_config
        )  # Save on change
        self.refresh_win_button = tk.Button(
            self.window_frame,
            text="Refresh",
            command=self.macro_controller.refresh_windows,
        )
        self.refresh_win_button.pack(side=tk.RIGHT, padx=(10, 0))

    def create_macro_folder_ui(self):
        """Creates the UI elements for macro folder selection."""
        self.macro_frame = tk.LabelFrame(
            self.root, text="3. Select Macro Folder", padx=10, pady=10
        )
        self.macro_frame.pack(padx=10, pady=10, fill="x")
        self.select_button = tk.Button(
            self.macro_frame, text="Browse Folder...", command=self.select_macro_folder
        )
        self.select_button.pack(side=tk.LEFT, padx=(0, 10))
        self.macro_label = tk.Label(
            self.macro_frame, textvariable=self.macro_folder_path, anchor="w"
        )
        self.macro_label.pack(side=tk.LEFT, fill="x", expand=True)

    def create_telegram_settings_ui(self):
        """Creates the UI elements for Telegram notification settings."""
        self.telegram_frame = tk.LabelFrame(
            self.root, text="4. Telegram Notification Settings", padx=10, pady=10
        )
        self.telegram_frame.pack(padx=10, pady=10, fill="x")

        # Bot Token
        tk.Label(self.telegram_frame, text="Bot Token:").grid(
            row=0, column=0, sticky="w"
        )
        self.bot_token_entry = tk.Entry(
            self.telegram_frame, textvariable=self.bot_token_var
        )
        self.bot_token_entry.grid(row=0, column=1, padx=(5, 0), sticky="ew")

        # Chat ID
        tk.Label(self.telegram_frame, text="Chat ID:").grid(row=1, column=0, sticky="w")
        self.chat_id_entry = tk.Entry(
            self.telegram_frame, textvariable=self.chat_id_var
        )
        self.chat_id_entry.grid(row=1, column=1, padx=(5, 0), sticky="ew")

        # Countdown Seconds
        tk.Label(self.telegram_frame, text="Countdown (sec):").grid(
            row=2, column=0, sticky="w"
        )
        self.countdown_entry = tk.Entry(
            self.telegram_frame, textvariable=self.countdown_seconds_var
        )
        self.countdown_entry.grid(row=2, column=1, padx=(5, 0), sticky="ew")

        # Preset Buttons
        self.ten_min_button = tk.Button(
            self.telegram_frame,
            text="10 Min",
            command=lambda: self.set_countdown_preset(600),
        )
        self.ten_min_button.grid(row=2, column=2, padx=(5, 0))
        self.fifteen_min_button = tk.Button(
            self.telegram_frame,
            text="15 Min",
            command=lambda: self.set_countdown_preset(900),
        )
        self.fifteen_min_button.grid(row=2, column=3, padx=(5, 0))

        # Configure grid to allow expansion
        self.telegram_frame.grid_columnconfigure(1, weight=1)

    def create_options_ui(self):
        """Creates the UI elements for application options."""
        self.pin_check = tk.Checkbutton(
            self.root,
            text="Pin window (always on top)",
            variable=self.pin_var,
            command=self.toggle_always_on_top,
        )
        self.pin_check.pack(padx=10, anchor="w")
        # Default to always-on-top on first launch; load_config may override
        try:
            self.root.attributes("-topmost", True)
        except Exception:
            pass

    def create_controls_ui(self):
        """Creates the UI elements for application controls."""
        self.control_frame = tk.Frame(self.root)
        self.control_frame.pack(padx=10, pady=5, fill="x")
        self.start_button = tk.Button(
            self.control_frame,
            text="START",
            command=self.start_macro,
            font=("Helvetica", 12, "bold"),
            bg="#4CAF50",
            fg="white",
            state=tk.NORMAL,
        )
        self.start_button.pack(side=tk.LEFT, fill="x", expand=True)

    def create_status_bars(self):
        """Creates the UI elements for status bars."""
        self.status_bar = tk.Label(
            self.root,
            textvariable=self.status_text,
            relief=tk.SUNKEN,
            anchor="w",
            padx=5,
        )
        self.status_bar.pack(side=tk.BOTTOM, fill="x")

        # Countdown Status
        self.countdown_status_label = tk.Label(
            self.root,
            textvariable=self.countdown_status_var,
            fg="blue",
            relief=tk.SUNKEN,
            anchor="w",
            padx=5,
        )
        self.countdown_status_label.pack(side=tk.BOTTOM, fill="x")

    def load_config(self):
        """Load configuration from config.json and apply to UI variables."""
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, "r") as f:
                    config = json.load(f)
                    # UI selections
                    self.selected_window.set(config.get("last_window", ""))
                    self.macro_folder_path.set(
                        config.get("last_folder", "No folder selected.")
                    )
                    # Always-on-top
                    aot = config.get("always_on_top", True)
                    self.pin_var.set(aot)
                    try:
                        self.root.attributes("-topmost", aot)
                    except Exception:
                        pass
                    # Telegram
                    self.bot_token_var.set(config.get("bot_token", ""))
                    self.chat_id_var.set(config.get("chat_id", ""))
                    self.countdown_seconds_var.set(
                        str(config.get("countdown_seconds", 60))
                    )
                    # Remote ports
                    if "ws_port" in config:
                        self.ws_port_var.set(str(config.get("ws_port")))
                    if "http_port" in config:
                        self.http_port_var.set(str(config.get("http_port")))
                    logging.info("Configuration loaded.")
        except json.JSONDecodeError as e:
            logging.error(f"Error loading config: Invalid JSON - {e}")
        except Exception as e:
            logging.error(f"Could not load config file: {e}")

    def save_config(self, event=None):
        """Save current configuration to config.json.

        Args:
            event (tk.Event, optional): The event that triggered the save.
        """
        config = {
            "last_window": self.selected_window.get(),
            "last_folder": self.macro_folder_path.get(),
            "always_on_top": bool(self.pin_var.get()),
            "bot_token": self.bot_token_var.get(),
            "chat_id": self.chat_id_var.get(),
            "countdown_seconds": int(self.countdown_seconds_var.get())
            if self.countdown_seconds_var.get().isdigit()
            else 60,
            "ws_port": int(self.ws_port_var.get())
            if str(self.ws_port_var.get()).isdigit()
            else 8765,
            "http_port": int(self.http_port_var.get())
            if str(self.http_port_var.get()).isdigit()
            else 8000,
        }
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(config, f, indent=4)
            logging.info("Configuration saved.")
        except Exception as e:
            logging.error(f"Could not save config file: {e}")

    def toggle_always_on_top(self):
        """Toggles the always-on-top window attribute and saves the preference."""
        try:
            self.root.attributes("-topmost", bool(self.pin_var.get()))
        except Exception as e:
            logging.error(f"Could not set always-on-top: {e}")
        # Persist preference
        self.save_config()

    def set_countdown_preset(self, seconds):
        """Sets the countdown duration to a preset value.

        Args:
            seconds (int): The countdown duration in seconds.
        """
        self.countdown_seconds_var.set(str(seconds))

    def start_macro(self):
        """Starts the macro playback and countdown timer if enabled."""
        # Auto-focus the target window if selected
        target_window = self.selected_window.get()
        if target_window and "No windows" not in target_window:
            try:
                target_windows = gw.getWindowsWithTitle(target_window)
                if target_windows:
                    target_windows[0].activate()
            except Exception as e:
                print(f"Could not activate window: {e}")

        # Stop countdown if running
        if self.countdown_running:
            self.countdown_running = False
            self.countdown_status_var.set("Countdown: Idle")
            if not self.is_playing:
                self.start_button.config(text="START", state=tk.NORMAL)

        # Check if countdown settings are filled
        countdown_enabled = (
            self.countdown_seconds_var.get().isdigit()
            and int(self.countdown_seconds_var.get()) > 0
        )

        # Always try to start macro if not already playing
        if not self.is_playing:
            port = self.selected_port.get()
            window_title = self.selected_window.get()
            macro_folder = self.macro_folder_path.get()

            if (
                (not port)
                or ("No COM" in port)
                or not window_title
                or ("No folder" in macro_folder)
            ):
                messagebox.showerror(
                    "Error",
                    "Please select a COM port, a target window, and a macro folder.",
                )
                return

            try:
                if not [f for f in os.listdir(macro_folder) if f.endswith(".txt")]:
                    messagebox.showerror(
                        "Error", "No '.txt' macro files found in the folder."
                    )
                    return
            except Exception as e:
                messagebox.showerror(
                    "Folder Error", f"Could not read macro folder.\nError: {e}"
                )
                return

            print("Starting macro loop...")
            self.start_button.config(state=tk.DISABLED)
            self.macro_thread = threading.Thread(
                target=self.macro_controller.play_macro_thread,
                args=(port, window_title, macro_folder),
            )
            self.macro_thread.daemon = True
            self.macro_thread.start()

            # Start countdown timer if enabled
            if countdown_enabled:
                self.start_countdown_internal()

    def stop_macro(self):
        """Programmatically stop the macro loop (used by Remote Control)."""
        if self.is_playing:
            self.is_playing = False
            try:
                self.status_text.set("Status: Stopping...")
            except Exception:
                pass

    def on_macro_thread_exit(self):
        """Safely updates GUI elements from the main thread after the macro thread has finished."""
        self.is_playing = False  # Ensure state is final
        self.status_text.set("Status: Stopped. Ready to start.")
        self.start_button.config(state=tk.NORMAL)
        print("GUI updated. Macro has fully stopped.")

    def auto_select_pico_port_async(self, force=False):
        """Background auto-detect of the Pico DATA port. If found, select it in the combobox.
        force=True will always set the detected port; otherwise only when selection is empty/invalid.
        """

        def worker():
            # 1) Quick heuristic: interface index 2 usually maps to DATA CDC (location endswith 'x.2')
            port = self.quick_guess_pico_data_port()
            # 2) Fallback to active handshake probing across ports
            if not port:
                port = self.macro_controller.find_data_port()
            if port:
                self.root.after(
                    0, lambda p=port: self._set_selected_port_if_appropriate(p, force)
                )
            else:
                # Leave selection empty and inform user
                self.root.after(
                    0,
                    lambda: self.status_text.set(
                        "Status: No Pico DATA port detected. Connect and click Refresh."
                    ),
                )

        threading.Thread(target=worker, daemon=True).start()

    def quick_guess_pico_data_port(self):
        """Return device name of Pico DATA port using USB interface location hint (x.2), else None."""
        try:
            for info in serial.tools.list_ports.comports():
                loc = getattr(info, "location", "") or ""
                # On Windows, CircuitPython typically enumerates CDC console as x.0 and DATA as x.2
                if loc.endswith("x.2"):
                    return info.device
        except Exception:
            pass
        return None

    def _set_selected_port_if_appropriate(self, port, force):
        try:
            values = list(self.port_menu["values"])
        except Exception:
            values = []
        if port not in values:
            values.append(port)
            self.port_menu["values"] = values
        current = self.selected_port.get()
        if force or (not current) or ("No COM" in current) or (current not in values):
            self.selected_port.set(port)
            try:
                self.status_text.set(f"Status: Auto-selected Pico DATA port {port}.")
            except Exception:
                pass

    def refresh_ports(self):
        """Refreshes the list of available COM ports and triggers auto-detection."""
        ports = [port.device for port in serial.tools.list_ports.comports()]
        # Populate list but do not auto-select any port
        self.port_menu["values"] = ports
        try:
            self.port_menu.set("")  # clear any previous selection in the widget
        except Exception:
            pass
        self.selected_port.set("")  # ensure state variable is empty
        # After listing, kick off background auto-detection to pick the DATA port if present
        self.auto_select_pico_port_async(force=False)

    def refresh_windows(self):
        """Refreshes the list of available windows and tries to re-select the saved window."""
        window_list = [title for title in gw.getAllTitles() if title]
        self.window_menu["values"] = (
            window_list if window_list else ["No windows found"]
        )
        # Try to re-select the saved window if it's in the list
        saved_window = self.selected_window.get()
        if saved_window and saved_window in window_list:
            self.window_menu.set(saved_window)
        elif window_list:
            self.window_menu.set(window_list[0])
        else:
            self.window_menu.set("No windows found")

    def select_macro_folder(self):
        """Opens a dialog for the user to select a macro folder and saves the selection."""
        folderpath = filedialog.askdirectory(title="Select Folder Containing Macros")
        if folderpath:
            self.macro_folder_path.set(folderpath)
            self.save_config()


def main() -> None:
    """Launch the PicoBot GUI application."""

    configure_logging()
    root = tk.Tk()
    MacroControllerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
