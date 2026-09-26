"""Stationary grinding state: attack in place for a while."""

from __future__ import annotations

import time

from .base import States, safety_transition


class Grind(States):
    """Stand still and cycle attacks/buffs until it's time to wander."""

    def __init__(self, bot) -> None:
        super().__init__(bot)
        self._endtime = None

    def enter(self) -> None:
        self._endtime = time.time() + self.bot.config.stationary_seconds
        self.bot.log("GRIND: attacking in place")

    def check_status(self):
        transition = safety_transition(self.bot)
        if transition is not None:
            return transition
        if not self.bot.config.stationary_mode:
            return "WANDER"
        if not self.bot.config.enable_random_wander:
            return None
        if time.time() >= self._endtime:
            return "WANDER"
        return None

    def execute(self) -> None:
        self.bot.grind_once()

    def exit(self) -> None:
        self.bot.hid.release_all()


__all__ = ["Grind"]
