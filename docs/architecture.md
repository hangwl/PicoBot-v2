# Architecture

PicoBot has three layers: a hardware HID relay, a perception/vision stack
that watches the game window, and a decision layer (the smart bot) plus a
web dashboard that exposes all of it. The host is a Rust workspace; the
original Python host lives on the `legacy/python` branch.

```
┌────────────┐   serial   ┌──────────────────────────────────────────┐
│ Pico /     │ ◄────────► │  picobot host (rust/, picobot.exe)        │
│ CircuitPy  │  HID bytes │                                          │
│ HID relay  │            │  io::serial ──► SerialLink (v2 protocol) │
└─────┬──────┘            │  core::bot  ──► Machine, Patrol, …       │
      │ USB HID           │  io + core  ──► capture, minimap, OCR    │
      ▼                   │  host       ──► Host (the app), streamer │
   game PC                └──────┬───────────────────┬───────────────┘
                                 │ WS :8765          │ HTTP :8000
                                 ▼                   ▼
                          host/src/server.rs     web/ (Preact dashboard)
                          (hid|… relay)          (the UI)
```

Three crates under `rust/crates/`:

- **`core`** — pure logic, no OS calls: config and data formats, the
  navigation graph and planner, learned reach, skills, the vision
  algorithms, map identity, title matching, and the bot itself. All of it
  is tested without a game, a screen or a Pico.
- **`io`** — Windows-facing I/O: the serial link and HID controller,
  window lookup, GDI screen capture, and the OCR recogniser.
- **`host`** — the `picobot` binary: the dashboard server, the threads,
  and the glue that gives the bot a real body.

## HID relay (`CIRCUITPY/`, `io/src/serial.rs`, `io/src/hid.rs`)

A Raspberry Pi Pico running CircuitPython presents itself to the game PC
as a real USB keyboard/mouse. The host sends compact payloads over serial
(`SerialLink`: framing, the `PICO_READY` handshake, port discovery);
`HidController` wraps them as press/release/move with ACKs, human key
spacing and held-key tracking, and releases everything when dropped. Key
timing is humanized host-side — the firmware relays raw down/up events
verbatim, so inputs are indistinguishable from a physical keyboard. That
is the point: the project exists because recorded macro playback is too
easily detected.

Wire protocol (one line each way):

- Host → Pico: `<seq>:hid|key|down|<name>` (also `up`, `hid|mouse|…`,
  `hid|move|dx|dy`, `hid|scroll|dx|dy`, `hid|release_all`);
  `hello|handshake` (answered with
  `PICO_READY`); `ka` keepalive when idle (~0.4s), no reply.
- Pico → host: `ACK <seq>` / `NACK <seq>` (unknown key or command, or
  one that raised — every numbered command is answered).
  Replies are matched by number, so one that arrives after its sender
  timed out can't be credited to a later command.
- Versions: current firmware announces `PICO_READY v2`; only then does
  the host number commands and send `ka`. With older firmware (plain
  `PICO_READY`) commands go out unnumbered and bare `ACK`s are matched
  first-in-first-out.
- Failsafe: the firmware releases every key and button when the DATA
  port disconnects, and — once the host has shown it speaks v2 (a
  numbered command or `ka`) — when nothing has arrived for 2s while
  anything is held: a crashed or hung host never leaves a key down.
  On the Pico itself: everything is released at startup and when
  `code.py` exits (a crash, or Ctrl-C in maintenance boot), a hardware watchdog (4s,
  `HW_WATCHDOG`) resets a hung board, and auto-reload is off so saving a
  file to CIRCUITPY can't restart it mid-hold (reset to load an edit).
- `HidController` counts a key as held from the moment it tries to press
  it, and an unconfirmed press (or click) still sends its release. A
  key-up is tried up to 3 times and the key stays tracked until one is
  confirmed; if `release_all` can't confirm every key-up it ends with
  `hid|release_all`.
- Key leases: the bot's key-downs carry a lease
  (`hid|key|down|<name>|3000`, `KEY_LEASE`); the firmware lets go of a
  leased key (or mouse button) not renewed or released in time. The bot
  thread renews held keys (a repeated leased down — the OS sees nothing)
  from its sleeps and long holds, so a hung bot thread loses its keys
  within 3s even while the reader's keepalives flow. A down without a
  lease (dashboard keys) holds until its up; older firmware ignores the
  field.
- Held-key query: `hid|held` is answered `ACK <seq> left|space|mouse:left`
  (`|`-joined — `,` is a key name; `SerialLink::query` returns the data,
  older firmware NACKs it). After a bot run or measurement the host asks,
  and releases anything still held that no dashboard finger holds
  (`Clients::held_keys`). Connection has "Check held keys" (`host|held`)
  and "Release all keys" (`host|release_all`).
- Key map: `hid|keys` is answered with every name the firmware's
  `KEY_MAP` knows. On connect the host compares it with
  `picobot_core::keys::PICO_KEYS` and warns when they differ (a stale
  `code.py` on the Pico) or when the firmware predates the query. A bot
  run or measurement won't start while a configured key (arrows, move
  keys, rune key, skills) is one the Pico can't press — checked against
  the Pico's list, or the host's when the Pico can't say.
- The reader only reads bytes already waiting: reader and writer share
  one synchronous Windows handle, and a read left blocking would hold
  every write behind it. Round trips are ~4 ms.
- A port that fails underneath the reader is closed and reported
  (`Remote: Serial lost`); waiting senders fail at once. A
  `SerialReconnect` thread then stops any bot run or measurement and
  retries every 2s: the lost port once it's back, otherwise only ports
  that appeared since the loss (probing toggles DTR). Reconnected, it
  waits for `PICO_READY` and sends `hid|release_all`; the run is left
  for the user to restart. A port picked in Connection meanwhile ends
  the retries.

## Vision (`io/src/window.rs`, `io/src/capture.rs`, `core/src/{vision,minimap,title,fuzzy,identity}.rs`, `io/src/ocr.rs`)

- `GameWindow` locates the game window and exposes the **client-area**
  rect. Every pixel coordinate below this layer is client-relative:
  captures never include the OS title bar or borders.
- `ScreenGrabber` — GDI `BitBlt` captures (BGRA) into a reused DIB
  section; not `Send`, so each thread keeps its own (`host/src/feed.rs`
  `Eyes`).
- `vision.rs` — colour masks, the percentile darkness test, frame
  finding (`find_frame`), marker blobs, platform lookups.
- `minimap.rs` — `TransitionDetector` (the loading-blackout trigger),
  `MinimapAnalyzer` (panel region + provenance, blackout, relocation,
  markers; shared behind a mutex) and a per-reader `PlayerTracker`.
- `title.rs` — the title band, its segmentation (`title_scan`) and the
  crop OCR reads; `fuzzy.rs` — `title_score` and the sibling rules;
  `identity.rs` — `MapIdentity` (pin + voted title reads).
- `ocr.rs` — PaddleOCR's recogniser (ONNX, via `ort`) on each title line.

See [map-detection.md](map-detection.md).

## Bot (`core/src/bot/`)

The bot is written against a `Body` trait (`body.rs`): the world it needs
(keys, minimap frames, the player dot, hazards, focus, a clock, the map)
plus the movement primitives as default methods. The host's `HostBody`
(`host/src/botbody.rs`) is the real one; the tests use a physics sim.

- `machine.rs` — the GRIND / TRAVEL / PAUSE state machine (PAUSE
  interrupts and resumes).
- `grind.rs` — arrival skills, summons, buffs, the single-anchor weave,
  recorded legs.
- `patrol.rs` — the continuous patrol: a planned anchor loop executed leg
  by leg, splices after a miss, bans, rope learning.
- `navigator.rs` — runs a graph route move by move (landing rule, rope
  fallback); `flight.rs` and `measure.rs` record flights and measure
  moves.

Around it in `core/src/`: `navgraph.rs` (the movement graph and Dijkstra),
`planner.rs` (loop order), `reach.rs` (learned move envelopes),
`rotation.rs` (anchors and legs), `maps.rs` (the map store), `skills.rs`
(the per-skill cooldown book), `summons.rs`, `timing.rs` (humanized
delays), `layout.rs` and `platform_fit.rs` (drawn-geometry edits and the
fit diagnostic). Movement geometry (platforms) is **hand-drawn in the
dashboard** — auto-detection of translucent minimap lines proved too
fragile and was deliberately abandoned.

## Host (`host/src/`)

- `main.rs` — arguments (`--root`, `--port`, `--window`, `--ws`, `--http`,
  `--notify-test`), the async runtime, the two listeners.
- `host.rs` — `Host`, the app: config, the serial link, the map store and
  identity, the vision feed, bot and measurement runs, the `maps` /
  `config` / `measure` events. `commands.rs` holds the dashboard's edit
  commands (class, skills, keys, patrol, layout, nav preview).
- `server.rs` — HTTP (the built dashboard with the live WS port filled
  in, `/health`) and the WebSocket (hello, ping/pong, routing);
  `clients.rs` — connected clients, held keys, one-frame slots.
- `feed.rs` — the shared analyzer and the `MapMonitor` thread (20 Hz:
  blackouts, panel moves, title bands for the `TitleOCR` worker).
- `streamer.rs` + `frames.rs` — the `FrameStreamer` thread: Panel /
  Window / Title view, overlays (`annotate`, `assemble_panel`), JPEG,
  `PBF1` frames.
- `botbody.rs` — `HostBody` and the bot and measurement threads.
- `bus.rs` — the levelled event bus (debug < info < warn < error;
  `hid`/serial chatter is debug); `telegram.rs` — safety alerts.

Threads: a small async runtime serves HTTP and WS; the serial reader, the
serial writer (remote keys), `DashboardCommands` (commands run in order,
never on the async runtime), `MapMonitor`, `FrameStreamer`, `TitleOCR`,
the bot or measurer, the flight sampler during a flight, and one thread
per Telegram alert. The bot owns its state; the dashboard reaches it
through the host (config versions, pending skill books, the published
`Viz`), and nothing calls out while holding a lock.

## Data flow (dashboard frame)

```
MapMonitor (20 Hz) → note_frame() (blackout → arrival, panel moves)
                  └► name band ──► TitleOCR worker ──► MapIdentity
FrameStreamer → capture region ──► player dot (or the bot's Viz)
map meta + overlays ──► assemble_panel (title+map composite, ox/oy)
                    ──► annotate ──► JPEG ──► PBF1 binary WS frame
```

## Directory map

```
rust/
├── crates/core/src/
│   ├── bot/            # body, machine, grind, navigator, patrol, flight, measure
│   ├── config.rs       # AppConfig + BotConfig (config.json)
│   ├── maps.rs         # maps/ store
│   ├── identity.rs     # map identity (pin + title reads)
│   ├── title.rs, fuzzy.rs, vision.rs, minimap.rs
│   ├── navgraph.rs, planner.rs, reach.rs, rotation.rs
│   ├── skills.rs, summons.rs, timing.rs, anchor_stats.rs
│   └── layout.rs, platform_fit.rs, json.rs, fileio.rs
├── crates/core/tests/  # sim-based bot suites, parity fixtures, key map check
├── crates/io/src/      # serial, hid, window, capture, ocr, perf
├── crates/io/examples/ # pico_ping, serial_latency, dot_rate, ocr_check, vision_bench
└── crates/host/src/    # the picobot binary
CIRCUITPY/              # Pico firmware (code.py)
web/                    # the dashboard (Preact)
scripts/                # cpu-sample.ps1
```
