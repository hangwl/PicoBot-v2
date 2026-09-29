"""Execute NavGraph routes with the bot's movement primitives.

:meth:`Navigator.step` plans from the player's *actual* position and
performs one leg, so a missed landing is corrected on the next step
instead of blindly continuing a stale route. Every jump-type move reports
its takeoff/landing to the :class:`ReachModel`, which is how move reach
is learned.
"""

from __future__ import annotations

import random
import time
from typing import Optional, Tuple

from .navgraph import DRIFT_REACH, Leg, NavGraph
from .reach import MOVES

Point = Tuple[float, float]


class Navigator:
    def __init__(
        self,
        bot,
        graph: NavGraph,
        *,
        jitter: float = 0.15,
        rng: Optional[random.Random] = None,
        align_px: float = 3.0,
    ) -> None:
        self.bot = bot
        self.graph = graph
        self.jitter = jitter
        self.rng = rng or random.Random()
        self.align_px = align_px
        self._seed = self.rng.random()

    # -- Entry points ---------------------------------------------------------------
    def execute_leg(self, leg: Leg) -> str:
        """Perform one planned leg (the patrol's fixed plan calls this)."""
        return self._leg(leg)

    def execute_walk(self, x: float) -> bool:
        """Walk to ``x`` on the current platform (planned walk leg)."""
        return self._walk_to(x)

    def step(self, goal: Point) -> str:
        """One leg toward ``goal``: ``arrived`` | ``moved`` | ``failed`` |
        ``noroute`` | ``lost`` (no player position)."""
        bot = self.bot
        pos = self._pos()
        if pos is None:
            return "lost"
        goal_plat = self.graph.locate(*goal)
        if self._arrived(pos, goal, goal_plat):
            return "arrived"
        # Same jitter draws for the whole segment — re-planning each step
        # must not flip between near-equal alternatives.
        rng = random.Random(hash((round(goal[0]), round(goal[1]), self._seed)))
        # Rope lift / teleport only when ready right now — never wait.
        exclude = tuple(
            k for k, remaining in (
                ("rope_lift", bot.rope_lift_remaining()),
                ("teleport", bot.teleport_remaining()),
            ) if remaining > 0
        )
        legs = self.graph.route(pos, goal, jitter=self.jitter, rng=rng, exclude=exclude)
        if legs is None:
            return "noroute"
        bot.viz["route"] = [(l.kind, l.x0, l.y0, l.x1, l.y1) for l in legs]
        # Perform the first leg that needs doing — a walk already within
        # tolerance would be a no-op and the next step would re-plan the
        # identical route forever.
        tol = bot.config.nav_threshold_px
        leg = next(
            (l for l in legs if l.kind != "walk" or abs(pos[0] - l.x1) > tol),
            None,
        )
        if leg is None:
            return "moved"
        res = self._leg(leg)
        if res != "ok":
            return res
        pos = self._pos()
        return "arrived" if pos is not None and self._arrived(pos, goal, goal_plat) else "moved"

    def go(self, goal: Point, max_failures: int = 3, max_steps: int = 40) -> bool:
        """Step until ``goal`` is reached; False on no route, repeated
        failures, or an unsafe condition."""
        bot = self.bot
        failures = 0
        for _ in range(max_steps):
            if not bot.should_continue() or bot.unsafe_reason() is not None:
                return False
            status = self.step(goal)
            if status == "arrived":
                return True
            if status in ("noroute", "lost"):
                bot.log(f"Nav: {status} toward {goal}")
                return False
            if status == "cooldown":
                continue          # rope lift started cooling — re-route
            if status == "failed":
                failures += 1
                bot.log(f"Nav: missed a landing — replanning ({failures})")
                if failures > max_failures:
                    bot.log("Nav: giving up after replans")
                    return False
        return False

    def _arrived(self, pos: Point, goal: Point, goal_plat) -> bool:
        tol = self.bot.config.nav_threshold_px
        return (
            abs(pos[0] - goal[0]) <= tol
            and self.graph.locate(*pos) == goal_plat
        )

    # -- Legs -------------------------------------------------------------------------
    def _leg(self, leg: Leg) -> str:
        """``ok`` | ``failed`` | ``cooldown`` (rope lift started cooling
        during the approach — re-route, don't count a failure)."""
        bot = self.bot
        if not bot.should_continue() or not bot.is_window_focused():
            return "failed"
        if leg.kind == "walk":
            return "ok" if self._walk_to(leg.x1) else "failed"
        # Rope lift fires from ~wherever the character stands (mid-air
        # works too) — no precise stop needed, and stopping precisely is
        # what caused flash ping-pong over the takeoff point.
        fire_tol = bot.config.walk_band_px if leg.kind == "rope_lift" else None
        if not self._walk_to(leg.x0, exact=fire_tol is None, tol=fire_tol):
            return "failed"
        if leg.kind == "rope_lift" and bot.rope_lift_remaining() > 0:
            return "cooldown"
        want = self.graph.locate(leg.x1, leg.y1)
        start = self._settle(quick=True)
        direction = "right" if leg.x1 > leg.x0 else "left"
        kind = leg.kind
        if kind == "rope_lift":
            if not bot.rope_lift():
                return "failed"
        elif kind == "up_flash":
            self.bot._up_flash(direction if abs(leg.x1 - leg.x0) > 2 else None)
        elif kind == "up_side_flash":
            bot._up_side_flash(direction)
        elif kind == "down_jump":
            bot.down_jump()
        elif kind == "climb_up":
            # A moving grab: hop (or flash, from a platform end further
            # out) toward the rope with Up held — not a standing jump.
            gap = abs(leg.x1 - leg.x0)
            direction = None
            if gap > 2:
                direction = "right" if leg.x1 > leg.x0 else "left"
            if not bot.rope_up(leg.y1, direction=direction, flash=gap > DRIFT_REACH):
                self._rope_fallback(leg)
                return "failed"
        elif kind == "climb_down":
            if not bot.climb("down", leg.y1, x=leg.x0):
                self._rope_fallback(leg)
                return "failed"
        elif kind == "teleport":
            if bot.teleport_remaining() > 0:
                return "cooldown"
            bot.teleport(direction)
        elif kind == "drop":
            self._hold_until(
                direction, lambda p: p[1] > leg.y0 + self.graph.snap_px, 1.5,
                min_progress=4.0,
            )
        elif kind in ("jump", "flash", "double_flash"):
            self._gap_jump(leg, direction)
        pos = self._settle()
        ok = pos is not None and self._on_platform(pos, want)
        if kind in MOVES and start is not None and pos is not None:
            self.graph.reach.observe(
                kind,
                planned=(abs(leg.x1 - leg.x0), leg.y0 - leg.y1),
                observed=(abs(pos[0] - start[0]), start[1] - pos[1]),
                ok=ok,
            )
        return "ok" if ok else "failed"

    def _rope_fallback(self, leg: Leg) -> None:
        """Mid-rope exit after a failed climb: hold a direction and jump —
        there is no direct release from a rope — toward a platform it can
        land on."""
        pos = self._pos() or (leg.x1, (leg.y0 + leg.y1) / 2.0)
        self.bot.rope_exit(self.graph.exit_direction(pos[0], pos[1]))

    def _on_platform(self, pos: Point, want) -> bool:
        """Tolerant landing check: the drawn row may sit a few px off the
        real platform, so accept the platform's x-span with row slack —
        a successful move must not shrink the reach model."""
        if want is None:
            return False
        if self.graph.locate(*pos) == want:
            return True
        p = self.graph.platforms[want]
        return (
            p.spans(pos[0], slack=2.0)
            and abs(p.y_at(pos[0]) - pos[1]) <= self.graph.snap_px + 2
        )

    def _walk_to(self, x: float, exact: bool = False,
                 tol: Optional[float] = None) -> bool:
        pos = self._pos()
        if pos is None:
            return False
        if tol is None:
            tol = self.align_px if exact else self.bot.config.nav_threshold_px
        if abs(pos[0] - x) <= tol:
            return True
        return bool(self.bot.move_to_point(
            int(round(x)), pos[1], threshold=int(tol), style="mixed", flat=True,
        ))

    def _hold_until(
        self, direction: str, done, timeout: float, action=None,
        *, min_progress: float = 0.0,
    ) -> None:
        """Hold ``direction`` until ``done``. With ``min_progress``, give
        up early when the character hasn't moved that far within 0.5s —
        a flash that never triggered must not walk the player off the
        takeoff edge."""
        bot = self.bot
        bot.hid.key_down(direction)
        try:
            if action is not None:
                action()
            t0 = time.time()
            end = t0 + timeout
            start_x = None
            while time.time() < end and bot.should_continue():
                pos = self._pos()
                if pos is not None:
                    if done(pos):
                        return
                    if start_x is None:
                        start_x = pos[0]
                    elif (min_progress and time.time() - t0 > 0.5
                          and abs(pos[0] - start_x) < min_progress):
                        return
                if bot.sleep(0.05):
                    return
        finally:
            bot.hid.key_up(direction)

    def _gap_jump(self, leg: Leg, direction: str) -> None:
        bot = self.bot
        sign = 1 if direction == "right" else -1
        action = {
            "jump": lambda: bot.hid.press(bot.config.jump_key),
            "flash": bot._flash_hop,
            "double_flash": bot._double_flash,
        }[leg.kind]
        self._hold_until(
            direction,
            lambda p: (p[0] - leg.x1) * sign >= 0
            or p[1] > leg.y0 + self.graph.snap_px,   # walked off the edge
            1.5, action=action, min_progress=4.0,
        )

    # -- Perception ------------------------------------------------------------------
    def _pos(self) -> Optional[Point]:
        img = self.bot.minimap_frame()
        pos = self.bot.minimap.player_pos(img) if img is not None else None
        note = getattr(self.bot, "_note_pos", None)
        if note is not None and pos is not None:
            note(pos)
        return pos

    def _settle(self, quick: bool = False, timeout: float = 0.7) -> Optional[Point]:
        """Position once the player dot is stable (within ~1.5px for two
        polls) — exact equality never settles when detection jitters."""
        last = self._pos()
        if quick or last is None:
            return last
        end = time.time() + timeout
        stable = 0
        while time.time() < end:
            if self.bot.sleep(0.1):
                return last
            pos = self._pos()
            if pos is None:
                continue
            if abs(pos[0] - last[0]) <= 1 and abs(pos[1] - last[1]) <= 1:
                stable += 1
                if stable >= 2:
                    return pos
            else:
                stable = 0
            last = pos
        return last


__all__ = ["Navigator"]
