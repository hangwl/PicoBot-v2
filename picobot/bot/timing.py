"""Humanized timing helpers for the smart bot.

Bots are detected less by *what* they do than by *how regularly* they do it.
These helpers draw delays and key-hold durations from skewed (lognormal /
clamped-Gaussian) distributions that resemble human timing, instead of the
flat ``random.uniform`` used by naive macros.
"""

from __future__ import annotations

import random


def human_delay(mean: float, sigma: float = 0.35, minimum: float = 0.02) -> float:
    """Lognormal-ish delay centered on ``mean`` seconds.

    ``sigma`` is the std-dev of the underlying normal (0.35 gives a long
    right tail similar to human reaction gaps). ``minimum`` floors the
    result so HID timing never becomes degenerate.
    """
    value = random.lognormvariate(0.0, sigma) * mean
    return max(minimum, value)


def human_hold() -> float:
    """Duration to keep a tapped key held, in seconds.

    Humans hold a key roughly 60-140ms with a soft Gaussian shape; the result
    is clamped into [0.04, 0.25]s.
    """
    return min(0.25, max(0.04, random.gauss(0.09, 0.025)))


def jittered(base: float, spread: float = 0.15) -> float:
    """Uniform jitter around ``base`` of +/- ``spread`` fraction."""
    return base * (1.0 + random.uniform(-spread, spread))


__all__ = ["human_delay", "human_hold", "jittered"]
