"""Pause state: idle safely until the map looks clean again."""

from __future__ import annotations

from .base import POP, States, safety_transition


class Pause(States):
    """Do nothing while an unsafe condition (focus loss, rune, players,
    verification prompt) persists; ``POP`` back to the interrupted state
    once it clears."""

    def enter(self) -> None:
        self.bot.hid.release_all()
        self.bot.log("PAUSE: holding until safe")

    def check_status(self):
        # While any unsafe condition persists, stay paused. safety_transition
        # returns "PAUSE" while unsafe and None when clear.
        if safety_transition(self.bot, notify=False) is not None:
            return None
        if not self.bot.is_window_focused():
            return None
        return POP

    def execute(self) -> None:
        self.bot.sleep(1)

    def exit(self) -> None:
        self.bot.hid.release_all()


__all__ = ["Pause"]
