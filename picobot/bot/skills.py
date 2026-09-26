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
                   "wait_on_arrival": 4},
      "booster":  {"key": "f", "cooldown": 115, "kind": "buff"}
    }

``cooldown: 0`` means always ready (spam attacks). ``kind`` controls where
the skill may fire: ``attack`` skills are picked by the attack loop,
``buff`` skills fire whenever ready, ``summon`` skills only fire at
anchors that list them in ``on_arrive`` (they're positional).

When no explicit ``skills`` map is given, the legacy ``attack_keys`` /
``buff_keys`` + ``buff_interval_seconds`` fields are synthesised into
equivalent skills so old configs keep working.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, List, Optional

VALID_KINDS = ("attack", "buff", "summon")


@dataclass
class Skill:
    name: str
    key: str
    cooldown: float = 0.0        # seconds; 0 = always ready
    kind: str = "attack"         # attack | buff | summon
    wait_on_arrival: float = 0.0  # max s to wait for CD after reaching an anchor
    hold: Optional[float] = None  # key-hold override (None = humanized tap)

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
        )

    def to_dict(self) -> dict:
        out = {"key": self.key, "kind": self.kind}
        if self.cooldown:
            out["cooldown"] = self.cooldown
        if self.wait_on_arrival:
            out["wait_on_arrival"] = self.wait_on_arrival
        if self.hold is not None:
            out["hold"] = self.hold
        return out


class SkillBook:
    """Named skills + last-used timestamps -> readiness lookups."""

    def __init__(self, skills: Optional[Dict[str, Skill]] = None) -> None:
        self.skills: Dict[str, Skill] = dict(skills or {})
        self._last_used: Dict[str, float] = {}

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
    def remaining(self, name: str, now: Optional[float] = None) -> float:
        skill = self.skills.get(name)
        if skill is None or skill.cooldown <= 0:
            return 0.0
        last = self._last_used.get(name)
        if last is None:
            return 0.0
        now = time.time() if now is None else now
        return max(0.0, skill.cooldown - (now - last))

    def ready(self, name: str, now: Optional[float] = None) -> bool:
        return name in self.skills and self.remaining(name, now) <= 0.0

    def mark_used(self, name: str, now: Optional[float] = None) -> None:
        self._last_used[name] = time.time() if now is None else now

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
