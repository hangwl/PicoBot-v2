"""Serial transport helpers and manager for PicoBot."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Callable, Optional

import serial
import serial.tools.list_ports

LineCallback = Callable[[str], None]

HANDSHAKE_COMMAND = b"hello|handshake\n"
_DEFAULT_BAUDRATE = 115200
_LOGGER = logging.getLogger(__name__)


def _toggle_control_lines(ser: serial.Serial) -> None:
    """Best-effort DTR/RTS toggling to coax Pico firmware into data mode."""
    try:
        ser.dtr = False
        time.sleep(0.05)
        ser.dtr = True
        ser.rts = False
    except Exception:
        _LOGGER.debug("Failed to toggle control lines", exc_info=True)


def discover_data_port(
    exclude_port: Optional[str] = None,
    *,
    baudrate: int = _DEFAULT_BAUDRATE,
    handshake_timeout: float = 1.0,
) -> Optional[str]:
    """Probe available serial ports and return the Pico DATA CDC port if found."""
    candidates = list(serial.tools.list_ports.comports())
    for info in candidates:
        port = getattr(info, "device", None)
        if not port or (exclude_port and port == exclude_port):
            continue
        ser = None
        try:
            ser = serial.Serial(port, baudrate, timeout=0.5, write_timeout=0.5)
        except Exception as exc:
            _LOGGER.debug("Skipping port %s during discovery: %s", port, exc)
            continue
        try:
            _toggle_control_lines(ser)
            time.sleep(0.1)
            got_ready = False
            found_console = False
            deadline = time.time() + handshake_timeout
            while time.time() < deadline:
                try:
                    line = ser.readline().decode("utf-8", errors="ignore").strip()
                except Exception:
                    break
                if not line:
                    continue
                lower = line.lower()
                if (
                    ("circuitpython" in lower)
                    or ("repl" in lower)
                    or lower.startswith(">>>")
                ):
                    found_console = True
                    break
                if line == "PICO_READY":
                    got_ready = True
                    break
            if not got_ready and not found_console:
                try:
                    ser.write(HANDSHAKE_COMMAND)
                    ser.flush()
                except Exception:
                    _LOGGER.debug(
                        "Failed to emit handshake probe on %s", port, exc_info=True
                    )
                deadline = time.time() + handshake_timeout
                while time.time() < deadline:
                    try:
                        line = ser.readline().decode("utf-8", errors="ignore").strip()
                    except Exception:
                        break
                    if not line:
                        continue
                    lower = line.lower()
                    if (
                        ("circuitpython" in lower)
                        or ("repl" in lower)
                        or lower.startswith(">>>")
                    ):
                        found_console = True
                        break
                    if line == "PICO_READY":
                        got_ready = True
                        break
            if got_ready and not found_console:
                _LOGGER.debug("Discovered Pico DATA port on %s", port)
                return port
        finally:
            if ser is not None:
                try:
                    ser.close()
                except Exception:
                    pass
    return None


def finalize_handshake(ser: serial.Serial, *, wait_window: float = 0.8) -> None:
    """Suppress periodic READY messages after the initial handshake."""
    try:
        ser.write(HANDSHAKE_COMMAND)
        ser.flush()
    except Exception:
        _LOGGER.debug("Failed to send handshake finalizer", exc_info=True)
    deadline = time.time() + wait_window
    while time.time() < deadline:
        try:
            line = ser.readline().decode("utf-8", errors="ignore").strip()
        except Exception:
            break
        if not line:
            continue
        if line == "PICO_READY":
            break
    try:
        ser.reset_input_buffer()
    except Exception:
        pass


def wait_for_ack(ser: serial.Serial, timeout: float = 1.5) -> bool:
    """Wait for an ACK line while ignoring blank lines and stray READY messages."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            line = ser.readline().decode("utf-8", errors="ignore").strip()
        except Exception:
            return False
        if not line:
            continue
        if line == "ACK":
            return True
        if line == "PICO_READY":
            continue
    return False


class SerialManager:
    """Owns a persistent serial session with handshake, ACK, and READY tracking."""

    def __init__(
        self,
        port: str,
        *,
        baudrate: int = _DEFAULT_BAUDRATE,
        timeout: float = 0.5,
        write_timeout: float = 0.5,
    ) -> None:
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.write_timeout = write_timeout
        self._serial: Optional[serial.Serial] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._stop_reader = threading.Event()
        self._ack_waiters: deque[threading.Event] = deque()
        self._ack_lock = threading.Lock()
        self._ready_event = threading.Event()
        self._last_ready = 0.0
        self._callbacks: list[LineCallback] = []
        self._callbacks_lock = threading.Lock()
        self._write_lock = threading.Lock()

    @property
    def is_open(self) -> bool:
        return bool(self._serial and self._serial.is_open)

    def open(self) -> serial.Serial:
        if self.is_open:
            return self._serial
        ser = serial.Serial(
            self.port,
            self.baudrate,
            timeout=self.timeout,
            write_timeout=self.write_timeout,
        )
        try:
            _toggle_control_lines(ser)
            try:
                ser.write(HANDSHAKE_COMMAND)
                ser.flush()
            except Exception:
                _LOGGER.debug("Failed to emit handshake during open", exc_info=True)
            self._serial = ser
            self._stop_reader.clear()
            self._reader_thread = threading.Thread(
                target=self._reader_loop,
                name=f"SerialManager[{self.port}]",
                daemon=True,
            )
            self._reader_thread.start()
            return ser
        except Exception:
            try:
                ser.close()
            except Exception:
                pass
            self._serial = None
            raise

    def close(self) -> None:
        self._stop_reader.set()
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=1.0)
        self._reader_thread = None
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass
        self._serial = None
        with self._ack_lock:
            self._ack_waiters.clear()
        self._ready_event.clear()

    def register_line_callback(self, callback: LineCallback) -> None:
        if not callback:
            return
        with self._callbacks_lock:
            self._callbacks.append(callback)

    def unregister_line_callback(self, callback: LineCallback) -> None:
        with self._callbacks_lock:
            try:
                self._callbacks.remove(callback)
            except ValueError:
                pass

    def wait_for_ready(self, timeout: float = 12.0) -> bool:
        if (time.time() - self._last_ready) < 1.0:
            return True
        if self._ready_event.wait(timeout):
            self._ready_event.clear()
            return True
        return False

    def send_payload(
        self, payload: str, *, wait_ack: bool = False, timeout: float = 1.5
    ) -> bool:
        if not payload:
            return False
        if not self.is_open:
            raise RuntimeError("Serial port is not open")
        cmd = payload if payload.endswith("\n") else f"{payload}\n"
        waiter: Optional[threading.Event] = None
        if wait_ack:
            waiter = threading.Event()
            with self._ack_lock:
                self._ack_waiters.append(waiter)
        try:
            with self._write_lock:
                assert self._serial is not None
                self._serial.write(cmd.encode("utf-8"))
                self._serial.flush()
        except Exception:
            if waiter is not None:
                with self._ack_lock:
                    try:
                        self._ack_waiters.remove(waiter)
                    except ValueError:
                        pass
            raise
        if not wait_ack or waiter is None:
            return True
        if waiter.wait(timeout):
            return True
        with self._ack_lock:
            try:
                self._ack_waiters.remove(waiter)
            except ValueError:
                pass
        return False

    def send_hid(
        self,
        event_type: str,
        key: str,
        *,
        wait_ack: bool = True,
        timeout: float = 1.5,
    ) -> bool:
        base = f"{event_type}|{key}" if key else event_type
        return self.send_payload(base, wait_ack=wait_ack, timeout=timeout)

    def _resolve_next_ack(self) -> None:
        with self._ack_lock:
            if self._ack_waiters:
                waiter = self._ack_waiters.popleft()
                try:
                    waiter.set()
                except Exception:
                    pass

    def _reader_loop(self) -> None:
        while not self._stop_reader.is_set():
            ser = self._serial
            if ser is None:
                break
            try:
                raw = ser.readline()
            except Exception:
                break
            if not raw:
                continue
            line = raw.decode("utf-8", errors="ignore").strip()
            if not line:
                continue
            if line == "ACK":
                self._resolve_next_ack()
            elif line == "PICO_READY":
                self._last_ready = time.time()
                self._ready_event.set()
            self._dispatch_line(line)
        self._stop_reader.set()

    def _dispatch_line(self, line: str) -> None:
        with self._callbacks_lock:
            callbacks = list(self._callbacks)
        for callback in callbacks:
            try:
                callback(line)
            except Exception:
                _LOGGER.debug("Serial line callback failed", exc_info=True)
