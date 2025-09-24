"""Reusable Tkinter view components."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable


class PortSelectorView:
    """Encapsulate the Pico port selection widgets."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        selected_port: tk.StringVar,
        on_refresh: Callable[[], None],
        on_auto_select: Callable[[], None],
    ) -> None:
        self.frame = tk.LabelFrame(
            master, text="1. Select Pico COM Port", padx=10, pady=10
        )
        self.frame.pack(padx=10, pady=10, fill="x")

        self.combobox = ttk.Combobox(
            self.frame, textvariable=selected_port, state="readonly"
        )
        self.combobox.pack(side=tk.LEFT, fill="x", expand=True)

        self.refresh_button = tk.Button(
            self.frame, text="Refresh", command=on_refresh
        )
        self.refresh_button.pack(side=tk.LEFT, padx=(10, 0))

        self.auto_button = tk.Button(
            self.frame,
            text="Auto Detect",
            command=on_auto_select,
        )
        self.auto_button.pack(side=tk.LEFT, padx=(5, 0))

    def set_ports(self, ports: list[str]) -> None:
        self.combobox["values"] = ports


class RemoteView:
    """Render the remote server controls and log."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        status_var: tk.StringVar,
        ws_port_var: tk.StringVar,
        http_port_var: tk.StringVar,
        on_start: Callable[[], None],
        on_stop: Callable[[], None],
    ) -> None:
        from tkinter.scrolledtext import ScrolledText

        self.frame = tk.LabelFrame(
            master, text="5. Remote Control (WebSocket)", padx=10, pady=10
        )
        self.frame.pack(padx=10, pady=10, fill="both", expand=True)

        tk.Label(self.frame, text="WS Port:").grid(row=0, column=0, sticky="w")
        self.ws_entry = tk.Entry(self.frame, textvariable=ws_port_var, width=8)
        self.ws_entry.grid(row=0, column=1, sticky="w")

        tk.Label(self.frame, text="HTTP Port:").grid(
            row=0, column=2, sticky="w", padx=(10, 0)
        )
        self.http_entry = tk.Entry(self.frame, textvariable=http_port_var, width=8)
        self.http_entry.grid(row=0, column=3, sticky="w")

        self.start_button = tk.Button(
            self.frame, text="Start Remote", command=on_start, bg="#1976D2", fg="white"
        )
        self.start_button.grid(row=0, column=4, padx=(10, 0))

        self.stop_button = tk.Button(
            self.frame, text="Stop Remote", command=on_stop, state=tk.DISABLED
        )
        self.stop_button.grid(row=0, column=5, padx=(5, 0))

        self.status_label = tk.Label(self.frame, textvariable=status_var, anchor="w")
        self.status_label.grid(row=1, column=0, columnspan=6, sticky="ew", pady=(8, 0))

        self.log = ScrolledText(self.frame, height=10, wrap="word", state=tk.DISABLED)
        self.log.grid(row=2, column=0, columnspan=6, sticky="nsew", pady=(8, 0))

        self.frame.grid_columnconfigure(5, weight=1)
        self.frame.grid_rowconfigure(2, weight=1)

    def set_buttons_state(self, *, start_state: str, stop_state: str) -> None:
        self.start_button.config(state=start_state)
        self.stop_button.config(state=stop_state)
