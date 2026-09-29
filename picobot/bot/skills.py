"""Per-skill cooldown scheduling for the smart bot.

Real rotations aren't "spam one attack": summons, buffs, and cooldown
skills each run on their own timer, and a player fires them as the icons
light up. :class:`SkillBook` tracks when each named skill was last used
and answers which are ready, so the rotation logic can do the same.

Configuration shape (inside ``config.json["bot"]`` or a map file)::

    "skills": {
      "main":     {"key": "a", "cooldown": 0},
      "burst":    {"key": "s", "cooldown": 28, "kind": "attack"},
      "fountain": {"key": "d", "cooldown": 57, "kind": "summon",
                   "charges": 2, "duration": 60},
      "booster":  {"key": "f", "cooldown": 115, "kind": "buff"}
    }

``cooldown: 0`` means always ready (spam attacks). ``kind`` controls where
the skill may fire: ``attack`` skills are picked by the attack loop,
``buff`` skills fire whenever ready, ``summon`` skills are placed at
anchors (see :mod:`picobot.bot.summons`).

``charges`` (default 1): the skill stores up to this many uses; while
below the maximum, one charge returns every ``cooldown`` seconds. A
summon's ``duration`` is how long each instance stays out (0 = until its
cooldown ends); up to ``charges`` instances can be out at once.
``movement`` skills never auto-fire — they document traversal keybinds
(flash jump & co.).

When no explicit ``skills`` map is given, the legacy ``attack_keys`` /
``buff_keys`` + ``buff_interval_seconds`` fields are synthesised into
equivalent skills so old configs keep working.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

VALID_KINDS = ("attack", "buff", "summon", "movement")


@dataclass
class Skill:
    name: str
    key: str
    cooldown: float = 0.0        # seconds; 0 = always ready
    kind: str = "attack"         # attack | buff | summon | movement
    wait_on_arrival: float = 0.0  # ignored — anchors are pass-through
    hold: Optional[float] = None  # key-hold override (None = humanized tap)
    charges: int = 1             # stored uses; one returns per cooldown
    duration: float = 0.0        # summon uptime in s (0 = until its cooldown)

    @classmethod
    def from_dict(cls, name: str, data: dict) -> "Skill":
        kind = str(data.get("kind", "attack"))
        if kind not in VALID_KINDS:
            raise ValueError(f"skill {name!r}: kind must be one of {VALID_KINDS}")
        if "key" not in data:
            raise ValueError(f"skill {name!r}: missing 'key'")
        return cls(
            name=name,
            key=str(data["key"]),
            cooldown=max(0.0, float(data.get("cooldown", 0.0))),
            kind=kind,
            wait_on_arrival=max(0.0, float(data.get("wait_on_arrival", 0.0))),
            hold=float(data["hold"]) if data.get("hold") is not None else None,
            charges=max(1, int(data.get("charges", 1))),
            duration=max(0.0, float(data.get("duration", 0.0))),
        )

    @property
    def uptime(self) -> float:
        """How long one cast stays out: its duration, else its cooldown."""
        return self.duration or self.cooldown

    def to_dict(self) -> dict:
        out = {"key": self.key, "kind": self.kind}
        if self.cooldown:
            out["cooldown"] = self.cooldown
        if self.wait_on_arrival:
            out["wait_on_arrival"] = self.wait_on_arrival
        if self.hold is not None:
            out["hold"] = self.hold
        if self.charges != 1:
            out["charges"] = self.charges
        if self.duration:
            out["duration"] = self.duration
        return out


class SkillBook:
    """Named skills + last-used timestamps -> readiness lookups."""

    def __init__(self, skills: Optional[Dict[str, Skill]] = None) -> None:
        self.skills: Dict[str, Skill] = dict(skills or {})
        # name -> (charges left, when the next one returns | None when full)
        self._charges: Dict[str, Tuple[int, Optional[float]]] = {}

    def __contains__(self, name: str) -> bool:
        return name in self.skills

    def __len__(self) -> int:
        return len(self.skills)

    def get(self, name: str) -> Optional[Skill]:
        return self.skills.get(name)

    def overlay(self, skills: Dict[str, Skill]) -> None:
        """Merge map-level skill definitions over the base set (by name)."""
        self.skills.update(skills)

    # -- Readiness ----------------------------------------------------------
    def _sync(self, skill: Skill, now: float) -> Tuple[int, Optional[float]]:
        """Charges left after recharging up to ``now``: one returns per
        ``cooldown`` while below the maximum."""
        have, nxt = self._charges.get(skill.name, (skill.charges, None))
        while have < skill.charges and nxt is not None and now >= nxt:
            have += 1
            nxt = nxt + skill.cooldown if have < skill.charges else None
        self._charges[skill.name] = (have, nxt)
        return have, nxt

    def charges(self, name: str, now: Optional[float] = None) -> int:
        skill = self.skills.get(name)
        if skill is None:
            return 0
        if skill.cooldown <= 0:
            return skill.charges
        return self._sync(skill, time.time() if now is None else now)[0]

    def remaining(self, name: str, now: Optional[float] = None) -> float:
        """Seconds until the skill can be used (0 with a charge left)."""
        skill = self.skills.get(name)
        if skill is None or skill.cooldown <= 0:
            return 0.0
        now = time.time() if now is None else now
        have, nxt = self._sync(skill, now)
        if have > 0 or nxt is None:
            return 0.0
        return max(0.0, nxt - now)

    def ready(self, name: str, now: Optional[float] = None) -> bool:
        return name in self.skills and self.remaining(name, now) <= 0.0

    def mark_used(self, name: str, now: Optional[float] = None) -> None:
        skill = self.skills.get(name)
        if skill is None or skill.cooldown <= 0:
            return
        now = time.time() if now is None else now
        have, nxt = self._sync(skill, now)
        have = max(0, have - 1)
        if nxt is None:
            nxt = now + skill.cooldown      # recharge starts with the first use
        self._charges[name] = (have, nxt)

    def ready_attacks(self, now: Optional[float] = None) -> List[Skill]:
        return [
            s for s in self.skills.values()
            if s.kind == "attack" and self.remaining(s.name, now) <= 0.0
        ]

    def due_buffs(self, now: Optional[float] = None) -> List[Skill]:
        return [
            s for s in self.skills.values()
            if s.kind == "buff" and self.remaining(s.name, now) <= 0.0
        ]

    # -- Config -------------------------------------------------------------
    @classmethod
    def from_config(cls, data: dict | None) -> "SkillBook":
        """Build from the ``"bot"`` config dict (explicit or legacy fields)."""
        data = data or {}
        explicit = data.get("skills")
        if isinstance(explicit, dict) and explicit:
            return cls(
                {name: Skill.from_dict(name, spec)
                 for name, spec in explicit.items()}
            )
        skills: Dict[str, Skill] = {}
        for key in data.get("attack_keys") or ["a"]:
            skills[f"attack_{key}"] = Skill(f"attack_{key}", str(key))
        interval = float(data.get("buff_interval_seconds", 60.0))
        for key in data.get("buff_keys") or []:
            skills[f"buff_{key}"] = Skill(
                f"buff_{key}", str(key), interval, "buff"
            )
        return cls(skills)


__all__ = ["Skill", "SkillBook", "VALID_KINDS"]
