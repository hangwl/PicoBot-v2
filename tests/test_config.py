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
                # Deprecated key should migrate into default_target_window
                "last_window": "Notepad",
                "ws_port": "9000",
                "countdown_seconds": "15",
                "always_on_top": False,
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
            cfg = load_config(path)
        self.assertEqual(cfg.default_target_window, "Notepad")
        self.assertEqual(cfg.ws_port, 9000)
        self.assertEqual(cfg.countdown_seconds, 15)
        self.assertFalse(cfg.always_on_top)
        # Unspecified fields fall back to defaults
        self.assertEqual(cfg.http_port, AppConfig().http_port)

    def test_save_and_reload_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            cfg = AppConfig(
                default_target_window="Game",
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

    def test_load_tls_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            payload = {
                "ws_tls": True,
                "ws_certfile": "C:/certs/cert.pem",
                "ws_keyfile": "C:/certs/key.pem",
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
            cfg = load_config(path)
        self.assertTrue(cfg.ws_tls)
        self.assertEqual(cfg.ws_certfile, "C:/certs/cert.pem")
        self.assertEqual(cfg.ws_keyfile, "C:/certs/key.pem")


if __name__ == "__main__":
    unittest.main()
