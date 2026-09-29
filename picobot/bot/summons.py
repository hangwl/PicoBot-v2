"""Where summons are, and for how long.

Summons are positional: the bot places them at anchors as it passes, and
an anchor holds at most one live summon of any kind. Each cast lives for
the skill's ``uptime``; a skill can have up to ``charges`` instances out
at once, and casting one more removes its oldest (as the game does).

The bot can't see summons, so this is bookkeeping from cast times. It is
cleared on a map change (summons don't follow the player) and when the
bot starts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .skills import Skill


@dataclass
class Placement:
    skill: str
    anchor: str
    placed: float
    expires: float


class SummonTracker:
    def __init__(self) -> None:
        self._placed: List[Placement] = []

    def reset(self) -> None:
        self._placed = []

    def _prune(self, now: float) -> None:
        self._placed = [p for p in self._placed if p.expires > now]

    def active(self, now: float) -> List[Placement]:
        self._prune(now)
        return list(self._placed)

    def anchor_free(self, anchor: str, now: float) -> bool:
        self._prune(now)
        return all(p.anchor != anchor for p in self._placed)

    def place(self, skill: Skill, anchor: str, now: float) -> Optional[Placement]:
        """Record a cast at ``anchor``; returns the placement the game
        removed to make room (that skill's oldest), if any."""
        self._prune(now)
        mine = [p for p in self._placed if p.skill == skill.name]
        gone = None
        if len(mine) >= skill.charges:
            gone = min(mine, key=lambda p: p.placed)
            self._placed.remove(gone)
        self._placed.append(Placement(skill.name, anchor, now, now + skill.uptime))
        return gone


__all__ = ["Placement", "SummonTracker"]
