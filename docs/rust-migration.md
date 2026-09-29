# Rust host — migration plan

Goal: a Rust host that replaces `picobot/` (Python) with **lower CPU use
and latency**, built alongside it on `feat/rust` until it reaches parity.
It is also a learning project, so each milestone notes the Rust ideas it
exercises.

## What does not change

- **Firmware** — `CIRCUITPY/code.py` stays CircuitPython (v2 wire
  protocol: numbered commands, `ka` keepalive, watchdog).
- **Dashboard** — `web/` stays Preact. The Rust host speaks the same
  WebSocket text/binary protocol (`docs/protocol.md`) and serves the same
  `web/dist`, so the dashboard needs no changes.
- **Data** — `config.json`, `maps/*.json`, `nav_reach*.json` keep their
  formats: either host can run on the same folder (not at the same time).

## Where the CPU goes today (to verify in M0)

Python spends most of its time in: the `MapMonitor` 20 Hz capture +
blackout check; the bot loop's per-tick captures and numpy colour masks
(often twice per tick — safety check, then the state's own read); the
streamer's annotate + JPEG encode (PIL) at the view fps; JSON event
broadcasts (every keystroke emits ~3 events). The Rust design targets
each: one capture per tick shared by safety and logic, SIMD-friendly
masks on the raw BGRA buffer, a fast JPEG encoder, debug events not
serialised unless someone subscribes to them.

## Architecture

```
rust/
  Cargo.toml            workspace
  crates/core/          pure logic, no OS deps — unit-testable anywhere
  crates/io/            Windows: serial, window lookup, screen capture, OCR
  crates/host/          the binary: bot thread, monitor, server, streamer
```

- **Threads, not everything async.** The bot loop and map monitor are
  dedicated OS threads: key timing needs precise, blocking sleeps.
  Networking (HTTP + WS) runs on a small tokio runtime.
- **One owner per piece of state.** The bot thread owns bot state; the
  dashboard sends it typed commands over a channel (`enum BotCommand`)
  and reads published snapshots (`Arc` swapped behind a lock-free cell).
  The races found in the review (skill dict, anchor list) can't compile.
- **Typed protocol.** WS commands parse into an enum once at the edge;
  `split('|')` never reaches the logic.
- **Serial.** One writer thread owns the port; commands carry a sequence
  number and a oneshot reply channel; a reader thread matches `ACK <seq>`.

Crates (initial picks): `serde`/`serde_json`, `serialport`, `windows`
(window lookup, focus), `windows-capture` or `xcap` (screen), `image`
+ `jpeg-encoder` or `turbojpeg`, `tokio` + `axum` (HTTP + WS), `rand` +
`rand_distr` (log-normal), `ort` (ONNX runtime for OCR), `tracing`.

## Working on it

```powershell
cd rust
cargo test                                   # all crates
$env:PICOBOT_DATA = "..\..\PicoBot-v2"; cargo test -p picobot-core --test parity   # + real data
python toolsision_trace.py ..\..\PicoBot-v2 trace.json; $env:PICOBOT_VISION_TRACE = "trace.json"; cargo test -p picobot-core --test vision_parity
cargo clippy --all-targets; cargo fmt
..\..\PicoBot-v2\.venv\Scripts\python.exe tools\gen_fixtures.py   # regenerate parity fixtures (Python writers)
```

Parity fixtures in `crates/core/tests/fixtures/` are written by the
Python code: data files must round-trip byte-for-byte (`tests/parity.rs`),
and `trace_*.json` hold seeded operations with the Python results, which
`tests/traces.rs` replays. `.gitattributes` keeps them out of line-ending
conversion.

## Status

- **M0** — toolchain (Rust 1.98), workspace, CPU sampler. Baseline
  numbers still to record.
- **M1** — done: `config` (AppConfig + BotConfig with class profiles,
  reach path), `maps` (MapEntry, MapStore with collision-safe names),
  `rotation` (anchors, legs, steps, leg chaining, anchor removal),
  `skills` (definitions), `reach` (file format), `json` (Python-compatible
  coercions and `json.dumps` output). Fixtures and the real data folder
  (12 maps, 2 reach files, config) round-trip byte-for-byte.
- **M2** — done: `timing` (log-normal delays, session tempo with a
  mean-reverting drift, injectable RNG/clock), `SkillBook` (charges,
  cooldowns, carry-over), `summons`, `anchor_stats`, reach learning
  (`observe` / `calibrate` / `limit` / `fits` / `plateau`, throttled save),
  `platform_fit` (straighten, merge, tidy, fit diagnostic). Time is always
  passed in (`now`) rather than read inside the logic. Behaviour traces —
  seeded random operations run through the Python code with every result
  recorded (400 reach steps, 400 skill-book checks, 200 tidy cases, a
  platform-fit walk) — replay identically in Rust.
- **M3** — done: `navgraph` (platforms, every edge kind incl. the up-flash
  overshoot rule and learned ropes, Dijkstra routing with exclusions and
  jitter, geometry queries, `graph_for`, `GraphCache`) and `planner` (loop
  planning with weighted/greedy order; skips returned with reasons for
  the caller to ban). The Python graph tests are ported; a trace of 60
  random maps (2,807 edges, 1,500 routes, geometry queries) matches the
  Python planner exactly. On the real maps (`examples/nav_bench.rs` vs
  `tools/nav_bench.py`, identical route checksums): graph build 83 µs vs
  962 µs, a route 17 µs vs 135 µs.
- **M4** — done (`picobot-io`): `serial::SerialLink` — numbered commands
  with one-shot reply channels, FIFO matching for older firmware, `ka`
  keepalive when idle (v2 only), a lost port closes the link and fails
  waiters at once, callbacks called with no lock held; `discover_data_port`
  / `list_ports`; `hid::HidController` — human key spacing, held keys
  tracked from the first attempt, unconfirmed presses still released, and
  `Drop` releases everything. Transport is two traits (read a line, write
  a line), so `tests/link.rs` runs against an in-memory firmware (late
  replies, silence, NACK, older protocol, port loss). `examples/pico_ping`
  handshakes with the real Pico (no key presses): it answers `PICO_READY
  v2`.
- **M5** — done: `core::vision` (BGRA `Image`, colour masks, frame finding,
  marker blobs, the percentile darkness test, platform lookups) and
  `core::minimap` (`TransitionDetector`, `MinimapAnalyzer` behind a mutex,
  an explicit per-thread `PlayerTracker`); `io::window` (find by title,
  client rect, focus, DPI-aware) and `io::capture` (GDI `BitBlt` into a
  reused DIB section — `!Send`, so each thread keeps its own). Parity on
  the recorded frames (`tools/vision_trace.py` + `tests/vision_parity.rs`,
  opt-in via `PICOBOT_VISION_TRACE`): 773 minimap frames — darkness, every
  blackout/arrival event and state, 490 dot positions with tracking,
  runes, other players — plus window frame finding and title crops, all
  identical. Live (`examples/vision_bench.rs` vs `tools/vision_bench.py`):
  same frame and dot; per minimap frame **0.10 ms CPU vs 1.93 ms** (analysis
  0.21 vs 1.6 ms). Wall time is ~7 ms for both: the capture waits for the
  compositor's next frame, which costs no CPU. `CAPTUREBLT` is off by
  default (no measurable difference; the game isn't a layered window).

## Milestones

Each milestone ports the matching Python tests first (they are the spec),
then the code, and ends with the Rust tests green.

| # | Scope | Python source | Rust ideas |
|---|---|---|---|
| M0 | Toolchain; workspace skeleton; CPU baseline of the Python host (idle, streaming 10 fps, bot running) with a repeatable script | — | cargo, workspaces, crates |
| M1 | Data formats: config, map entries, reach file, rotation — load/save round-trips on the real files in `maps/` | `config.py`, `bot/config.py`, `bot/maps.py`, `bot/reach.py`, `bot/rotation.py` | structs, `serde`, `Option`, `Result`, `?` |
| M2 | Pure logic: timing (log-normal, tempo), skills + charges, summons, anchor stats, reach learning, platform fit, tidy | `bot/timing.py`, `skills.py`, `summons.py`, `anchor_stats.py`, `reach.py`, `platform_fit.py` | ownership, borrowing, enums, traits, unit tests |
| M3 | Nav graph + routing + patrol planning (policy, bans, overshoot rule) | `bot/navgraph.py`, planning half of `bot/patrol.py` | `Vec` indices vs references, `BinaryHeap`, lifetimes |
| M4 | Serial/HID: `SerialManager` (v2 protocol, keepalive, loss), `HidController` (spacing, held keys, release) | `transport/serial_manager.py`, `bot/inputs.py` | threads, channels, `Drop` for cleanup |
| M5 | Vision: window lookup/focus, capture, minimap frame find, dot tracking, markers, transition (blackout) | `vision/*.py` except OCR | `unsafe`/FFI via `windows`, slices, performance |
| M6 | Bot: FSM, navigator (`_land`), primitives (flash, up flash, rope lift…), patrol execution, flight recorder, measurement | `bot/smart_bot.py`, `navigator.py`, `patrol.py`, `flight.py`, `measure.py`, `states/` | trait objects vs enums for states, time, testing with fakes |
| M7 | Host: HTTP static + WS, dashboard commands, event bus, frame streamer (annotate + JPEG), map identity + monitor | `serve.py`, `remote/*.py`, `events.py`, `bot/identity.py`, `bot/monitor.py` | tokio, axum, `Arc`/`Mutex`, message passing |
| M8 | Title OCR via `ort` + the PaddleOCR ONNX models RapidOCR ships; fuzzy title matching | `vision/mapname.py` | FFI-backed crates, model I/O |
| M9 | Parity run: same maps, both hosts; compare CPU, capture latency, landing/miss stats; switch over | — | profiling (`cargo flamegraph`) |

Until M8 lands, the Rust host can run with OCR off (a pinned map).

## Parity checks

- The Python suite's scenarios ported per module (~580 tests today).
- Golden files: the real `maps/*.json` and `nav_reach*.json` must load
  and save back byte-compatible (key order aside).
- Recorded frames from `debug/frames/` replayed through the vision code
  must give the same regions, dot positions and blackout events.
- Planner parity: for each stored map, routes between every anchor pair
  match the Python planner's costs.

## Out of scope for now

Replacing the dashboard, changing the firmware beyond v2, new bot
features. Python stays the working host until M9.
