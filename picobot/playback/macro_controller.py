"""Macro playback controller and helpers for PicoBot."""

from __future__ import annotations

import logging
import os
import random
import time
from typing import Callable, List, Optional

import serial

from ..services.system import PortSelection, PortService, WindowSelection, WindowService
from ..transport import finalize_handshake, wait_for_ack

MacroEvent = dict[str, object]
PlaylistBuilder = Callable[[str], List[str]]
MacroParser = Callable[[str], Optional[List[MacroEvent]]]


def parse_macro_file(filename: str) -> Optional[List[MacroEvent]]:
    """Parse a macro text file into timestamped HID events."""
    events: List[MacroEvent] = []
    try:
        with open(filename, "r", encoding="utf-8") as handle:
            for line in handle:
                parts = line.strip().split()
                if len(parts) != 3:
                    continue
                timestamp, event_type, key = parts
                events.append(
                    {
                        "time": float(timestamp),
                        "type": event_type,
                        "key": key,
                    }
                )
    except Exception as exc:
        logging.warning("Could not parse macro file '%s': %s", filename, exc)
        return None
    return events


def build_playlist(macro_folder: str) -> List[str]:
    """Return a randomized playlist, prioritising files prefixed with ``START_``."""
    try:
        macro_files = [
            name for name in os.listdir(macro_folder) if name.endswith(".txt")
        ]
    except FileNotFoundError as exc:
        raise FileNotFoundError("Macro folder not found") from exc
    except Exception as exc:
        raise RuntimeError(f"Error reading macro folder: {exc}") from exc

    if not macro_files:
        raise FileNotFoundError("No '.txt' files found in macro folder")

    start_files = [name for name in macro_files if name.startswith("START_")]
    other_files = [name for name in macro_files if not name.startswith("START_")]

    random.shuffle(start_files)
    random.shuffle(other_files)
    return start_files + other_files


class MacroController:
    """Coordinates macro playback, serial communication, and window focus."""

    def __init__(
        self,
        app,
        *,
        playlist_builder: PlaylistBuilder = build_playlist,
        parser: MacroParser = parse_macro_file,
        port_service: Optional[PortService] = None,
        window_service: Optional[WindowService] = None,
    ) -> None:
        self.app = app
        self._playlist_builder = playlist_builder
        self._parser = parser
        self._port_service = port_service or PortService()
        self._window_service = window_service or WindowService()

    # -- UI coordination helpers -------------------------------------------------
    def build_port_selection(
        self,
        current: Optional[str],
        *,
        force_auto: bool = False,
    ) -> PortSelection:
        """Return the port snapshot the UI should render."""

        return self._port_service.build_selection(current, force_auto=force_auto)

    def build_window_selection(self, current: Optional[str]) -> WindowSelection:
        """Return the window snapshot the UI should render."""

        return self._window_service.build_selection(current)

    # -- Transport helpers -------------------------------------------------------
    def find_data_port(self, exclude_port: Optional[str] = None) -> Optional[str]:
        return self._port_service.discover_data_port(exclude_port=exclude_port)

    def _finalize_handshake(self, ser: serial.Serial) -> None:
        finalize_handshake(ser)

    def _wait_for_ack(self, ser: serial.Serial, timeout: float = 1.5) -> bool:
        return wait_for_ack(ser, timeout=timeout)

    # -- Playback helpers --------------------------------------------------------
    def interruptible_sleep(self, duration: float) -> bool:
        end_time = time.time() + duration
        while time.time() < end_time:
            if not self.app.is_playing:
                return False
            time.sleep(0.01)
        return True

    def parse_macro_file(self, filename: str) -> Optional[List[MacroEvent]]:
        return self._parser(filename)

    def build_playlist(self, macro_folder: str) -> List[str]:
        return self._playlist_builder(macro_folder)

    # -- Core playback loop ------------------------------------------------------
    def play_macro_thread(
        self, port: str, window_title: str, macro_folder: str
    ) -> None:
        self.app.is_playing = True
        self.app.status_text.set("Status: Playing...")
        self.app.keys_currently_down.clear()

        if not self._window_service.activate(window_title):
            logging.error(
                "Target window '%s' not found or could not be activated.", window_title
            )
            self.app.is_playing = False
            self.app.root.after(0, self.app.on_macro_thread_exit)
            return

        time.sleep(1)
        remote = self.app.remote_server
        use_remote = remote is not None

        while self.app.is_playing:
            try:
                current_playlist = self.build_playlist(macro_folder)
                logging.info("New randomized playlist created: %s", current_playlist)
            except FileNotFoundError as exc:
                logging.error("%s Stopping loop.", exc)
                break
            except Exception as exc:
                logging.error("Error creating playlist: %s. Stopping loop.", exc)
                break

            for chosen_macro_name in current_playlist:
                if not self.app.is_playing:
                    break

                logging.info("Playing from sequence: %s", chosen_macro_name)
                macro_file_path = os.path.join(macro_folder, chosen_macro_name)

                events = self.parse_macro_file(macro_file_path)
                if events is None:
                    continue

                ser: Optional[serial.Serial] = None
                try:
                    if use_remote:
                        logging.info("Waiting for PICO_READY via Remote server...")
                        if not remote.wait_for_ready(timeout=12.0):
                            logging.error("Timed out waiting for PICO_READY (Remote)")
                            self.app.is_playing = False
                            continue
                    else:
                        ser = serial.Serial(port, 115200, timeout=5)
                        try:
                            ser.dtr = False
                            time.sleep(0.05)
                            ser.dtr = True
                            ser.rts = False
                        except Exception:
                            pass
                        time.sleep(0.1)
                        logging.info("Waiting for PICO_READY signal...")
                        ready_signal_received = False
                        start_time = time.time()
                        hello_sent = False
                        try:
                            ser.timeout = 0.2
                        except Exception:
                            pass
                        while time.time() - start_time < 12:
                            line = ser.readline().decode("utf-8").strip()
                            if line == "PICO_READY":
                                logging.info(
                                    "PICO_READY signal received. Starting macro."
                                )
                                self._finalize_handshake(ser)
                                ready_signal_received = True
                                break
                            elif line:
                                lower = line.lower()
                                if (
                                    ("circuitpython" in lower)
                                    or ("repl" in lower)
                                    or lower.startswith(">>>")
                                ):
                                    logging.error(
                                        "Detected console CDC port; please select Pico DATA port."
                                    )
                                    self.app.is_playing = False
                                    break
                            if (not hello_sent) and (time.time() - start_time >= 1.0):
                                try:
                                    ser.write(b"hello|handshake\n")
                                    ser.flush()
                                    hello_sent = True
                                except Exception:
                                    pass
                        if not ready_signal_received:
                            auto_port = self.find_data_port(exclude_port=port)
                            if auto_port and auto_port != port:
                                logging.info(
                                    "Auto-detected Pico DATA port %s. Retrying handshake...",
                                    auto_port,
                                )
                                try:
                                    ser.close()
                                except Exception:
                                    pass
                                port = auto_port
                                try:
                                    ser = serial.Serial(port, 115200, timeout=5)
                                    try:
                                        ser.dtr = False
                                        time.sleep(0.05)
                                        ser.dtr = True
                                        ser.rts = False
                                    except Exception:
                                        pass
                                    time.sleep(0.1)
                                    logging.info("Waiting for PICO_READY signal...")
                                    ready_signal_received = False
                                    start_time = time.time()
                                    hello_sent = False
                                    try:
                                        ser.timeout = 0.2
                                    except Exception:
                                        pass
                                    while time.time() - start_time < 12:
                                        line = ser.readline().decode("utf-8").strip()
                                        if line == "PICO_READY":
                                            logging.info(
                                                "PICO_READY signal received. Starting macro."
                                            )
                                            self._finalize_handshake(ser)
                                            ready_signal_received = True
                                            break
                                        elif line:
                                            lower = line.lower()
                                            if (
                                                ("circuitpython" in lower)
                                                or ("repl" in lower)
                                                or lower.startswith(">>>")
                                            ):
                                                logging.error(
                                                    "Detected console CDC port; please select Pico DATA port."
                                                )
                                                self.app.is_playing = False
                                                break
                                        if (not hello_sent) and (
                                            time.time() - start_time >= 1.0
                                        ):
                                            try:
                                                ser.write(b"hello|handshake\n")
                                                ser.flush()
                                                hello_sent = True
                                            except Exception:
                                                pass
                                except Exception:
                                    ready_signal_received = False
                            if not ready_signal_received:
                                logging.error(
                                    "Timed out waiting for PICO_READY signal."
                                )
                                self.app.is_playing = False
                                try:
                                    ser.close()
                                except Exception:
                                    pass
                                continue
                    for index, event in enumerate(events):
                        if not self.app.is_playing:
                            break
                        active_window_title = self._window_service.get_active_title()
                        if active_window_title != window_title:
                            logging.error(
                                "Window focus lost. Expected '%s', got '%s'. Stopping macro.",
                                window_title,
                                active_window_title,
                            )
                            self.app.is_playing = False
                            break
                        if index > 0:
                            delay = event["time"] - events[index - 1]["time"]
                            if not self.interruptible_sleep(delay):
                                break
                        if use_remote:
                            ok = remote.send_hid(
                                event["type"], event["key"], wait_ack=True, timeout=1.5
                            )
                            if not ok:
                                logging.error(
                                    "Expected ACK but got none/other (Remote). Stopping to prevent de-sync."
                                )
                                self.app.is_playing = False
                                break
                        else:
                            command = f"{event['type']}|{event['key']}\n"
                            assert ser is not None
                            ser.write(command.encode("utf-8"))
                            if not self._wait_for_ack(ser, timeout=1.5):
                                logging.error(
                                    "Expected ACK but got none/other. Stopping to prevent de-sync."
                                )
                                self.app.is_playing = False
                                break
                        if event["type"] == "down":
                            self.app.keys_currently_down.add(event["key"])
                        elif event["key"] in self.app.keys_currently_down:
                            self.app.keys_currently_down.discard(event["key"])
                except serial.SerialException as exc:
                    logging.error("Serial Error: %s. Stopping macro.", exc)
                    self.app.is_playing = False
                finally:
                    try:
                        if self.app.keys_currently_down:
                            for key in list(self.app.keys_currently_down):
                                if use_remote:
                                    ok = remote.send_hid(
                                        "up", key, wait_ack=True, timeout=0.8
                                    )
                                    if ok:
                                        logging.info(
                                            "Sent release for %s and received ACK (Remote).",
                                            key,
                                        )
                                    else:
                                        logging.warning(
                                            "Timeout on final release ACK for key '%s' (Remote).",
                                            key,
                                        )
                                else:
                                    command = f"up|{key}\n"
                                    assert ser is not None
                                    ser.write(command.encode("utf-8"))
                                    if self._wait_for_ack(ser, timeout=0.8):
                                        logging.info(
                                            "Sent release for %s and received ACK.", key
                                        )
                                    else:
                                        logging.warning(
                                            "Timeout on final release ACK for key '%s'.",
                                            key,
                                        )
                            self.app.keys_currently_down.clear()
                    except Exception as exc:
                        logging.error("Cleanup error: %s", exc)
                    finally:
                        try:
                            if ser:
                                ser.close()
                        except Exception:
                            pass
        logging.info("Macro thread is finishing.")
        self.app.root.after(0, self.app.on_macro_thread_exit)
