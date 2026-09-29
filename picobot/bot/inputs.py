"""HID input sink for the smart bot.

Wraps PicoBot's acknowledged serial path (``SerialManager.send_payload`` or
``RemoteControlServer.enqueue_hid_payload`` — same call shape) and emits the
``hid|...`` command lines understood by the CircuitPython firmware.

Unlike the firmware in simpler bots, this firmware relays raw down/up
events, so key-hold duration is controlled *here* — see ``timing.py``.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Optional, Set

from .timing import human_hold, key_gap

logger = logging.getLogger(__name__)

Sender = Callable[[str], bool]
"""Callable taking a full command payload (no trailing newline) → sent OK."""


class HidController:
    """Sends keyboard/mouse commands to the Pico with held-key tracking.

    ``send`` is a payload→bool callable; use :meth:`from_serial_manager` or
    :meth:`from_remote_server` to adapt the existing transports. The
    ``sleep`` parameter is injectable so ``press`` stays interruptible when
    driven by ``BotBase.sleep``.
    """

    def __init__(
        self,
        send: Sender,
        *,
        sleep: Callable[[float], object] = time.sleep,
        gap: Optional[Callable[[], float]] = key_gap,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._send = send
        self._sleep = sleep
        self._held_keys: Set[str] = set()
        # Fingers never land at once: consecutive key events are spaced
        # by at least a drawn human gap. Time already spent (a deliberate
        # sleep, the serial round-trip) counts toward it, so timed
        # sequences like the flash re-press barely shift.
        self._gap = gap
        self._clock = clock
        self._last_event: Optional[float] = None

    def _space(self) -> None:
        if self._gap is not None and self._last_event is not None:
            wait = self._gap() - (self._clock() - self._last_event)
            if wait > 0:
                self._sleep(wait)
        self._last_event = self._clock()

    # -- Transport adapters ----------------------------------------------------
    @classmethod
    def from_serial_manager(cls, manager, **kwargs) -> "HidController":
        """Adapt ``SerialManager.send_payload(payload, wait_ack, timeout)``."""
        def send(payload: str) -> bool:
            try:
                return bool(
                    manager.send_payload(payload, wait_ack=True, timeout=1.5)
                )
            except Exception as exc:
                logger.error("HID send failed: %s", exc)
                return False

        return cls(send, **kwargs)

    @classmethod
    def from_remote_server(cls, server, **kwargs) -> "HidController":
        """Adapt ``RemoteControlServer.enqueue_hid_payload(...)``."""
        def send(payload: str) -> bool:
            try:
                return bool(
                    server.enqueue_hid_payload(payload, wait_ack=True, timeout=1.5)
                )
            except Exception as exc:
                logger.error("HID send failed: %s", exc)
                return False

        return cls(send, **kwargs)

    # -- Keyboard ---------------------------------------------------------------
    def key_down(self, key: str) -> bool:
        self._space()
        # Tracked even when unconfirmed: a press that timed out may still
        # land, and release_all must let it go too.
        self._held_keys.add(key)
        return bool(self._send(f"hid|key|down|{key}"))

    def key_up(self, key: str) -> bool:
        self._space()
        ok = self._send(f"hid|key|up|{key}")
        self._held_keys.discard(key)
        return ok

    def press(self, key: str, hold: Optional[float] = None) -> bool:
        """Tap ``key``; hold duration defaults to a human-like value."""
        if not self.key_down(key):
            self.key_up(key)
            return False
        self._sleep(hold if hold is not None else human_hold(key))
        return self.key_up(key)

    # -- Mouse ------------------------------------------------------------------
    def move(self, dx: int, dy: int) -> bool:
        return self._send(f"hid|move|{int(dx)}|{int(dy)}")

    def click(self, button: str = "left") -> bool:
        if not self._send(f"hid|mouse|down|{button}"):
            return False
        self._sleep(human_hold())
        return self._send(f"hid|mouse|up|{button}")

    def scroll(self, dy: int) -> bool:
        return self._send(f"hid|scroll|0|{int(dy)}")

    # -- Safety -------------------------------------------------------------------
    @property
    def held_keys(self) -> frozenset:
        return frozenset(self._held_keys)

    def release_all(self) -> None:
        """Release every key this controller believes is held.

        Idempotent; always clears local tracking even if a send fails.
        """
        for key in list(self._held_keys):
            try:
                self._space()
                self._send(f"hid|key|up|{key}")
            except Exception as exc:
                logger.error("Failed to release key %r: %s", key, exc)
        self._held_keys.clear()


__all__ = ["HidController", "Sender"]
