"""Game window handle for PicoBot.

Thin wrapper around pygetwindow so the rest of the bot does not depend on
win32 APIs directly. The import is deferred to construction time so this
module remains importable (and testable) on non-Windows platforms.
"""

from __future__ import annotations

from typing import Optional, Tuple


class GameWindow:
    """Locates and describes the target game window by its title."""

    def __init__(self, title: str) -> None:
        import pygetwindow as gw  # Windows-only dependency, imported lazily

        self._gw = gw
        self.title = title
        self._window = None
        for window in gw.getWindowsWithTitle(title):
            if window.title.strip() == title.strip():
                self._window = window
                break
        if self._window is None:
            matches = gw.getWindowsWithTitle(title)
            if matches:
                self._window = matches[0]
        if self._window is None:
            raise RuntimeError(f"Window not found: {title!r}")

    # -- Geometry -------------------------------------------------------------
    def rect(self) -> Tuple[int, int, int, int]:
        """(left, top, right, bottom) in virtual-desktop coordinates."""
        w = self._window
        return int(w.left), int(w.top), int(w.right), int(w.bottom)

    @property
    def left(self) -> int:
        return self.rect()[0]

    @property
    def top(self) -> int:
        return self.rect()[1]

    @property
    def width(self) -> int:
        return int(self._window.width)

    @property
    def height(self) -> int:
        return int(self._window.height)

    # -- Focus ----------------------------------------------------------------
    @property
    def is_active(self) -> bool:
        active = self._gw.getActiveWindow()
        if active is None:
            return False
        return active.title.strip() == self.title.strip()

    def activate(self) -> None:
        try:
            self._window.activate()
        except Exception:
            # pygetwindow raises on already-minimized/failed activation; the
            # caller treats a subsequent is_active check as ground truth.
            pass


__all__ = ["GameWindow"]
