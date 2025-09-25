"""Service utilities for device and window discovery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import pygetwindow as gw
import serial.tools.list_ports

from ..transport import discover_data_port


@dataclass
class PortSelection:
    """Snapshot of available ports and the recommended selection."""

    ports: List[str]
    selected: Optional[str]
    auto_selected: bool


@dataclass
class WindowSelection:
    """Snapshot of window titles and the recommended selection."""

    titles: List[str]
    selected: Optional[str]


class PortService:
    """Provide serial port discovery helpers decoupled from the GUI."""

    def list_ports(self) -> List[str]:
        return [port.device for port in serial.tools.list_ports.comports()]

    def guess_data_port(self) -> Optional[str]:
        try:
            for info in serial.tools.list_ports.comports():
                loc = getattr(info, "location", "") or ""
                if loc.endswith("x.2"):
                    return info.device
        except Exception:
            return None
        return None

    def discover_data_port(self, exclude_port: Optional[str] = None) -> Optional[str]:
        return discover_data_port(exclude_port=exclude_port)

    def build_selection(
        self,
        current: Optional[str],
        *,
        force_auto: bool = False,
    ) -> PortSelection:
        """Return the list of ports and a suggested selection.

        Args:
            current: Current selection maintained by the caller.
            force_auto: When ``True`` always prefer an automatically detected port.
        """

        ports = self.list_ports()
        normalized = (current or "").strip()
        selected: Optional[str] = None
        auto_selected = False

        if normalized and not force_auto and normalized in ports:
            selected = normalized
        else:
            candidate = self.guess_data_port()
            if not candidate:
                candidate = self.discover_data_port(
                    exclude_port=normalized if normalized else None
                )
            if candidate:
                if candidate not in ports:
                    ports.append(candidate)
                selected = candidate
                auto_selected = True

        return PortSelection(
            ports=ports, selected=selected, auto_selected=auto_selected
        )


class WindowService:
    """Wrap window listing and activation to ease testing."""

    def list_titles(self) -> List[str]:
        return [title for title in gw.getAllTitles() if title]

    def activate(self, title: str) -> bool:
        try:
            windows = gw.getWindowsWithTitle(title)
        except Exception:
            return False
        if not windows:
            return False
        try:
            windows[0].activate()
            return True
        except Exception:
            return False

    def get_active_title(self) -> Optional[str]:
        try:
            return gw.getActiveWindowTitle()
        except Exception:
            return None

    def build_selection(self, current: Optional[str]) -> WindowSelection:
        """Return window titles and a preferred selection for the caller."""

        titles = self.list_titles()
        normalized = (current or "").strip()
        if normalized and normalized in titles:
            selected = normalized
        else:
            selected = titles[0] if titles else None
        return WindowSelection(titles=titles, selected=selected)
