"""Write the Rust parity fixtures with the *Python* host's own code.

The Rust tests load each file, save it back and require identical bytes,
so these must come from the Python writers, not be typed by hand.

    .venv\\Scripts\\python.exe rust\\tools\\gen_fixtures.py
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from picobot.bot.config import BotConfig  # noqa: E402
from picobot.bot.maps import MapEntry, MapStore  # noqa: E402
from picobot.bot.reach import ReachModel, base_reach  # noqa: E402
from picobot.bot.rotation import Anchor, Rotation, Step  # noqa: E402
from picobot.bot.skills import Skill  # noqa: E402
from picobot.config import AppConfig, save_config  # noqa: E402

OUT = ROOT / "rust" / "crates" / "core" / "tests" / "fixtures"


def maps() -> None:
    d = OUT / "maps"
    store = MapStore(d)
    rich = MapEntry(
        name="Limina — East 1",
        map_name="Limina : 1-5 East",
        fingerprint="legacy-fp",
        minimap_region=(7, 68, 170, 97),
        walls={"left": 0.05, "floor": 0.9},
        platforms=[[0.1824, 0.4227, 0.3235, 0.4227], [0.1824, 0.8454, 0.8235, 0.8454]],
        ropes=[[0.3647, 0.6598, 0.3647, 0.7526]],
        rotation=Rotation(
            anchors=[
                Anchor("a0", 0.2647, 0.4845),
                Anchor("a1", 0.7235, 0.4536, on_arrive=("orb",), face="left"),
                Anchor("a2", 37.0, 64.0),
            ],
            legs={
                (1, 2): [Step("climb", x=0.5, direction="down", until_y=0.8)],
                (0, 1): [
                    Step("walk_to", x=0.5, y=0.45, style="flash"),
                    Step("up_jump"),
                    Step("wait", seconds=0.25),
                ],
            },
            position_jitter_px=6,
            travel_style="flash",
        ),
        skills={
            "orb": Skill("orb", "d", 60.0, "summon", charges=2, duration=90.0),
            "burst": Skill("burst", "s", 28.0, hold=0.3),
        },
    )
    store.save(rich)
    store.save(MapEntry(name="bare"))


def config() -> None:
    cfg = AppConfig(
        serial_port="COM6",
        view_fps=3.0,
        bot={
            "patrol_policy": "greedy",
            "patrol_weight_temp": 1.5,
            "nav_threshold_px": 6,
            "minimap_colors": {"player": [10, 240, 240]},
            "skills": {"g": {"key": "g", "kind": "attack"}},
            "class": {
                "active": "erel",
                "profiles": {
                    "mage": {"travel": "teleport", "air_attacks": False,
                             "teleport_key": "w", "skills": {}},
                    "erel": {"travel": "flash", "jump_key": "space",
                             "flash_jump": {"key": "c"},
                             "skills": {"h": {"key": "h", "kind": "summon",
                                              "cooldown": 5.0, "charges": 2,
                                              "duration": 90.0}}},
                },
            },
        },
    )
    save_config(cfg, OUT / "config.json")
    b = BotConfig.from_dict(cfg.bot)
    # What the Python host makes of that "bot" block, for the Rust test.
    expected = {
        "class_active": b.class_active,
        "class_travel": b.class_travel,
        "air_attacks": b.air_attacks,
        "jump_key": b.jump_key,
        "flash_jump_key": b.flash_jump_key,
        "teleport_key": b.teleport_key,
        "nav_threshold_px": b.nav_threshold_px,
        "patrol_policy": b.patrol_policy,
        "patrol_weight_temp": b.patrol_weight_temp,
        "player_color": list(b.minimap_colors.player),
        "reach_path": b.reach_path(),
        "skills": {n: s.to_dict() for n, s in b.skills.items()},
    }
    (OUT / "config_bot_expected.json").write_text(json.dumps(expected, indent=2))


def reach() -> None:
    path = OUT / "nav_reach_erel.json"
    m = ReachModel(base_reach(BotConfig()), path=path)
    m.calibrate("flash", dx=36.0)
    m.calibrate("up_flash", rise=25.699999999999996, tag="up_flash")
    m.observe("rope_lift", planned=(0, 13), observed=(0, 2), ok=False)
    m.observe("double_flash", planned=(50, 0), observed=(20, 0), ok=False)
    m.set_profile("up_flash", [
        {"delay": None, "n": 3, "gap": None, "rise": 7.3, "sd": 0.5},
        {"delay": 0.2, "n": 3, "gap": 0.216, "rise": 25.7, "sd": 0.5},
    ])
    m.save(force=True)


if __name__ == "__main__":
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    maps()
    config()
    reach()
    for p in sorted(OUT.rglob("*.json")):
        print(p.relative_to(OUT))
