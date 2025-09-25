"""Configuration helpers and shared constants for PicoBot."""

from __future__ import annotations

import logging

CONFIG_FILE = "config.json"
LOG_LEVEL = logging.INFO
LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"


def configure_logging(
    *, level: int = LOG_LEVEL, fmt: str = LOG_FORMAT, force: bool = False
) -> None:
    """Initialize the root logger used across PicoBot."""

    if force:
        logging.basicConfig(level=level, format=fmt, force=True)
        return

    if logging.getLogger().handlers:
        return
    logging.basicConfig(level=level, format=fmt)
