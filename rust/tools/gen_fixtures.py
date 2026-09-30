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


def traces() -> None:
    """Seeded random operations through the Python logic, with every
    result recorded: the Rust side replays them and must agree."""
    import random

    from picobot.bot.platform_fit import PlatformFit, tidy_segments
    from picobot.bot.skills import SkillBook

    rng = random.Random(7)
    moves = ["jump", "flash", "double_flash", "up_flash", "up_side_flash",
             "rope_lift", "teleport"]

    # Reach learning.
    m = ReachModel(base_reach(BotConfig()))
    ops, states = [], []
    for _ in range(400):
        mv = rng.choice(moves)
        if rng.random() < 0.05:
            dx = rng.choice([None, round(rng.uniform(5, 60), 2)])
            rise = rng.choice([None, round(rng.uniform(3, 40), 2)])
            m.calibrate(mv, dx=dx, rise=rise)
            ops.append(["calibrate", mv, dx, rise])
        else:
            e = m.get(mv)
            planned = [round(rng.uniform(0, e.dx * 1.3), 2), round(rng.uniform(-5, e.rise * 1.3), 2)]
            observed = [round(planned[0] + rng.uniform(-12, 8), 2),
                        round(planned[1] + rng.uniform(-12, 8), 2)]
            ok = rng.random() < 0.55
            m.observe(mv, planned=tuple(planned), observed=tuple(observed), ok=ok)
            ops.append(["observe", mv, planned, observed, ok])
        fin = lambda v: None if v == float("inf") else round(v, 6)  # noqa: E731
        states.append([[k, round(v.dx, 6), round(v.rise, 6),
                        *((fin(m.ceiling[k].dx), fin(m.ceiling[k].rise))
                          if k in m.ceiling else ())]
                       for k, v in m.est.items()])
    (OUT / "trace_reach.json").write_text(json.dumps({"ops": ops, "states": states}))

    # Skill book: charges and cooldowns under random use.
    specs = {"s1": {"key": "a", "cooldown": 10.0},
             "s2": {"key": "b", "cooldown": 7.5, "kind": "summon", "charges": 3},
             "s3": {"key": "c", "cooldown": 0.0},
             "s4": {"key": "d", "cooldown": 30.0, "kind": "buff", "charges": 2}}
    book = SkillBook({n: Skill.from_dict(n, s) for n, s in specs.items()})
    now, steps = 0.0, []
    for _ in range(400):
        now = round(now + rng.uniform(0, 6), 3)
        name = rng.choice(list(specs))
        if rng.random() < 0.5:
            book.mark_used(name, now=now)
            steps.append(["use", name, now])
        else:
            steps.append(["check", name, now, book.charges(name, now=now),
                          round(book.remaining(name, now=now), 6),
                          sorted(s.name for s in book.ready_attacks(now=now)),
                          sorted(s.name for s in book.due_buffs(now=now))])
    (OUT / "trace_skills.json").write_text(json.dumps({"skills": specs, "steps": steps}))

    # Tidy: random drags, some near-flat, some overlapping, some sloped.
    cases = []
    for _ in range(200):
        segs = []
        for _ in range(rng.randint(1, 6)):
            y = rng.choice([40, 41, 60, 80.5])
            x0 = round(rng.uniform(0, 150), 1)
            segs.append([x0, round(y + rng.uniform(-3, 3), 1),
                         round(x0 + rng.uniform(-40, 60), 1),
                         round(y + rng.choice([rng.uniform(-3, 3), rng.uniform(-15, 15)]), 1)])
        cases.append({"in": segs, "out": [list(s) for s in tidy_segments(segs)]})
    (OUT / "trace_tidy.json").write_text(json.dumps(cases))

    # Platform fit: a walk with pauses over three drawn lines.
    segs = [[0.1, 0.5, 0.6, 0.5], [0.1, 0.8, 0.9, 0.8], [0.62, 0.3, 0.9, 0.34]]
    region = (0, 0, 200, 100)
    clock = [0.0]
    fit = PlatformFit(clock=lambda: clock[0])
    feed = []
    for _ in range(600):
        clock[0] = round(clock[0] + rng.uniform(0.05, 0.2), 3)
        if rng.random() < 0.7 and feed:
            pos = feed[-1][1]                      # standing still
        else:
            row = rng.choice([50, 80, 32])
            pos = [rng.randint(15, 185), row + rng.randint(-9, 9)]
        fit.observe("m", segs, region, tuple(pos))
        feed.append([clock[0], pos])
    (OUT / "trace_fit.json").write_text(json.dumps({
        "segs": segs, "feed": feed,
        "summary": fit.summary("m", segs, region),
    }))


def nav_trace() -> None:
    """Random maps through the Python planner: every transfer edge, routes
    between random points, and the geometry queries."""
    import random

    from picobot.bot.navgraph import NavGraph
    from picobot.bot.reach import Reach

    rng = random.Random(11)
    r6 = lambda v: round(v, 6)  # noqa: E731
    graphs = []
    for gi in range(60):
        plats = []
        for _ in range(rng.randint(2, 8)):
            y = rng.choice([30, 45, 52, 64, 70, 82, 100]) + rng.choice([0, 0, 0.5])
            x0 = rng.randint(0, 150)
            slope = rng.choice([0, 0, 0, rng.randint(-10, 10)])
            plats.append([x0, y, x0 + rng.randint(8, 120), y + slope])
        ropes = []
        for _ in range(rng.choice([0, 0, 1, 2])):
            x = rng.randint(10, 190)
            top = rng.choice([30, 45, 52, 64])
            ropes.append([x, top + rng.randint(15, 60), x + rng.choice([0, 0, 2]), top])
        base = base_reach(BotConfig())
        for k in base:
            base[k] = Reach(round(base[k].dx * rng.uniform(0.6, 1.4), 2),
                            round(base[k].rise * rng.uniform(0.6, 1.4), 2))
        m = ReachModel(base, explore=rng.choice([1.0, 1.15, 1.3]))
        for _ in range(rng.randint(0, 3)):
            mv = rng.choice(list(base))
            e = m.get(mv)
            m.observe(mv, planned=(e.dx, e.rise), observed=(0, 0), ok=False)
        kit = rng.choice([(True, False), (False, True), (False, False)])
        penalty = rng.choice([0.0, 5.0])
        g = NavGraph(plats, m, ropes=ropes, rope_penalty=penalty,
                     allow_flash=kit[0], allow_teleport=kit[1])
        edges = sorted([l.kind, r6(l.x0), r6(l.y0), r6(l.x1), r6(l.y1), r6(l.cost)]
                       for l in g.transfer_legs())

        def spot():
            p = rng.choice(g.platforms) if g.platforms else None
            if p is None or rng.random() < 0.15:
                return [rng.randint(0, 200), rng.randint(20, 110)]
            x = round(rng.uniform(p.x0, p.x1), 1)
            return [x, round(p.y_at(x) - rng.choice([0, 0, 2, 4]), 1)]

        routes = []
        for _ in range(25):
            a, b = spot(), spot()
            ex = rng.choice([[], [], ["rope_lift"], ["rope_lift", "teleport"]])
            legs = g.route(tuple(a), tuple(b), exclude=tuple(ex))
            routes.append({
                "from": a, "to": b, "exclude": ex,
                "cost": None if legs is None else r6(sum(l.cost for l in legs)),
            })
        queries = []
        for _ in range(25):
            x, y = rng.randint(0, 200), rng.randint(20, 110)
            ha = g.highest_above(x, y, 30.0)
            queries.append({
                "at": [x, y], "locate": g.locate(x, y), "above": g.above(x, y),
                "below": g.below(x, y),
                "highest30": None if ha is None else [ha[0], r6(ha[1])],
                "exit": g.exit_direction(x, y) if g.platforms else None,
            })
        graphs.append({
            "platforms": plats, "ropes": ropes, "penalty": penalty,
            "allow_flash": kit[0], "allow_teleport": kit[1],
            "explore": m.explore,
            "base": {k: [v.dx, v.rise] for k, v in m.base.items()},
            "est": {k: [v.dx, v.rise] for k, v in m.est.items()},
            "ceiling": {k: [None if v.dx == float("inf") else v.dx,
                            None if v.rise == float("inf") else v.rise]
                        for k, v in m.ceiling.items()},
            "edges": edges, "routes": routes, "queries": queries,
        })
    (OUT / "trace_nav.json").write_text(json.dumps(graphs))


def title_bands() -> None:
    """Synthetic title bands (text lines, icon tiles, filled icons, the
    divider, faded tails, noise) and what the Python title_scan finds."""
    import random

    import numpy as np
    from PIL import Image

    from picobot.vision.mapname import title_scan

    d = OUT / "title"
    d.mkdir()
    rng = random.Random(11)
    cases = []
    for i in range(60):
        h, w = rng.randint(30, 80), rng.randint(140, 260)
        band = np.array(
            [[[rng.choice((0, 0, 0, 35, 70))] * 3 for _ in range(w)] for _ in range(h)],
            dtype=np.uint8,
        )
        band[:, :, 1] = band[:, :, 1] // 2 + rng.choice((0, 20, 40))
        x = rng.randint(2, 12)
        if rng.random() < 0.3:                     # region-icon tile
            s = rng.randint(20, min(34, h - 4))
            y0 = rng.randint(0, h - s - 1)
            band[y0:y0 + s, x] = 230
            band[y0:y0 + s, x + s - 1] = 230
            band[y0, x:x + s] = 230
            band[y0 + s - 1, x:x + s] = 230
            band[y0 + 2:y0 + s - 2, x + 2:x + s - 2] = (40, 180, 90)
            x += s + 3
        if rng.random() < 0.3:                     # filled toolbar icon
            band[2:12, x:x + 9] = 240
            x += 14
        y = rng.randint(2, 8)
        for _ in range(rng.randint(1, 2)):         # text lines
            lh = rng.randint(5, 10)
            gx = x
            for _ in range(rng.randint(5, 14)):
                gw = rng.randint(2, 6)
                if gx + gw >= w - 2 or y + lh >= h:
                    break
                for _ in range(rng.randint(3, 10)):
                    band[y + rng.randint(0, lh - 1), gx + rng.randint(0, gw - 1)] = 250
                gx += gw + rng.randint(1, 7)
            if rng.random() < 0.3:                 # faded tail
                for _ in range(rng.randint(2, 5)):
                    tx = gx + rng.randint(1, 10)
                    if tx < w:
                        band[y + rng.randint(0, lh - 1), tx] = rng.randint(90, 160)
            y += lh + rng.randint(1, 6)
        if rng.random() < 0.6 and y + 2 < h and w > 130:   # divider
            a = rng.randint(0, max(0, w - 125))
            band[y + 1, a:min(w, a + rng.randint(121, w))] = 235
            band[y + 2:, :] = np.maximum(band[y + 2:, :], rng.randint(0, 250))
        name = f"{i:02}.png"
        Image.fromarray(np.ascontiguousarray(band[:, :, ::-1])).save(d / name)
        lines, div = title_scan(band)
        cases.append({
            "png": name,
            "lines": [[int(v) for v in l] for l in lines],
            "div": [int(v) for v in div] if div is not None else None,
        })
    (d / "trace_title.json").write_text(json.dumps(cases))


def fuzzy() -> None:
    """Title matching: difflib ratios, sibling calls and title scores of
    synthetic titles against OCR-style variants (drops, doubles, swaps,
    merged words, clipped tails, region prefixes, other numbers)."""
    import random
    from difflib import SequenceMatcher

    from picobot.vision.mapname import looks_like_sibling, title_score

    titles = [
        "Limina : 1-5 East", "Limina : 1-5 West", "Lake of Oblivion Nameless Town",
        "Cernium Western City Ramparts 2", "Cernium Western City Ramparts 3",
        "Arcana Cavern Lower Path", "Arcana Cavern Upper Path", "Storehouse",
        "Storehouse Entrance", "Road of Happiness", "Weathered Land of Rage 4",
        "Moonbridge Outpost", "Hotel Arcus Lobby", "EZFZ", "Odium Main Street 1",
    ]
    rng = random.Random(5)

    def noisy(t: str) -> str:
        s = list(t)
        for _ in range(rng.randint(0, 3)):
            op = rng.random()
            i = rng.randrange(len(s))
            if op < 0.25:
                del s[i]
            elif op < 0.45:
                s.insert(i, s[i])
            elif op < 0.6 and i + 1 < len(s):
                s[i], s[i + 1] = s[i + 1], s[i]
            elif op < 0.75:
                s[i] = rng.choice("abcdefghijklmnopqrstuvwxyz0123456789 .:-")
        out = "".join(s)
        r = rng.random()
        if r < 0.15:
            out = out.replace(" ", "", 1)
        elif r < 0.3:
            out = out[: rng.randint(4, max(4, len(out)))]
        elif r < 0.4:
            out = "Lake of Oblivion " + out
        elif r < 0.5:
            out = "".join(str((int(c) + 1) % 10) if c.isdigit() else c for c in out)
        return out

    cases = []
    for _ in range(600):
        cand = rng.choice(titles)
        ocr = noisy(rng.choice(titles) if rng.random() < 0.3 else cand)
        cases.append({
            "ocr": ocr, "cand": cand,
            "ratio": SequenceMatcher(None, ocr, cand).ratio(),
            "sibling": looks_like_sibling(ocr, cand),
            "score": title_score(ocr, cand),
        })
    (OUT / "trace_fuzzy.json").write_text(json.dumps(cases))


if __name__ == "__main__":
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    maps()
    config()
    reach()
    traces()
    nav_trace()
    title_bands()
    fuzzy()
    for p in sorted(OUT.rglob("*.json")):
        print(p.relative_to(OUT))
