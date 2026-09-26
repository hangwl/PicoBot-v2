"""Grind state: farm at the current anchor until the dwell elapses."""

from __future__ import annotations

from .base import States, safety_transition


class Grind(States):
    """Dwell at the current anchor (attacks/buffs/arrival skills), then
    move on via TRAVEL. Without a configured rotation this degrades to the
    legacy stationary-grind -> WANDER behaviour."""

    def enter(self) -> None:
        self.bot.begin_dwell()
        self.bot.log("GRIND: farming")

    def check_status(self):
        transition = safety_transition(self.bot)
        if transition is not None:
            return transition
        if self.bot.rotation_active():
            return "TRAVEL" if self.bot.dwell_done() else None
        cfg = self.bot.config
        if not cfg.stationary_mode:
            return "WANDER"
        if not cfg.enable_random_wander:
            return None
        if self.bot.dwell_done():
            return "WANDER"
        return None

    def execute(self) -> None:
        if self.bot.rotation_active():
            self.bot.dwell_tick()
        else:
            self.bot.grind_once()

    def exit(self) -> None:
        self.bot.hid.release_all()


__all__ = ["Grind"]
