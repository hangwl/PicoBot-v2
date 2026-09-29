# AGENTS.md — working notes for agents

Learning-oriented MapleStory private-server bot: a Raspberry Pi Pico /
CircuitPython device relays real HID input; a Python host watches the
minimap and works a perception-driven farming rotation. Docs live in
`docs/`; `README.md` is the landing page.

## Commands

Development happens on Windows (the game and the Pico live there):

```powershell
.venv\Scripts\python.exe -m pytest tests/ -x -q   # test suite (~400 tests)
.venv\Scripts\python.exe -m picobot               # run host (WS :8765, HTTP :8000)
.venv\Scripts\python.exe -m picobot --debug-frames   # + save debug captures to debug/frames/
cd web; npm install; npm run build                 # build the dashboard (web/dist)
```

- Use the venv interpreter, not bare `python`/`pytest`.
- Line endings: files are CRLF on disk, LF in git (`core.autocrlf=true`).
- The host `chdir`s to the project folder on start, so `config.json`,
  `maps/` and `nav_reach*.json` resolve there wherever it's launched from.
- Deps are already installed in `.venv` (numpy, Pillow, RapidOCR, mss,
  pyserial, pygetwindow, keyboard, websockets).
- Git: work on feature branches off `feat/smart-bot`; only push when asked.

## Comments

Keep comments concise, or leave them out. Comments go stale; code is
always current. Never narrate history or past attempts in code — record
what was tried and what was learned in `docs/learnings.md` instead.

## Invariants — don't break these

- **Map identity**: `MapEntry.name` = user alias; `MapEntry.map_name` =
  OCR'd in-game title only — it's what identity matches. One
  `MapIdentity` is shared by host and bot; don't add parallel resolvers.
- **Map change = loading blackout** (`vision/transition.py`), sampled
  only by the `MapMonitor` thread (20 Hz, own grabber). Never feed
  `note_frame` from the bot/feed, and never use pixel-content change as
  a trigger — translucent UI defeats it.
- **OCR is request-driven** (startup, arrival, pin, title band) and runs
  on the `TitleOCR` worker — never per-frame, never on the frame thread.
- **Manual geometry is authoritative**: drawn platforms/walls drive
  navigation. No line auto-detection.
- **Panel region**: found by `find_frame` (sizes differ per map); dropped
  on arrival unless `config`-pinned; relocated only when a frame is
  actually found elsewhere.
- **Pin semantics**: `active_map` stands unless a confident title match
  names a different stored map. Blank-name layout writes need `via: ocr`.
- **Class profiles** gate the move kit: `class_travel` (flash|teleport|
  walk) decides which edges the graph generates and which weave
  primitive runs; `air_attacks: false` moves the attack tail after
  landing (mages can't attack suspended). Teleport is a move kind with
  a learned envelope and cooldown-aware planning.
- **Movement rule (human-like)**: bot-controlled travel between points
  is flash hops — jump, then the mid-air re-press — with **1–2 attacks
  woven after the flash triggers** (never before: it eats the re-press
  window). Walk only for short final approaches. Delays are log-normal
  (`timing.human_between`/`human_delay`), never flat `uniform`, and
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
  **Every flash move weaves attacks; no attack outside a flash move** in
  moving paths.
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
  broadcasts or callbacks inside `with …lock:`. Logging reaches the
  event bus, which broadcasts under `clients_lock`: a call-out under that
  lock deadlocks the streamer, then the bot and the WS loop.
- **Profile skills**: a profile with a `skills` entry (even `{}`) owns its
  kit; without one it inherits the global book. Anything that changes the
  kit re-broadcasts `skills`/`config`; reads never seed a profile.
- **Remote keys mirror the finger**: `key|down` on touch, `key|up` on
  lift — never synthesized taps. `web/src/keys.ts` `PICO_KEYS` must match
  the firmware `KEY_MAP` (a test checks it).

## Map

| Area | Files |
|---|---|
| Bot loop / FSM | `picobot/bot/smart_bot.py`, `bot/states/` |
| Pathfinding | `bot/navgraph.py` (graph + Dijkstra), `bot/navigator.py` (execution), `bot/reach.py` (learned reach), `bot/patrol.py` (continuous loop) |
| Ropes/ladders | **learned from stable off-graph hangs confirmed by a Down probe** (`MapEntry.ropes`, merged per column), never drawn → `climb_up` (jump-grab: direction + up held through the jump) / `climb_down` edges; no direct rope release — failed climbs exit via direction + jump (`rope_exit`) |
| Rope avoidance | climb edges cost `rope_penalty` (default 5s) — ropes are a **last resort**; platforms are normally reachable via jumps/rope lift/teleport. Rope mapping exists for accidental-grab recovery and future precise moves (rune solving) |
| Map-change monitor + identity | `bot/monitor.py`, `bot/identity.py`, `vision/transition.py` |
| Map store + identity | `bot/maps.py`, `vision/minimap.py`, `vision/mapname.py` |
| Move measurement | `bot/measure.py` |
| Host + dashboard cmds | `serve.py`, `remote/control.py` |
| Frame pipeline | `remote/streamer.py` (`assemble_panel`, `annotate`) |
| Dashboard UI | `web/` (Preact, mobile-first — build with `npm run build` in `web/`, served from `web/dist`): `src/protocol.ts` (store, WS, hash routes), `live.tsx` (top bar, view, pad, log), `setup.tsx` (Setup pages), `keys.ts` (held keys); protocol in `docs/protocol.md` |
| Debug frame capture | `vision/framelog.py` → `debug/frames/` |

See `docs/` for the full picture: `architecture.md`, `map-detection.md`,
`layout.md`, `bot-behavior.md`, `dashboard.md`, `configuration.md`,
`development.md`, `learnings.md` (what was tried and why it changed).
