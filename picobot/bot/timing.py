"""Humanized timing helpers for the smart bot.

Bots are detected less by *what* they do than by *how regularly* they do it.
Every delay here is lognormal (a long right tail, like human timing) and
scaled by a session **tempo**: each session plays a little faster or
slower than the last, and the pace drifts slowly within a session. The
tempo moves means only — ``human_between`` still clamps to its bounds, so
game input windows (e.g. the flash-jump re-press) always hold.
"""

from __future__ import annotations

import math
import random
import threading
import time
from typing import Optional


class Tempo:
    """Session pace: a random base times a slow mean-reverting drift
    (an Ornstein–Uhlenbeck walk on the log factor)."""

    def __init__(
        self,
        *,
        base_sigma: float = 0.07,
        drift_sigma: float = 0.05,
        tau: float = 240.0,
        rng: Optional[random.Random] = None,
        clock=time.monotonic,
    ) -> None:
        self.base_sigma = base_sigma
        self.drift_sigma = drift_sigma
        self.tau = tau
        self._rng = rng or random.Random()
        self._clock = clock
        self._lock = threading.Lock()
        self.new_session()

    def new_session(self) -> None:
        with self._lock:
            self.base = self._rng.lognormvariate(0.0, self.base_sigma)
            self._x = 0.0
            self._t = self._clock()

    def factor(self) -> float:
        with self._lock:
            now = self._clock()
            dt = max(0.0, now - self._t)
            self._t = now
            if dt:
                decay = math.exp(-dt / self.tau)
                spread = self.drift_sigma * math.sqrt(1.0 - decay * decay)
                self._x = self._x * decay + self._rng.gauss(0.0, spread)
            return self.base * math.exp(self._x)


TEMPO = Tempo()


def new_session() -> None:
    """Draw a fresh session pace (call when the bot starts)."""
    TEMPO.new_session()


def human_delay(mean: float, sigma: float = 0.35, minimum: float = 0.02) -> float:
    """Lognormal delay centered on ``mean`` seconds (tempo-scaled).

    ``sigma`` is the std-dev of the underlying normal (0.35 gives a long
    right tail similar to human reaction gaps). ``minimum`` floors the
    result so HID timing never becomes degenerate.
    """
    value = random.lognormvariate(0.0, sigma) * mean * TEMPO.factor()
    return max(minimum, value)


def human_between(mean: float, lo: float, hi: float, sigma: float = 0.3) -> float:
    """Lognormal delay around ``mean`` (tempo-scaled), clamped to
    ``[lo, hi]`` — for gaps that must stay inside a game input window
    (e.g. the flash-jump re-press) while still varying like a person's
    timing."""
    value = random.lognormvariate(0.0, sigma) * mean * TEMPO.factor()
    return min(hi, max(lo, value))


# Keys held a little longer than a tapped letter.
_LONG_HOLD = {"up", "down", "left", "right", "shift", "ctrl", "alt", "space"}


def human_hold(key: Optional[str] = None) -> float:
    """How long a tapped key stays down: lognormal around ~85ms (a bit
    longer for arrows and modifiers), clamped to [0.045, 0.22]s."""
    median = 0.085 * (1.15 if key in _LONG_HOLD else 1.0)
    return human_between(median, 0.045, 0.22, sigma=0.28)


def key_gap() -> float:
    """Minimum spacing between two key events: fingers never land at
    once — ~25ms, clamped to [0.01, 0.07]s."""
    return human_between(0.025, 0.01, 0.07, sigma=0.5)


def human_reaction() -> float:
    """Time to notice something unexpected and respond: ~0.22s,
    clamped to [0.13, 0.55]s."""
    return human_between(0.22, 0.13, 0.55, sigma=0.3)


def release_lag() -> float:
    """How late a held key is let go after the goal is seen: ~40ms,
    clamped to [0.015, 0.1]s."""
    return human_between(0.04, 0.015, 0.1, sigma=0.4)


__all__ = [
    "TEMPO",
    "Tempo",
    "human_between",
    "human_delay",
    "human_hold",
    "human_reaction",
    "key_gap",
    "new_session",
    "release_lag",
]
