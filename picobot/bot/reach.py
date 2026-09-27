"""Per-move reach envelopes, learned from observed takeoffs and landings.

A move's reach is how far it can carry the character sideways (``dx``)
and upward (``rise``), in minimap px. Defaults are conservative; each
executed move reports where it took off and landed:

- success → the envelope grows to what was observed (it's proven);
- failure at or inside the envelope → the envelope shrinks below the
  attempted size;
- failure beyond it (an exploratory attempt) → a ceiling stops retrying.

The planner may explore up to ``explore`` × the envelope (capped by the
ceiling) at a cost penalty, so estimates grow from conservative starts.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

HORIZONTAL = ("jump", "flash", "double_flash")
UPWARD = ("up_flash", "up_side_flash", "rope_lift")
MOVES = HORIZONTAL + UPWARD


@dataclass
class Reach:
    dx: float
    rise: float


def base_reach(config) -> Dict[str, Reach]:
    """Conservative starting envelopes from ``BotConfig`` nav_* keys."""
    return {
        "jump": Reach(config.nav_jump_px, 4.0),
        "flash": Reach(config.nav_gap_px, 4.0),
        "double_flash": Reach(config.nav_double_gap_px, 4.0),
        "up_flash": Reach(6.0, config.nav_up_flash_px),
        "up_side_flash": Reach(config.nav_up_side_dx_px, config.nav_up_flash_px * 0.8),
        "rope_lift": Reach(3.0, config.nav_rope_lift_px),
    }


class ReachModel:
    def __init__(
        self,
        base: Dict[str, Reach],
        *,
        path: Optional[str | Path] = None,
        explore: float = 1.15,
        shrink: float = 0.95,
    ) -> None:
        self.base = dict(base)
        self.est: Dict[str, Reach] = {k: Reach(v.dx, v.rise) for k, v in base.items()}
        self.ceiling: Dict[str, Reach] = {}
        self.explore = explore
        self.shrink = shrink
        self.path = Path(path) if path else None
        self._dirty = False
        self._saved_at = 0.0
        self.version = 0
        self.load()

    # -- Queries --------------------------------------------------------------------
    def get(self, move: str) -> Reach:
        return self.est[move]

    def limit(self, move: str) -> Reach:
        """Largest envelope the planner may attempt (exploration included)."""
        e = self.est[move]
        lim = Reach(e.dx * self.explore, e.rise * self.explore)
        c = self.ceiling.get(move)
        if c is not None:
            lim = Reach(min(lim.dx, max(e.dx, c.dx)), min(lim.rise, max(e.rise, c.rise)))
        return lim

    def fits(self, move: str, dx: float, rise: float) -> Optional[bool]:
        """None = out of reach; True = proven envelope; False = exploratory."""
        lim = self.limit(move)
        if dx > lim.dx or rise > lim.rise:
            return None
        e = self.est[move]
        return dx <= e.dx and rise <= e.rise

    def snapshot(self) -> Tuple:
        return tuple(
            (k, round(v.dx, 1), round(v.rise, 1),
             *((round(self.ceiling[k].dx, 1), round(self.ceiling[k].rise, 1))
               if k in self.ceiling else ()))
            for k, v in sorted(self.est.items())
        )

    # -- Learning -------------------------------------------------------------------
    def observe(
        self,
        move: str,
        *,
        planned: Tuple[float, float],
        observed: Tuple[float, float],
        ok: bool,
    ) -> None:
        """``planned``/``observed`` are (dx, rise) of the attempted and the
        actual displacement (absolute dx; rise positive = upward)."""
        if move not in self.est:
            return
        e = self.est[move]
        before = (e.dx, e.rise)
        c0 = self.ceiling.get(move)
        ceil_before = (c0.dx, c0.rise) if c0 else None
        pdx, prise = planned
        horizontal = move in HORIZONTAL
        if ok:
            odx, orise = observed
            if horizontal or move == "up_side_flash":
                e.dx = max(e.dx, odx)
            if not horizontal:
                e.rise = max(e.rise, orise)
        else:
            c = self.ceiling.setdefault(move, Reach(float("inf"), float("inf")))
            if horizontal or move == "up_side_flash":
                if pdx <= e.dx:
                    e.dx = max(self.base[move].dx * 0.5, pdx * self.shrink)
                c.dx = min(c.dx, pdx * 0.97)
            if not horizontal:
                if prise <= e.rise:
                    e.rise = max(self.base[move].rise * 0.5, prise * self.shrink)
                c.rise = min(c.rise, prise * 0.97)
        c1 = self.ceiling.get(move)
        if (e.dx, e.rise) != before or (c1 and (c1.dx, c1.rise)) != ceil_before:
            self.version += 1
            self._dirty = True
            logger.info("reach %s: dx %.1f→%.1f rise %.1f→%.1f",
                        move, before[0], e.dx, before[1], e.rise)
        self.save()

    # -- Persistence ----------------------------------------------------------------
    def load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("unreadable reach file %s", self.path)
            return
        for k, v in (data.get("est") or {}).items():
            if k in self.est:
                self.est[k] = Reach(float(v["dx"]), float(v["rise"]))
        inf = lambda v: float("inf") if v is None else float(v)
        for k, v in (data.get("ceiling") or {}).items():
            if k in self.est:
                self.ceiling[k] = Reach(inf(v.get("dx")), inf(v.get("rise")))
        self.version += 1

    def save(self, force: bool = False) -> None:
        if self.path is None or not self._dirty:
            return
        now = time.monotonic()
        if not force and now - self._saved_at < 10.0:
            return
        doc = {
            "est": {k: {"dx": v.dx, "rise": v.rise} for k, v in self.est.items()},
            "ceiling": {
                k: {"dx": v.dx, "rise": v.rise} for k, v in self.ceiling.items()
                if v.dx != float("inf") or v.rise != float("inf")
            },
        }
        doc["ceiling"] = {
            k: {kk: (None if vv == float("inf") else vv) for kk, vv in v.items()}
            for k, v in doc["ceiling"].items()
        }
        try:
            self.path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
            self._dirty = False
            self._saved_at = now
        except OSError:
            logger.warning("could not save reach file %s", self.path, exc_info=True)


__all__ = ["HORIZONTAL", "MOVES", "Reach", "ReachModel", "UPWARD", "base_reach"]
