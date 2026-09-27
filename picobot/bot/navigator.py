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

from .navgraph import Leg, NavGraph
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
        exclude = () if bot.rope_lift_remaining() <= 1.0 else ("rope_lift",)
        legs = self.graph.route(pos, goal, jitter=self.jitter, rng=rng, exclude=exclude)
        if legs is None:
            return "noroute"
        bot.viz["route"] = [(l.kind, l.x0, l.y0, l.x1, l.y1) for l in legs]
        if not self._leg(legs[0]):
            return "failed"
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
    def _leg(self, leg: Leg) -> bool:
        bot = self.bot
        if not bot.should_continue() or not bot.is_window_focused():
            return False
        if leg.kind == "walk":
            return self._walk_to(leg.x1)
        if not self._walk_to(leg.x0, exact=True):
            return False
        want = self.graph.locate(leg.x1, leg.y1)
        start = self._settle(quick=True)
        direction = "right" if leg.x1 > leg.x0 else "left"
        kind = leg.kind
        if kind == "rope_lift":
            if not self._rope_lift():
                return False
        elif kind == "up_flash":
            self.bot._up_flash(direction if abs(leg.x1 - leg.x0) > 2 else None)
        elif kind == "up_side_flash":
            bot._up_side_flash(direction)
        elif kind == "down_jump":
            bot.down_jump()
        elif kind == "drop":
            self._hold_until(direction, lambda p: p[1] > leg.y0 + self.graph.snap_px, 1.5)
        elif kind in ("jump", "flash", "double_flash"):
            self._gap_jump(leg, direction)
        pos = self._settle()
        ok = pos is not None and self.graph.locate(*pos) == want
        if kind in MOVES and start is not None and pos is not None:
            self.graph.reach.observe(
                kind,
                planned=(abs(leg.x1 - leg.x0), leg.y0 - leg.y1),
                observed=(abs(pos[0] - start[0]), start[1] - pos[1]),
                ok=ok,
            )
        return ok

    def _walk_to(self, x: float, exact: bool = False) -> bool:
        pos = self._pos()
        if pos is None:
            return False
        tol = self.align_px if exact else self.bot.config.nav_threshold_px
        if abs(pos[0] - x) <= tol:
            return True
        return bool(self.bot.move_to_point(
            int(round(x)), pos[1], threshold=int(tol), style="mixed",
        ))

    def _rope_lift(self) -> bool:
        """Rope lift, waiting out a short remaining cooldown."""
        bot = self.bot
        wait = bot.rope_lift_remaining()
        if wait > 1.5:
            return False
        if wait > 0 and bot.sleep(wait + 0.05):
            return False
        return bool(bot.rope_lift())

    def _hold_until(self, direction: str, done, timeout: float, action=None) -> None:
        bot = self.bot
        bot.hid.key_down(direction)
        try:
            if action is not None:
                action()
            end = time.time() + timeout
            while time.time() < end and bot.should_continue():
                pos = self._pos()
                if pos is not None and done(pos):
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
            direction, lambda p: (p[0] - leg.x1) * sign >= 0, 1.5, action=action
        )

    # -- Perception ------------------------------------------------------------------
    def _pos(self) -> Optional[Point]:
        img = self.bot.minimap_frame()
        return self.bot.minimap.player_pos(img) if img is not None else None

    def _settle(self, quick: bool = False, timeout: float = 1.5) -> Optional[Point]:
        """Position once the player stops moving (landed), or the last
        seen position at timeout."""
        last = self._pos()
        if quick:
            return last
        end = time.time() + timeout
        while time.time() < end:
            if self.bot.sleep(0.12):
                return last
            pos = self._pos()
            if pos is not None and pos == last:
                return pos
            last = pos if pos is not None else last
        return last


__all__ = ["Navigator"]
