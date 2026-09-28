import unittest
from types import SimpleNamespace

from picobot.vision.game_window import GameWindow


def _win(hwnd, title="Eluna (x64)", left=10):
    return SimpleNamespace(
        _hWnd=hwnd, title=title, left=left, top=20, right=left + 800,
        bottom=620, width=800, height=600, activate=lambda: None,
    )


class _Gw:
    def __init__(self, windows):
        self.windows = windows
        self.lookups = 0

    def getWindowsWithTitle(self, title):
        self.lookups += 1
        return [w for w in self.windows if title in w.title]


class GameWindowTests(unittest.TestCase):
    def test_restarted_game_is_found_again(self):
        alive = {1}
        gw = _Gw([_win(1)])
        win = GameWindow("Eluna (x64)", gw=gw, is_window=lambda h: h in alive)
        self.assertEqual(win.rect()[0], 10)
        alive = {2}                                 # game restarted
        win._is_window = lambda h: h in alive
        gw.windows = [_win(2, left=300)]
        self.assertEqual(win.rect()[0], 300)

    def test_gone_window_raises_and_lookups_are_throttled(self):
        gw = _Gw([_win(1)])
        win = GameWindow("Eluna (x64)", gw=gw, is_window=lambda h: False)
        gw.windows = []
        before = gw.lookups
        for _ in range(5):
            with self.assertRaises(RuntimeError):
                win.rect()
        self.assertEqual(gw.lookups - before, 1)    # once per RELOOKUP_S

    def test_missing_at_start_raises(self):
        with self.assertRaises(RuntimeError):
            GameWindow("Eluna (x64)", gw=_Gw([]))


if __name__ == "__main__":
    unittest.main()
