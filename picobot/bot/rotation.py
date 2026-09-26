"""Route graph for map rotations.

A farming rotation is a loop of *anchors* (positions where the player
farms and drops summons) connected by *legs* (how to get between them).
Legs are lists of small steps — walk, climb, jumps — because "go to (x, y)"
alone can't express "align to the rope at this x, then climb".

All coordinates are written normalized (0-1 fractions of the minimap
region) so a rotation survives resolution/window-size changes; values
>1 are treated as absolute minimap pixels for hand-tuned configs.

Config shape (inside ``"rotation"`` in config.json or a map file)::

    {
      "style": "loop",              // loop | pingpong | shuffle
      "position_jitter_px": 4,      // aim near the anchor, never exactly on it
      "rest_chance": 0.02,          // occasional idle stretch per dwell
      "wander_chance": 0.05,        // occasional detour after a leg
      "travel_style": "mixed",      // walk | flash | mixed (default leg style)
      "patrol": false,              // anchors as checkpoints: weave toward
                                    // the next one instead of parking
      "anchors": [
        {"name": "west", "pos": [0.31, 0.55], "dwell": [8, 14],
         "on_arrive": ["fountain"], "face": "left"}
      ],
      "legs": [
        {"from": 0, "to": 1, "steps": [
          {"walk_to": [0.50, 0.55], "style": "flash"},
          {"climb": {"dir": "up", "until_y": 0.31, "x": 0.50}},
          {"walk_to": [0.72, 0.31]}
        ]}
      ]
    }
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

Coord = Tuple[float, float]

ROTATION_STYLES = ("loop", "pingpong", "shuffle")
TRAVEL_STYLES = ("walk", "flash", "mixed")
CLIMB_DIRS = ("up", "down")


def resolve_coord(value: float, span: int) -> int:
    """Normalized 0-1 -> ``value*span`` px; >1 is already pixels."""
    v = float(value)
    return int(round(v * span)) if 0.0 <= v <= 1.0 else int(round(v))


@dataclass
class Step:
    """One micro-step of a leg between anchors."""

    kind: str                        # walk_to | climb | up_jump | down_jump | wait
    x: Optional[float] = None        # walk_to / climb align target
    y: Optional[float] = None        # walk_to target
    direction: str = "up"            # climb: up | down
    until_y: Optional[float] = None  # climb: stop when player y crosses this
    seconds: float = 0.0             # wait
    style: Optional[str] = None      # walk_to: walk | flash | mixed

    @classmethod
    def from_dict(cls, data: dict) -> "Step":
        if not isinstance(data, dict):
            raise ValueError(f"leg step must be an object, got {data!r}")
        if "walk_to" in data:
            x, y = data["walk_to"]
            style = data.get("style")
            if style is not None and style not in TRAVEL_STYLES:
                raise ValueError(f"walk_to style must be one of {TRAVEL_STYLES}")
            return cls("walk_to", x=float(x), y=float(y), style=style)
        if "climb" in data:
            spec = data["climb"] or {}
            direction = str(spec.get("dir", "up"))
            if direction not in CLIMB_DIRS:
                raise ValueError(f"climb dir must be one of {CLIMB_DIRS}")
            if "until_y" not in spec:
                raise ValueError("climb step requires 'until_y'")
            x = spec.get("x")
            return cls(
                "climb",
                x=float(x) if x is not None else None,
                direction=direction,
                until_y=float(spec["until_y"]),
            )
        if "up_jump" in data or "down_jump" in data:
            return cls("up_jump" if "up_jump" in data else "down_jump")
        if "wait" in data:
            return cls("wait", seconds=max(0.0, float(data["wait"])))
        raise ValueError(f"unknown leg step: {data!r}")

    def to_dict(self) -> dict:
        if self.kind == "walk_to":
            out = {"walk_to": [self.x, self.y]}
            if self.style:
                out["style"] = self.style
            return out
        if self.kind == "climb":
            spec = {"dir": self.direction, "until_y": self.until_y}
            if self.x is not None:
                spec["x"] = self.x
            return {"climb": spec}
        if self.kind == "wait":
            return {"wait": self.seconds}
        return {self.kind: True}


@dataclass
class Anchor:
    """A farming spot: minimap position + what to do when we get there."""

    name: str
    x: float
    y: float
    dwell: Tuple[float, float] = (8.0, 14.0)  # seconds range to farm here
    on_arrive: Tuple[str, ...] = ()           # skill names to fire on arrival
    face: Optional[str] = None                # left | right tap after arriving

    @classmethod
    def from_dict(cls, data: dict, index: int = 0) -> "Anchor":
        pos = data.get("pos")
        if not pos or len(pos) != 2:
            raise ValueError(f"anchor #{index}: 'pos' must be [x, y]")
        dwell = data.get("dwell", [8, 14])
        if len(dwell) != 2:
            raise ValueError(f"anchor #{index}: 'dwell' must be [lo, hi]")
        face = data.get("face")
        if face is not None and face not in ("left", "right"):
            raise ValueError(f"anchor #{index}: 'face' must be left|right")
        return cls(
            name=str(data.get("name", f"anchor_{index}")),
            x=float(pos[0]),
            y=float(pos[1]),
            dwell=(float(dwell[0]), float(dwell[1])),
            on_arrive=tuple(str(s) for s in data.get("on_arrive", ())),
            face=face,
        )

    def dwell_seconds(self) -> float:
        lo, hi = self.dwell
        return random.uniform(lo, hi) if hi > lo else lo

    def to_dict(self) -> dict:
        out = {"name": self.name, "pos": [self.x, self.y],
               "dwell": list(self.dwell)}
        if self.on_arrive:
            out["on_arrive"] = list(self.on_arrive)
        if self.face:
            out["face"] = self.face
        return out


@dataclass
class Rotation:
    """Anchor list + leg graph + traversal policy."""

    anchors: List[Anchor] = field(default_factory=list)
    legs: Dict[Tuple[int, int], List[Step]] = field(default_factory=dict)
    style: str = "loop"
    position_jitter_px: int = 4
    rest_chance: float = 0.02
    wander_chance: float = 0.05
    travel_style: str = "mixed"
    # Checkpoints instead of parking spots: the dwell weaves toward the
    # next anchor and advances on arrival rather than waiting out a
    # dwell timer — a 2-anchor map becomes a back-and-forth patrol.
    patrol: bool = False

    # -- Traversal -----------------------------------------------------------
    def next_index(self, current: int, direction: int = 1) -> Tuple[int, int]:
        """Next anchor index and (possibly flipped) pingpong direction."""
        n = len(self.anchors)
        if n <= 1:
            return current, direction
        if self.style == "shuffle":
            choices = [i for i in range(n) if i != current]
            return random.choice(choices), direction
        if self.style == "pingpong":
            nxt = current + direction
            if nxt < 0 or nxt >= n:
                direction = -direction
                nxt = current + direction
            return nxt, direction
        return (current + 1) % n, direction

    def leg_steps(self, from_idx: int, to_idx: int) -> List[Step]:
        """Steps for a leg; defaults to a direct walk to the anchor."""
        steps = self.legs.get((from_idx, to_idx))
        if steps is not None:
            return steps
        target = self.anchors[to_idx]
        return [Step("walk_to", x=target.x, y=target.y)]

    # -- (De)serialisation ----------------------------------------------------
    @classmethod
    def from_dict(cls, data: dict | None) -> "Rotation":
        if not data:
            return cls()
        anchors = [
            Anchor.from_dict(a, i) for i, a in enumerate(data.get("anchors") or [])
        ]
        legs: Dict[Tuple[int, int], List[Step]] = {}
        for leg in data.get("legs") or []:
            f, t = int(leg["from"]), int(leg["to"])
            if not (0 <= f < len(anchors) and 0 <= t < len(anchors)):
                raise ValueError(f"leg ({f},{t}) out of range")
            legs[(f, t)] = [Step.from_dict(s) for s in leg.get("steps") or []]
        style = str(data.get("style", "loop"))
        if style not in ROTATION_STYLES:
            raise ValueError(f"rotation style must be one of {ROTATION_STYLES}")
        travel_style = str(data.get("travel_style", "mixed"))
        if travel_style not in TRAVEL_STYLES:
            raise ValueError(f"travel_style must be one of {TRAVEL_STYLES}")
        return cls(
            anchors=anchors,
            legs=legs,
            style=style,
            position_jitter_px=int(data.get("position_jitter_px", 4)),
            rest_chance=float(data.get("rest_chance", 0.02)),
            wander_chance=float(data.get("wander_chance", 0.05)),
            travel_style=travel_style,
            patrol=bool(data.get("patrol", False)),
        )

    def to_dict(self) -> dict:
        return {
            "style": self.style,
            "position_jitter_px": self.position_jitter_px,
            "rest_chance": self.rest_chance,
            "wander_chance": self.wander_chance,
            "travel_style": self.travel_style,
            "patrol": self.patrol,
            "anchors": [a.to_dict() for a in self.anchors],
            "legs": [
                {"from": f, "to": t, "steps": [s.to_dict() for s in steps]}
                for (f, t), steps in sorted(self.legs.items())
            ],
        }


__all__ = [
    "Anchor",
    "Rotation",
    "Step",
    "ROTATION_STYLES",
    "TRAVEL_STYLES",
    "resolve_coord",
]
