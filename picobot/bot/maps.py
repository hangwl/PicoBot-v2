"""Per-map storage: one JSON file per farmed map under ``maps/``.

File shape::

    {
      "name": "limina_1f_east",          // user alias (file name)
      "map_name": "Limina : 1-5 East",   // OCR'd in-game title
      "fingerprint": null,               // legacy, carried through unused
      "minimap_region": [x, y, w, h],    // remembered panel layout
      "platforms": [[x0,y0,x1,y1], ...], // drawn platform lines
      "rotation": { ...Rotation.to_dict()... },
      "skills":   { "fountain": {"key": "d", "cooldown": 57, "kind": "summon"} }
    }

Identity is the OCR'd title matched by :meth:`MapStore.match_title`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..fileio import write_text_atomic
from ..vision.mapname import title_score
from .rotation import Rotation
from .skills import Skill

logger = logging.getLogger(__name__)


def _segs(raw) -> Optional[list]:
    """Normalized [[x0,y0,x1,y1], ...] from raw JSON, or None."""
    if not isinstance(raw, list):
        return None
    out = []
    for s in raw:
        if not isinstance(s, (list, tuple)) or len(s) != 4:
            continue
        try:
            seg = [float(v) for v in s]
        except (TypeError, ValueError):
            continue
        if all(0.0 <= v <= 1.5 for v in seg):
            out.append(seg)
    return out or None


@dataclass
class MapEntry:
    name: str
    rotation: Rotation = field(default_factory=Rotation)
    skills: Dict[str, Skill] = field(default_factory=dict)
    fingerprint: Optional[str] = None
    map_name: Optional[str] = None
    minimap_region: Optional[tuple] = None  # remembered (x, y, w, h) layout
    # Optional per-map boundaries (normalized fractions): player left of
    # "left" must face right, right of "right" must face left — for maps
    # whose play area doesn't span the minimap edge-to-edge. "floor" is
    # a normalized y: at/below it, no downward movement is attempted.
    walls: Optional[dict] = None
    # Hand-drawn platform segments, normalized [[x0,y0,x1,y1], ...] —
    # the authoritative walkable geometry: auto-detection proved too
    # fragile on translucent minimaps, so platforms are drawn on the
    # dashboard (drag along each line on the minimap view).
    platforms: Optional[list] = None
    # Hand-drawn rope/ladder segments, normalized [[x0,y0,x1,y1], ...] —
    # climbable connections between platforms (drawn like platforms).
    ropes: Optional[list] = None
    path: Optional[Path] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "map_name": self.map_name,
            "fingerprint": self.fingerprint,
            "minimap_region": (
                list(self.minimap_region) if self.minimap_region else None
            ),
            "walls": dict(self.walls) if self.walls else None,
            "platforms": (
                [list(s) for s in self.platforms]
                if self.platforms else None
            ),
            "ropes": [list(s) for s in self.ropes] if self.ropes else None,
            "rotation": self.rotation.to_dict(),
            "skills": {n: s.to_dict() for n, s in self.skills.items()},
        }

    @classmethod
    def from_dict(cls, data: dict, path: Optional[Path] = None) -> "MapEntry":
        if not isinstance(data, dict) or not data.get("name"):
            raise ValueError("map file requires a 'name'")
        skills = {
            name: Skill.from_dict(name, spec)
            for name, spec in (data.get("skills") or {}).items()
        }
        region = data.get("minimap_region")
        try:
            region = tuple(int(v) for v in region) if region else None
        except (TypeError, ValueError):
            region = None
        if region is not None and len(region) != 4:
            region = None
        walls = None
        raw_walls = data.get("walls")
        if isinstance(raw_walls, dict):
            walls = {}
            for side in ("left", "right", "floor"):
                v = raw_walls.get(side)
                if isinstance(v, (int, float)) and 0.0 <= float(v) <= 1.5:
                    walls[side] = float(v)
            walls = walls or None
        platforms = _segs(data.get("platforms"))
        return cls(
            name=str(data["name"]),
            rotation=Rotation.from_dict(data.get("rotation")),
            skills=skills,
            fingerprint=data.get("fingerprint"),
            map_name=data.get("map_name"),
            minimap_region=region,
            walls=walls,
            platforms=platforms,
            ropes=_segs(data.get("ropes")),
            path=path,
        )


class MapStore:
    """Directory of :class:`MapEntry` JSON files with title lookup."""

    def __init__(self, directory: str | Path = "maps") -> None:
        self.directory = Path(directory)
        self._entries: Optional[List[MapEntry]] = None

    def load_all(self) -> List[MapEntry]:
        if self._entries is not None:
            return self._entries
        entries: List[MapEntry] = []
        if self.directory.is_dir():
            for path in sorted(self.directory.glob("*.json")):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    entries.append(MapEntry.from_dict(data, path=path))
                except Exception as exc:
                    logger.warning("Skipping map file %s: %s", path, exc)
        self._entries = entries
        return entries

    def reload(self) -> List[MapEntry]:
        self._entries = None
        return self.load_all()

    def names(self) -> List[str]:
        return [e.name for e in self.load_all()]

    def get(self, name: str) -> Optional[MapEntry]:
        for entry in self.load_all():
            if entry.name == name:
                return entry
        return None

    def match_title(
        self,
        ocr_text: Optional[str],
        *,
        min_score: float = 0.93,
        margin: float = 0.05,
    ) -> Tuple[Optional[MapEntry], float]:
        """Best entry for an OCR'd title plus its score (see
        :func:`~picobot.vision.mapname.title_score`).

        Each entry scores under its ``map_name`` and its ``name`` alias.
        The winner must reach ``min_score`` — above what sibling maps
        sharing a street name score — and lead the runner-up by
        ``margin``; otherwise the result is ``(None, best_score)``.
        """
        from ..vision.mapname import normalize_name

        # The exact title wins outright — a sibling's near score must not
        # make it "ambiguous".
        want = normalize_name(ocr_text)
        if want:
            for e in self.load_all():
                if normalize_name(e.map_name) == want:
                    return e, 1.0
        scored = sorted(
            (
                (max(title_score(ocr_text, e.map_name),
                     title_score(ocr_text, e.name)), i, e)
                for i, e in enumerate(self.load_all())
            ),
            key=lambda t: (-t[0], t[1]),
        )
        if not scored:
            return None, 0.0
        best, _, entry = scored[0]
        second = scored[1][0] if len(scored) > 1 else 0.0
        if best < min_score or best - second < margin:
            return None, best
        return entry, best

    def match_name(self, ocr_text: Optional[str]) -> Optional[MapEntry]:
        return self.match_title(ocr_text)[0]

    def save(self, entry: MapEntry) -> Path:
        """Write the map file; returns its path."""
        self.directory.mkdir(parents=True, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in entry.name)
        path = self.directory / f"{safe}.json"
        write_text_atomic(path, json.dumps(entry.to_dict(), indent=2))
        entry.path = path
        if self._entries is not None and entry not in self._entries:
            self._entries.append(entry)
        return path


__all__ = ["MapEntry", "MapStore"]
