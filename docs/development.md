# Development

## Setup

Rust (<https://rustup.rs>) and Node.js. Development happens on Windows
(the game and the Pico live there).

```powershell
cd web; npm install; npm run build                          # build the dashboard (web/dist)
cd rust; cargo build --release                              # build the host
cd rust; cargo run --release -p picobot-host -- --root ..   # run it (or picobot.bat in the root)
cd rust; cargo test                                         # the suite (~220 tests, a few seconds)
cd rust; cargo clippy --all-targets; cargo fmt              # lints and formatting
cd web; npm run dev                                         # hot-reload UI against a running host
```

`web/dist` is not committed — build it before the first run. Git should
use `core.autocrlf=true` (files are CRLF on disk, LF in the repo); the
test fixtures are `-text` (`.gitattributes`). In Git Bash, Cargo lives in
`%USERPROFILE%\.cargo\bin`.

Title OCR needs `PP-OCRv6_rec_small.onnx` in `models/` (it ships in the
`rapidocr` Python package's `models/` folder); a `.venv` with RapidOCR
installed is found too. The first build downloads the ONNX runtime.

## Test layout

`rust/crates/core/tests/`:

- `bot_navigator.rs`, `bot_patrol.rs`, `bot_machine.rs`,
  `bot_measure.rs`, `bot_flight.rs` — the bot against a physics sim
  (`tests/sim`): landings, replans, rope lift, loops, bans, rope learning,
  pause/resume, move measurement, flight analysis.
- `bot_primitives.rs` — the exact key sequences the Pico receives.
- `navgraph.rs` — graph edges, routing, the overshoot rule.
- `parity.rs`, `traces.rs`, `title_parity.rs`, `fuzzy_parity.rs` —
  checks against output recorded from the original Python host (file
  round-trips, seeded behaviour traces, title segmentation, title
  scores). `vision_parity.rs` is opt-in (`PICOBOT_VISION_TRACE`).
- `web_keys.rs` — the dashboard's key list matches the firmware's
  `KEY_MAP`.

Unit tests sit next to the code (`#[cfg(test)]`); `io/tests/link.rs`
runs the serial link against an in-memory firmware.

The parity fixtures were written by the Python host's own code; the
generator (`rust/tools/gen_fixtures.py`) lives on the `legacy/python`
branch with it.

## Tools (`rust/crates/io/examples/`)

```powershell
cargo run --release -p picobot-io --example pico_ping -- COM6        # handshake only
cargo run --release -p picobot-io --example serial_latency -- COM6   # round trips (NACKed no-ops, no HID)
cargo run --release -p picobot-io --example dot_rate -- "Rien"       # dot sampling rate (captures only)
cargo run --release -p picobot-io --example ocr_check -- ..          # OCR on recorded title bands
rust\target\release\picobot.exe --root . --notify-test               # one Telegram alert
```

## Conventions worth knowing

- **Two coordinate systems**: map files store 0–1 fractions of the
  minimap region; capture/region rects are client-area px. Overlay meta
  rides frames in minimap px; the Panel view reports `ox`/`oy` for the
  composite's offset.
- **`entry.name` is the user's alias; `entry.map_name` is the OCR'd
  in-game title.** Never conflate them.
- **Hand-drawn platforms are authoritative** for movement — don't
  reintroduce line auto-detection.
- **OCR is request-driven** and runs on the `TitleOCR` worker — never on
  the frame thread, never per-frame.
- **Map change = loading blackout only.** Don't reintroduce pixel-content
  change as a trigger — translucent UI defeats it (see learnings.md).
- **Pure logic goes in `core`** and is tested there; `io` and `host` stay
  thin. New bot behaviour gets a sim test.
- Event levels: `hid`/serial chatter is `debug`; think before emitting
  chatty kinds at `info`.
- `CIRCUITPY/` is the Pico firmware (`code.py`). Changes take effect
  only once copied onto the Pico's CIRCUITPY drive.

## Debug frame captures

Saving detection evidence to `debug/frames/` (blackout episodes, every
title read, frame-not-found windows) was a feature of the Python host
(`--debug-frames`, on `legacy/python`); the Rust host has the two
evidence captures below (`evidence.rs`; at most one per 2s each, newest
200 of each kind kept). The recorded title bands in `debug/frames/*_ocr/`
are what `ocr_check` reads.

**Lost-dot captures**: whenever the player dot can't be read while the bot
runs (not during a map load), the host saves the minimap crop to
`debug/frames/<utc-stamp>_dotlost/` — `frame.png` (lossless) and
`meta.json`: map, bot state, last position, `marker_inset`, patrol target
and move, and pixel counts near the dot colour plus the closest pixel, in
the detector's own distance (sum of the three channel differences; it
accepts under 30). They tell a covered dot from a recoloured one.

**Off-platform captures**: the first tick of each spell the patrol finds
the player off every drawn platform ("Player is not on any drawn
platform"), the host saves `debug/frames/<utc-stamp>_offplatform/` —
`frame.png`, `overlay.png` (drawn platforms, learned ropes, the player and
the last leg) and `meta.json`: position, patrol target, the last leg run
(kind, from, to, how it ended), the platforms just above and below with
their row offsets (`dy`), and learned ropes within 8px of the column.

**Rune captures**: `debug/frames/<utc-stamp>_rune/` when a rune is first
seen (`event: seen`), when the bot stands beside it (`arrived`, with the
glyph gap and facing) and when it gives up (`failed`, with why) —
`frame.png`, `overlay.png` and `meta.json` with the rune's box. They are
the evidence for the rune glyph's real size and the side-by-side rule.

**Rune-solve recordings** (the dataset for solving runes): while the bot
is paused at a rune, the `RuneRecorder` thread (`solves.rs`, own grabber,
8 fps) keeps 2s of game-window frames; the first dashboard key starts a
recording. It ends when the bot resumes farming (`solved`), stops or
pauses for something else (`interrupted`), or after 8s without a key or
30s in all (`unsolved`), and is saved to
`debug/frames/<utc-stamp>_runesolve/`:

- `frames/NNN.jpg` (quality 90) from 2s before the first key to 1s after
  the last, with their times in `frames.json`;
- `key_NN_<arrow>.png` — the last frame before each arrow key-down,
  lossless: the puzzle as it was read, labelled by the key pressed;
- `keys.json` — every dashboard key event (down/up), times relative to
  the first key;
- `meta.json` — outcome, the arrow sequence, frame count, window size.

Newest 30 kept; `record_rune_solves` turns it off. Only `solved`
recordings make reliable labels.
