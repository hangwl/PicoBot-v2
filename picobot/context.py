"""Application context container for shared services."""
from __future__ import annotations

from dataclasses import dataclass

from .countdown import CountdownService
from .messaging import TelegramHandler
from .services.system import PortService, WindowService


@dataclass
class AppContext:
    telegram: TelegramHandler
    port_service: PortService
    window_service: WindowService
    countdown: CountdownService
