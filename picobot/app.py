import logging
import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .config import AppConfig
from .config import load_config as load_app_config
from .config import save_config as save_app_config
from .context import AppContext
from .countdown import CountdownService
from .messaging import TelegramHandler
from .playback import MacroController
from .remote import EmbeddedHTTPServer, RemoteCallbacks, RemoteControlServer
from .services.system import PortSelection, PortService, WindowSelection, WindowService
from .settings import configure_logging
from .ui import PortSelectorView, RemoteView


class MacroControllerApp:
    """Main application class for the PicoBot macro controller GUI."""

    def __init__(
        self,
        root,
        *,
        context: AppContext | None = None,
        config: AppConfig | None = None,
    ):
        """Initialize the MacroControllerApp with the main window."""
        self.root = root
        self.root.title("Pico Continuous Macro Controller")
        self.root.geometry("500x450")

        self.config = config or load_app_config()

        if context is None:
            telegram = TelegramHandler(self.config.bot_token, self.config.chat_id)
            context = AppContext(
                telegram=telegram,
                port_service=PortService(),
                window_service=WindowService(),
                countdown=CountdownService(telegram),
            )
        self.context = context
        self.telegram = self.context.telegram
        self.countdown_service = self.context.countdown

        # --- State Variables ---
        self.is_playing = False
        self.macro_thread = None
        self.keys_currently_down = set()
        self.remote_server = None
        self.remote_status_var = tk.StringVar(value="Remote: Stopped")
        self.ws_port_var = tk.StringVar(value=str(self.config.ws_port))
        self.http_server = None
        self.http_port_var = tk.StringVar(value=str(self.config.http_port))

        # --- Telegram Settings ---
        self.bot_token_var = tk.StringVar(value=self.config.bot_token)
        self.chat_id_var = tk.StringVar(value=self.config.chat_id)
        self.countdown_seconds_var = tk.StringVar(
            value=str(self.config.countdown_seconds)
        )
        self.countdown_status_var = tk.StringVar(value="Countdown: Idle")

        # --- Macro Controller ---
        self.macro_controller = MacroController(
            self,
            port_service=self.context.port_service,
            window_service=self.context.window_service,
        )

        # --- UI Construction ---
        self.selected_port = tk.StringVar(root)
        self.create_pico_connection_ui()

        self.selected_window = tk.StringVar(root, value=self.config.last_window)
        self.create_window_selection_ui()

        self.macro_folder_path = tk.StringVar(
            value=self.config.last_folder or "No folder selected."
        )
        self.create_macro_folder_ui()
        self.create_telegram_settings_ui()
        self.create_remote_ui()

        self.pin_var = tk.BooleanVar(value=self.config.always_on_top)
        self.create_options_ui()
        self.create_controls_ui()

        self.status_text = tk.StringVar(
            value="Status: Idle. Click START to begin. Switch windows to stop."
        )
        self.create_status_bars()

        self._apply_config_to_ui()
        self.refresh_windows()
        self.root.update_idletasks()
        height = self.root.winfo_reqheight()
        self.root.geometry(f"500x{height}")

    def create_pico_connection_ui(self):
        """Creates the UI elements for Pico COM port selection."""
        self.port_view = PortSelectorView(
            self.root,
            selected_port=self.selected_port,
            on_refresh=self.refresh_ports,
            on_auto_select=lambda: self.auto_select_port_async(force=True),
        )
        self.pico_frame = self.port_view.frame
        self.port_menu = self.port_view.combobox
        self.refresh_ports_button = self.port_view.refresh_button
        self.auto_select_button = self.port_view.auto_button
        self.refresh_ports()

    def start_countdown_internal(self):
        """Starts the countdown timer using the countdown service."""
        try:
            seconds = int(self.countdown_seconds_var.get())
        except ValueError:
            messagebox.showerror("Error", "Invalid countdown seconds.")
            return
        if seconds <= 0:
            messagebox.showerror("Error", "Countdown must be greater than zero.")
            return
        bot_token = self.bot_token_var.get()
        chat_id = self.chat_id_var.get()
        if not bot_token or not chat_id:
            messagebox.showerror("Error", "Please enter Bot Token and Chat ID.")
            return
        self.telegram.bot_token = bot_token
        self.telegram.chat_id = chat_id
        self.countdown_status_var.set(f"Countdown: {seconds} seconds remaining")

        def on_tick(remaining: int) -> None:
            self.root.after(
                0,
                lambda r=remaining: self.countdown_status_var.set(
                    f"Countdown: {r} seconds remaining"
                ),
            )

        def on_status(message: str) -> None:
            self.root.after(0, lambda m=message: self.countdown_status_var.set(m))

        def on_complete(success: bool) -> None:
            def update() -> None:
                if success:
                    self.countdown_status_var.set("Countdown: Completed!")
                    self.root.after(
                        2000, lambda: self.countdown_status_var.set("Countdown: Idle")
                    )
                else:
                    self.countdown_status_var.set("Countdown: Idle")

            self.root.after(0, update)

        try:
            self.countdown_service.start(
                seconds,
                on_tick=on_tick,
                on_status=on_status,
                on_complete=on_complete,
            )
        except ValueError as exc:
            messagebox.showerror("Error", str(exc))
            self.countdown_status_var.set("Countdown: Idle")

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
        self.window_menu.bind("<<ComboboxSelected>>", self.save_config)
        self.refresh_win_button = tk.Button(
            self.window_frame, text="Refresh", command=self.refresh_windows
        )
        self.refresh_win_button.pack(side=tk.RIGHT, padx=(10, 0))
        self.refresh_windows()

    def create_macro_folder_ui(self):
        """Build the macro folder chooser widgets."""
        self.macro_folder_frame = tk.LabelFrame(
            self.root,
            text="3. Select Macro Folder",
            padx=10,
            pady=10,
        )
        self.macro_folder_frame.pack(padx=10, pady=10, fill="x")

        self.macro_folder_entry = tk.Entry(
            self.macro_folder_frame,
            textvariable=self.macro_folder_path,
            state="readonly",
        )
        self.macro_folder_entry.pack(side=tk.LEFT, fill="x", expand=True)

        browse_button = tk.Button(
            self.macro_folder_frame,
            text="Browse",
            command=self.select_macro_folder,
        )
        browse_button.pack(side=tk.LEFT, padx=(10, 0))

    def create_telegram_settings_ui(self):
        """Create inputs for Telegram credentials and countdown presets."""
        self.telegram_frame = tk.LabelFrame(
            self.root, text="4. Telegram & Countdown", padx=10, pady=10
        )
        self.telegram_frame.pack(padx=10, pady=10, fill="x")

        tk.Label(self.telegram_frame, text="Bot Token:").grid(
            row=0, column=0, sticky="w"
        )
        self.bot_token_entry = tk.Entry(
            self.telegram_frame, textvariable=self.bot_token_var, width=32
        )
        self.bot_token_entry.grid(row=0, column=1, sticky="ew", padx=(5, 0))
        self.bot_token_entry.bind("<FocusOut>", self.save_config)

        tk.Label(self.telegram_frame, text="Chat ID:").grid(
            row=1, column=0, sticky="w", pady=(6, 0)
        )
        self.chat_id_entry = tk.Entry(
            self.telegram_frame, textvariable=self.chat_id_var, width=32
        )
        self.chat_id_entry.grid(row=1, column=1, sticky="ew", padx=(5, 0), pady=(6, 0))
        self.chat_id_entry.bind("<FocusOut>", self.save_config)

        tk.Label(self.telegram_frame, text="Countdown (s):").grid(
            row=2, column=0, sticky="w", pady=(6, 0)
        )
        self.countdown_entry = tk.Entry(
            self.telegram_frame, textvariable=self.countdown_seconds_var, width=6
        )
        self.countdown_entry.grid(row=2, column=1, sticky="w", padx=(5, 0), pady=(6, 0))
        self.countdown_entry.bind("<FocusOut>", self.save_config)

        preset_frame = tk.Frame(self.telegram_frame)
        preset_frame.grid(row=2, column=2, sticky="e", padx=(0, 0), pady=(6, 0))
        for seconds in (600, 900):
            tk.Button(
                preset_frame,
                text=f"{seconds // 60}m",
                command=lambda s=seconds: self.set_countdown_preset(s),
            ).pack(side=tk.LEFT, padx=(0, 5))

        self.telegram_frame.grid_columnconfigure(1, weight=1)

    def create_remote_ui(self):
        """Creates the UI elements for Remote Control via WebSocket."""
        self.remote_view = RemoteView(
            self.root,
            status_var=self.remote_status_var,
            ws_port_var=self.ws_port_var,
            http_port_var=self.http_port_var,
            on_start=self.start_remote,
            on_stop=self.stop_remote,
        )
        self.remote_frame = self.remote_view.frame
        self.ws_port_entry = self.remote_view.ws_entry
        self.http_port_entry = self.remote_view.http_entry
        self.remote_status_label = self.remote_view.status_label
        self.remote_log = self.remote_view.log
        self.ws_port_entry.bind("<FocusOut>", self.save_config)
        self.http_port_entry.bind("<FocusOut>", self.save_config)

    def _current_ws_port(self) -> int:
        try:
            return int(self.ws_port_var.get())
        except (TypeError, ValueError):
            return AppConfig().ws_port

    def _start_http_server(self) -> None:
        http_port = self._coerce_positive_int(
            self.http_port_var.get(), AppConfig().http_port
        )
        self.http_port_var.set(str(http_port))
        if self.http_server:
            self.http_server.stop()
        self.http_server = EmbeddedHTTPServer(self._current_ws_port, http_port)
        self.http_server.start()

    def _stop_http_server(self) -> None:
        if self.http_server:
            self.http_server.stop()
            self.http_server = None

    def _handle_ws_port_rebind(self, port: int) -> None:
        self.ws_port_var.set(str(port))
        self.save_config()
        self._start_http_server()

    def log_remote(self, message: str) -> None:
        widget = getattr(self, "remote_log", None)
        if not widget:
            return
        widget.configure(state=tk.NORMAL)
        widget.insert(tk.END, f"{message}\n")
        widget.see(tk.END)
        widget.configure(state=tk.DISABLED)

    def start_remote(self) -> None:
        if self.remote_server:
            return
        port_name = self.selected_port.get()
        if not port_name or "No COM" in port_name:
            messagebox.showerror(
                "Remote Control",
                "Select a Pico DATA port before starting remote control.",
            )
            return
        ws_port = self._coerce_positive_int(self.ws_port_var.get(), AppConfig().ws_port)
        self.ws_port_var.set(str(ws_port))
        callbacks = RemoteCallbacks(
            schedule=lambda func: self.root.after(0, func),
            log=self.log_remote,
            set_status=lambda message: self.remote_status_var.set(message),
            set_ws_port=self._handle_ws_port_rebind,
            start_macro=self.start_macro,
            stop_macro=self.stop_macro,
        )
        self.remote_server = RemoteControlServer(port_name, ws_port, callbacks)
        self.remote_status_var.set("Remote: Starting...")
        self.remote_server.start()
        if not self.remote_server.serial_manager.is_open:
            self.remote_view.set_running(False)
            self.remote_status_var.set("Remote: Serial error")
            self.remote_server = None
            return
        self._start_http_server()
        self.remote_view.set_running(True)

    def stop_remote(self) -> None:
        if self.remote_server:
            self.remote_server.stop()
            self.remote_server = None
        self._stop_http_server()
        self.remote_status_var.set("Remote: Stopped")
        self.remote_view.set_running(False)

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

    def load_config(self) -> None:
        """Reload configuration from disk and apply to the UI."""
        self.config = load_app_config()
        self._apply_config_to_ui()
        logging.info("Configuration loaded.")

    def save_config(self, event=None) -> None:
        """Persist the current UI state to the config file."""
        _ = event
        self._capture_config_from_ui()
        save_app_config(self.config)
        logging.info("Configuration saved.")

    def _apply_config_to_ui(self) -> None:
        cfg = self.config
        self.selected_window.set(cfg.last_window or "")
        folder = cfg.last_folder or "No folder selected."
        self.macro_folder_path.set(folder)
        self.pin_var.set(cfg.always_on_top)
        try:
            self.root.attributes("-topmost", cfg.always_on_top)
        except Exception:
            pass
        self.bot_token_var.set(cfg.bot_token)
        self.chat_id_var.set(cfg.chat_id)
        self.countdown_seconds_var.set(str(cfg.countdown_seconds))
        self.ws_port_var.set(str(cfg.ws_port))
        self.http_port_var.set(str(cfg.http_port))
        self.telegram.bot_token = cfg.bot_token
        self.telegram.chat_id = cfg.chat_id

    def _capture_config_from_ui(self) -> None:
        cfg = self.config
        cfg.last_window = self.selected_window.get()
        folder = self.macro_folder_path.get() or "No folder selected."
        cfg.last_folder = folder
        cfg.always_on_top = bool(self.pin_var.get())
        cfg.bot_token = self.bot_token_var.get()
        cfg.chat_id = self.chat_id_var.get()
        cfg.countdown_seconds = self._coerce_positive_int(
            self.countdown_seconds_var.get(), AppConfig().countdown_seconds
        )
        cfg.ws_port = self._coerce_int(self.ws_port_var.get(), AppConfig().ws_port)
        cfg.http_port = self._coerce_int(
            self.http_port_var.get(), AppConfig().http_port
        )
        self.telegram.bot_token = cfg.bot_token
        self.telegram.chat_id = cfg.chat_id

    @staticmethod
    def _coerce_int(value: str, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _coerce_positive_int(cls, value: str, default: int, minimum: int = 1) -> int:
        parsed = cls._coerce_int(value, default)
        return parsed if parsed >= minimum else default

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
        target_window = self.selected_window.get()
        if target_window and "No windows" not in target_window:
            if not self.context.window_service.activate(target_window):
                logging.error("Could not activate window: %s", target_window)

        if self.countdown_service.is_running:
            self.countdown_service.stop()
            self.countdown_status_var.set("Countdown: Idle")
            if not self.is_playing:
                self.start_button.config(text="START", state=tk.NORMAL)

        countdown_enabled = (
            self.countdown_seconds_var.get().isdigit()
            and int(self.countdown_seconds_var.get()) > 0
        )

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
                        "Error",
                        "No '.txt' macro files found in the folder.",
                    )
                    return
            except Exception as e:
                messagebox.showerror(
                    "Folder Error",
                    f"Could not read macro folder.\nError: {e}",
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

            if countdown_enabled:
                self.start_countdown_internal()

    def stop_macro(self):
        """Programmatically stop the macro loop and cancel the countdown."""
        if self.is_playing:
            self.is_playing = False
            try:
                self.status_text.set("Status: Stopping...")
            except Exception:
                pass
        if self.countdown_service.is_running:
            self.countdown_service.stop()
            self.countdown_status_var.set("Countdown: Idle")

    def on_macro_thread_exit(self):
        """Safely updates GUI elements from the main thread after the macro thread has finished."""
        self.is_playing = False  # Ensure state is final
        self.status_text.set("Status: Stopped. Ready to start.")
        self.start_button.config(state=tk.NORMAL)
        print("GUI updated. Macro has fully stopped.")

    def refresh_ports(self):
        """Refresh the available COM ports using the port service."""
        self._update_ports_async(force_auto=False)

    def auto_select_port_async(self, force: bool) -> None:
        """Trigger an asynchronous auto-selection of the Pico data port."""
        self._update_ports_async(force_auto=force)

    def _update_ports_async(self, *, force_auto: bool) -> None:
        def worker() -> None:
            try:
                selection = self.context.port_service.build_selection(
                    self.selected_port.get(),
                    force_auto=force_auto,
                )
            except Exception as exc:
                logging.error("Port refresh failed: %s", exc)
                return
            self.root.after(0, lambda: self._apply_port_selection(selection))

        threading.Thread(target=worker, name="PortRefresh", daemon=True).start()

    def _apply_port_selection(self, selection: PortSelection) -> None:
        ports = list(selection.ports)
        display_ports = ports if ports else ["No COM ports found"]
        self.port_view.set_ports(display_ports)

        previous = self.selected_port.get()
        new_value = previous

        if selection.selected:
            new_value = selection.selected
        elif previous in ports:
            new_value = previous
        elif ports:
            new_value = ports[0]
        else:
            new_value = "No COM ports found"

        self.selected_port.set(new_value)

        if selection.auto_selected and selection.selected:
            logging.info("Auto-selected Pico DATA port %s", selection.selected)

        if selection.selected and not self.remote_server:
            self.start_remote()

    def refresh_windows(self):
        """Refresh the list of available windows using the window service."""
        selection = self.context.window_service.build_selection(
            self.selected_window.get()
        )
        self._apply_window_selection(selection)

    def _apply_window_selection(self, selection: WindowSelection) -> None:
        titles = list(selection.titles)
        display_titles = titles if titles else ["No windows found"]
        self.window_menu["values"] = display_titles

        previous = self.selected_window.get()
        new_value = selection.selected or (
            previous
            if previous in titles
            else (titles[0] if titles else "No windows found")
        )
        self.selected_window.set(new_value)

    def select_macro_folder(self):
        """Opens a dialog for the user to select a macro folder and saves the selection."""
        folderpath = filedialog.askdirectory(title="Select Folder Containing Macros")
        if folderpath:
            self.macro_folder_path.set(folderpath)
            self.save_config()


def create_application(
    *,
    root: tk.Tk | None = None,
    config: AppConfig | None = None,
    context: AppContext | None = None,
) -> MacroControllerApp:
    """Construct the PicoBot GUI without entering the Tk main loop."""

    root = root or tk.Tk()
    cfg = config or load_app_config()
    return MacroControllerApp(root, context=context, config=cfg)


def main() -> None:
    """Launch the PicoBot GUI application."""

    configure_logging()
    app = create_application()
    app.root.mainloop()


if __name__ == "__main__":
    main()
