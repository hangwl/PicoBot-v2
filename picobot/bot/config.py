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
    up_jump_skill_cooldown: float = 3.0     # rope lift re-cast delay

    # -- Timing ----------------------------------------------------------------
    skill_gap_seconds: Tuple[float, float] = (0.5, 1.0)  # base gap between attacks

    # -- Navigation ------------------------------------------------------------
    nav_threshold_px: int = 5             # minimap px tolerance for "arrived"
    nav_stuck_limit: int = 40             # identical polls before rope-escape
    vert_jump_interval: float = 0.9       # min gap between vertical jump tries
    # Starting move reach (minimap px) — conservative; learned upward
    # from observed moves into nav_reach_file.
    nav_up_flash_px: float = 26.0         # rise of an upward flash jump
    nav_rope_lift_px: float = 90.0        # rope-lift max grab range (highest platform in range)
    nav_up_side_dx_px: float = 30.0       # sideways reach of up→side flash
    nav_jump_px: float = 10.0             # widest gap a plain jump clears
    nav_gap_px: float = 30.0              # widest gap a flash jump clears
    nav_double_gap_px: float = 48.0       # widest gap a double flash clears
    nav_reach_file: str = "nav_reach.json"
    anchor_float_px: float = 4.0           # anchors hover this far above the platform line
    flash_repress_seconds: float = 0.15    # jump -> flash re-press gap
    combo_repress_seconds: float = 0.16    # gap between chained flashes

    # -- Dwell weave -------------------------------------------------------------
    dwell_weave: bool = True              # move + weave attacks at anchors
    weave_double_chance: float = 0.4      # P(2 attacks) per flash weave, else 1
    weave_range_px: int = 24              # fallback half-width around the anchor
    weave_edge_margin_px: int = 4         # stay this far inside platform bounds
    wall_zone_px: int = 16                # force inward dir inside this edge zone
    wall_pad_px: float = 6.0              # buffer beyond each drawn wall/floor zone

    # -- Vision ----------------------------------------------------------------
    minimap_colors: MinimapColors = field(default_factory=MinimapColors)
    minimap_region: Optional[Tuple[int, int, int, int]] = None
    """Explicit (x, y, w, h) minimap rect relative to the window's
    client area (excludes the OS title bar).
    Set this if border auto-detection fails on your client/resolution."""

    # -- Skills -----------------------------------------------------------------
    skills: Dict[str, Skill] = field(default_factory=dict)
    """Named skills with per-skill cooldowns. Populated by ``from_dict``
    (explicit ``"skills"`` map, or synthesised from the legacy
    attack_keys/buff_keys fields in ``__post_init__``)."""

    # -- Rotation ---------------------------------------------------------------
    rotation: Rotation = field(default_factory=Rotation)
    """Anchor/leg route graph. When empty the bot weave-hops around where
    grinding started. Map files can override this."""

    # -- Movement ---------------------------------------------------------------
    flash_jump_enabled: bool = True
    flash_jump_key: Optional[str] = None  # None = reuse jump_key
    travel_style: str = "mixed"           # default leg style: walk|flash|mixed

    # -- Maps -------------------------------------------------------------------
    maps_dir: str = "maps"
    auto_select_map: bool = True
    active_map: Optional[str] = None      # force a map by name (skip auto-match)
    marker_inset_px: int = 4              # marker scans ignore this many rim px
    name_ocr: bool = True                 # OCR the map-name strip (preferred ID)
    minimap_name_region: Optional[Tuple[int, int, int, int]] = None
    name_scan_px: int = 160               # how far into the minimap region the
                                          # title scan reaches — generous so the
                                          # divider row is always in the band;
                                          # segmentation cuts it back out

    # -- Debug ------------------------------------------------------------------
    debug_capture_dir: str = "debug/frames"
    debug_capture_max_events: int = 100   # oldest event folders are pruned

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
            "stop_when_players_appear", "stop_when_rune_appears",
            "pause_on_lie_detector", "dwell_weave", "name_ocr",
        )
        for name in bools:
            if name in data:
                setattr(cfg, name, bool(data[name]))
        floats = (
            "buff_interval_seconds",
            "up_jump_skill_cooldown", "nav_up_flash_px", "nav_rope_lift_px",
            "nav_up_side_dx_px", "nav_jump_px", "nav_gap_px", "nav_double_gap_px",
            "vert_jump_interval",
            "weave_double_chance", "wall_pad_px", "anchor_float_px",
            "flash_repress_seconds", "combo_repress_seconds",
        )
        for name in floats:
            if name in data:
                setattr(cfg, name, float(data[name]))
        ints = ("nav_threshold_px", "nav_stuck_limit",
                "marker_inset_px", "weave_range_px", "weave_edge_margin_px",
                "wall_zone_px", "name_scan_px", "debug_capture_max_events")
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
        if data.get("nav_reach_file"):
            cfg.nav_reach_file = str(data["nav_reach_file"])
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
        if "minimap_name_region" in data and data["minimap_name_region"]:
            r = data["minimap_name_region"]
            if len(r) != 4:
                raise ValueError("minimap_name_region must be [x, y, w, h]")
            cfg.minimap_name_region = tuple(int(v) for v in r)
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
        if data.get("debug_capture_dir"):
            cfg.debug_capture_dir = str(data["debug_capture_dir"])
        if "active_map" in data:
            v = data["active_map"]
            cfg.active_map = str(v) if v else None
        if "auto_select_map" in data:
            cfg.auto_select_map = bool(data["auto_select_map"])
        if not (isinstance(data.get("skills"), dict) and data["skills"]):
            # No explicit skills map: rebuild from the (possibly overridden)
            # legacy key lists.
            cfg.skills = {}
        cfg.__post_init__()
        return cfg


__all__ = ["BotConfig"]
