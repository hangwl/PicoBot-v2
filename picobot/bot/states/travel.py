"""Travel state: run the leg to the next anchor in the rotation."""

from __future__ import annotations

import random

from .base import States, safety_transition


class Travel(States):
    """Executes ``bot.run_travel()`` — the leg's walk/flash/climb steps —
    then hands back to GRIND. On arrival there is a small chance of a
    WANDER detour so the route isn't a perfect metronome. A leg that
    aborts still returns to GRIND (safety transitions win next switch)."""

    def __init__(self, bot) -> None:
        super().__init__(bot)
        self._finished = False
        self._has_target = False

    def enter(self) -> None:
        self._finished = False
        self._has_target = self.bot.begin_travel()
        if not self._has_target:
            self._finished = True

    def check_status(self):
        transition = safety_transition(self.bot)
        if transition is not None:
            return transition
        if self._finished:
            rot = self.bot.effective_rotation()
            if self._has_target and random.random() < rot.wander_chance:
                return "WANDER"
            return "GRIND"
        return None

    def execute(self) -> None:
        self.bot.run_travel()
        self._finished = True

    def exit(self) -> None:
        self.bot.hid.release_all()


__all__ = ["Travel"]
