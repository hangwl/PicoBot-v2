# AGENTS.md — working notes for agents

Learning-oriented MapleStory private-server bot: a Raspberry Pi Pico /
CircuitPython device relays real HID input; a Python host watches the
minimap and works a perception-driven farming rotation. Docs live in
`docs/`; `README.md` is the landing page.

## Commands

```bash
.venv/bin/python -m pytest tests/ -x -q   # test suite (~300 tests)
.venv/bin/python -m picobot               # run host (WS :8765, HTTP :8000)
.venv/bin/python -m picobot --debug-frames   # + save debug captures to debug/frames/
```

- The `.python-version` shim (`3.14`) may not be installed — use
  `.venv/bin/python`, not bare `python`/`pytest`.
- Deps are already installed in `.venv` (numpy, Pillow, RapidOCR, mss,
  pyserial, pygetwindow, keyboard, websockets).
- Git: branch `feat/smart-bot`; only push when asked.

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
- **Manual geometry is authoritative**: drawn platforms/walls/floor drive
  navigation. No line auto-detection.
- **Panel region**: found by `find_frame` (sizes differ per map); dropped
  on arrival unless `config`-pinned; relocated only when a frame is
  actually found elsewhere.
- **Pin semantics**: `active_map` stands unless a confident title match
  names a different stored map. Blank-name layout writes need `via: ocr`.
- **Calibration save preserves** walls/platforms/`map_name`/skills —
  `merge_recording` exists because a fresh `MapEntry` wiped layouts.
- Region/coords: client-area px for rects; 0–1 normalized in map files.
  Panel-view frames carry `ox`/`oy` offsets — shift overlay meta and
  subtract it on canvas drags.
- Event bus has levels; `hid`/serial chatter stays `debug`.

## Map

| Area | Files |
|---|---|
| Bot loop / FSM | `picobot/bot/smart_bot.py`, `bot/states/` |
| Map-change monitor + identity | `bot/monitor.py`, `bot/identity.py`, `vision/transition.py` |
| Map store + identity | `bot/maps.py`, `vision/minimap.py`, `vision/mapname.py` |
| Recording | `bot/calibrate.py` |
| Host + dashboard cmds | `serve.py`, `remote/control.py` |
| Frame pipeline | `remote/streamer.py` (`assemble_panel`, `annotate`) |
| Dashboard UI | `remote/dashboard.html` |
| Debug frame capture | `vision/framelog.py` → `debug/frames/` |

See `docs/` for the full picture: `architecture.md`, `map-detection.md`,
`calibration.md`, `bot-behavior.md`, `dashboard.md`, `configuration.md`,
`development.md`, `learnings.md` (what was tried and why it changed).
