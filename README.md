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

## Dashboard

- **Connection** — pick the Pico DATA serial port (or *Auto*-detect) and the
  game window title. Selections persist to `config.json`.
- **View** — live annotated minimap feed (player dot, anchors, nav target,
  hazard markers) or the full game window on demand. The fps selector sets
  the stream rate (persisted as `"view_fps"` in `config.json`, default 10;
  1–30 allowed — higher rates cost more CPU on captures + JPEG encode).
  Layout controls: *Save layout* / *Forget* / *Re-detect* / *Draw minimap*.
  With the map field blank, *Save/Forget* only act when the live minimap
  fingerprint verifies the map — a stale `active_map` pin can't redirect
  a write to the wrong file. Typing a map name writes that file
  explicitly (creating a stub if it doesn't exist yet) and refreshes its
  fingerprint.
- **Bot** — Start/Stop the smart bot.
- **Calibrate** — Record → walk your rotation pressing *Mark anchor* at each
  farming spot → Save. The recorder derives walk/climb legs from your position
  trace, dwell ranges from how long you stood, and candidate skills from the
  keys you pressed at each anchor. Marks taken mid-air or off a platform
  warn immediately, and saved anchors are snapped onto the nearest
  platform ink so replayed targets stay reachable. Headless alternative:
  `python -m picobot.bot.calibrate --window "Eluna (x64)" --name my_map`
  (F9 = mark, ESC = save).
- **Remote input** — arrow pad + key buttons for rune solving or nudges from a
  phone.
- **Events** — structured stream of everything the bot perceives and does
  (`fsm`, `nav`, `skill`, `hid`, `safety`, …).

## Maps & rotations

Each map is a JSON file in `maps/` holding a rotation graph — anchors (farming
spots) and legs (walk / flash-jump / climb steps between them), plus skill
bindings. Map identity is a minimap **ink fingerprint** (`fingerprint`) —
only platform/line pixels feed the hash, so translucent minimap backgrounds
can't drift matching as the character moves.
`minimap_region` is the minimap layout remembered at calibration time — when
the map is identified, the bot restores that region, so auto-detection drift
can't accumulate on known maps (delete the key to force re-detection). When
no region is set at all, the bot seeds itself by trying each saved map's
remembered region + fingerprint before border auto-detection — so a layout
you verified in the dashboard carries over to the bot at Start:

```json
{
  "name": "limina_1f_east",
  "map_name": "Limina : 1-5 East",
  "fingerprint": "<hex>",
  "minimap_region": [8, 56, 200, 150],
  "rotation": {
    "style": "loop",
    "position_jitter_px": 4,
    "rest_chance": 0.02,
    "wander_chance": 0.05,
    "travel_style": "mixed",
    "anchors": [
      {"name": "west", "pos": [0.31, 0.55], "dwell": [8, 14],
       "on_arrive": ["fountain"], "face": "left"}
    ],
    "legs": [
      {"from": 0, "to": 1, "steps": [
        {"walk_to": [0.5, 0.55], "style": "flash"},
        {"climb": {"dir": "up", "until_y": 0.31, "x": 0.5}},
        {"walk_to": [0.72, 0.31]}
      ]}
    ]
  },
  "skills": {
    "fountain": {"key": "s", "cooldown": 57, "kind": "summon",
                 "wait_on_arrival": 4}
  }
}
```

- Coordinates are 0–1 fractions of the minimap region (values >1 are raw px).
- `style`: `loop` | `pingpong` | `shuffle`.
- Step kinds: `walk_to` (+ `style`: walk|flash|mixed), `climb`
  (`dir`, `until_y`, optional align `x`), `up_jump`, `down_jump`, `wait`.
- Skill `kind`: `attack` (attack loop), `buff` (fires when ready anywhere),
  `summon` (fires at anchors listing it via `on_arrive`).
- `wait_on_arrival`: seconds the bot waits at an anchor for a skill's cooldown
  before giving up — players wait a beat for their summon too.
- During dwells the bot **weaves**: it flash-hops (or walks) back and
  forth across the anchor's platform — bounds come from the map's
  hand-drawn platform segment, falling back to `weave_range_px` when
  nothing is drawn — pressing the attack *after* the flash-jump re-press
  fires (an earlier press eats the FJ input window).
  `wall_zone_px` forces an inward facing near the map's left/right edges
  so weave can't wall-bang. Set `dwell_weave: false` to stand still.
- **Per-map walls**: on maps whose play area doesn't span the minimap,
  stand at the left/right wall and press **L wall** / **R wall** in the
  View panel — the boundary is stored on that map (normalized x), drawn
  as a dashed red line, and overrides `wall_zone_px` on that side.
  **Floor** does the same vertically: stand on the bottom platform and
  the bot stops attempting down-jumps at/below it. **Clear** removes
  all boundaries; the map field targets a different map.
- **Platforms are hand-drawn, not detected.** Minimap platform lines are
  alpha-blended over the live scene, so neither colour matching nor
  structural detection proved trustworthy — instead, click **Draw plats**
  in the View panel and drag a segment along each platform line on the
  minimap view (the mode stays armed; drag once per platform). Segments
  are stored on the map normalized to the minimap size, rendered back as
  cyan lines so you can verify them, and drive anchor snapping, nav
  target projection, weave bounds, and Floor placement. **Undo** pops the
  last segment, **Clear** removes all; the map field targets a different
  map. Maps without drawn platforms fall back to `weave_range_px` and
  unsnapped targets — nothing breaks, geometry just isn't verified.
- Player positions are **feet-anchored**: `player_pos` reports the
  marker icon's bottom row — the point touching the platform — so
  recorded anchors and the Floor boundary land on the platform line
  rather than floating at icon-center height. (Floor placement also
  snaps to the nearest drawn platform segment.)
- **Patrol mode** (`"patrol": true` on a map's rotation, or the
  **patrol anchors** checkbox): anchors become checkpoints — instead of
  parking at one, the dwell weaves *toward the next anchor* and advances
  on arrival. A 2-anchor map becomes a continuous back-and-forth patrol;
  legs are only used when the next checkpoint sits on another level.
  Anchor precision matters less in patrol mode — only the heading (x)
  and same-level check (y) are used, not a pixel-exact servo target.

## Bot configuration

Global tuning lives in `config.json` under `"bot"` — keys, timing, safety
toggles, minimap colors/region, flash jump, map store:

```json
{
  "bot": {
    "attack_keys": ["a"],
    "buff_keys": ["shift"],
    "buff_interval_seconds": 60,
    "jump_key": "alt",
    "up_jump_skill_key": null,
    "up_jump_skill_cooldown": 3.0,
    "stationary_seconds": 20,
    "wander_seconds": 15,
    "skill_gap_seconds": [0.5, 1.0],
    "nav_threshold_px": 5,
    "dwell_weave": true,
    "weave_range_px": 24,
    "wall_zone_px": 16,
    "stationary_mode": true,
    "enable_random_wander": true,
    "stop_when_players_appear": true,
    "stop_when_rune_appears": true,
    "pause_on_lie_detector": false,
    "minimap_region": [x, y, w, h],
    "minimap_colors": {
      "player": [12, 240, 239],
      "other_player": [118, 45, 253],
      "rune": [255, 102, 221],
      "border": [228, 228, 228],
      "ink": null
    },
    "flash_jump": {"enabled": true, "key": null},
    "travel_style": "mixed",
    "maps_dir": "maps",
    "active_map": null,
    "auto_select_map": true,
    "map_match_threshold": 15.0,
    "marker_inset_px": 4
  }
}
```

Notes:

- `up_jump_skill_key` is the rope-lift style skill used instead of the
  jump+up+jump combo; presses are gated by `up_jump_skill_cooldown`
  (default 3s) and nav rides out the cooldown rather than misreading a
  suppressed press as "can't climb".
- Flash jump is jump-again-mid-air — it always uses `jump_key` (`key` is
  a legacy override, normally left null).
- Legacy `attack_keys`/`buff_keys` are synthesized into skills when no explicit
  `"skills"` map exists — new configs should use named skills.
- `minimap_region` is `(x, y, w, h)` **relative to the game window's client
  area** (i.e. below the OS title bar — captures never include the title bar
  or window borders). Set it if
  auto-detection fails on your client.
- `marker_inset_px` (default 4) crops that many pixels off the minimap rim
  before marker (player/rune/other-player) detection — frame and panel-chrome
  pixels can't register as markers, and marker positions are the centroid of
  the largest matching blob, not a global mean.
- Navigation targets are projected onto the nearest drawn platform
  segment before travel (within ~8px; beyond that the target is left
  alone), and vertical jumps that produce no progress twice in a row end
  the leg (aligned in x = "arrived"; misaligned = abort) — a target
  recorded beneath the lowest platform can no longer loop the bot forever.
- `minimap_colors.ink` is an optional *global* platform-line colour that
  only feeds the map **fingerprint** mask — fingerprints hash detected
  line structure (plus configured ink) so translucent backgrounds can't
  drift map matching. It plays no role in movement/platform geometry.
- Safety: losing window focus, a rune marker, other players on the minimap, or
  an unexpected map change mid-leg pauses the bot (and fires a Telegram alert
  if configured). Solve the check via the dashboard's remote input pad.
- `pause_on_lie_detector` is a **seam, not a feature**: `check_lie_detector` is
  a documented stub pending template images. Keep it off.
- Key timing is humanized host-side; the Pico firmware relays raw down/up
  events and is unchanged.

## Remote connections over mobile data (Tailscale)

- Install Tailscale on the desktop host and your phone, sign in to the same
  tailnet, and enable **MagicDNS**.
- Open `http://<host>.tail-xxxx.ts.net:8000` on the phone — the dashboard shows
  the live minimap/window feed and the remote input pad (rune solving, lie
  detector checks) works anywhere. WireGuard encryption means no port
  forwarding and no TLS needed.
- The Flutter controller app (`picobot_controller/`) still works as a remote
  control: it sends `hid|…` payloads over the same WebSocket protocol. Macro
  playlist commands are gone along with playback.

## Picobot directory tree

```
picobot/
├── bot/                # smart bot
│   ├── states/         # FSM states: Grind, Travel, Wander, Pause
│   ├── __main__.py     # headless bot entry
│   ├── base.py         # lifecycle + event sink
│   ├── calibrate.py    # walk-once rotation recorder (+ CLI)
│   ├── config.py       # BotConfig (config.json["bot"])
│   ├── inputs.py       # HidController (ACK'd payloads, held-key tracking)
│   ├── machine.py      # FSM runtime
│   ├── maps.py         # maps/ store + fingerprint matching
│   ├── rotation.py     # anchor/leg graph model
│   ├── skills.py       # per-skill cooldown scheduler
│   ├── smart_bot.py    # perception → decide → act task
│   └── timing.py       # humanized delays
├── remote/
│   ├── control.py      # WS server + hid|… relay + dashboard commands
│   ├── dashboard.html  # web UI
│   ├── http.py         # embedded HTTP server
│   └── streamer.py     # annotated frame feed
├── transport/
│   └── serial_manager.py
├── vision/             # mss capture, window handle, minimap analysis
├── events.py           # structured event bus
├── serve.py            # headless host (the app)
└── config.py, settings.py, messaging.py
```

## Deprecated

Recorded macro playback (`playback/`, `macro_recorder.py`, playlist commands,
the Tk GUI) was removed — blind playback is too easily detected. The rotation
system replaces it.
