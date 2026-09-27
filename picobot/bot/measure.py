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

    # Rope lift before up_flash: both need a platform above, and each
    # climb ends standing on the next tier.
    PLAN = ("flash", "double_flash", "jump", "rope_lift", "up_flash")
    ROOM = {"flash": 34.0, "double_flash": 64.0, "jump": 16.0}

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
            for move in self.PLAN:
                n = self.reps if move not in ("up_flash", "rope_lift") else 1
                for _ in range(n):
                    if self._stop.is_set() or not bot.is_window_focused():
                        self._emit("measure", "measurement stopped")
                        return
                    self._measure(move)
            bot.reach.save(force=True)
            self._emit(
                "measure",
                "done — reach saved; check the Route overlay to see the new graph",
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("move measurement failed: %s", exc, exc_info=True)
            self._emit("error", f"measurement failed: {exc}")

    def _measure(self, move: str) -> bool:
        """One attempt; False when the move can't be attempted here."""
        bot = self.bot
        graph = bot._nav_graph()
        start = self._settle()
        if start is None:
            return False
        vertical = move in ("up_flash", "rope_lift")
        if vertical:
            if graph.above(start[0], start[1]) is None:
                return False
            direction = None
        else:
            span = platform_span_at(bot._platform_segments_px(), *start)
            if span is None:
                return False
            room_r, room_l = span[1] - start[0], start[0] - span[0]
            if max(room_r, room_l) < self.ROOM[move]:
                return False
            direction = "right" if room_r >= room_l else "left"
        if move == "rope_lift" and not bot.rope_lift():
            self._emit("measure", "rope lift not bound — skipped")
            return False
        if direction:
            bot.hid.key_down(direction)
        try:
            if move == "flash":
                bot._flash_hop()
            elif move == "double_flash":
                bot._double_flash()
            elif move == "jump":
                bot.hid.press(bot.config.jump_key)
                bot.sleep(0.5)
            elif move == "up_flash":
                bot._up_flash(None)
        finally:
            if direction:
                bot.hid.key_up(direction)
        land = self._settle(timeout=1.2)
        if land is None or land == start:
            return False
        dx = abs(land[0] - start[0])
        rise = start[1] - land[1]
        if not vertical and rise > 8:      # drifted off a ledge — unusable
            return False
        bot.reach.observe(
            move, planned=(0.0, 0.0),
            observed=(0.0 if vertical else dx, rise if vertical else 0.0),
            ok=True,
        )
        self._emit(
            "measure",
            f"{move}: dx {dx:.0f}px rise {rise:.0f}px"
            + (f" ({direction})" if direction else ""),
        )
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
