"""PicoBot: perception-driven game bot over a Pico HID relay."""

from __future__ import annotations

__all__ = ["main"]


def main() -> None:
    """Launch the PicoBot dashboard host (``python -m picobot.serve``)."""

    from .serve import main as _serve_main

    _serve_main()
