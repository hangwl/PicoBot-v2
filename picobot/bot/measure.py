"""Measure real move reach by performing each move a few times.

Run from the dashboard with the bot stopped: the host builds a throwaway
SmartBot (same HID wiring, shared :class:`ReachModel`), the measurer
executes each move its class can use in the safest available direction,
and calibrates the reach model to the best result per move — a
measurement is ground truth, so it may also lower a too-generous guess.
Farming keeps refining from there.

The up flash is measured by its recorded peak (a :class:`FlightRecorder`
arc), not by the ledge it happened to land on. A separate timing sweep
records how the peak depends on the re-press delay.
"""

from __future__ import annotations

import logging
import statistics
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from ..vision.minimap import platform_span_at
from .flight import Flight, FlightRecorder
from .timing import human_between

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
    # Up-flash timing sweep: re-press delays (s) after the first key-down.
    PROFILE_DELAYS = (0.08, 0.12, 0.16, 0.20, 0.25, 0.30, 0.36, 0.44)
    PROFILE_REPS = 3
    PROFILE_CLEAR_PX = 45.0

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
        recorder_factory: Optional[Callable[[], FlightRecorder]] = None,
    ) -> None:
        self.bot = bot
        if recorder_factory is None and callable(getattr(bot, "sample_player", None)):
            recorder_factory = lambda: FlightRecorder(bot.sample_player)
        self._make_recorder = recorder_factory
        self._emit = on_event or (lambda k, m: None)
        self._on_status = on_status or (lambda st: None)
        self.reps = reps
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # move -> {"dx", "rise"} | {"skipped": reason}
        self.results: Dict[str, dict] = {}
        self.current: Optional[str] = None
        self._running = False
        self.mode = "moves"
        self.profile_rows: List[dict] = []

    # -- Lifecycle ------------------------------------------------------------------
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, mode: str = "moves") -> None:
        """``mode``: "moves" (the class's plan) or "up_flash_profile"."""
        if self.running():
            return
        self._stop.clear()
        self.results = {}
        self.profile_rows = []
        self.current = None
        self.mode = mode
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
            "mode": self.mode,
            "profile": list(self.profile_rows),
            "profiles": dict(getattr(self.bot.reach, "profiles", {})),
        }

    def _publish(self) -> None:
        try:
            self._on_status(self.status())
        except Exception:  # pragma: no cover - listener must not kill a run
            logger.debug("measure status listener failed", exc_info=True)

    # -- Measurement ------------------------------------------------------------------
    def _peak_measured(self, move: str) -> bool:
        return move == "up_flash" and self._make_recorder is not None

    def _loop(self) -> None:
        bot = self.bot
        try:
            bot._sync_map()                # resolve the map for the graph
            if bot._nav_graph() is None:
                self._emit(
                    "measure", "draw the platforms first — nothing to measure against"
                )
                return
            if self.mode == "up_flash_profile":
                self._profile()
                return
            self._emit("measure", "measuring moves — keep the game focused")
            for move in self.plan_for(bot.config):
                self.current = move
                self._publish()
                best: Optional[float] = None
                reason = ""
                peak = self._peak_measured(move)
                n = self.reps if move not in self.VERTICAL or peak else 1
                for _ in range(n):
                    if self._stop.is_set() or not bot.is_window_focused():
                        self._emit("measure", "measurement stopped")
                        return
                    got = self._measure(move)
                    if isinstance(got, str):
                        reason = got
                    # The re-press timing varies: plan on the lowest peak.
                    elif best is None or (got < best if peak else got > best):
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
        if self._peak_measured(move):
            return self._measure_peak(move)
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

    def _measure_peak(self, move: str):
        """Up flash as it runs on patrol; the recorded peak is its rise."""
        got = self._fly(None, up_flash=True, open_sky=False)
        if isinstance(got, str):
            return self._skip(move, got)
        pk = got.peak()
        if pk is None or pk[1] < 2:
            return self._skip(move, "no flight recorded — is the dot visible?")
        self._emit("measure", f"{move}: peak {pk[1]:.0f}px at {pk[0]:.2f}s")
        return pk[1]

    def _fly(self, delay: Optional[float], *, up_flash: bool, open_sky: bool = True):
        """Record one flight from standing: a plain jump, or an up flash
        (re-press at ``delay`` s, or the patrol's own timing when None).
        A :class:`Flight`, or the reason it can't be used — with
        ``open_sky``, landing on a platform above is one."""
        bot = self.bot
        start = self._settle()
        if start is None:
            return "player dot not visible"
        rec = self._make_recorder()
        rec.start()
        try:
            bot.sleep(0.12)                # a few standing samples first
            if not up_flash:
                rec.mark("jump")
                bot.hid.press(bot.config.jump_key)
                bot.sleep(0.7)
            elif delay is None:
                rec.mark("jump")
                bot._up_flash(None)
            else:
                bot._up_flash(None, delay=delay, mark=rec.mark)
            land = self._settle(timeout=1.5)
        finally:
            flight = rec.stop()
        if open_sky and land is not None and start[1] - land[1] > 3:
            return (f"landed {start[1] - land[1]:.0f}px higher on a platform — "
                    "measure where nothing is overhead")
        return flight

    # -- Up-flash timing sweep -------------------------------------------------------
    def _profile(self) -> None:
        bot = self.bot
        cfg = bot.config
        if self._make_recorder is None:
            self._emit("measure", "timing sweep needs flight recording")
            return
        if getattr(cfg, "class_travel", "flash") != "flash" \
                or not getattr(cfg, "flash_jump_enabled", True):
            self._emit("measure", "the up-flash sweep is for flash-jump classes")
            return
        start = self._settle()
        if start is None:
            self._emit("measure", "player dot not visible")
            return
        graph = bot._nav_graph()
        if graph.locate(*start) is None:
            self._emit("measure", "stand on a drawn platform first")
            return
        above = graph.above(*start)
        if above is not None:
            gap = start[1] - graph.platforms[above].y_at(start[0])
            if gap < self.PROFILE_CLEAR_PX:
                self._emit(
                    "measure",
                    f"a platform is {gap:.0f}px overhead — stand where there's "
                    f"at least {self.PROFILE_CLEAR_PX:.0f}px of open space above",
                )
                return
        plan: List[Optional[float]] = [None, *self.PROFILE_DELAYS]
        flights: Dict[Optional[float], List[Flight]] = {d: [] for d in plan}
        self._emit(
            "measure",
            f"up-flash timing sweep: {len(plan) * self.PROFILE_REPS} jumps "
            "— keep the game focused",
        )
        stopped = False
        for _ in range(self.PROFILE_REPS):
            for d in plan:
                if self._stop.is_set() or not bot.is_window_focused():
                    self._emit("measure", "sweep stopped")
                    stopped = True
                    break
                self.current = "jump" if d is None else f"up_flash @{d:.2f}s"
                self._publish()
                got = self._fly(d, up_flash=d is not None)
                if isinstance(got, str):
                    self._emit("measure", f"sweep stopped — {got}")
                    stopped = True
                    break
                flights[d].append(got)
                self.profile_rows = self._profile_rows(flights)
                self._publish()
                bot.sleep(human_between(0.35, 0.25, 0.6))
            if stopped:
                break
        rows = self._profile_rows(flights)
        if not any(r["n"] for r in rows):
            self._emit("measure", "sweep recorded nothing")
            return
        bot.reach.set_profile("up_flash", rows)
        best = max((r for r in rows if r["delay"] is not None and r["n"]),
                   key=lambda r: r["rise"], default=None)
        msg = "sweep saved" + (" (partial)" if stopped else "")
        if best is not None:
            msg += f" — highest peak {best['rise']}px at {best['delay']:.2f}s"
        self._emit("measure", msg)

    @staticmethod
    def _profile_rows(flights: Dict[Optional[float], List[Flight]]) -> List[dict]:
        """One row per delay (None = the plain-jump baseline)."""
        def avg(vals):
            return round(statistics.fmean(vals), 3) if vals else None

        rows = []
        for delay, fs in flights.items():
            peaks = [p for p in (f.peak() for f in fs) if p is not None]
            rises = [r for _, r in peaks]
            lands = [l for l in (f.landing() for f in fs) if l is not None]
            gaps = [g for g in (f.gap() for f in fs) if g is not None]
            rows.append({
                "delay": delay,
                "n": len(rises),
                "gap": avg(gaps),
                "rise": round(statistics.fmean(rises), 1) if rises else None,
                "sd": round(statistics.pstdev(rises), 1) if len(rises) > 1 else 0.0,
                "min": min(rises) if rises else None,
                "max": max(rises) if rises else None,
                "peak_t": avg([t for t, _ in peaks]),
                "air": avg([t for t, _ in lands]),
            })
        return rows

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
