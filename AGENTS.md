# AGENTS.md — working notes for agents

Learning-oriented MapleStory private-server bot: a Raspberry Pi Pico /
CircuitPython device relays real HID input; a Python host watches the
minimap and works a perception-driven farming rotation. Docs live in
`docs/`; `README.md` is the landing page.

## Commands

```bash
.venv/bin/python -m pytest tests/ -x -q   # test suite (~283 tests)
.venv/bin/python -m picobot               # run host (WS :8765, HTTP :8000)
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
  OCR'd in-game title only. Fingerprints are `g2:` structural hashes —
  never colour-based.
- **OCR is event-gated** (map change, pin change, cal save, map|list) —
  never run it per-frame.
- **Manual geometry is authoritative**: drawn platforms/walls/floor drive
  navigation. Ink/platform auto-detection feeds fingerprints only.
- **Watchdog self-consistency**: a miss counts only when it matches the
  previous miss frame; on confirm, drop non-`config` regions.
- **Pin semantics**: `active_map` survives unless its stored fingerprint
  verifiably mismatches or OCR names a different stored map.
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
| Map store + identity | `bot/maps.py`, `vision/minimap.py`, `vision/mapname.py` |
| Recording | `bot/calibrate.py` |
| Host + dashboard cmds | `serve.py`, `remote/control.py` |
| Frame pipeline | `remote/streamer.py` (`assemble_panel`, `annotate`) |
| Dashboard UI | `remote/dashboard.html` |
| Debug frame capture | `vision/framelog.py` → `debug/frames/` |

See `docs/` for the full picture: `architecture.md`, `map-detection.md`,
`calibration.md`, `bot-behavior.md`, `dashboard.md`, `configuration.md`,
`development.md`, `learnings.md` (what was tried and why it changed).
