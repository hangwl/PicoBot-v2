"""Configuration helpers for PicoBot."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from .settings import CONFIG_FILE

logger = logging.getLogger(__name__)


def _ensure_parent(path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        # Directory creation failures will surface during write; keep silent here.
        pass


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass
class AppConfig:
    last_window: str = ""
    last_folder: str = "No folder selected."
    always_on_top: bool = True
    bot_token: str = ""
    chat_id: str = ""
    countdown_seconds: int = 60
    ws_port: int = 8765
    http_port: int = 8000


def load_config(path: str | Path = CONFIG_FILE) -> AppConfig:
    """Load configuration data from *path* or return defaults on failure."""

    defaults = AppConfig()
    cfg_path = Path(path)
    if not cfg_path.exists():
        return defaults

    try:
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.error("Config file %s contains invalid JSON: %s", cfg_path, exc)
        return defaults
    except OSError as exc:
        logger.error("Could not read config file %s: %s", cfg_path, exc)
        return defaults

    if not isinstance(raw, dict):
        logger.error("Config file %s did not contain an object", cfg_path)
        return defaults

    data = asdict(defaults)
    data["last_window"] = str(raw.get("last_window", data["last_window"]))
    data["last_folder"] = str(raw.get("last_folder", data["last_folder"]))
    data["always_on_top"] = bool(raw.get("always_on_top", data["always_on_top"]))
    data["bot_token"] = str(raw.get("bot_token", data["bot_token"]))
    data["chat_id"] = str(raw.get("chat_id", data["chat_id"]))
    data["countdown_seconds"] = max(1, _coerce_int(raw.get("countdown_seconds"), defaults.countdown_seconds))
    data["ws_port"] = _coerce_int(raw.get("ws_port"), defaults.ws_port)
    data["http_port"] = _coerce_int(raw.get("http_port"), defaults.http_port)

    return AppConfig(**data)


def save_config(config: AppConfig, path: str | Path = CONFIG_FILE) -> None:
    """Persist *config* to *path*, logging errors without raising."""

    cfg_path = Path(path)
    _ensure_parent(cfg_path)
    try:
        cfg_path.write_text(json.dumps(asdict(config), indent=4), encoding="utf-8")
    except OSError as exc:
        logger.error("Could not write config file %s: %s", cfg_path, exc)
