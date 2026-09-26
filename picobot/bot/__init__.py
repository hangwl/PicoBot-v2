"""Perception-driven bot layer for PicoBot.

- :class:`SmartBot` — closed-loop grinding task (FSM + minimap navigation).
- :class:`HidController` — ACK'd HID command sender with held-key tracking.
- :class:`BotConfig` — tuning knobs loaded from ``config.json["bot"]``.
- :class:`Machine` — FSM runtime; states live under ``picobot.bot.states``.
"""

from .base import BotBase
from .config import BotConfig
from .inputs import HidController
from .machine import Machine
from .smart_bot import SmartBot
from .timing import human_delay, human_hold, jittered

__all__ = [
    "BotBase",
    "BotConfig",
    "HidController",
    "Machine",
    "SmartBot",
    "human_delay",
    "human_hold",
    "jittered",
]
