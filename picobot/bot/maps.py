"""Per-map rotation storage.

Each farmed map gets a JSON file under ``maps/`` holding its rotation
graph, skill bindings, and a minimap fingerprint. The fingerprint lets
the bot auto-select the right file when the character changes maps —
capture the minimap, hash it, match against the store.

File shape::

    {
      "name": "limina_1f_east",
      "map_name": "Limina : 1-5 East",   // optional label (legacy OCR field)
      "fingerprint": "<hex from vision.minimap.fingerprint>",
      "minimap_region": [x, y, w, h],    // remembered layout (optional)
      "platforms": [[x0,y0,x1,y1], ...], // drawn platform lines (optional)
      "rotation": { ...Rotation.to_dict()... },
      "skills":   { "fountain": {"key": "d", "cooldown": 57, "kind": "summon"} }
    }

``map_name`` is a free-form label kept for file compatibility — it is
not used for matching (the OCR reader was removed; the strip's
translucent background made it unreliable). ``fingerprint`` is the map
identity. ``minimap_region`` (client-area-relative) is the minimap
layout captured at calibration time; once a map is identified the bot
restores it, so auto-detection drift can't accumulate on known maps.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..vision.mapname import normalize_name
from ..vision.minimap import fingerprint_distance
from .rotation import Rotation
from .skills import Skill

logger = logging.getLogger(__name__)


@dataclass
class MapEntry:
    name: str
    rotation: Rotation = field(default_factory=Rotation)
    skills: Dict[str, Skill] = field(default_factory=dict)
    fingerprint: Optional[str] = None
    map_name: Optional[str] = None   # optional label (legacy OCR field)
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
        platforms = None
        raw_plats = data.get("platforms")
        if isinstance(raw_plats, list):
            platforms = []
            for s in raw_plats:
                if not isinstance(s, (list, tuple)) or len(s) != 4:
                    continue
                try:
                    seg = [float(v) for v in s]
                except (TypeError, ValueError):
                    continue
                if all(0.0 <= v <= 1.5 for v in seg):
                    platforms.append(seg)
            platforms = platforms or None
        return cls(
            name=str(data["name"]),
            rotation=Rotation.from_dict(data.get("rotation")),
            skills=skills,
            fingerprint=data.get("fingerprint"),
            map_name=data.get("map_name"),
            minimap_region=region,
            walls=walls,
            platforms=platforms,
            path=path,
        )


class MapStore:
    """Directory of :class:`MapEntry` JSON files with fingerprint lookup."""

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

    def match(self, fingerprint: Optional[str], threshold: float = 15.0) -> Optional[MapEntry]:
        """Best fingerprint match under ``threshold``, else None."""
        entry, _ = self.match_scored(fingerprint, threshold)
        return entry

    def match_scored(
        self, fingerprint: Optional[str], threshold: float = 15.0
    ) -> Tuple[Optional[MapEntry], float]:
        """Best fingerprint match plus its distance (``inf`` = no match).

        The distance doubles as a confidence signal — well under
        ``threshold`` is a confident match, at/over it is not a match.
        """
        if not fingerprint:
            return None, float("inf")
        best, best_dist = None, float("inf")
        for entry in self.load_all():
            dist = fingerprint_distance(fingerprint, entry.fingerprint or "")
            if dist < best_dist:
                best, best_dist = entry, dist
        if best is None or best_dist >= threshold:
            return None, best_dist
        return best, best_dist

    def match_name(self, ocr_text: Optional[str]) -> Optional[MapEntry]:
        """Match on the OCR'd map title (normalized), else None.

        The stored ``map_name`` only needs to appear inside the OCR'd
        text — the strip may also pick up neighbouring UI text. Longest
        stored name wins, so overlapping names stay unambiguous.
        """
        norm = normalize_name(ocr_text)
        if not norm:
            return None
        best, best_len = None, 0
        for entry in self.load_all():
            en = normalize_name(entry.map_name)
            if en and en in norm and len(en) > best_len:
                best, best_len = entry, len(en)
        return best

    def save(self, entry: MapEntry) -> Path:
        """Write the map file; returns its path."""
        self.directory.mkdir(parents=True, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in entry.name)
        path = self.directory / f"{safe}.json"
        path.write_text(json.dumps(entry.to_dict(), indent=2), encoding="utf-8")
        entry.path = path
        if self._entries is not None and entry not in self._entries:
            self._entries.append(entry)
        return path


__all__ = ["MapEntry", "MapStore"]
