# PicoBot

PicoBot is a perception-driven game bot. A compatible microcontroller (e.g.
Raspberry Pi Pico running CircuitPython) acts as a real HID device and relays
keyboard/mouse inputs to a computer — inputs arrive exactly like a physical
keyboard. On top of that transport, a smart bot watches the game's minimap,
navigates between anchor points, and fires skills off per-skill cooldowns,
mimicking how a real player works a farming rotation.

Note that while I am using a Raspberry Pi Pico device, other microcontroller
devices that support `Circuit Python` should still work. The `Adafruit HID`
library is required to relay physical keyboard inputs.

## Disclaimer

This project is purely for learning purposes. Picobot was built for my personal
botting needs in a Maplestory private server. Botting is a punishable offense,
please use the program at your own risk.

## Installation

The host is written in Rust (`rust/`). The original Python host is kept
on the `legacy/python` branch.

1. Install Rust (<https://rustup.rs>) and Node.js.
2. Build the dashboard: `cd web; npm install; npm run build`.
3. Build the host: `cd rust; cargo build --release` (the first build
   downloads the ONNX runtime used for title OCR).
4. Title OCR needs PaddleOCR's recogniser, `PP-OCRv6_rec_small.onnx`:
   put it in `models/` in the project folder (the file ships in the
   `rapidocr` Python package's `models/` folder). Without it, map
   identity falls back to the pin.

## Running

```bash
cd rust
cargo run --release -p picobot-host -- --root ..
# explicit overrides:  ... -- --root .. --port COM3 --window "Eluna (x64)"
# auto-detect the Pico: ... -- --root .. --port auto
```

`--root` is the project folder (where `config.json`, `maps/`, the reach
files and `web/dist` live). The built binary works the same way:
`rust\target\release\picobot.exe --root .` from the project root.

This starts the headless host: serial transport + WebSocket (default :8765) +
HTTP dashboard (default :8000). Open `http://localhost:8000` — the dashboard is
the UI. The Pico DATA port and game window can be picked from the dashboard's
**Connection** panel (selects, or *Auto* to probe for the Pico); both are
remembered in `config.json` so subsequent runs need no flags. CLI flags
override the remembered values and are persisted the same way.

Telegram alerts use `bot_token` and `chat_id` in `config.json`;
`picobot --root . --notify-test` sends one test alert and exits.

The Python host on `legacy/python` reads the same `config.json`, `maps/`
and reach files and uses the same ports and COM port — run one host at a
time.

## The dashboard at a glance

Mobile-first: on a phone it's four screens behind a bottom nav; on a
desktop, two columns. See [docs/dashboard.md](docs/dashboard.md).

- **Home** — live view, bot status, one big Start/Stop.
- **Control** — rune solving: hazard banner, view, arrow pad and keys.
  A rune or another player jumps here and vibrates the phone.
- **Setup** — readiness checklist plus pages for the map, its layout,
  class (with move keys), skills, measuring moves, patrol (with anchor
  stats) and connection.
- **Log** — levelled events (`debug < info < warn < error`) with
  severity filters; HID/serial chatter sits at debug.

Drawing platforms and placing anchors happens on the desktop, with the
tools under the view.

## How it works

- **Farming**: anchors are patrol checkpoints — with ≥2 the bot plans a
  nearest-neighbour route from the player's position and weave-attacks
  between every checkpoint; single anchors weave in place. Movement
  geometry (platforms, walls, floor) is **hand-drawn** — authoritative,
  since auto-detecting translucent minimap lines proved too fragile.
- **Map identity**: map changes are detected from the loading blackout,
  the minimap panel is located by its white frame, and the map is named
  by its **OCR'd title** (voted, fuzzy-matched against `map_name`, read
  on a worker thread). Translucent UI can't trigger any of these.
- **Safety**: rune markers, other players, window-focus loss, or a
  map transfer (loading screen) pause the bot (Telegram alert if configured);
  remote input solves checks from a phone over Tailscale.

## Documentation

| Doc | Covers |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Components, layers, data flow, directory map |
| [docs/map-detection.md](docs/map-detection.md) | Blackout trigger, panel detection, title OCR, pins |
| [docs/layout.md](docs/layout.md) | Layout drawing, anchor placement, move measurement, map format |
| [docs/bot-behavior.md](docs/bot-behavior.md) | Patrol routes, weaving, skills, safety |
| [docs/dashboard.md](docs/dashboard.md) | Panels, views, event levels, WS protocol |
| [docs/configuration.md](docs/configuration.md) | `config.json` reference |
| [docs/development.md](docs/development.md) | Setup, tests, conventions, debug frame captures |
| [docs/learnings.md](docs/learnings.md) | What was tried, what we learned |

## Remote connections over mobile data (Tailscale)

- Install Tailscale on the desktop host and your phone, sign in to the same
  tailnet, and enable **MagicDNS**.
- Open `http://<host>.tail-xxxx.ts.net:8000` on the phone — the dashboard shows
  the live feed and the remote input pad works anywhere. WireGuard
  encryption means no port forwarding and no TLS needed.
