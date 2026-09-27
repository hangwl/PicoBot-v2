# Architecture

PicoBot has three layers: a hardware HID relay, a perception/vision stack
that watches the game window, and a decision layer (the smart bot) plus a
web dashboard that exposes all of it.

```
┌────────────┐   serial   ┌──────────────────────────────────────┐
│ Pico /     │ ◄────────► │  picobot host (`python -m picobot`)   │
│ CircuitPy  │  HID bytes │                                      │
│ HID relay  │            │  transport/ ──► SerialManager        │
└─────┬──────┘            │  bot/       ──► SmartBot + FSM       │
      │ USB HID           │  vision/    ──► screen/minimap/OCR   │
      ▼                   │  remote/    ──► WS relay + streamer  │
   game PC                │  serve.py   ──► BotHost (the app)    │
                          └──────┬───────────────────┬───────────┘
                                 │ WS :8765          │ HTTP :8000
                                 ▼                   ▼
                          remote/control.py      dashboard.html
                          (hid|… relay)          (the UI)
```

## HID relay (`CIRCUITPY/`, `transport/`, `bot/inputs.py`)

A Raspberry Pi Pico running CircuitPython presents itself to the game PC
as a real USB keyboard/mouse. The host sends compact payloads over serial
(`serial_manager.py` handles framing, PICO_READY handshake, port
autodetect); `bot/inputs.py`'s `HidController` wraps them as press/release/
move with ACKs and held-key tracking. Key timing is humanized host-side —
the firmware relays raw down/up events verbatim, so inputs are
indistinguishable from a physical keyboard. That is the point: the
project exists because recorded macro playback is too easily detected.

## Vision (`vision/`)

- `game_window.py` — locates the game window, exposes the **client-area**
  rect. Every pixel coordinate in the codebase below this layer is
  client-relative: captures never include the OS title bar or borders.
- `screen.py` — `mss`-backed BGR captures of arbitrary client rects.
- `minimap.py` — `MinimapAnalyzer` finds the panel by its white frame
  (`find_frame`), tracks player/rune/other-player blobs (feet-anchored),
  feeds the blackout detector, and relocates when the panel moves.
- `transition.py` — loading-blackout detector (the map-change trigger).
- `mapname.py` — title band, segmentation, RapidOCR, `title_score`.
- `framelog.py` — debug frame capture to `debug/frames/`.

See [map-detection.md](map-detection.md).

## Bot (`bot/`)

`smart_bot.py` is a perception → decide → act loop. `monitor.py`'s
`MapMonitor` thread samples the minimap at 20 Hz for blackouts, panel
moves and title reads into the shared `MapIdentity` (`identity.py`); the
bot's `minimap_frame()` captures and applies identity changes;
a small FSM (`machine.py`, `states/`) owns behavior:

- **Grind** — the farming state. ≥2 anchors → planned checkpoint patrol
  (nearest-neighbour routing, per-tick attack weaving, stall guards);
  1 anchor → weave-in-place dwells.
- **Travel** — `walk_to`/`climb`/jump legs between anchors on different
  levels. Attacks weave in during travel too — no blind legs.
- **Wander** — anti-patternization detours.
- **Pause** — safety stop (rune, other players, focus loss, map change
  mid-leg).

`rotation.py` models the map's anchor/leg graph; `maps.py` is the JSON
store plus identity matching; `skills.py` is the per-skill cooldown
scheduler (attack/buff/summon kinds); `calibrate.py` is the anchor
recorder. Movement geometry (platforms, walls, floor) is **hand-drawn in
the dashboard** — auto-detection of translucent minimap lines proved too
fragile and was deliberately abandoned.

## Host & remote (`serve.py`, `remote/`)

`serve.py`'s `BotHost` is the app: it owns the serial transport, the
`_VisionFeed` (idle-mode perception), the bot lifecycle, map store,
calibration runner, and the dashboard command surface (`map|set`,
`cal|*`, `layout|*`, `skills|*`, `dash|view|*`, …).

`remote/control.py` relays `hid|…` payloads (dashboard input pad, the
Flutter app) to serial. `remote/streamer.py` captures frames at
`view_fps`, annotates overlays (`annotate`, `assemble_panel`,
`annotate_title`), JPEG-encodes, and sends binary frames to clients that
subscribed (`dash|subscribe|frames`); a client still receiving the
previous frame skips the next one. Dashboard commands run in order on a
`DashboardCommands` worker thread, never on the WS event loop, so slow
commands can't stall `hid|…` relay. `ScreenGrabber` keeps one mss
instance per thread.
`remote/http.py` serves `dashboard.html`.

Threads: the WS server, the HTTP server, the frame streamer, and the bot
task all run concurrently — all mutation of shared state funnels through
`BotHost` methods or the `EventBus` (`events.py`, leveled: debug < info <
warn < error; `hid`/serial chatter is debug).

## Data flow (dashboard frame)

```
MapMonitor (20 Hz) → note_frame() (blackout → arrival, panel moves)
                  └► identity.pump(name band) ──► TitleOCR worker
feed/bot capture → minimap_img() ──► overlays, player position
snap = viz/map meta + overlays ──► assemble_panel (title+map composite,
                                   ox/oy offset) ──► annotate ──► JPEG
                                                          └─► binary WS frame
```

## Directory map

```
picobot/
├── bot/                # smart bot
│   ├── states/         # FSM states: Grind, Travel, Wander, Pause
│   ├── __main__.py     # headless bot entry
│   ├── base.py         # lifecycle + event sink
│   ├── calibrate.py    # anchor recorder (+ CLI)
│   ├── config.py       # BotConfig (config.json["bot"])
│   ├── inputs.py       # HidController (ACK'd payloads, held-key tracking)
│   ├── machine.py      # FSM runtime
│   ├── maps.py         # maps/ store + identity matching
│   ├── rotation.py     # anchor/leg graph model
│   ├── skills.py       # per-skill cooldown scheduler
│   ├── smart_bot.py    # perception → decide → act task
│   └── timing.py       # humanized delays
├── remote/
│   ├── control.py      # WS server + hid|… relay + dashboard commands
│   ├── dashboard.html  # web UI
│   ├── http.py         # embedded HTTP server
│   └── streamer.py     # annotated frame feed (assemble_panel, annotate)
├── transport/
│   └── serial_manager.py
├── vision/             # mss capture, window handle, minimap + title OCR
├── events.py           # structured leveled event bus
├── serve.py            # headless host (the app)
└── config.py, settings.py, messaging.py
CIRCUITPY/              # Pico firmware (code.py)
picobot_controller/     # Flutter remote app
tests/                  # pytest suite, mirrors picobot/
```
