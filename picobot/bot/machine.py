"""Finite state machine driving the smart bot.

States communicate transitions by returning string keys (mapped through
``state_mapping``) so they never import each other. ``PAUSE`` and other
interrupt-style transitions push the current state onto ``self.stack``;
``POP`` resumes it.
"""

from __future__ import annotations

import logging

from .states.base import POP
from .states.grind import Grind
from .states.pause import Pause
from .states.travel import Travel

logger = logging.getLogger(__name__)


class Machine:
    state_mapping = {
        "GRIND": Grind,
        "TRAVEL": Travel,
        "PAUSE": Pause,
    }

    def __init__(self, bot) -> None:
        self.bot = bot
        self.current_state = None
        self.stack = []

    def switch(self) -> bool:
        """Run one transition check. Returns True if the state changed."""
        if not self.bot.should_continue():
            if self.current_state is not None:
                self.current_state.exit()
            return False

        result = self.current_state.check_status()

        if result is None:
            return False

        if result is POP:
            if self.stack:
                self.current_state.exit()
                self.current_state = self.stack.pop()
                self.current_state.enter()
                return True
            logger.warning("POP requested with empty state stack")
            return False

        state_cls = self.state_mapping.get(result)
        if state_cls is None:
            logger.warning("Unknown state key %r", result)
            return False

        previous = self.current_state
        if result == "PAUSE":
            self.stack.append(previous)
        previous.exit()
        self.current_state = state_cls(self.bot)
        self.current_state.enter()
        self._notify_state(result)
        return True

    def run(self) -> None:
        """Start in GRIND and loop until the bot is stopped."""
        self.current_state = Grind(self.bot)
        self.current_state.enter()
        self._notify_state("GRIND")
        try:
            while self.bot.should_continue():
                if not self.switch() and self.bot.should_continue():
                    self.current_state.execute()
        finally:
            if self.current_state is not None:
                self.current_state.exit()
            self._notify_state("STOPPED")

    def _notify_state(self, name: str) -> None:
        """Tell the bot which state we're in (for dashboards); optional."""
        hook = getattr(self.bot, "_viz_state", None)
        if callable(hook):
            try:
                hook(name)
            except Exception:
                pass


__all__ = ["Machine"]
