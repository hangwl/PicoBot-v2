"""Measure real move reach by performing each move a few times.

Run from the dashboard with the bot stopped: the host builds a throwaway
SmartBot (same HID wiring, shared :class:`ReachModel`), the measurer
executes each move type in the safest available direction and records
the observed takeoff→landing into the reach model, which persists to
``nav_reach_file``. Farming keeps refining from there.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional, Tuple

from ..vision.minimap import platform_span_at

logger = logging.getLogger(__name__)

Point = Tuple[float, float]


class MoveMeasurer:
    """Execute each move type a few times and grow the reach envelopes."""

    # Rope lift before the upward move: both need a platform above, and
    # each climb ends standing on the next tier.
    FLASH_PLAN = ("flash", "double_flash", "jump", "rope_lift", "up_flash")
    TELEPORT_PLAN = ("jump", "teleport", "rope_lift", "teleport_up")
    WALK_PLAN = ("jump", "rope_lift")
    VERTICAL = ("up_flash", "rope_lift", "teleport_up")
    ROOM = {"flash": 34.0, "double_flash": 64.0, "jump": 16.0, "teleport": 40.0}

    @classmethod
    def plan_for(cls, config) -> Tuple[str, ...]:
        """The moves this class's planner can use — only those are worth
        measuring (a mage never flash-jumps; its teleport matters)."""
        travel = getattr(config, "class_travel", "flash")
        if travel == "teleport" and getattr(config, "teleport_key", None):
            return cls.TELEPORT_PLAN
        if travel == "flash" and getattr(config, "flash_jump_enabled", True):
            return cls.FLASH_PLAN
        return cls.WALK_PLAN

    def __init__(self, bot, on_event: Optional[Callable[[str, str], None]] = None,
                 reps: int = 2) -> None:
        self.bot = bot
        self._emit = on_event or (lambda k, m: None)
        self.reps = reps
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # -- Lifecycle ------------------------------------------------------------------
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="MoveMeasurer", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        self._thread = None

    # -- Measurement ------------------------------------------------------------------
    def _loop(self) -> None:
        bot = self.bot
        try:
            bot._sync_map()                # resolve the map for the graph
            if bot._nav_graph() is None:
                self._emit(
                    "measure", "draw the platforms first — nothing to measure against"
                )
                return
            self._emit("measure", "measuring moves — keep the game focused")
            measured, skipped = [], {}
            for move in self.plan_for(bot.config):
                n = self.reps if move not in self.VERTICAL else 1
                for _ in range(n):
                    if self._stop.is_set() or not bot.is_window_focused():
                        self._emit("measure", "measurement stopped")
                        return
                    if self._measure(move):
                        measured.append(move)
                    else:
                        skipped[move] = skipped.get(move, 0) + 1
            bot.reach.save(force=True)
            summary = "done — reach saved. Measured: " + (
                ", ".join(sorted(set(measured))) or "nothing"
            )
            if skipped:
                summary += "; skipped: " + ", ".join(
                    f"{m} x{n}" for m, n in skipped.items()
                )
            self._emit("measure", summary)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("move measurement failed: %s", exc, exc_info=True)
            self._emit("error", f"measurement failed: {exc}")

    def _measure(self, move: str) -> bool:
        """One attempt; False when the move can't be attempted here."""
        bot = self.bot
        graph = bot._nav_graph()
        vertical = move in self.VERTICAL
        teleport = move in ("teleport", "teleport_up")
        if teleport and not self._teleport_ready(move):
            return False
        start = self._settle()
        if start is None:
            return False
        if vertical:
            if graph.above(start[0], start[1]) is None:
                self._emit("measure", f"{move}: skipped — no platform above the player")
                return False
            direction = None
        else:
            span = platform_span_at(bot._platform_segments_px(), *start)
            if span is None:
                self._emit(
                    "measure",
                    f"{move}: skipped — player not on a drawn platform",
                )
                return False
            room_r, room_l = span[1] - start[0], start[0] - span[0]
            if max(room_r, room_l) < self.ROOM[move]:
                self._emit(
                    "measure",
                    f"{move}: skipped — only {max(room_r, room_l):.0f}px of "
                    "platform room (needs "
                    f"{self.ROOM[move]:.0f})",
                )
                return False
            direction = "right" if room_r >= room_l else "left"
        if move == "rope_lift" and not bot.rope_lift():
            self._emit("measure", "rope_lift: skipped — skill key not bound")
            return False
        if move == "teleport_up":
            direction = "up"
        # teleport() holds its own direction around the key press.
        held = direction if not teleport else None
        if held:
            bot.hid.key_down(held)
        try:
            if teleport:
                if not bot.teleport(direction):
                    self._emit("measure", f"{move}: skipped — teleport not ready")
                    return False
            elif move == "flash":
                bot._flash_hop()
            elif move == "double_flash":
                bot._double_flash()
            elif move == "jump":
                bot.hid.press(bot.config.jump_key)
                bot.sleep(0.5)
            elif move == "up_flash":
                bot._up_flash(None)
        finally:
            if held:
                bot.hid.key_up(held)
        land = self._settle(timeout=1.2)
        if land is None or land == start:
            self._emit("measure", f"{move}: skipped — the character didn't move")
            return False
        dx = abs(land[0] - start[0])
        rise = start[1] - land[1]
        if not vertical and rise > 8:      # drifted off a ledge — unusable
            return False
        # Both teleport directions grow the one (dx, rise) envelope.
        bot.reach.observe(
            "teleport" if teleport else move, planned=(0.0, 0.0),
            observed=(0.0 if vertical else dx, rise if vertical else 0.0),
            ok=True,
        )
        self._emit(
            "measure",
            f"{move}: dx {dx:.0f}px rise {rise:.0f}px"
            + (f" ({direction})" if direction else ""),
        )
        return True

    def _teleport_ready(self, move: str) -> bool:
        """Wait out the teleport cooldown; False when no key is bound."""
        bot = self.bot
        wait = bot.teleport_remaining()
        if wait == float("inf"):
            self._emit("measure", f"{move}: skipped — teleport key not bound")
            return False
        if wait > 0 and bot.sleep(wait + 0.05):
            return False
        return True

    # -- Perception --------------------------------------------------------------------
    def _settle(self, timeout: float = 0.9) -> Optional[Point]:
        bot = self.bot
        last = bot.minimap.player_pos(bot.minimap_frame())
        if last is None:
            return None
        end = time.time() + timeout
        stable = 0
        while time.time() < end:
            if bot.sleep(0.1) or self._stop.is_set():
                return last
            pos = bot.minimap.player_pos(bot.minimap_frame())
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


__all__ = ["MoveMeasurer"]
