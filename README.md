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

1. Install Python 3.14 or later.
2. (Optional) Create and activate a virtual environment.
3. From the project root, install dependencies in editable mode: `pip install -e .`

## Running

```bash
python -m picobot
# or with explicit overrides: python -m picobot --port COM3 --window "Eluna (x64)"
# or auto-detect the Pico:   python -m picobot --port auto
```

This starts the headless host: serial transport + WebSocket (default :8765) +
HTTP dashboard (default :8000). Open `http://localhost:8000` — the dashboard is
the UI. The Pico DATA port and game window can be picked from the dashboard's
**Connection** panel (selects, or *Auto* to probe for the Pico); both are
remembered in `config.json` so subsequent runs need no flags. CLI flags
override the remembered values and are persisted the same way.

## The dashboard at a glance

- **Connection** — Pico DATA serial port + game window (persisted).
- **Map** — the single source of truth for which map every section acts on:
  pin a map, `auto-detect` defers to live identity (the OCR'd map
  title), and `detected:` shows the map, how it was resolved, and the title.
- **View** — the **Panel** feed composites the located minimap panel:
  title strip on top (text lines boxed, orange = OCR crop), a divider
  separator, then the annotated minimap below — its right edge extends to
  the title's end so long names aren't clipped. Also **Title** (raw
  segmented band) and **Window** (full client).
  Layout tools: *Save layout* / *Forget* / *Re-detect* / *Draw plats* /
  *Place anchors* / route preview.
- **Bot** — Start/Stop the smart bot.
- **Measure moves** — the character works each move kind so the planner
  learns its reach.
- **Skills** — registry for attacks, buffs, summons, movement keys.
- **Remote input** — arrow pad + key buttons (rune solving from a phone).
- **Events** — levelled log (`debug < info < warn < error`) with
  severity tabs; HID/serial chatter sits at debug.

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
