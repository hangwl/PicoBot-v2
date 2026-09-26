"""Base class for perception-driven PicoBot tasks.

Owns the lifecycle bits every bot loop needs: an interruptible stop event,
interruptible sleep, a log sink, and the shared peripherals (HID
controller, game window, screen grabber, minimap analyzer). States and the
SmartBot interact with hardware only through here.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from .config import BotConfig
from .inputs import HidController

logger = logging.getLogger(__name__)

LogCallback = Optional[Callable[[str], None]]
NotifyCallback = Optional[Callable[[str], None]]


class BotBase:
    """Perception + input plumbing shared by bot tasks and states."""

    def __init__(
        self,
        controller: HidController,
        window,
        screen,
        minimap,
        config: BotConfig | None = None,
        *,
        log_callback: LogCallback = None,
        notify_callback: NotifyCallback = None,
        event_bus=None,
    ) -> None:
        self.hid = controller
        self.window = window
        self.screen = screen
        self.minimap = minimap
        self.config = config or BotConfig()
        self._log_callback = log_callback
        self._notify_callback = notify_callback
        if event_bus is None:
            from ..events import EventBus

            event_bus = EventBus()
        self.events = event_bus
        self._stop_event = threading.Event()

    # -- Lifecycle --------------------------------------------------------------
    def stop(self) -> None:
        """Signal all loops to exit at the next checkpoint."""
        self._stop_event.set()

    def cleanup(self) -> None:
        """Release held keys and the screen grabber."""
        try:
            self.hid.release_all()
        finally:
            self.screen.close()

    def should_continue(self) -> bool:
        return not self._stop_event.is_set()

    def sleep(self, duration: float) -> bool:
        """Interruptible sleep. True if woken by stop(), False if elapsed."""
        return self._stop_event.wait(timeout=duration)

    # -- Messaging ---------------------------------------------------------------
    def log(self, message: str) -> None:
        logger.info("%s", message)
        self.events.emit("log", message)
        if self._log_callback:
            try:
                self._log_callback(message)
            except Exception:
                pass

    def event(self, kind: str, message: str, data: Optional[dict] = None) -> None:
        """Emit a structured event without a log line."""
        self.events.emit(kind, message, data)

    def notify(self, message: str) -> None:
        """Push a high-priority alert (e.g. Telegram) for safety events."""
        self.log(message)
        if self._notify_callback:
            try:
                self._notify_callback(message)
            except Exception:
                pass

    # -- Convenience ------------------------------------------------------------
    def is_window_focused(self) -> bool:
        try:
            return bool(self.window.is_active)
        except Exception:
            return False


__all__ = ["BotBase", "LogCallback", "NotifyCallback"]
