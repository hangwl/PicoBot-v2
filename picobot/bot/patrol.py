"""Continuous patrol: a full traversal plan through every anchor.

The loop order is planned greedily by *route cost* (seconds over the
movement graph, jittered so loops vary), and the complete traversal is
published for the dashboard. Each tick performs one move toward the
current anchor — re-planned from the player's actual position — so the
bot always has a next position to move to. Anchors are pass-through
waypoints with a short randomized linger (``linger_hops`` weave hops).
"""

from __future__ import annotations

import random
import time
from typing import List, Optional, Tuple

from .navigator import Navigator

Point = Tuple[float, float]


class Patrol:
    def __init__(self, bot, rng: Optional[random.Random] = None) -> None:
        self.bot = bot
        self.rng = rng or random.Random()
        self.reset()

    def reset(self) -> None:
        self.order: List[int] = []
        self.linger = 0
        self.fails = 0
        self._nav: Optional[Navigator] = None

    # -- Tick -------------------------------------------------------------------------
    def tick(self) -> None:
        bot = self.bot
        rot = bot.effective_rotation()
        img = bot.minimap_frame()
        pos = bot.minimap.player_pos(img) if img is not None else None
        bot.viz["player"] = pos
        if pos is None:
            bot._attack_once()
            return
        graph = bot._nav_graph()
        if graph is None or graph.locate(*pos) is None:
            # No drawn platforms (or standing off them): straight-line patrol.
            bot._patrol_tick()
            return
        if self.linger > 0:
            self.linger -= 1
            bot._weave_attack()
            return
        anchors = [(bot._rx(a.x), bot._ry(a.y)) for a in rot.anchors]
        if not self.order:
            self._plan(graph, pos, anchors)
            if not self.order:
                bot._weave_attack()
                return
        idx = self.order[0]
        goal = anchors[idx]
        if self._nav is None or self._nav.graph is not graph:
            self._nav = Navigator(bot, graph, rng=self.rng)
        recorded = rot.legs.get((bot._anchor_idx, idx))
        if recorded is not None:
            status = "arrived" if bot._run_leg(recorded) else "failed"
        else:
            status = self._nav.step(goal)
        if status == "arrived":
            self._arrive(idx, rot.anchors[idx])
        elif status == "noroute":
            self._ban(idx, "no route")
        elif status == "failed":
            self.fails += 1
            bot.log(f"Patrol: missed a landing toward {rot.anchors[idx].name} ({self.fails})")
            if self.fails >= 3:
                self._ban(idx, "unreachable after retries")

    # -- Planning ----------------------------------------------------------------------
    def _plan(self, graph, pos: Point, anchors: List[Point]) -> None:
        bot = self.bot
        now = time.time()
        bot._ckpt_ban = {i: t for i, t in bot._ckpt_ban.items() if t > now}
        tol = bot.config.nav_threshold_px
        here = graph.locate(*pos)
        remaining = [
            i for i, (ax, ay) in enumerate(anchors)
            if i not in bot._ckpt_ban
            and not (abs(ax - pos[0]) <= tol and graph.locate(ax, ay) == here)
        ]
        order, legs, cur = [], [], pos
        while remaining:
            costs = {
                i: graph.route_cost(cur, anchors[i]) * (1 + self.rng.uniform(-0.2, 0.2))
                for i in remaining
            }
            nxt = min(remaining, key=costs.get)
            if costs[nxt] == float("inf"):
                names = ", ".join(bot.effective_rotation().anchors[i].name for i in remaining)
                bot.log(f"Patrol: unreachable from here — {names}")
                break
            legs += graph.route(cur, anchors[nxt]) or []
            order.append(nxt)
            remaining.remove(nxt)
            cur = anchors[nxt]
        self.order = order
        self.fails = 0
        bot.viz["plan"] = [(l.kind, l.x0, l.y0, l.x1, l.y1) for l in legs]
        if order:
            rot = bot.effective_rotation()
            bot.log("Patrol plan: " + " → ".join(rot.anchors[i].name for i in order))

    # -- Bookkeeping ---------------------------------------------------------------------
    def _arrive(self, idx: int, anchor) -> None:
        bot = self.bot
        now = time.time()
        self.order.pop(0)
        self.fails = 0
        bot._anchor_idx = idx
        bot._weave_dir = None
        bot._weave_bounds = None
        bot._arrive_pending = bot._arrival_skills(anchor, now)
        if anchor.face:
            bot.hid.press(anchor.face)
        lo, hi = bot.config.linger_hops
        self.linger = self.rng.randint(lo, hi)
        bot.viz["route"] = None
        bot.log(f"Checkpoint: {anchor.name}")

    def _ban(self, idx: int, why: str) -> None:
        bot = self.bot
        name = bot.effective_rotation().anchors[idx].name
        bot.log(f"Patrol: skipping {name} for a while ({why})")
        bot._ckpt_ban[idx] = time.time() + 30.0
        if self.order and self.order[0] == idx:
            self.order.pop(0)
        self.fails = 0


__all__ = ["Patrol"]
