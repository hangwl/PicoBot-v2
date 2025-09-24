"""Serial transport helpers for PicoBot."""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Iterable, Optional

import serial
import serial.tools.list_ports

SerialLineHandler = Callable[[str], None]


class SerialTransportError(Exception):
    """Raised when a serial transport operation fails."""


@dataclass
class _SerialCommand:
    payload: bytes
    wait_ack: bool
    timeout: float
    completion: threading.Event = field(default_factory=threading.Event)
    ack_event: Optional[threading.Event] = None
    success: bool = False
    error: Optional[Exception] = None


class SerialSession:
    """Manage a serial connection with background reader/writer helpers."""

    def __init__(
        self,
        serial_obj: serial.Serial,
        *,
        line_handler: Optional[SerialLineHandler] = None,
    ) -> None:
        self._serial = serial_obj
        self._serial.timeout = getattr(self._serial, "timeout", 0.2) or 0.2
        self._line_handler = line_handler
        self._cmd_queue: "queue.Queue[Optional[_SerialCommand]]" = queue.Queue()
        self._ack_lock = threading.Lock()
        self._ack_waiters: Deque[threading.Event] = deque()
        self._write_lock = threading.Lock()
        self._ready_event = threading.Event()
        self._running = True
        self._last_ready_time = 0.0
        self._last_handshake = 0.0

        self._reader_thread = threading.Thread(
            target=self._reader_loop, name="SerialSessionReader", daemon=True
        )
        self._writer_thread = threading.Thread(
            target=self._writer_loop, name="SerialSessionWriter", daemon=True
        )
        self._reader_thread.start()
        self._writer_thread.start()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def close(self) -> None:
        """Stop background helpers and close the underlying serial port."""

        if not self._running:
            try:
                if self._serial and self._serial.is_open:
                    self._serial.close()
            except Exception:
                pass
            return

        self._running = False
        try:
            self._cmd_queue.put_nowait(None)
        except Exception:
            pass

        for thread in (self._writer_thread, self._reader_thread):
            if thread and thread.is_alive():
                thread.join(timeout=1.0)

        try:
            if self._serial and self._serial.is_open:
                self._serial.close()
        except Exception:
            pass

    @property
    def last_ready_time(self) -> float:
        """Return the timestamp of the last observed ``PICO_READY`` line."""

        return self._last_ready_time

    def wait_for_ready(self, timeout: float = 12.0) -> bool:
        """Block until a ``PICO_READY`` message is observed."""

        if (time.time() - self._last_ready_time) < 1.0:
            return True

        self._ready_event.clear()
        self.send_handshake()
        deadline = time.time() + timeout
        while time.time() < deadline:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            if self._ready_event.wait(timeout=min(remaining, 0.25)):
                self.finalize_handshake()
                return True
        return False

    def send_handshake(self) -> None:
        """Send the handshake payload if we haven't sent one very recently."""

        now = time.time()
        if (now - self._last_handshake) < 0.5:
            return
        self._last_handshake = now
        # Fire-and-forget; ignore success to avoid recursive waits.
        try:
            self.send_text("hello|handshake", wait_ack=False)
        except Exception:
            logging.debug("Failed to dispatch handshake payload", exc_info=True)

    def finalize_handshake(self) -> None:
        """Send a final HELLO and drain residual READY chatter."""

        self._ready_event.clear()
        try:
            self.send_text("hello|handshake", wait_ack=False)
        except Exception:
            return

        end = time.time() + 0.8
        while time.time() < end:
            if self._ready_event.wait(timeout=0.1):
                break
        try:
            self._serial.reset_input_buffer()
        except Exception:
            pass

    def send_text(
        self, payload: str, *, wait_ack: bool = True, timeout: float = 1.5
    ) -> bool:
        """Send a text command to Pico, optionally waiting for an ACK."""

        if not payload:
            return False
        text = payload if payload.endswith("\n") else payload + "\n"
        cmd = _SerialCommand(text.encode("utf-8"), wait_ack, timeout)
        try:
            self._cmd_queue.put(cmd, timeout=timeout + 0.5)
        except Exception:
            return False
        completed = cmd.completion.wait(timeout=timeout + 1.0)
        if not completed:
            return False
        if cmd.error:
            logging.debug("Serial write failed", exc_info=cmd.error)
        return cmd.success

    def send_hid(
        self,
        event_type: str,
        key: str,
        *,
        wait_ack: bool = True,
        timeout: float = 1.5,
    ) -> bool:
        """Convenience wrapper for legacy keyboard-style HID commands."""

        return self.send_text(f"{event_type}|{key}", wait_ack=wait_ack, timeout=timeout)

    # ------------------------------------------------------------------
    # Background helpers
    # ------------------------------------------------------------------
    def _reader_loop(self) -> None:
        while self._running:
            try:
                line = (
                    self._serial.readline().decode("utf-8", errors="ignore").strip()
                )
            except Exception:
                break

            if not line:
                continue

            if line == "ACK":
                with self._ack_lock:
                    if self._ack_waiters:
                        ev = self._ack_waiters.popleft()
                        try:
                            ev.set()
                        except Exception:
                            pass
            elif line == "PICO_READY":
                self._last_ready_time = time.time()
                self._ready_event.set()

            if self._line_handler:
                try:
                    self._line_handler(line)
                except Exception:
                    logging.debug("Serial line handler raised", exc_info=True)

        self._running = False

    def _writer_loop(self) -> None:
        while self._running:
            try:
                cmd = self._cmd_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            if cmd is None:
                break

            try:
                if cmd.wait_ack:
                    cmd.ack_event = threading.Event()
                    with self._ack_lock:
                        self._ack_waiters.append(cmd.ack_event)

                with self._write_lock:
                    self._serial.write(cmd.payload)
                    self._serial.flush()

                if not cmd.wait_ack or cmd.ack_event is None:
                    cmd.success = True
                elif cmd.ack_event.wait(cmd.timeout):
                    cmd.success = True
                else:
                    cmd.success = False
                    with self._ack_lock:
                        try:
                            self._ack_waiters.remove(cmd.ack_event)
                        except ValueError:
                            pass
                cmd.completion.set()
            except Exception as exc:
                cmd.error = exc
                cmd.success = False
                cmd.completion.set()

        # Drain any remaining commands to unblock waiters
        while True:
            try:
                cmd = self._cmd_queue.get_nowait()
            except queue.Empty:
                break
            if cmd is None:
                continue
            cmd.success = False
            cmd.completion.set()


class SerialManager:
    """Utility helpers for locating Pico serial ports and opening sessions."""

    def __init__(self, baudrate: int = 115200) -> None:
        self._baudrate = baudrate

    # ------------------------------------------------------------------
    # Discovery helpers
    # ------------------------------------------------------------------
    def list_ports(self) -> Iterable[serial.tools.list_ports.ListPortInfo]:
        return serial.tools.list_ports.comports()

    def quick_guess_data_port(self) -> Optional[str]:
        try:
            for info in self.list_ports():
                loc = getattr(info, "location", "") or ""
                if loc.endswith("x.2"):
                    return info.device
        except Exception:
            pass
        return None

    def discover_data_port(self, exclude_port: Optional[str] = None) -> Optional[str]:
        for info in self.list_ports():
            port = info.device
            if exclude_port and port == exclude_port:
                continue
            try:
                ser = serial.Serial(
                    port, self._baudrate, timeout=0.5, write_timeout=0.5
                )
            except Exception:
                continue
            try:
                self._prepare_serial(ser)
                if self._probe_for_ready(ser):
                    return port
            finally:
                try:
                    ser.close()
                except Exception:
                    pass
        return None

    # ------------------------------------------------------------------
    # Session helpers
    # ------------------------------------------------------------------
    def open_session(
        self,
        port: str,
        *,
        line_handler: Optional[SerialLineHandler] = None,
        timeout: float = 0.5,
        write_timeout: float = 0.5,
    ) -> SerialSession:
        try:
            ser = serial.Serial(
                port, self._baudrate, timeout=timeout, write_timeout=write_timeout
            )
        except Exception as exc:
            raise SerialTransportError(
                f"Failed to open serial port {port}: {exc}"
            ) from exc

        self._prepare_serial(ser)
        return SerialSession(ser, line_handler=line_handler)

    def normalize_payload(self, payload: str) -> Optional[str]:
        text = (payload or "").strip()
        if not text:
            return None
        if text.startswith("hid|"):
            return text
        parts = text.split("|")
        if len(parts) >= 3 and parts[0] in {"key", "mouse", "scroll"}:
            return "hid|" + text
        if len(parts) == 2 and parts[0] in {"down", "up"}:
            return text
        return text

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _prepare_serial(self, ser: serial.Serial) -> None:
        try:
            ser.dtr = False
            time.sleep(0.05)
            ser.dtr = True
            ser.rts = False
        except Exception:
            pass
        time.sleep(0.1)

    def _probe_for_ready(self, ser: serial.Serial) -> bool:
        got_ready = False
        found_console = False

        start = time.time()
        while time.time() - start < 1.0:
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
                ser.write(b"hello|handshake\n")
                ser.flush()
            except Exception:
                pass
            start = time.time()
            while time.time() - start < 1.5:
                try:
                    line = ser.readline().decode("utf-8", errors="ignore").strip()
                except Exception:
                    break
                if line == "PICO_READY":
                    got_ready = True
                    break
                if line:
                    lower = line.lower()
                    if (
                        ("circuitpython" in lower)
                        or ("repl" in lower)
                        or lower.startswith(">>>")
                    ):
                        found_console = True
                        break

        return bool(got_ready and not found_console)


__all__ = ["SerialManager", "SerialSession", "SerialTransportError"]

