"""Countdown timer service used by the GUI."""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from .messaging import TelegramHandler


TickCallback = Callable[[int], None]
StatusCallback = Callable[[str], None]
CompleteCallback = Callable[[bool], None]


class CountdownService:
    """Manage a background countdown and optional Telegram notification."""

    def __init__(self, telegram: Optional[TelegramHandler] = None) -> None:
        self._telegram = telegram
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._is_running = False

    @property
    def is_running(self) -> bool:
        return self._is_running

    def start(
        self,
        seconds: int,
        *,
        on_tick: Optional[TickCallback] = None,
        on_status: Optional[StatusCallback] = None,
        on_complete: Optional[CompleteCallback] = None,
        message: str = "Countdown timer finished!",
    ) -> None:
        if seconds <= 0:
            raise ValueError("Countdown length must be greater than zero")

        with self._lock:
            self.stop()
            self._stop_event.clear()
            self._is_running = True

            def worker() -> None:
                try:
                    for remaining in range(seconds, 0, -1):
                        if self._stop_event.is_set():
                            if on_complete:
                                on_complete(False)
                            return
                        if on_tick:
                            on_tick(remaining)
                        time.sleep(1)
                    if self._stop_event.is_set():
                        if on_complete:
                            on_complete(False)
                        return
                    if on_status:
                        on_status("Countdown: Sending notification...")
                    sent = False
                    if (
                        self._telegram
                        and getattr(self._telegram, "bot_token", "")
                        and getattr(self._telegram, "chat_id", "")
                    ):
                        try:
                            self._telegram.send_message(message)
                            sent = True
                            if on_status:
                                on_status("Countdown: Notification sent")
                        except Exception as exc:  # pragma: no cover - network
                            if on_status:
                                on_status(f"Countdown: Notification failed ({exc})")
                    if not sent and on_status:
                        on_status("Countdown: Completed!")
                    if on_complete:
                        on_complete(True)
                finally:
                    self._stop_event.clear()
                    self._is_running = False

            thread = threading.Thread(target=worker, name="CountdownService", daemon=True)
            self._thread = thread
            thread.start()

    def stop(self) -> None:
        with self._lock:
            if not self._thread or not self._thread.is_alive():
                self._thread = None
                self._stop_event.clear()
                self._is_running = False
                return
            self._stop_event.set()
        # The wait() method should be used to wait for the thread to finish.

    def wait(self, timeout: Optional[float] = None) -> None:
        thread = self._thread
        if thread:
            thread.join(timeout)
