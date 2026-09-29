"""Live capture + minimap analysis timing in the Python host (read-only).

    python rust/tools/vision_bench.py "<window title>"

Same work as `cargo run --release -p picobot-io --example vision_bench`.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from picobot.vision.game_window import GameWindow  # noqa: E402
from picobot.vision.minimap import MinimapAnalyzer, MinimapColors, find_frame  # noqa: E402
from picobot.vision.screen import ScreenGrabber  # noqa: E402
from picobot.vision.transition import is_dark  # noqa: E402


def main() -> None:
    win = GameWindow(sys.argv[1])
    l, t, r, b = win.client_rect()
    grab = ScreenGrabber()
    t0 = time.perf_counter()
    window = grab.capture((l, t, r - l, b - t))
    t_window = time.perf_counter() - t0
    t0 = time.perf_counter()
    frame = find_frame(window, MinimapColors().border, 10)
    t_find = time.perf_counter() - t0
    print(f"client {r - l}x{b - t}: window capture {t_window * 1e3:.2f} ms, "
          f"find_frame {t_find * 1e3:.2f} ms -> {frame}")
    if frame is None:
        return
    x, y, w, h = frame
    mm = MinimapAnalyzer(region=frame, marker_inset=4)
    n = 300
    t_cap = t_ana = 0.0
    last = None
    cpu0, wall0 = time.process_time(), time.perf_counter()
    for _ in range(n):
        t0 = time.perf_counter()
        img = grab.capture((l + x, t + y, w, h))
        t_cap += time.perf_counter() - t0
        t0 = time.perf_counter()
        is_dark(img)
        last = mm.player_pos(img)
        mm.rune_pos(img)
        mm.has_other_players(img)
        t_ana += time.perf_counter() - t0
    cpu = (time.process_time() - cpu0) / n
    wall = (time.perf_counter() - wall0) / n
    print(f"python: minimap {w}x{h}: capture {t_cap / n * 1e3:.3f} ms, "
          f"analysis {t_ana / n * 1e3:.3f} ms per frame; CPU {cpu * 1e3:.3f} ms "
          f"of {wall * 1e3:.3f} ms wall (player {last})")


if __name__ == "__main__":
    main()
