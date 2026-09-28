"""Game window handle for PicoBot.

Thin wrapper around pygetwindow so the rest of the bot does not depend on
win32 APIs directly. The import is deferred to construction time so this
module remains importable (and testable) on non-Windows platforms.
"""

from __future__ import annotations

import time
from typing import Callable, Optional, Tuple


def _win32_is_window(hwnd: int) -> bool:
    try:
        import ctypes

        return bool(ctypes.windll.user32.IsWindow(hwnd))
    except Exception:
        return True    # no win32 (tests / other OS): trust the handle


class GameWindow:
    """Locates and describes the target game window by its title.

    The handle is re-found by title when the game is restarted — a dead
    handle would otherwise break every capture until the host restarts.
    """

    RELOOKUP_S = 1.0

    def __init__(
        self,
        title: str,
        *,
        gw=None,
        is_window: Callable[[int], bool] = _win32_is_window,
    ) -> None:
        if gw is None:
            import pygetwindow as gw  # Windows-only dependency, imported lazily

        self._gw = gw
        self._is_window = is_window
        self.title = title
        self._last_lookup = 0.0
        self._window = self._find()
        if self._window is None:
            raise RuntimeError(f"Window not found: {title!r}")

    def _find(self):
        matches = self._gw.getWindowsWithTitle(self.title)
        for window in matches:
            if window.title.strip() == self.title.strip():
                return window
        return matches[0] if matches else None

    @property
    def _live(self):
        """The window, re-found by title if its handle died."""
        w = self._window
        hwnd = int(getattr(w, "_hWnd", 0) or 0)
        if w is not None and (not hwnd or self._is_window(hwnd)):
            return w
        now = time.monotonic()
        if now - self._last_lookup < self.RELOOKUP_S:
            raise RuntimeError(f"game window gone: {self.title!r}")
        self._last_lookup = now
        found = self._find()
        if found is None:
            raise RuntimeError(f"game window gone: {self.title!r}")
        self._window = found
        return found

    # -- Geometry -------------------------------------------------------------
    def rect(self) -> Tuple[int, int, int, int]:
        """(left, top, right, bottom) in virtual-desktop coordinates —
        the OUTER rect, including the OS title bar and borders."""
        w = self._live
        return int(w.left), int(w.top), int(w.right), int(w.bottom)

    def client_rect(self) -> Tuple[int, int, int, int]:
        """(left, top, right, bottom) of the CLIENT AREA in screen
        coordinates — excludes the OS title bar and window borders, so
        captures and window-relative regions never see non-client
        chrome. Falls back to the outer rect if the Win32 call fails."""
        try:
            import ctypes
            from ctypes import wintypes

            hwnd = int(getattr(self._live, "_hWnd", 0))
            if not hwnd:
                raise RuntimeError("no hwnd")
            rc = wintypes.RECT()
            user32 = ctypes.windll.user32
            if not user32.GetClientRect(hwnd, ctypes.byref(rc)):
                raise RuntimeError("GetClientRect failed")
            pt = wintypes.POINT(0, 0)
            user32.ClientToScreen(hwnd, ctypes.byref(pt))
            return (
                int(pt.x),
                int(pt.y),
                int(pt.x + rc.right - rc.left),
                int(pt.y + rc.bottom - rc.top),
            )
        except Exception:
            return self.rect()

    @property
    def client_left(self) -> int:
        return self.client_rect()[0]

    @property
    def client_top(self) -> int:
        return self.client_rect()[1]

    @property
    def left(self) -> int:
        return self.rect()[0]

    @property
    def top(self) -> int:
        return self.rect()[1]

    @property
    def width(self) -> int:
        return int(self._live.width)

    @property
    def height(self) -> int:
        return int(self._live.height)

    # -- Focus ----------------------------------------------------------------
    @property
    def is_active(self) -> bool:
        active = self._gw.getActiveWindow()
        if active is None:
            return False
        return active.title.strip() == self.title.strip()

    def activate(self) -> None:
        try:
            self._live.activate()
        except Exception:
            # pygetwindow raises on already-minimized/failed activation; the
            # caller treats a subsequent is_active check as ground truth.
            pass


__all__ = ["GameWindow"]
