"""State base class and the shared safety preamble.

States never reference each other directly — ``check_status`` returns a
string key looked up in ``Machine.state_mapping`` (avoids circular imports),
``None`` to keep going, or the ``POP`` sentinel to resume whatever state is
on the machine's interruption stack.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

# Returned by check_status() to pop Machine.stack and resume the
# interrupted state (e.g. leaving Pause once the map is safe again).
POP = object()


class States(ABC):
    def __init__(self, bot) -> None:
        self.bot = bot

    @abstractmethod
    def enter(self) -> None: ...

    @abstractmethod
    def execute(self) -> None: ...

    @abstractmethod
    def check_status(self):
        """Return a state key to transition, ``POP`` to resume, None to stay."""

    @abstractmethod
    def exit(self) -> None: ...


def safety_transition(bot, *, notify: bool = True):
    """Common unsafe-condition checks shared by every active state.

    Returns ``"PAUSE"`` when the bot should halt, ``None`` when clear. The
    machine also checks ``bot.should_continue()`` before consulting states,
    so this helper only covers *environmental* safety.

    ``notify`` controls whether a one-shot alert is emitted — Pause passes
    False so a persistent hazard doesn't spam notifications every tick.
    """
    if not bot.is_window_focused():
        return "PAUSE"
    if not bot.should_continue():
        return None
    if hasattr(bot, "unsafe_reason"):
        reason = bot.unsafe_reason()
    else:
        # Fallback probe for bare bot doubles lacking unsafe_reason().
        reason = None
        if bot.config.pause_on_lie_detector and bot.check_lie_detector():
            reason = "verification prompt"
        elif bot.config.stop_when_rune_appears and bot.rune_present():
            reason = "rune"
        elif bot.config.stop_when_players_appear and bot.other_players_present():
            reason = "other players"
    if reason is not None:
        if notify:
            bot.notify(f"{reason.capitalize()} detected — pausing")
        return "PAUSE"
    return None


__all__ = ["POP", "States", "safety_transition"]
