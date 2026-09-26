"""Wander state: bounded random walk, then back to the anchor point."""

from __future__ import annotations

from .base import States, safety_transition


class Wander(States):
    """Walk randomly for ``wander_seconds``, then return to the origin."""

    def __init__(self, bot) -> None:
        super().__init__(bot)
        self._has_run = False

    def enter(self) -> None:
        self.bot.log("WANDER: roaming")
        self._has_run = False

    def check_status(self):
        transition = safety_transition(self.bot)
        if transition is not None:
            return transition
        if not self.bot.config.enable_random_wander:
            return "GRIND"
        if self._has_run:
            return "GRIND"
        return None

    def execute(self) -> None:
        self.bot.random_wander()
        self._has_run = True

    def exit(self) -> None:
        self.bot.hid.release_all()


__all__ = ["Wander"]
