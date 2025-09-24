import unittest
from unittest import mock

from picobot.app import create_application
from picobot.config import AppConfig


class CreateApplicationTests(unittest.TestCase):
    def test_uses_injected_dependencies(self) -> None:
        root = mock.Mock(name="root")
        cfg = AppConfig(last_window="Game")
        context = mock.Mock(name="context")

        with mock.patch("picobot.app.MacroControllerApp", autospec=True) as factory:
            sentinel_app = mock.Mock(name="app")
            factory.return_value = sentinel_app

            result = create_application(root=root, config=cfg, context=context)

        factory.assert_called_once_with(root, context=context, config=cfg)
        self.assertIs(result, sentinel_app)

    def test_creates_root_and_loads_config_when_missing(self) -> None:
        fake_root = mock.Mock(name="tk_root")
        cfg = AppConfig(last_folder="C:/macros")

        with mock.patch("picobot.app.tk.Tk", return_value=fake_root) as tk_ctor:
            with mock.patch("picobot.app.load_app_config", return_value=cfg) as loader:
                with mock.patch(
                    "picobot.app.MacroControllerApp", autospec=True
                ) as factory:
                    sentinel_app = mock.Mock(name="app")
                    factory.return_value = sentinel_app

                    result = create_application()

        tk_ctor.assert_called_once_with()
        loader.assert_called_once_with()
        factory.assert_called_once_with(fake_root, context=None, config=cfg)
        self.assertIs(result, sentinel_app)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

