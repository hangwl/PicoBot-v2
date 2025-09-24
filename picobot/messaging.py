"""Messaging helpers for PicoBot."""

from __future__ import annotations

import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class TelegramHandler:
    """Handles sending messages via the Telegram Bot API."""

    def __init__(self, bot_token: str, chat_id: str) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id

    def send_message(self, text: str) -> Optional[requests.Response]:
        """Send *text* to the configured chat."""

        if not self.bot_token or not self.chat_id:
            logger.debug("Telegram credentials are not configured; skipping send.")
            return None

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        params = {"chat_id": self.chat_id, "text": text}
        try:
            response = requests.post(url, params=params, timeout=10)
        except Exception as exc:  # pragma: no cover - network errors aren't deterministic
            logger.error("Error sending Telegram message: %s", exc)
            return None

        if response.status_code == 200:
            logger.info("Telegram message sent successfully.")
        else:
            logger.error("Failed to send message: %s", response.text)
        return response
