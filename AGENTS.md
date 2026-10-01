# AGENTS.md — working notes for agents

Learning-oriented MapleStory private-server bot: a Raspberry Pi Pico /
CircuitPython device relays real HID input; a Rust host (`rust/`) watches
the minimap and works a perception-driven farming rotation. The original
Python host is kept on the `legacy/python` branch (not maintained). Docs
live in `docs/`; `README.md` is the landing page.

## Commands

Development happens on Windows (the game and the Pico live there):

```powershell
cd rust; cargo run --release -p picobot-host -- --root ..   # run host (WS :8765, HTTP :8000)
cd rust; cargo test; cargo clippy --all-targets; cargo fmt   # Rust tests (~220), lints, format
cd rust; cargo run --release -p picobot-io --example serial_latency -- COM6   # Pico round trips (no HID)
rust\target\release\picobot.exe --root . --notify-test     # one Telegram test alert
cd web; npm install; npm run build                         # build the dashboard (web/dist)
```

- Cargo lives in `%USERPROFILE%\.cargo\bin` (in Git Bash:
  `export PATH="$USERPROFILE/.cargo/bin:$PATH"`).
- Line endings: files are CRLF on disk, LF in git (`core.autocrlf=true`);
  the parity fixtures are `-text`.
- The host `chdir`s to `--root` (the project folder), so `config.json`,
  `maps/`, `nav_reach*.json`, `models/` and `web/dist` resolve there.
- Title OCR: `PP-OCRv6_rec_small.onnx` from `models/`, else the RapidOCR
  copy in a local `.venv`.
- The parity fixtures (`core/tests/fixtures/`) were written by the Python
  host; their generator lives on `legacy/python`. New behaviour gets a
  sim test (`core/tests/sim`) instead.
- Git: `master` is the development branch — work on feature branches
  off it and merge back; only push when asked.

## Comments

Keep comments concise, or leave them out. Comments go stale; code is
always current. Never narrate history or past attempts in code — record
what was tried and what was learned in `docs/learnings.md` instead.

## Invariants — don't break these

- **Map identity**: `MapEntry.name` = user alias; `MapEntry.map_name` =
  OCR'd in-game title only — it's what identity matches. One
  `MapIdentity` is shared by host and bot; don't add parallel resolvers.
- **Map change = loading blackout** (`TransitionDetector`,
  `core/src/minimap.rs`), sampled only by the `MapMonitor` thread (20 Hz,
  own grabber). Never call `note_frame` from the bot or the streamer, and
  never use pixel-content change as a trigger — translucent UI defeats it.
- **OCR is request-driven** (startup, arrival, pin, title band, Re-detect,
  and a slow verification read every 5 s that catches a map change with no
  blackout) and runs on the `TitleOCR` worker — never per-frame, never on
  the frame thread.
- **Manual geometry is authoritative**: drawn platforms/walls drive
  navigation. No line auto-detection.
- **Panel region**: found by `find_frame` (sizes differ per map); dropped
  on arrival unless `config`-pinned; relocated only when a frame is
  actually found elsewhere. The whole frame (all four edges) is re-checked
  against the live window every second, and a located frame beats a map's
  stored layout.
- **Pin semantics**: `active_map` stands unless a confident title match
  names a different stored map. Blank-name layout writes need `via: ocr`.
- **Class profiles** gate the move kit: `class_travel` (flash|teleport|
  walk) decides which edges the graph generates and which weave
  primitive runs; `air_attacks: false` moves the attack tail after
  landing (mages can't attack suspended). Teleport is a move kind with
  a learned envelope and cooldown-aware planning.
- **Movement rule (human-like)**: bot-controlled travel between points
  is flash hops — jump, then the mid-air re-press — with an **air attack
  window after the flash triggers** (never before: it eats the re-press
  window; odds `move_attack_chance`, default 1 = every hop; a firing
  window casts 1–2 attacks). Walk only for short final approaches. Delays are log-normal
  (`timing::human_between`/`human_delay`), never flat `uniform`, and
  scaled by the session `TEMPO` (means only — clamps still hold).
  `HidController` spaces consecutive key events; don't bypass it with
  raw sends.
- **Loop ordering is a policy** (`patrol_policy`): `weighted` roulette
  (∝ 1/cost^temp, default) or `greedy` cheapest-next — both ban
  unreachable anchors and execute the plan strictly.
- **Always on the move, strictly on plan**: the patrol executes a
  pre-planned anchor loop leg by leg; the next loop is planned before the
  current one ends, and with no planned path the bot halts (break) rather
  than improvise. Anchors are pure pass-through waypoints (no linger).
  No wander state, dwell timers, breathers, or stationary attack loops.
  Attacks are cast only in **windows** — after a flash triggers (air) or
  after a landing (ground, `ground_attack_chance`) — never in a stationary
  loop, and a rope-grab flash opens none (an attack mid-air costs the
  grab). Skills carry a `stance` (ground|air|any) and `weight`; a
  `target_attacks_per_min` steers the odds toward a rate.
- **Summons**: one live summon per anchor (any kind), cast only while
  standing on a platform (two stable reads, on a drawn line), a moving
  character gets up to 1s to settle and the route is never touched (a
  skipped visit is retried on the next); up to `charges` instances per skill, the oldest replaced.
  Placements are timed bookkeeping in `SummonTracker`, reset on map
  change.
- **Rope grabs are moving grabs**: Up down well before takeoff, then a
  hop (or flash) toward the rope from beside it — never a standing jump
  unless the platform is too narrow. A rope's bottom stays
  `ROPE_BOTTOM_GAP` above the platform under it; its top stands
  `ROPE_TOP_OVERSHOOT` above its platform (older tops up to
  `ROPE_TOP_SLACK` under are lifted when the graph is built).
- **No wall zones**: drawn platform ends are the boundaries — travel
  room, weave bounds and graph edges all stop at the drawn span. Rope
  lift is preferred for rises when ready and grabs the **highest**
  platform within `nav_rope_lift_px`, firing without a precise stop.
- **Move reach is learned**, not hard-coded: planner edges come from
  `ReachModel` envelopes; every jump-type move reports takeoff/landing.
  Landings are scored tolerantly (platform span + row slack) — a
  successful move must never shrink the envelope. A deliberate
  measurement is the exception: `ReachModel.calibrate` sets the envelope
  to the measured result (lower included) and records it in `measured`.
  Upward: `up_flash` (Up + jump mid-air), `up_side_flash`, `rope_lift`.
- **Layout save preserves** walls/platforms/`map_name`/skills — a fresh
  `MapEntry` must never wipe drawn geometry.
- Region/coords: client-area px for rects; 0–1 normalized in map files.
  Panel-view frames carry `ox`/`oy` offsets — shift overlay meta and
  subtract it on canvas drags.
- Event bus has levels; `hid`/serial chatter stays `debug`.
- **Dashboard commands** run on the `DashboardCommands` worker, never the
  WS event loop. View frames are binary and opt-in
  (`dash|subscribe|frames`).
- **Never call out while holding a lock** — no logging, bus emits,
  broadcasts or callbacks while a guard is alive. Logging reaches the
  event bus, whose subscribers broadcast to the clients: a call-out under
  a lock can deadlock the streamer, then the bot and the WS runtime.
  Compute under the lock, emit after.
- **Profile skills**: a profile with a `skills` entry (even `{}`) owns its
  kit; without one it inherits the global book. Anything that changes the
  kit re-broadcasts `skills`/`config`; reads never seed a profile.
- **HID wire safety**: with v2 firmware (`PICO_READY v2`) commands are
  numbered (`<seq>:hid|…` → `ACK <seq>`) and the firmware releases
  everything on disconnect or after 2s of host silence (the serial reader
  sends `ka` when idle); older firmware gets plain commands. Never send HID without
  going through `SerialLink`, and never drop a key-*up*. The serial reader
  only reads bytes already waiting — a blocking read holds every write.
- **Landing = takeoff + steady on a platform** (`Navigator::land`); an
  up flash is planned only onto the highest platform its peak clears; a
  miss counts against reach only when it fell short.
- **Cross-thread state**: the dashboard never mutates bot-owned
  state in place — skill books go through the host's pending book (the
  bot swaps it in on its own thread), settings through the config
  version, maps through saves (the patrol replans on changed anchors).
- **Remote keys mirror the finger**: `key|down` on touch, `key|up` on
  lift — never synthesized taps. `web/src/keys.ts` `PICO_KEYS` must match
  the firmware `KEY_MAP` (a test checks it).

## Map

Rust host (`rust/crates/`):

| Area | Files |
|---|---|
| Bot loop / FSM | `core/src/bot/machine.rs` (GRIND/TRAVEL/PAUSE), `bot/grind.rs` (arrival skills, summons, buffs, weave), `bot/body.rs` (`Body` trait + movement primitives) |
| Pathfinding | `core/src/navgraph.rs` (graph + Dijkstra), `bot/navigator.rs` (execution, landing rule), `reach.rs` (learned reach), `planner.rs` (loop order), `bot/patrol.rs` (continuous loop, rope learning) |
| Map-change monitor + identity | `host/src/feed.rs` (`MapMonitor` thread), `core/src/minimap.rs` (blackout, panel, dot), `core/src/identity.rs` (pin + voted title reads) |
| Titles | `core/src/title.rs` (band segmentation, crop), `core/src/fuzzy.rs` (`title_score`), `io/src/ocr.rs` (recogniser) |
| Move measurement | `core/src/bot/measure.rs`, `bot/flight.rs` (dot arc sampler: peak, landing) |
| Host + dashboard cmds | `host/src/host.rs` (state, bot/measure runs, maps), `commands.rs` (edits: class, skills, layout, nav), `server.rs` (HTTP + WS), `clients.rs`, `bus.rs`, `telegram.rs` |
| The real body | `host/src/botbody.rs` (`HostBody`: Pico keys, captures, stop-aware sleeps) |
| Frame pipeline | `host/src/streamer.rs` (`FrameStreamer` thread), `frames.rs` (`assemble_panel`, `annotate`, `PBF1`) |
| Evidence captures | `host/src/evidence.rs` — minimap crop + context to `debug/frames/*_dotlost/` when the dot is unreadable, and `*_offplatform/` (with a geometry overlay) when the player stands off every drawn platform, while the bot runs |
| Serial / HID / screen | `io/src/serial.rs` (`SerialLink`, v2 protocol), `hid.rs` (`HidController`), `capture.rs`, `window.rs` |
| Layout geometry | `core/src/layout.rs` (tidy, anchors follow lines), `platform_fit.rs` |
| Tests | `core/tests/` (physics sim `tests/sim`, bot suites ported from Python, parity against fixtures the Python host wrote, generator on `legacy/python`) |
| Dashboard UI | `web/` (Preact, mobile-first — build with `npm run build` in `web/`, served from `web/dist`): `src/protocol.ts` (store, WS, hash routes), `live.tsx` (top bar, view, pad, log), `setup.tsx` (Setup list + readiness; Skills/move keys live on Class, Attacks/Patrol/Safety on `pages/tuning.tsx`), `pages/*.tsx`, `ui.tsx` (shared bits: `Field`, `Section` with a "?" hint, `Fold`, `useReply`/`Reply` for the host's answer under a button), `keys.ts` (held keys); protocol in `docs/protocol.md` |

Behaviour notes:

| Area | Notes |
|---|---|
| Ropes/ladders | **learned from stable off-graph hangs confirmed by a Down probe** (`MapEntry.ropes`, merged per column), never drawn → `climb_up` (jump-grab: direction + up held through the jump) / `climb_down` edges; no direct rope release — failed climbs exit via direction + jump (`rope_exit`) |
| Rope avoidance | climb edges cost `rope_penalty` (default 5s) — ropes are a **last resort**; platforms are normally reachable via jumps/rope lift/teleport. Rope mapping exists for accidental-grab recovery and future precise moves (rune solving) |

See `docs/` for the full picture: `architecture.md`, `map-detection.md`,
`layout.md`, `bot-behavior.md`, `dashboard.md`, `configuration.md`,
`development.md`, `learnings.md` (what was tried and why it changed),
`rust-migration.md` (how the Rust host was built and checked against Python).
