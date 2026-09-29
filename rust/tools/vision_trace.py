"""Run the Python vision code over recorded debug frames; write what it
found, for the Rust parity test.

    python rust/tools/vision_trace.py <PicoBot folder> <out.json>

Reads <folder>/debug/frames/*/ (transition sequences with meta.json, and
window/crop captures). The images stay where they are — the output holds
relative paths plus results. Then:

    $env:PICOBOT_VISION_TRACE = "<out.json>"; cargo test -p picobot-core --test vision_parity
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from picobot.vision.minimap import MinimapAnalyzer, MinimapColors, find_frame  # noqa: E402
from picobot.vision.transition import TransitionDetector, is_dark  # noqa: E402


def bgr(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGB"))[:, :, ::-1].copy()


def main() -> None:
    data, out = Path(sys.argv[1]), Path(sys.argv[2])
    frames_dir = data / "debug" / "frames"
    colors = MinimapColors()
    sequences, windows, stills = [], [], []
    for folder in sorted(p for p in frames_dir.iterdir() if p.is_dir()):
        meta_path = folder / "meta.json"
        meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        for win in sorted(folder.glob("window*.png")):
            windows.append({"path": str(win.relative_to(data)),
                            "frame": find_frame(bgr(win), colors.border, 10)})
        if meta.get("reason") == "transition":
            clock = [0.0]
            det = TransitionDetector(clock=lambda: clock[0])
            mm = MinimapAnalyzer(colors=colors, marker_inset=4)
            steps = []
            for f in meta["frames"]:
                p = folder / f"{f['i']:03d}.png"
                if not p.exists():
                    continue
                img = bgr(p)
                clock[0] = f["t"]
                steps.append({
                    "path": str(p.relative_to(data)), "t": f["t"],
                    "dark": is_dark(img), "event": det.note(img), "state": det.state,
                    "player": mm.player_pos(img), "rune": mm.rune_pos(img),
                    "others": mm.has_other_players(img),
                })
            sequences.append({"folder": folder.name, "steps": steps})
        for crop in sorted(folder.glob("crop.png")):
            img = bgr(crop)
            mm = MinimapAnalyzer(colors=colors, marker_inset=4)
            stills.append({"path": str(crop.relative_to(data)), "dark": is_dark(img),
                           "player": mm.player_pos(img), "rune": mm.rune_pos(img),
                           "others": mm.has_other_players(img)})
    out.write_text(json.dumps({"root": str(data), "sequences": sequences,
                               "windows": windows, "stills": stills}))
    n = sum(len(s["steps"]) for s in sequences)
    print(f"{len(sequences)} sequences ({n} frames), {len(windows)} windows, {len(stills)} stills -> {out}")


if __name__ == "__main__":
    main()
