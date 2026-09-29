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
from .maps import MapEntry, MapStore
from .rotation import Anchor, Rotation, Step
from .skills import Skill, SkillBook
from .smart_bot import SmartBot
from .timing import human_between, human_delay, human_hold, new_session

__all__ = [
    "Anchor",
    "BotBase",
    "BotConfig",
    "HidController",
    "Machine",
    "MapEntry",
    "MapStore",
    "Rotation",
    "Skill",
    "SkillBook",
    "SmartBot",
    "Step",
    "human_between",
    "human_delay",
    "human_hold",
    "new_session",
]
