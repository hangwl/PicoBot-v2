"""Configuration model for the smart bot.

Loaded from the ``"bot"`` object inside the main ``config.json`` (same file
as the GUI settings) so everything stays in one place. All values have
defaults so the bot can run with an empty config.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..vision.minimap import MinimapColors
from .rotation import Rotation
from .skills import Skill, SkillBook


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

    # -- Skills -----------------------------------------------------------------
    skills: Dict[str, Skill] = field(default_factory=dict)
    """Named skills with per-skill cooldowns. Populated by ``from_dict``
    (explicit ``"skills"`` map, or synthesised from the legacy
    attack_keys/buff_keys fields in ``__post_init__``)."""

    # -- Rotation ---------------------------------------------------------------
    rotation: Rotation = field(default_factory=Rotation)
    """Anchor/leg route graph. When empty the bot falls back to the legacy
    stationary-grind/wander behaviour. Map files can override this."""

    # -- Movement ---------------------------------------------------------------
    flash_jump_enabled: bool = True
    flash_jump_key: Optional[str] = None  # None = reuse jump_key
    travel_style: str = "mixed"           # default leg style: walk|flash|mixed

    # -- Maps -------------------------------------------------------------------
    maps_dir: str = "maps"
    auto_select_map: bool = True
    active_map: Optional[str] = None      # force a map by name (skip auto-match)
    map_match_threshold: float = 15.0     # fingerprint distance bound (0-255)
    name_ocr: bool = True                 # OCR the map-name strip (preferred ID)
    minimap_name_region: Optional[Tuple[int, int, int, int]] = None
    """Explicit (x, y, w, h) map-name strip relative to the window.
    None = scan the whole band above the minimap region."""
    name_strip_height: int = 26           # inner-strip fallback when minimap
                                          # is flush with the window top

    def __post_init__(self) -> None:
        if not self.skills:
            self.skills = SkillBook.from_config({
                "attack_keys": self.attack_keys,
                "buff_keys": self.buff_keys,
                "buff_interval_seconds": self.buff_interval_seconds,
            }).skills

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
        if isinstance(data.get("skills"), dict) and data["skills"]:
            cfg.skills = {
                name: Skill.from_dict(name, spec)
                for name, spec in data["skills"].items()
            }
        if isinstance(data.get("rotation"), dict):
            cfg.rotation = Rotation.from_dict(data["rotation"])
        if "flash_jump" in data and isinstance(data["flash_jump"], dict):
            fj = data["flash_jump"]
            cfg.flash_jump_enabled = bool(fj.get("enabled", True))
            key = fj.get("key")
            cfg.flash_jump_key = str(key) if key else None
        if "travel_style" in data:
            cfg.travel_style = str(data["travel_style"])
        if "maps_dir" in data:
            cfg.maps_dir = str(data["maps_dir"])
        if "active_map" in data:
            v = data["active_map"]
            cfg.active_map = str(v) if v else None
        if "auto_select_map" in data:
            cfg.auto_select_map = bool(data["auto_select_map"])
        if "map_match_threshold" in data:
            cfg.map_match_threshold = float(data["map_match_threshold"])
        if "name_ocr" in data:
            cfg.name_ocr = bool(data["name_ocr"])
        if "minimap_name_region" in data and data["minimap_name_region"]:
            r = data["minimap_name_region"]
            if len(r) != 4:
                raise ValueError("minimap_name_region must be [x, y, w, h]")
            cfg.minimap_name_region = tuple(int(v) for v in r)
        if "name_strip_height" in data:
            cfg.name_strip_height = int(data["name_strip_height"])
        if not (isinstance(data.get("skills"), dict) and data["skills"]):
            # No explicit skills map: rebuild from the (possibly overridden)
            # legacy key lists.
            cfg.skills = {}
        cfg.__post_init__()
        return cfg


__all__ = ["BotConfig"]
