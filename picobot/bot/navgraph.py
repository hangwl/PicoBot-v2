"""Movement graph over hand-drawn platforms + route planning.

Nodes are points on platforms: segment ends plus the x-positions where a
move to another platform is possible. Edges:

- ``walk``      — along one platform between neighbouring points.
- ``down_jump`` — drop through a platform onto the next one below.
- ``up_jump``   — rise onto the next platform above (≤ ``up_px``).
- ``drop``      — walk off a platform's end onto what's below.
- ``jump``/``flash`` — hop across a horizontal gap to another platform's
  end (``flash`` when wider than a plain jump covers).

All coordinates are minimap px. Costs are rough seconds, so routes trade
walking distance against the time a jump takes. ``jitter`` perturbs edge
costs per query — near-equal routes vary like a player's would.
"""

from __future__ import annotations

import heapq
import random
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

Segment = Tuple[float, float, float, float]

COSTS = {"down_jump": 0.7, "up_jump": 1.1, "drop": 0.6, "jump": 0.6, "flash": 0.8}


@dataclass(frozen=True)
class Platform:
    x0: float
    y0: float
    x1: float
    y1: float

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


@dataclass(frozen=True)
class Leg:
    kind: str
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class _Edge:
    to: int
    kind: str
    cost: float


class NavGraph:
    def __init__(
        self,
        segments: Iterable[Segment],
        *,
        up_px: float = 20.0,
        jump_px: float = 8.0,
        gap_px: float = 25.0,
        rise_px: float = 4.0,
        walk_speed: float = 40.0,
        snap_px: float = 8.0,
        edge_inset_px: float = 4.0,
    ) -> None:
        self.edge_inset_px = edge_inset_px
        self.platforms: List[Platform] = [
            Platform.from_segment(s) for s in segments
            if abs(s[2] - s[0]) >= 1.0
        ]
        self.up_px = up_px
        self.jump_px = jump_px
        self.gap_px = gap_px
        self.rise_px = rise_px
        self.walk_speed = walk_speed
        self.snap_px = snap_px
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
        """Platform the point stands on (within ``snap_px``), or None."""
        best = None
        for i, p in enumerate(self.platforms):
            if not p.spans(x, slack=3.0):
                continue
            d = abs(p.y_at(x) - y)
            if d <= self.snap_px and (best is None or d < best[0]):
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

    def _build(self) -> None:
        plats = self.platforms
        for i, p in enumerate(plats):
            self._node(i, p.x0)
            self._node(i, p.x1)
        for i, p in enumerate(plats):
            for j, q in enumerate(plats):
                if i == j:
                    continue
                lo, hi = max(p.x0, q.x0), min(p.x1, q.x1)
                if lo <= hi:
                    inset = min(self.edge_inset_px, (hi - lo) / 4.0)
                    for x in {lo + inset, hi - inset, (lo + hi) / 2.0}:
                        if self.below(x, p.y_at(x), exclude=i) != j:
                            continue
                        self._link(self._node(i, x), self._node(j, x),
                                   "down_jump", COSTS["down_jump"])
                        if q.y_at(x) - p.y_at(x) <= self.up_px:
                            self._link(self._node(j, x), self._node(i, x),
                                       "up_jump", COSTS["up_jump"])
            for end, step in ((p.x0, -1.0), (p.x1, 1.0)):
                self._link_off_end(i, end, step)
        for i in range(len(plats)):
            pts = sorted(
                (x, n) for n, (pi, x) in enumerate(self.nodes) if pi == i
            )
            for (xa, na), (xb, nb) in zip(pts, pts[1:]):
                cost = (xb - xa) / self.walk_speed
                self._link(na, nb, "walk", cost)
                self._link(nb, na, "walk", cost)

    def _link_off_end(self, i: int, end: float, step: float) -> None:
        p = self.platforms[i]
        src = self._node(i, end)
        ey = p.y_at(end)
        land = self.below(end + 2.0 * step, ey, exclude=i)
        if land is not None:
            lx = end + 2.0 * step
            self._link(src, self._node(land, lx), "drop", COSTS["drop"])
        for j, q in enumerate(self.platforms):
            if j == i:
                continue
            near = q.x0 if step > 0 else q.x1
            gap = (near - end) * step
            rise = ey - q.y_at(near)
            if 0 < gap <= self.gap_px and rise <= self.rise_px:
                kind = "jump" if gap <= self.jump_px else "flash"
                self._link(src, self._node(j, near), kind, COSTS[kind])

    # -- Queries ------------------------------------------------------------------
    def point(self, node: int) -> Tuple[float, float]:
        plat, x = self.nodes[node]
        return x, self.platforms[plat].y_at(x)

    def transfer_legs(self) -> List[Leg]:
        """Every non-walk edge — for the dashboard overlay."""
        out = []
        for a, edges in enumerate(self.edges):
            for e in edges:
                if e.kind != "walk":
                    out.append(Leg(e.kind, *self.point(a), *self.point(e.to)))
        return out

    def route(
        self,
        start: Tuple[float, float],
        goal: Tuple[float, float],
        *,
        jitter: float = 0.0,
        rng: Optional[random.Random] = None,
    ) -> Optional[List[Leg]]:
        """Cheapest legs from ``start`` to ``goal``; None when either point
        is off the drawn platforms or no route exists."""
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
            return list(base) + extra.get(u, [])

        dist = {s: 0.0}
        prev: Dict[int, Tuple[int, str]] = {}
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
                    prev[e.to] = (u, e.kind)
                    heapq.heappush(heap, (nd, e.to))
        if g not in prev:
            return None

        def pos(u: int) -> Tuple[float, float]:
            if u == s:
                return (start[0], self.platforms[sp].y_at(start[0]))
            if u == g:
                return (goal[0], self.platforms[gp].y_at(goal[0]))
            return self.point(u)

        legs: List[Leg] = []
        u = g
        while u != s:
            p, kind = prev[u]
            legs.append(Leg(kind, *pos(p), *pos(u)))
            u = p
        legs.reverse()
        return _merge_walks(legs)


def _merge_walks(legs: List[Leg]) -> List[Leg]:
    out: List[Leg] = []
    for leg in legs:
        if abs(leg.x1 - leg.x0) < 0.5 and abs(leg.y1 - leg.y0) < 0.5:
            continue
        if out and leg.kind == "walk" and out[-1].kind == "walk":
            last = out.pop()
            leg = Leg("walk", last.x0, last.y0, leg.x1, leg.y1)
        out.append(leg)
    return out


class GraphCache:
    """One NavGraph per (map, platforms, region size, jump params)."""

    def __init__(self) -> None:
        self._key = None
        self._graph: Optional[NavGraph] = None

    def get(self, entry, region, config=None) -> Optional[NavGraph]:
        if entry is None or not entry.platforms or not region:
            return None
        key = (
            entry.name, tuple(tuple(s) for s in entry.platforms),
            region[2], region[3],
            None if config is None else (
                config.nav_up_px, config.nav_jump_px, config.nav_gap_px
            ),
        )
        if key != self._key:
            self._key, self._graph = key, graph_for(entry, region, config)
        return self._graph


def graph_for(entry, region, config=None) -> Optional[NavGraph]:
    """NavGraph from a map entry's normalized platforms in ``region`` px."""
    if entry is None or not entry.platforms or not region:
        return None
    w, h = region[2], region[3]
    segs = [(s[0] * w, s[1] * h, s[2] * w, s[3] * h) for s in entry.platforms]
    kw = {}
    if config is not None:
        kw = dict(
            up_px=config.nav_up_px, jump_px=config.nav_jump_px,
            gap_px=config.nav_gap_px,
        )
    return NavGraph(segs, **kw)


__all__ = ["COSTS", "GraphCache", "Leg", "NavGraph", "Platform", "graph_for"]
