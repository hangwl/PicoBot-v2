"""Headless smart-bot entry: ``python -m picobot.bot --port COM3 --window TITLE``.

Runs the FSM-driven bot without the dashboard host. Configuration comes
from the ``"bot"`` object in config.json.
"""

from __future__ import annotations

import argparse
import logging

from ..config import AppConfig, load_config
from ..settings import configure_logging
from ..vision import framelog
from .config import BotConfig
from .smart_bot import SmartBot


def main() -> None:
    parser = argparse.ArgumentParser(prog="picobot.bot")
    parser.add_argument("--port", required=True, help="Pico DATA COM port")
    parser.add_argument(
        "--window",
        default=None,
        help="Game window title (defaults to config's default_target_window)",
    )
    parser.add_argument(
        "--debug-frames", action="store_true",
        help="save detection debug captures to debug_capture_dir",
    )
    args = parser.parse_args()

    configure_logging()
    app_config = load_config()
    window_title = args.window or AppConfig().default_target_window
    bot_config = BotConfig.from_dict(getattr(app_config, "bot", None))
    framelog.configure_from(bot_config, args.debug_frames)

    bot = SmartBot.connect(args.port, window_title, bot_config)
    try:
        bot.start()
    except KeyboardInterrupt:
        bot.stop()


if __name__ == "__main__":
    main()
