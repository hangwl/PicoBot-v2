"""Screen perception layer for PicoBot.

- :class:`GameWindow` — locates the game window by title (pygetwindow).
- :class:`ScreenGrabber` — `mss`-based BGR captures of screen regions.
- :class:`MinimapAnalyzer` — pure-NumPy minimap marker detection.
- :func:`template_rect` — optional OpenCV template matching.
"""

from .game_window import GameWindow
from .minimap import (
    MinimapAnalyzer,
    MinimapColors,
    Region,
    blob_centroid,
    color_mask,
    erode3,
)
from .screen import ScreenGrabber, template_rect

__all__ = [
    "GameWindow",
    "MinimapAnalyzer",
    "MinimapColors",
    "Region",
    "ScreenGrabber",
    "blob_centroid",
    "color_mask",
    "erode3",
    "template_rect",
]
