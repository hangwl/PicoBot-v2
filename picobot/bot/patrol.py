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

from .navgraph import Leg
from .timing import human_reaction
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
        self._stuck_since: Optional[float] = None
        self._stuck_pos: Optional[Point] = None

    # -- Tick -------------------------------------------------------------------------
    def tick(self) -> None:
        bot = self.bot
        img = bot.minimap_frame()
        pos = bot.minimap.player_pos(img) if img is not None else None
        bot.viz["player"] = pos
        note = getattr(bot, "_note_pos", None)
        if note is not None and pos is not None:
            note(pos)
        if pos is None:
            # Blind: never learn or leap on a guess (the dot may be under
            # UI, or the map loading) — wait until it is seen again.
            bot._blind_wait()
            return
        graph = bot._nav_graph()
        if graph is None:
            # No drawn platforms: straight-line patrol.
            bot._patrol_tick()
            return
        if graph.locate(*pos) is None:
            # Platforms exist but the player isn't on any of them: either
            # mid-move or hanging on an (undrawn) game rope. Planning from
            # an off-graph start would ban every anchor.
            now = time.time()
            if now - getattr(self, "_off_graph_logged", 0.0) > 5.0:
                self._off_graph_logged = now
                bot.log(
                    "Player is not on any drawn platform — waiting "
                    "(mid-move, or check the platform drawing)"
                )
            if self._stuck_pos is None or abs(pos[0] - self._stuck_pos[0]) > 3 \
                    or abs(pos[1] - self._stuck_pos[1]) > 3:
                self._stuck_since = now
                self._stuck_pos = pos
            elif now - self._stuck_since > 2.0:
                # Stable and off-graph: hanging on a game rope, or standing
                # on ground that is not drawn. Down tells them apart.
                self._stuck_since = now
                on_rope = bot.probe_rope()
                if on_rope:
                    self._learn_rope(graph, pos)
                    bot.rope_exit(self._exit_direction(graph, pos))
                elif on_rope is False:
                    bot.log(
                        f"Standing off the drawn platforms at "
                        f"({pos[0]:.0f}, {pos[1]:.0f}) — not a rope; draw "
                        "the platform there. Hopping back."
                    )
                    bot.rope_exit(self._exit_direction(graph, pos))
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
            # Noticing the missed landing takes a moment before re-routing.
            bot.sleep(human_reaction())
            if self.fails >= 3:
                self._ban(graph, pos, idx, "unreachable after retries")
            else:
                self._splice(graph, pos, idx)

    def _learn_rope(self, graph, pos: Point) -> None:
        """Record a confirmed rope hang, connected to the platform above.
        Hangs on the same rope (same column) extend one segment instead
        of adding another. Learned ropes are graph edges like drawn ones
        — and ``rope_penalty`` keeps them a last resort."""
        bot = self.bot
        entry = bot._current_map_entry()
        if entry is None:
            return
        above = graph.above(pos[0], pos[1])
        if above is None:
            return
        p = graph.platforms[above]
        region = bot.minimap.region
        if not region:
            return
        w, h = region[2], region[3]
        top = p.y_at(min(p.x1, max(p.x0, pos[0])))
        bottom = pos[1]
        ropes = list(entry.ropes or [])
        for i, r in enumerate(ropes):
            if abs((r[0] + r[2]) / 2 * w - pos[0]) >= 5:
                continue
            lo, hi = max(r[1], r[3]) * h, min(r[1], r[3]) * h
            nb, nt = max(lo, bottom), min(hi, top)
            if nb <= lo + 0.5 and nt >= hi - 0.5:
                return                          # nothing new
            ropes[i] = [r[0], round(nb / h, 4), r[2], round(nt / h, 4)]
            entry.ropes = ropes
            bot.maps.save(entry)
            bot.log(f"Extended the rope at x {pos[0]:.0f} "
                    f"(y {nt:.0f}-{nb:.0f})")
            return
        entry.ropes = ropes + [[
            round(pos[0] / w, 4), round(bottom / h, 4),
            round(pos[0] / w, 4), round(top / h, 4),
        ]]
        bot.maps.save(entry)
        bot.log(f"Learned a rope at ({pos[0]:.0f}, {pos[1]:.0f}) — "
                "climbs there are a last resort")
        bot.event("map", f"rope learned at ({pos[0]:.0f}, {pos[1]:.0f})")

    def _exit_direction(self, graph, pos: Point) -> str:
        """Which way to leap off a rope (or hop off undrawn ground): toward
        the nearest platform it can land on — at or below the player,
        never one above. Straight over a platform, toward its middle."""
        x, y = pos
        best = None
        for p in graph.platforms:
            near_x = min(p.x1, max(p.x0, x))
            row = p.y_at(near_x)
            if row < y - 4:
                continue                        # above: a leap cannot reach it
            gap = abs(near_x - x)
            if gap == 0:
                d = "right" if (p.x0 + p.x1) / 2 >= x else "left"
            else:
                d = "right" if near_x > x else "left"
            key = (gap, row - y)
            if best is None or key < best[0]:
                best = (key, d)
        if best is not None:
            return best[1]
        near = min(
            graph.platforms,
            key=lambda p: min(abs(p.x0 - x), abs(p.x1 - x)),
        )
        cx = min((abs(near.x0 - x), near.x0), (abs(near.x1 - x), near.x1))[1]
        return "right" if cx > x else "left"

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
            costs = {i: graph.route_cost(cur, anchors[i]) for i in remaining}
            # Unreachable anchors can't be picked by either policy — ban
            # them up front so the loop always makes progress.
            for i in [i for i in remaining if costs[i] == float("inf")]:
                why = (
                    "not on a drawn platform — re-place it"
                    if graph.locate(*anchors[i]) is None
                    else "no route from here"
                )
                bot.log(f"Patrol: skipping {rot.anchors[i].name} for a while ({why})")
                bot._ckpt_ban[i] = now + 30.0
                remaining.remove(i)
            if not remaining:
                break
            nxt = self._pick_next(costs, remaining)
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

    def _pick_next(self, costs: dict, remaining: list) -> int:
        """Loop-order policy: ``weighted`` roulette (P ∝ 1/cost^temp — far
        anchors stay in the draw instead of being neglected) or ``greedy``
        cheapest-next with ±20% cost jitter for variety."""
        cfg = self.bot.config
        if getattr(cfg, "patrol_policy", "weighted") == "greedy":
            jittered = {
                i: costs[i] * (1 + self.rng.uniform(-0.2, 0.2))
                for i in remaining
            }
            return min(remaining, key=jittered.get)
        temp = max(0.05, getattr(cfg, "patrol_weight_temp", 1.0))
        weights = {i: 1.0 / (max(costs[i], 0.5) ** temp) for i in remaining}
        total = sum(weights.values())
        r = self.rng.random() * total
        acc = 0.0
        for i, w in weights.items():
            acc += w
            if r <= acc:
                return i
        return next(iter(weights))

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
