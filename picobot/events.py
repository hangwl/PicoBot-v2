"""Structured event bus for PicoBot.

Everything the dashboard shows — detection, FSM transitions, navigation,
HID commands, safety pauses — flows through one bus. Subscribers get
``{"t": epoch, "kind": str, "msg": str, "data": dict|None}`` dicts; a
short history is retained so late-joining clients see recent context.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Callable, Deque, Dict, Optional

logger = logging.getLogger(__name__)

Event = Dict
Subscriber = Callable[[Event], None]


class EventBus:
    def __init__(self, history: int = 300) -> None:
        self._subscribers: list[Subscriber] = []
        self._lock = threading.Lock()
        self._history: Deque[Event] = deque(maxlen=history)

    def emit(self, kind: str, msg: str, data: Optional[dict] = None) -> Event:
        event: Event = {"t": round(time.time(), 3), "kind": kind, "msg": msg}
        if data:
            event["data"] = data
        with self._lock:
            self._history.append(event)
            subscribers = list(self._subscribers)
        for sub in subscribers:
            try:
                sub(event)
            except Exception:
                logger.debug("event subscriber failed", exc_info=True)
        return event

    def subscribe(self, fn: Subscriber) -> None:
        with self._lock:
            self._subscribers.append(fn)

    def unsubscribe(self, fn: Subscriber) -> None:
        with self._lock:
            try:
                self._subscribers.remove(fn)
            except ValueError:
                pass

    def history(self) -> list[Event]:
        with self._lock:
            return list(self._history)


__all__ = ["EventBus"]
