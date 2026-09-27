"""Continuous patrol: strictly execute a pre-planned anchor loop.

The loop is a sequence of anchor-to-anchor segments over the movement
graph, ordered greedily by route cost (jittered so loops vary). The bot
follows the planned legs in order — one per tick, no re-planning between
legs. Only a failed leg splices a re-route from the player's actual
position; three misses ban the anchor. The next loop is planned before
the current one finishes, so a planned path always exists; when none
does, the bot halts and takes a break until planning succeeds. Anchors
are pure pass-through waypoints — no linger.
"""

from __future__ import annotations

import random
import time
from typing import List, Optional, Tuple

from .navgraph import Leg, wall_bounds
from .navigator import Navigator

Point = Tuple[float, float]


class Patrol:
    def __init__(self, bot, rng: Optional[random.Random] = None) -> None:
        self.bot = bot
        self.rng = rng or random.Random()
        self.reset()

    def reset(self) -> None:
        # Segments: (anchor_idx, legs | None for hand-authored, from_idx).
        self.plan: List[Tuple[int, Optional[List[Leg]], int]] = []
        self.seg = 0
        self.leg_i = 0
        self.fails = 0
        self._nav: Optional[Navigator] = None
        self._replan_at = 0.0
        self._break_logged = 0.0

    # -- Tick -------------------------------------------------------------------------
    def tick(self) -> None:
        bot = self.bot
        img = bot.minimap_frame()
        pos = bot.minimap.player_pos(img) if img is not None else None
        bot.viz["player"] = pos
        if pos is None:
            bot._blind_wait()
            return
        graph = bot._nav_graph()
        if graph is None:
            # No drawn platforms: straight-line patrol.
            bot._patrol_tick()
            return
        if graph.locate(*pos) is None:
            # Platforms exist but the player isn't on any of them (mid-move,
            # or a wall zone ate the platform under them). Planning from an
            # off-graph start would ban every anchor.
            now = time.time()
            reason = self._off_graph_reason(graph, pos)
            if now - getattr(self, "_off_graph_logged", 0.0) > 5.0:
                self._off_graph_logged = now
                bot.log(f"Player is not on any drawn platform — {reason}")
            if reason.startswith("inside"):
                # Walk back toward the nearest graph platform — a flash
                # overshoot can land inside a zone; waiting there stalls.
                near = min(
                    graph.platforms,
                    key=lambda p: min(abs(pos[0] - p.x0), abs(pos[0] - p.x1)),
                )
                tx = min((abs(pos[0] - near.x0), near.x0),
                         (abs(pos[0] - near.x1), near.x1))[1]
                bot.move_to_point(int(tx), pos[1], style="mixed", flat=True)
            else:
                bot._blind_wait()
            return
        if self._nav is None or self._nav.graph is not graph:
            self._nav = Navigator(bot, graph, rng=self.rng)
        if self.seg >= len(self.plan):
            if time.monotonic() >= self._replan_at:
                self._replan_at = time.monotonic() + 3.0
                self._plan_loop(graph, pos)
            if self.seg >= len(self.plan):
                self._break()
                return
        idx, legs, from_idx = self.plan[self.seg]
        rot = bot.effective_rotation()
        if legs is not None and self.leg_i >= len(legs):
            self._arrive(idx, rot.anchors[idx])
            self.seg += 1
            self.leg_i = 0
            if self.seg == len(self.plan) - 1:
                self._plan_next(graph)   # next loop ready before this one ends
            return
        if legs is None:                 # hand-authored leg
            recorded = rot.legs.get((from_idx, idx)) or []
            status = "ok" if bot._run_leg(recorded) else "failed"
            if status == "ok":
                self._arrive(idx, rot.anchors[idx])
                self.seg += 1
                if self.seg == len(self.plan) - 1:
                    self._plan_next(graph)
                return
        else:
            leg = legs[self.leg_i]
            if leg.kind == "walk":
                status = "ok" if self._nav.execute_walk(leg.x1) else "failed"
            else:
                status = self._nav.execute_leg(leg)
            if status == "ok":
                self.leg_i += 1
                self._publish()
                return
        if status == "cooldown":
            # Rope lift started cooling: re-route without it (up-flash
            # instead) — never wait, never count a failure.
            if not self._splice(graph, pos, idx):
                self._ban(graph, pos, idx, "no route")
        elif status == "noroute":
            self._ban(graph, pos, idx, "no route")
        elif status == "failed":
            self.fails += 1
            bot.log(
                f"Patrol: missed a landing toward {rot.anchors[idx].name}"
                f" ({self.fails})"
            )
            if self.fails >= 3:
                self._ban(graph, pos, idx, "unreachable after retries")
            else:
                self._splice(graph, pos, idx)

    def _off_graph_reason(self, graph, pos: Point) -> str:
        """Why the player's position isn't on the graph — name a covering
        wall/floor zone when there is one."""
        bot = self.bot
        entry = bot._current_map_entry()
        bounds = wall_bounds(
            entry.walls if entry else None,
            bot.minimap.region[2], bot.minimap.region[3],
            bot.config.wall_pad_px,
        ) if entry else None
        if bounds is not None:
            if bounds.left is not None and pos[0] < bounds.left:
                return "inside the left wall zone — walking back out"
            if bounds.right is not None and pos[0] > bounds.right:
                return "inside the right wall zone — walking back out"
        return "waiting — check the platform drawing (Panel view)"

    # -- Planning ----------------------------------------------------------------------
    def _greedy(self, graph, cur: Point, cur_i: Optional[int]):
        """Anchor-to-anchor segments by route cost, banning unreachable
        ones. Returns (segments, flattened legs)."""
        bot = self.bot
        rot = bot.effective_rotation()
        anchors = [(bot._rx(a.x), bot._ry(a.y)) for a in rot.anchors]
        now = time.time()
        bot._ckpt_ban = {i: t for i, t in bot._ckpt_ban.items() if t > now}
        tol = bot.config.nav_threshold_px
        here = graph.locate(*cur)
        remaining = [
            i for i, (ax, ay) in enumerate(anchors)
            if i not in bot._ckpt_ban
            and not (abs(ax - cur[0]) <= tol and graph.locate(ax, ay) == here)
        ]
        segments, flat = [], []
        while remaining:
            costs = {
                i: graph.route_cost(cur, anchors[i]) * (1 + self.rng.uniform(-0.2, 0.2))
                for i in remaining
            }
            nxt = min(remaining, key=costs.get)
            if costs[nxt] == float("inf"):
                why = (
                    "not on a drawn platform — re-place it"
                    if graph.locate(*anchors[nxt]) is None
                    else "no route from here"
                )
                bot.log(f"Patrol: skipping {rot.anchors[nxt].name} for a while ({why})")
                bot._ckpt_ban[nxt] = now + 30.0
                remaining.remove(nxt)
                continue
            recorded = rot.legs.get((cur_i, nxt)) if cur_i is not None else None
            if recorded is not None:
                segments.append((nxt, None, cur_i))
            else:
                legs = graph.route(cur, anchors[nxt]) or []
                segments.append((nxt, legs, -1))
                flat += legs
            remaining.remove(nxt)
            cur = anchors[nxt]
            cur_i = nxt
        return segments, flat

    def _plan_loop(self, graph, pos: Point) -> None:
        self.plan, _ = self._greedy(graph, pos, None)
        self.seg = 0
        self.leg_i = 0
        self.fails = 0
        self._publish()
        if self.plan:
            rot = self.bot.effective_rotation()
            self.bot.log(
                "Patrol plan: "
                + " → ".join(rot.anchors[i].name for i, _, _ in self.plan)
            )

    def _plan_next(self, graph) -> None:
        """Plan the next loop from the loop's final anchor — runs while the
        current loop still has a segment to go, so the plan never runs dry."""
        bot = self.bot
        rot = bot.effective_rotation()
        idx = self.plan[self.seg][0]
        cur = (bot._rx(rot.anchors[idx].x), bot._ry(rot.anchors[idx].y))
        segments, _ = self._greedy(graph, cur, idx)
        if segments:
            self.plan += segments
            self._publish()
            bot.log(
                "Next loop planned: "
                + " → ".join(rot.anchors[i].name for i, _, _ in segments)
            )

    def _splice(self, graph, pos: Point, idx: int) -> bool:
        """Re-route the current segment from the player's actual position."""
        bot = self.bot
        exclude = ("rope_lift",) if bot.rope_lift_remaining() > 0 else ()
        legs = graph.route(pos, self._anchor_pos(idx), exclude=exclude)
        if legs is None:
            return False
        self.plan[self.seg] = (idx, legs, -1)
        self.leg_i = 0
        self._publish()
        return True

    # -- Bookkeeping ---------------------------------------------------------------------
    def _anchor_pos(self, idx: int) -> Point:
        bot = self.bot
        a = bot.effective_rotation().anchors[idx]
        return (bot._rx(a.x), bot._ry(a.y))

    def _arrive(self, idx: int, anchor) -> None:
        bot = self.bot
        self.fails = 0
        bot._anchor_idx = idx
        bot._weave_dir = None
        bot._weave_bounds = None
        bot._arrive_pending = bot._arrival_skills(anchor, time.time())
        if anchor.face:
            bot.hid.press(anchor.face)
        bot.viz["route"] = None
        bot.log(f"Checkpoint: {anchor.name}")

    def _ban(self, graph, pos: Point, idx: int, why: str) -> None:
        bot = self.bot
        bot.log(f"Patrol: skipping {bot.effective_rotation().anchors[idx].name} for a while ({why})")
        bot._ckpt_ban[idx] = time.time() + 30.0
        if self.seg < len(self.plan) and self.plan[self.seg][0] == idx:
            self.plan.pop(self.seg)
            self.leg_i = 0
            self.fails = 0
            while self.seg < len(self.plan):
                nxt = self.plan[self.seg][0]
                if self._splice(graph, pos, nxt):
                    break
                bot._ckpt_ban[nxt] = time.time() + 30.0
                self.plan.pop(self.seg)
                bot.log(f"Patrol: skipping {bot.effective_rotation().anchors[nxt].name} for a while (no route)")

    def _break(self) -> None:
        """No planned path: halt all actions until planning succeeds."""
        bot = self.bot
        now = time.time()
        if now - self._break_logged > 5.0:
            self._break_logged = now
            bot.log("No planned path — taking a break")
        bot.sleep(2.0)

    def _publish(self) -> None:
        bot = self.bot
        if self.seg < len(self.plan):
            legs = self.plan[self.seg][1]
            if legs:
                bot.viz["route"] = [
                    (l.kind, l.x0, l.y0, l.x1, l.y1) for l in legs[self.leg_i:]
                ]
        plan = []
        for _, seg_legs, _ in self.plan[self.seg:]:
            if seg_legs:
                plan += [(l.kind, l.x0, l.y0, l.x1, l.y1) for l in seg_legs]
        bot.viz["plan"] = plan


__all__ = ["Patrol"]
