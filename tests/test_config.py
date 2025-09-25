import json
import tempfile
import unittest
from pathlib import Path

from picobot.config import AppConfig, load_config, save_config


class ConfigTests(unittest.TestCase):
    def test_load_returns_defaults_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            cfg = load_config(path)
        self.assertEqual(cfg, AppConfig())

    def test_load_merges_and_coerces_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            payload = {
                "last_window": "Notepad",
                "ws_port": "9000",
                "countdown_seconds": "15",
                "always_on_top": False,
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
            cfg = load_config(path)
        self.assertEqual(cfg.last_window, "Notepad")
        self.assertEqual(cfg.ws_port, 9000)
        self.assertEqual(cfg.countdown_seconds, 15)
        self.assertFalse(cfg.always_on_top)
        # Unspecified fields fall back to defaults
        self.assertEqual(cfg.http_port, AppConfig().http_port)

    def test_save_and_reload_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            cfg = AppConfig(
                last_window="Game",
                last_folder="C:/macros",
                always_on_top=False,
                bot_token="abc",
                chat_id="123",
                countdown_seconds=42,
                ws_port=9100,
                http_port=9200,
            )
            save_config(cfg, path)
            loaded = load_config(path)
        self.assertEqual(loaded, cfg)


if __name__ == "__main__":
    unittest.main()
