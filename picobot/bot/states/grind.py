"""Grind state: patrol the checkpoint route / farm until travel fires."""

from __future__ import annotations

from .base import States, safety_transition


class Grind(States):
    """Patrol the anchors (continuous plan), or — without a rotation —
    keep weave-hopping around where grinding started."""

    def enter(self) -> None:
        self.bot.begin_dwell()
        self.bot.log("GRIND: farming")

    def check_status(self):
        transition = safety_transition(self.bot)
        if transition is not None:
            return transition
        if self.bot.rotation_active() and self.bot.dwell_done():
            return "TRAVEL"
        return None

    def execute(self) -> None:
        if self.bot.rotation_active():
            self.bot.dwell_tick()
        else:
            self.bot.grind_once()

    def exit(self) -> None:
        self.bot.hid.release_all()


__all__ = ["Grind"]
