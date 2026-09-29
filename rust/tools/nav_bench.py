"""Graph build + all-pairs anchor routing, timed in the Python host.

    python rust/tools/nav_bench.py <PicoBot folder> [reach file]

Same work as `cargo run --release -p picobot-core --example nav_bench`.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from picobot.bot.config import BotConfig  # noqa: E402
from picobot.bot.maps import MapStore  # noqa: E402
from picobot.bot.navgraph import graph_for  # noqa: E402
from picobot.bot.reach import ReachModel, base_reach  # noqa: E402
from picobot.bot.rotation import resolve_coord  # noqa: E402


def main() -> None:
    data = Path(sys.argv[1])
    reach_file = sys.argv[2] if len(sys.argv) > 2 else "nav_reach_erel.json"
    reach = ReachModel(base_reach(BotConfig()), path=data / reach_file)
    maps = [e for e in MapStore(data / "maps").load_all() if e.platforms]
    rounds = 5
    builds = routes = 0
    t_build = t_route = checksum = 0.0
    for _ in range(rounds):
        for e in maps:
            w, h = (e.minimap_region[2], e.minimap_region[3]) if e.minimap_region else (200, 150)
            t = time.perf_counter()
            g = graph_for(e, (0, 0, w, h), reach)
            t_build += time.perf_counter() - t
            builds += 1
            pts = [(resolve_coord(a.x, w), resolve_coord(a.y, h)) for a in e.rotation.anchors]
            t = time.perf_counter()
            for a in pts:
                for b in pts:
                    c = g.route_cost(a, b)
                    if c != float("inf"):
                        checksum += c
                    routes += 1
            t_route += time.perf_counter() - t
    print(f"python: {len(maps)} maps, graph build {t_build / builds * 1e6:.1f} us, "
          f"route {t_route / routes * 1e6:.1f} us (checksum {checksum / rounds:.3f})")


if __name__ == "__main__":
    main()
