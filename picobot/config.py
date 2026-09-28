"""Configuration helpers for PicoBot."""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .fileio import write_text_atomic
from .settings import CONFIG_FILE

# Commands, the port probe and the bot can all persist settings.
_SAVE_LOCK = threading.Lock()

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


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass
class AppConfig:
    # Default target window to prefer on startup/refresh when unlocked
    default_target_window: str = "Eluna (x64)"
    bot_token: str = ""
    chat_id: str = ""
    serial_port: str = ""
    ws_port: int = 8765
    http_port: int = 8000
    # Dashboard minimap/window stream rate
    view_fps: float = 10.0
    # TLS for WebSocket server
    ws_tls: bool = False
    ws_certfile: str = ""
    ws_keyfile: str = ""
    # Smart-bot tuning — see picobot.bot.config.BotConfig for the schema.
    bot: dict = field(default_factory=dict)


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
    data["default_target_window"] = str(
        raw.get("default_target_window", data["default_target_window"])
    )
    data["bot_token"] = str(raw.get("bot_token", data["bot_token"]))
    data["chat_id"] = str(raw.get("chat_id", data["chat_id"]))
    data["serial_port"] = str(raw.get("serial_port", data["serial_port"]))
    data["ws_port"] = _coerce_int(raw.get("ws_port"), defaults.ws_port)
    data["http_port"] = _coerce_int(raw.get("http_port"), defaults.http_port)
    data["view_fps"] = min(
        30.0,
        max(
            1.0,
            _coerce_float(raw.get("view_fps"), defaults.view_fps),
        ),
    )
    data["ws_tls"] = bool(raw.get("ws_tls", defaults.ws_tls))
    data["ws_certfile"] = str(raw.get("ws_certfile", defaults.ws_certfile))
    data["ws_keyfile"] = str(raw.get("ws_keyfile", defaults.ws_keyfile))
    if isinstance(raw.get("bot"), dict):
        data["bot"] = raw["bot"]

    return AppConfig(**data)


def save_config(config: AppConfig, path: str | Path = CONFIG_FILE) -> None:
    """Persist *config* to *path*, logging errors without raising."""

    cfg_path = Path(path)
    _ensure_parent(cfg_path)
    try:
        payload = asdict(config)
        # Ensure deprecated keys are not written back out
        payload.pop("last_window", None)
        text = json.dumps(payload, indent=4)
        with _SAVE_LOCK:
            write_text_atomic(cfg_path, text)
    except OSError as exc:
        logger.error("Could not write config file %s: %s", cfg_path, exc)
