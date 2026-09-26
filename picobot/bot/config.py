"""Configuration model for the smart bot.

Loaded from the ``"bot"`` object inside the main ``config.json`` (same file
as the GUI settings) so everything stays in one place. All values have
defaults so the bot can run with an empty config.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from ..vision.minimap import MinimapColors


@dataclass
class BotConfig:
    """Tuning knobs for :class:`~picobot.bot.smart_bot.SmartBot`."""

    # -- Behavior toggles ------------------------------------------------------
    stationary_mode: bool = True          # attack in place between wanders
    enable_random_wander: bool = True     # periodically walk around the map
    stop_when_players_appear: bool = True # pause if other players show on minimap
    stop_when_rune_appears: bool = True   # pause if a rune marker appears

    # Lie detector / human-verification prompts (e.g. Eluna's popups).
    # Detection is not implemented yet — when True the states call
    # ``SmartBot.check_lie_detector()`` which is a documented stub seam for a
    # future template-image match. Flip on once templates exist.
    pause_on_lie_detector: bool = False

    # -- Keys ------------------------------------------------------------------
    attack_keys: List[str] = field(default_factory=lambda: ["a"])
    buff_keys: List[str] = field(default_factory=list)
    buff_interval_seconds: float = 60.0
    jump_key: str = "alt"
    up_jump_skill_key: Optional[str] = None  # e.g. a rope-lift skill; None = use combo

    # -- Timing ----------------------------------------------------------------
    skill_gap_seconds: Tuple[float, float] = (0.5, 1.0)  # base gap between attacks
    stationary_seconds: float = 20.0      # grind in place before wandering
    wander_seconds: float = 15.0          # how long a wander phase lasts

    # -- Navigation ------------------------------------------------------------
    nav_threshold_px: int = 3             # minimap px tolerance for "arrived"
    nav_stuck_limit: int = 40             # identical polls before rope-escape
    wander_edge_margin_px: int = 20       # turn around this close to map edge

    # -- Vision ----------------------------------------------------------------
    minimap_colors: MinimapColors = field(default_factory=MinimapColors)
    minimap_region: Optional[Tuple[int, int, int, int]] = None
    """Explicit (x, y, w, h) minimap rect relative to the window.
    Set this if border auto-detection fails on your client/resolution."""

    @classmethod
    def from_dict(cls, data: dict | None) -> "BotConfig":
        if not data:
            return cls()
        cfg = cls()
        bools = (
            "stationary_mode", "enable_random_wander",
            "stop_when_players_appear", "stop_when_rune_appears",
            "pause_on_lie_detector",
        )
        for name in bools:
            if name in data:
                setattr(cfg, name, bool(data[name]))
        floats = (
            "buff_interval_seconds", "stationary_seconds", "wander_seconds",
        )
        for name in floats:
            if name in data:
                setattr(cfg, name, float(data[name]))
        ints = ("nav_threshold_px", "nav_stuck_limit", "wander_edge_margin_px")
        for name in ints:
            if name in data:
                setattr(cfg, name, int(data[name]))
        if "attack_keys" in data:
            cfg.attack_keys = [str(k) for k in data["attack_keys"]]
        if "buff_keys" in data:
            cfg.buff_keys = [str(k) for k in data["buff_keys"]]
        if "jump_key" in data:
            cfg.jump_key = str(data["jump_key"])
        if "up_jump_skill_key" in data:
            v = data["up_jump_skill_key"]
            cfg.up_jump_skill_key = str(v) if v else None
        if "skill_gap_seconds" in data:
            lo, hi = data["skill_gap_seconds"]
            cfg.skill_gap_seconds = (float(lo), float(hi))
        if "minimap_colors" in data:
            cfg.minimap_colors = MinimapColors.from_dict(data["minimap_colors"])
        if "minimap_region" in data and data["minimap_region"]:
            r = data["minimap_region"]
            if len(r) != 4:
                raise ValueError("minimap_region must be [x, y, w, h]")
            cfg.minimap_region = tuple(int(v) for v in r)
        return cfg


__all__ = ["BotConfig"]
