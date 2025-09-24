"""PicoBot package exposing the GUI application and related services."""

from __future__ import annotations

__all__ = ["main"]


def main() -> None:
    """Launch the PicoBot GUI application."""

    from .app import main as _app_main

    _app_main()
