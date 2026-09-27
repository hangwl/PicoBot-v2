"""Execute NavGraph routes with the bot's movement primitives.

Each transfer (up/down jump, drop, gap jump) is verified by where the
player actually lands; a miss replans from the real position instead of
blindly continuing the old route.
"""

from __future__ import annotations

import random
import time
from typing import Optional, Tuple

from .navgraph import Leg, NavGraph

Point = Tuple[float, float]


class Navigator:
    def __init__(
        self,
        bot,
        graph: NavGraph,
        *,
        jitter: float = 0.15,
        max_replans: int = 3,
        rng: Optional[random.Random] = None,
        align_px: float = 3.0,
    ) -> None:
        self.bot = bot
        self.graph = graph
        self.jitter = jitter
        self.max_replans = max_replans
        self.rng = rng or random.Random()
        self.align_px = align_px

    # -- Entry ----------------------------------------------------------------------
    def go(self, goal: Point) -> bool:
        """Travel to ``goal`` (minimap px); True when standing there."""
        bot = self.bot
        goal_plat = self.graph.locate(*goal)
        for attempt in range(self.max_replans + 1):
            pos = self._settle()
            if pos is None:
                return False
            if self._arrived(pos, goal, goal_plat):
                return True
            legs = self.graph.route(pos, goal, jitter=self.jitter, rng=self.rng)
            if legs is None:
                bot.log(f"Nav: no route from {pos} to {goal}")
                return False
            bot.event("nav", "route: " + " → ".join(l.kind for l in legs))
            if attempt:
                bot.log(f"Nav: replanned from {pos} (attempt {attempt})")
            bot.viz["route"] = [(l.kind, l.x0, l.y0, l.x1, l.y1) for l in legs]
            if all(self._leg(leg) for leg in legs):
                pos = self._settle()
                if pos is not None and self._arrived(pos, goal, goal_plat):
                    return True
            if not bot.should_continue() or bot.unsafe_reason() is not None:
                return False
        bot.log("Nav: giving up after replans")
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
        if leg.kind == "up_jump":
            self._up_jump()
        elif leg.kind == "down_jump":
            bot.down_jump()
        elif leg.kind == "drop":
            self._run_off(leg)
        elif leg.kind in ("jump", "flash"):
            self._gap_jump(leg)
        pos = self._settle()
        return pos is not None and self.graph.locate(*pos) == want

    def _walk_to(self, x: float, exact: bool = False) -> bool:
        pos = self._settle(quick=True)
        if pos is None:
            return False
        tol = self.align_px if exact else self.bot.config.nav_threshold_px
        if abs(pos[0] - x) <= tol:
            return True
        far = abs(pos[0] - x) > 30 and not exact
        style = "mixed" if far else "walk"
        return bool(self.bot.move_to_point(
            int(round(x)), pos[1], threshold=int(tol), style=style
        ))

    def _up_jump(self) -> None:
        """Up-jump, riding out a rope-lift cooldown instead of failing."""
        deadline = time.time() + 5.0
        while self.bot.up_jump() is False and time.time() < deadline:
            if self.bot.sleep(0.2):
                return

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

    def _run_off(self, leg: Leg) -> None:
        direction = "right" if leg.x1 > leg.x0 else "left"
        self._hold_until(
            direction, lambda p: p[1] > leg.y0 + self.graph.snap_px, 1.5
        )

    def _gap_jump(self, leg: Leg) -> None:
        bot = self.bot
        direction = "right" if leg.x1 > leg.x0 else "left"
        sign = 1 if direction == "right" else -1

        def jump():
            if leg.kind == "flash":
                bot._flash_hop()
            else:
                bot.hid.press(bot.config.jump_key)

        self._hold_until(
            direction, lambda p: (p[0] - leg.x1) * sign >= 0, 1.5, action=jump
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
