"""Measure real move reach by performing each move a few times.

Run from the dashboard with the bot stopped: the host builds a throwaway
SmartBot (same HID wiring, shared :class:`ReachModel`), the measurer
executes each move its class can use in the safest available direction,
and calibrates the reach model to the best result per move — a
measurement is ground truth, so it may also lower a too-generous guess.
Farming keeps refining from there.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Dict, Optional, Tuple

from ..vision.minimap import platform_span_at

logger = logging.getLogger(__name__)

Point = Tuple[float, float]


class MoveMeasurer:
    """Execute each move type a few times and calibrate its envelope."""

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

    def __init__(
        self,
        bot,
        on_event: Optional[Callable[[str, str], None]] = None,
        reps: int = 2,
        on_status: Optional[Callable[[dict], None]] = None,
    ) -> None:
        self.bot = bot
        self._emit = on_event or (lambda k, m: None)
        self._on_status = on_status or (lambda st: None)
        self.reps = reps
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # move -> {"dx", "rise"} | {"skipped": reason}
        self.results: Dict[str, dict] = {}
        self.current: Optional[str] = None
        self._running = False

    # -- Lifecycle ------------------------------------------------------------------
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running():
            return
        self._stop.clear()
        self.results = {}
        self.current = None
        self._running = True
        self._publish()
        self._thread = threading.Thread(
            target=self._loop, name="MoveMeasurer", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        self._thread = None

    def status(self) -> dict:
        return {
            "running": self._running,
            "move": self.current,
            "plan": list(self.plan_for(self.bot.config)),
            "results": dict(self.results),
        }

    def _publish(self) -> None:
        try:
            self._on_status(self.status())
        except Exception:  # pragma: no cover - listener must not kill a run
            logger.debug("measure status listener failed", exc_info=True)

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
            for move in self.plan_for(bot.config):
                self.current = move
                self._publish()
                best: Optional[float] = None
                reason = ""
                n = self.reps if move not in self.VERTICAL else 1
                for _ in range(n):
                    if self._stop.is_set() or not bot.is_window_focused():
                        self._emit("measure", "measurement stopped")
                        return
                    got = self._measure(move)
                    if isinstance(got, str):
                        reason = got
                    elif best is None or got > best:
                        best = got
                self._record(move, best, reason)
            measured = [m for m, r in self.results.items() if "skipped" not in r]
            skipped = {m: r["skipped"] for m, r in self.results.items()
                       if "skipped" in r}
            summary = "done — reach saved. Measured: " + (
                ", ".join(measured) or "nothing"
            )
            if skipped:
                summary += "; skipped: " + ", ".join(skipped)
            self._emit("measure", summary)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("move measurement failed: %s", exc, exc_info=True)
            self._emit("error", f"measurement failed: {exc}")
        finally:
            # Finished moves are kept even when the run was stopped.
            bot.reach.save(force=True)
            self.current = None
            self._running = False
            self._publish()

    def _record(self, move: str, best: Optional[float], reason: str) -> None:
        """Calibrate ``move`` to its best attempt this run (rise for
        upward moves, dx for the rest)."""
        if best is None:
            self.results[move] = {"skipped": reason or "no usable attempt"}
            self._publish()
            return
        kind = "teleport" if move == "teleport_up" else move
        if move in self.VERTICAL:
            self.bot.reach.calibrate(kind, rise=best, tag=move)
            self.results[move] = {"rise": round(best, 1)}
        else:
            self.bot.reach.calibrate(kind, dx=best, tag=move)
            self.results[move] = {"dx": round(best, 1)}
        self._publish()

    def _skip(self, move: str, why: str) -> str:
        self._emit("measure", f"{move}: skipped — {why}")
        return why

    def _measure(self, move: str):
        """One attempt: the measured reach (rise for upward moves, dx for
        the rest), or the reason it was skipped."""
        bot = self.bot
        graph = bot._nav_graph()
        vertical = move in self.VERTICAL
        teleport = move in ("teleport", "teleport_up")
        if teleport:
            why = self._teleport_wait()
            if why:
                return self._skip(move, why)
        start = self._settle()
        if start is None:
            return self._skip(move, "player dot not visible")
        if vertical:
            if graph.above(start[0], start[1]) is None:
                return self._skip(move, "no platform above the player")
            direction = None
        else:
            span = platform_span_at(bot._platform_segments_px(), *start)
            if span is None:
                return self._skip(move, "player not on a drawn platform")
            room_r, room_l = span[1] - start[0], start[0] - span[0]
            if max(room_r, room_l) < self.ROOM[move]:
                return self._skip(
                    move,
                    f"only {max(room_r, room_l):.0f}px of platform room "
                    f"(needs {self.ROOM[move]:.0f})",
                )
            direction = "right" if room_r >= room_l else "left"
        if move == "rope_lift" and not bot.rope_lift():
            return self._skip(move, "rope lift key not bound or cooling down")
        if move == "teleport_up":
            direction = "up"
        # teleport() holds its own direction around the key press.
        held = direction if not teleport else None
        if held:
            bot.hid.key_down(held)
        try:
            if teleport:
                if not bot.teleport(direction):
                    return self._skip(move, "teleport not ready")
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
            return self._skip(move, "the character didn't move")
        dx = abs(land[0] - start[0])
        rise = start[1] - land[1]
        # Rise is positive upward: a sideways move must land on its own level.
        if not vertical and rise < -8:
            return self._skip(move, f"landed {-rise:.0f}px lower — fell off a ledge")
        if not vertical and rise > 8:
            return self._skip(move, f"landed {rise:.0f}px higher — caught a ledge above")
        if vertical and rise <= 0:
            return self._skip(move, "didn't reach the platform above")
        self._emit(
            "measure",
            f"{move}: dx {dx:.0f}px rise {rise:.0f}px"
            + (f" ({direction})" if direction else ""),
        )
        return rise if vertical else dx

    def _teleport_wait(self) -> str:
        """Wait out the teleport cooldown; a reason when it can't be used."""
        bot = self.bot
        wait = bot.teleport_remaining()
        if wait == float("inf"):
            return "teleport key not bound"
        if wait > 0 and bot.sleep(wait + 0.05):
            return "stopped"
        return ""

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
