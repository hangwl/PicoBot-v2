"""Movement graph over hand-drawn platforms + route planning.

Nodes are points on platforms: segment ends plus the x-positions where a
move to another platform is possible. Edges:

- ``walk`` — along one platform (executed as flash weaves when long).
- ``down_jump`` onto the next platform below; ``drop`` off an end.
- Upward: ``up_flash`` / ``rope_lift`` onto the platform directly above;
  ``up_side_flash`` (up flash, then a sideways flash) up-and-over onto a
  higher platform across a gap.
- Horizontal gaps: ``jump``, ``flash``, ``double_flash``.
- ``rope_lift`` targets the **highest** platform within its grab range
  (``rope_max_px``, ~90 in-game) — the skill always grabs the topmost
  platform in range, so no edge points at a lower one.

Wall zones (``Bounds``, already padded) clip platforms: nothing is
planned inside a zone, and clipped ends are closed — no drop or gap move
leaves toward the wall. Platforms in the padded floor band are removed.

Whether a jump-type edge exists comes from the :class:`ReachModel`
envelopes (learned from observed moves); edges beyond the proven
envelope but within its exploration limit cost more. Coordinates are
minimap px; costs are rough seconds. ``jitter`` perturbs costs per query
so near-equal routes vary.
"""

from __future__ import annotations

import heapq
import random
from dataclasses import dataclass
from typing import Collection, Dict, Iterable, List, Optional, Sequence, Tuple

from .reach import ReachModel

Segment = Tuple[float, float, float, float]

COSTS = {
    "down_jump": 0.7, "drop": 0.6,
    "jump": 0.6, "flash": 0.8, "double_flash": 1.1,
    # Rope lift is preferred for rises whenever it's ready; the navigator
    # drops it from plans while it's cooling down.
    "up_flash": 1.0, "up_side_flash": 1.3, "rope_lift": 0.5,
}
EXPLORE_PENALTY = 1.6
LEVEL_PX = 4.0          # max rise for a "horizontal" gap move


@dataclass(frozen=True)
class Bounds:
    """Walkable area: x in [left, right], y above ``floor`` (None = open)."""

    left: Optional[float] = None
    right: Optional[float] = None
    floor: Optional[float] = None


@dataclass(frozen=True)
class Platform:
    x0: float
    y0: float
    x1: float
    y1: float
    open0: bool = True   # left end is a real edge (moves may leave it)
    open1: bool = True

    @classmethod
    def from_segment(cls, s: Segment) -> "Platform":
        x0, y0, x1, y1 = s
        return cls(x0, y0, x1, y1) if x0 <= x1 else cls(x1, y1, x0, y0)

    def spans(self, x: float, slack: float = 0.0) -> bool:
        return self.x0 - slack <= x <= self.x1 + slack

    def y_at(self, x: float) -> float:
        if self.x1 == self.x0:
            return (self.y0 + self.y1) / 2.0
        t = min(1.0, max(0.0, (x - self.x0) / (self.x1 - self.x0)))
        return self.y0 + t * (self.y1 - self.y0)

    def clip(self, b: Bounds) -> Optional["Platform"]:
        x0, x1, open0, open1 = self.x0, self.x1, True, True
        if b.left is not None and x0 < b.left:
            x0, open0 = b.left, False
        if b.right is not None and x1 > b.right:
            x1, open1 = b.right, False
        if x1 - x0 < 1.0:
            return None
        y0, y1 = self.y_at(x0), self.y_at(x1)
        if b.floor is not None and max(y0, y1) >= b.floor:
            return None
        return Platform(x0, y0, x1, y1, open0, open1)


@dataclass(frozen=True)
class Leg:
    kind: str
    x0: float
    y0: float
    x1: float
    y1: float
    cost: float = 0.0


@dataclass
class _Edge:
    to: int
    kind: str
    cost: float


class NavGraph:
    def __init__(
        self,
        segments: Iterable[Segment],
        reach: ReachModel,
        *,
        bounds: Optional[Bounds] = None,
        walk_speed: float = 40.0,
        snap_px: float = 8.0,
        edge_inset_px: float = 4.0,
        takeoff_inset_px: float = 3.0,
        rope_max_px: Optional[float] = None,
    ) -> None:
        plats = [
            Platform.from_segment(s) for s in segments
            if abs(s[2] - s[0]) >= 1.0
        ]
        if bounds is not None:
            plats = [c for p in plats if (c := p.clip(bounds)) is not None]
        self.platforms: List[Platform] = plats
        self.bounds = bounds
        self.reach = reach
        self.walk_speed = walk_speed
        self.snap_px = snap_px
        self.edge_inset_px = edge_inset_px
        self.takeoff_inset_px = takeoff_inset_px
        # Hard grab range: the rope's base reach (nav_rope_lift_px).
        self.rope_max_px = (
            reach.base["rope_lift"].rise if rope_max_px is None else rope_max_px
        )
        self.nodes: List[Tuple[int, float]] = []
        self._index: Dict[Tuple[int, float], int] = {}
        self.edges: List[List[_Edge]] = []
        self._build()

    # -- Geometry -----------------------------------------------------------------
    def below(self, x: float, y: float, exclude: int = -1) -> Optional[int]:
        """Nearest platform strictly below ``y`` at column ``x``."""
        best = None
        for i, p in enumerate(self.platforms):
            if i == exclude or not p.spans(x):
                continue
            py = p.y_at(x)
            if py > y + 1.0 and (best is None or py < best[0]):
                best = (py, i)
        return best[1] if best else None

    def highest_above(
        self, x: float, y: float, max_rise: float, exclude: int = -1
    ) -> Optional[Tuple[int, float]]:
        """(platform, rise) of the highest platform above ``y`` at column
        ``x`` within ``max_rise`` — the rope-lift grab target."""
        best = None
        for j, q in enumerate(self.platforms):
            if j == exclude or not q.spans(x):
                continue
            rise = y - q.y_at(x)
            if 1.0 < rise <= max_rise and (best is None or rise > best[1]):
                best = (j, rise)
        return best

    def above(self, x: float, y: float, exclude: int = -1) -> Optional[int]:
        """Nearest platform strictly above ``y`` at column ``x``."""
        best = None
        for i, p in enumerate(self.platforms):
            if i == exclude or not p.spans(x):
                continue
            py = p.y_at(x)
            if py < y - 1.0 and (best is None or py > best[0]):
                best = (py, i)
        return best[1] if best else None

    def locate(self, x: float, y: float) -> Optional[int]:
        """Platform the point stands on, or None.

        Asymmetric: the point may sit up to ``snap_px`` ABOVE the drawn
        row (the player glyph floats above the line) and up to 2px below
        it — stacked tiers stay unambiguous."""
        best = None
        for i, p in enumerate(self.platforms):
            if not p.spans(x, slack=3.0):
                continue
            d = p.y_at(x) - y                 # +: point floats above the row
            if -2 <= d <= self.snap_px and (best is None or d < best[0]):
                best = (d, i)
        return best[1] if best else None

    # -- Build --------------------------------------------------------------------
    def _node(self, plat: int, x: float) -> int:
        p = self.platforms[plat]
        key = (plat, round(min(p.x1, max(p.x0, x)), 1))
        idx = self._index.get(key)
        if idx is None:
            idx = len(self.nodes)
            self._index[key] = idx
            self.nodes.append(key)
            self.edges.append([])
        return idx

    def _link(self, a: int, b: int, kind: str, cost: float) -> None:
        if a != b:
            self.edges[a].append(_Edge(b, kind, cost))

    def _move(self, i: int, xa: float, j: int, xb: float, kind: str,
              dx: float, rise: float) -> bool:
        """Add a jump-type edge if ``kind``'s reach covers (dx, rise)."""
        fit = self.reach.fits(kind, dx, max(0.0, rise))
        if fit is None:
            return False
        cost = COSTS[kind] * (1.0 if fit else EXPLORE_PENALTY)
        self._link(self._node(i, xa), self._node(j, xb), kind, cost)
        return True

    def _build(self) -> None:
        plats = self.platforms
        for i, p in enumerate(plats):
            self._node(i, p.x0)
            self._node(i, p.x1)
        for i, p in enumerate(plats):
            for j, q in enumerate(plats):
                if i != j:
                    self._link_stacked(i, p, j, q)
            for end, step, open_ in ((p.x0, -1.0, p.open0), (p.x1, 1.0, p.open1)):
                if open_:
                    self._link_off_end(i, end, step)
        for i, p in enumerate(plats):
            self._link_rope_tiers(i, p)
        for i in range(len(plats)):
            pts = sorted(
                (x, n) for n, (pi, x) in enumerate(self.nodes) if pi == i
            )
            for (xa, na), (xb, nb) in zip(pts, pts[1:]):
                cost = (xb - xa) / self.walk_speed
                self._link(na, nb, "walk", cost)
                self._link(nb, na, "walk", cost)

    def _link_stacked(self, i: int, p: Platform, j: int, q: Platform) -> None:
        lo, hi = max(p.x0, q.x0), min(p.x1, q.x1)
        if lo > hi:
            return
        inset = min(self.edge_inset_px, (hi - lo) / 4.0)
        for x in {lo + inset, hi - inset, (lo + hi) / 2.0}:
            yp = p.y_at(x)
            if self.below(x, yp, exclude=i) == j:
                self._link(self._node(i, x), self._node(j, x),
                           "down_jump", COSTS["down_jump"])
            elif self.above(x, yp, exclude=i) == j:
                rise = yp - q.y_at(x)
                self._move(i, x, j, x, "up_flash", 0.0, rise)

    def _link_rope_tiers(self, i: int, p: Platform) -> None:
        """Rope lift grabs the highest platform within ``rope_max_px``."""
        for n, (pi, x) in enumerate(self.nodes):
            if pi != i:
                continue
            target = self.highest_above(x, p.y_at(x), self.rope_max_px, exclude=i)
            if target is None:
                continue
            j, rise = target
            self._move(i, x, j, x, "rope_lift", 0.0, rise)

    def _link_off_end(self, i: int, end: float, step: float) -> None:
        p = self.platforms[i]
        ey = p.y_at(end)
        src = self._node(i, end)
        land = self.below(end + 2.0 * step, ey, exclude=i)
        if land is not None:
            self._link(src, self._node(land, end + 2.0 * step), "drop", COSTS["drop"])
        takeoff = end - step * min(self.takeoff_inset_px, (p.x1 - p.x0) / 4.0)
        for j, q in enumerate(self.platforms):
            if j == i:
                continue
            near = q.x0 if step > 0 else q.x1
            if (near - end) * step <= 0:
                continue
            land_x = near + step * min(self.edge_inset_px, (q.x1 - q.x0) / 4.0)
            dx = abs(land_x - takeoff)
            rise = ey - q.y_at(near)
            if rise <= LEVEL_PX:
                for kind in ("jump", "flash", "double_flash"):
                    self._move(i, takeoff, j, land_x, kind, dx, rise)
            else:
                self._move(i, takeoff, j, land_x, "up_side_flash", dx, rise)
                self._move(i, takeoff, j, land_x, "up_flash", dx, rise)

    # -- Queries ------------------------------------------------------------------
    def point(self, node: int) -> Tuple[float, float]:
        plat, x = self.nodes[node]
        return x, self.platforms[plat].y_at(x)

    def transfer_legs(self) -> List[Leg]:
        """Every non-walk edge — for the dashboard overlay."""
        return [
            Leg(e.kind, *self.point(a), *self.point(e.to), e.cost)
            for a, edges in enumerate(self.edges)
            for e in edges if e.kind != "walk"
        ]

    def route(
        self,
        start: Tuple[float, float],
        goal: Tuple[float, float],
        *,
        jitter: float = 0.0,
        rng: Optional[random.Random] = None,
        exclude: Collection[str] = (),
    ) -> Optional[List[Leg]]:
        """Cheapest legs from ``start`` to ``goal`` avoiding ``exclude``d
        move kinds; None when either point is off the drawn platforms or
        no route exists."""
        sp, gp = self.locate(*start), self.locate(*goal)
        if sp is None or gp is None:
            return None
        rng = rng or random
        n = len(self.nodes)
        s, g = n, n + 1
        extra: Dict[int, List[_Edge]] = {s: [], g: []}
        on_plat = lambda plat: [(x, k) for k, (pi, x) in enumerate(self.nodes) if pi == plat]
        for x, k in on_plat(sp):
            extra[s].append(_Edge(k, "walk", abs(x - start[0]) / self.walk_speed))
        for x, k in on_plat(gp):
            extra.setdefault(k, []).append(
                _Edge(g, "walk", abs(x - goal[0]) / self.walk_speed)
            )
        if sp == gp:
            extra[s].append(_Edge(g, "walk", abs(goal[0] - start[0]) / self.walk_speed))

        def neighbours(u: int) -> Sequence[_Edge]:
            base = self.edges[u] if u < n else []
            return [e for e in list(base) + extra.get(u, []) if e.kind not in exclude]

        dist = {s: 0.0}
        prev: Dict[int, Tuple[int, str, float]] = {}
        heap = [(0.0, s)]
        while heap:
            d, u = heapq.heappop(heap)
            if u == g:
                break
            if d > dist.get(u, float("inf")):
                continue
            for e in neighbours(u):
                c = e.cost * (1.0 + rng.uniform(-jitter, jitter)) if jitter else e.cost
                nd = d + max(c, 1e-6)
                if nd < dist.get(e.to, float("inf")):
                    dist[e.to] = nd
                    prev[e.to] = (u, e.kind, e.cost)
                    heapq.heappush(heap, (nd, e.to))
        if g not in prev:
            return None

        def pos(u: int) -> Tuple[float, float]:
            # Endpoints keep their given y — anchors float above the row
            # (like the player icon), so plans connect anchors, not lines.
            if u == s:
                return start
            if u == g:
                return goal
            return self.point(u)

        legs: List[Leg] = []
        u = g
        while u != s:
            p, kind, cost = prev[u]
            legs.append(Leg(kind, *pos(p), *pos(u), cost))
            u = p
        legs.reverse()
        return _merge_walks(legs)

    def route_cost(self, start, goal, **kw) -> float:
        legs = self.route(start, goal, **kw)
        return float("inf") if legs is None else sum(l.cost for l in legs)


def _merge_walks(legs: List[Leg]) -> List[Leg]:
    out: List[Leg] = []
    for leg in legs:
        if abs(leg.x1 - leg.x0) < 0.5 and abs(leg.y1 - leg.y0) < 0.5:
            continue
        if out and leg.kind == "walk" and out[-1].kind == "walk":
            last = out.pop()
            leg = Leg("walk", last.x0, last.y0, leg.x1, leg.y1, last.cost + leg.cost)
        out.append(leg)
    return out


def wall_bounds(walls: Optional[dict], w: float, h: float, pad: float) -> Optional[Bounds]:
    """Padded walkable bounds from a map's normalized ``walls``: the left
    zone grows ``pad`` px rightward, the right zone leftward, the floor
    zone upward."""
    if not walls:
        return None
    left, right, floor = walls.get("left"), walls.get("right"), walls.get("floor")
    return Bounds(
        None if left is None else left * w + pad,
        None if right is None else right * w - pad,
        None if floor is None else floor * h - pad,
    )


def graph_for(entry, region, reach: ReachModel, pad: float = 0.0) -> Optional[NavGraph]:
    """NavGraph from a map entry's normalized platforms in ``region`` px,
    clipped to its padded wall zones."""
    if entry is None or not entry.platforms or not region:
        return None
    w, h = region[2], region[3]
    segs = [(s[0] * w, s[1] * h, s[2] * w, s[3] * h) for s in entry.platforms]
    return NavGraph(segs, reach, bounds=wall_bounds(entry.walls, w, h, pad))


class GraphCache:
    """One NavGraph per (map, platforms, region size, reach estimates)."""

    def __init__(self) -> None:
        self._key = None
        self._graph: Optional[NavGraph] = None

    def get(self, entry, region, reach: ReachModel, pad: float = 0.0) -> Optional[NavGraph]:
        if entry is None or not entry.platforms or not region:
            return None
        key = (
            entry.name, tuple(tuple(s) for s in entry.platforms),
            tuple(sorted((entry.walls or {}).items())), pad,
            region[2], region[3], reach.snapshot(),
        )
        if key != self._key:
            self._key, self._graph = key, graph_for(entry, region, reach, pad)
        return self._graph


__all__ = [
    "Bounds", "COSTS", "GraphCache", "Leg", "NavGraph", "Platform",
    "graph_for", "wall_bounds",
]
