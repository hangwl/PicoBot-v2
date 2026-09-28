# Dashboard protocol (v1)

The host speaks one WebSocket protocol for every client — the legacy
single-file dashboard and the Preact app (`web/`) are both clients.
Versioned by the `hello` handshake: the host sends
`dash|{"event":"hello","protocol":1}` immediately after a client
connects. A client that doesn't understand the version should warn and
reconnect.

Transport: one WebSocket (`/`), text frames for JSON, binary frames for
the view stream. No compression.

## Client → host (text, `|`-separated)

| Message | Meaning |
|---|---|
| `ping|<nonce>` | heartbeat — the host echoes `pong|<nonce>` |
| `bot|start` / `bot|stop` / `bot|query` | bot lifecycle; `bot|running`/`bot|stopped` replies |
| `map|list` | re-send the maps payload |
| `map|set|<name>` | pin a map ("" = auto-detect) |
| `dash|view|<minimap\|window\|title>` | switch the streamed view |
| `dash|fps|<n>` | stream rate (1–30) |
| `dash|subscribe|frames` | opt in to binary view frames |
| `class|list` | re-send the class/policy state |
| `class|use|<name>` | apply a profile live (stops the bot first) |
| `class|add|<name>\|{spec}` | create a profile (travel, air_attacks, teleport_key, teleport_cooldown, skills) and apply it |
| `patrol|policy|<weighted\|greedy>` | set the loop-ordering policy |
| `patrol|temp|<v>` | set the weighted-roulette temperature |
| `skills|set\|{json}` / `skills|del\|<name>` / `skills|list` | edit the active skill book (profile kit when a profile is active) |
| `movekeys|set|{json}` | movement keybinds (jump/rope-lift/flash) + nav radius |
| `layout|save\|clear\|reset[|<name>]` | layout lifecycle |
| `layout|plat\|anchor\|…[|<name>]` | drawn geometry (see below) |
| `layout|region|<minimap\|title>\|x,y,w,h` | region calibration (Window view px) |
| `nav|show|on\|off` / `nav|preview|x,y` | graph overlay + route preview (minimap px) |
| `measure|start` / `measure|stop` | move measurement |
| `host|serial\|<port\|auto>` / `host|window|<title>` | connection |
| `key|down\|<k>` / `key|up\|<k>` | remote input pad (Pico HID) |
| `config|get` | full config snapshot |

Commands with a trailing `[|<name>]` target the named map; blank
resolves through map identity (title-OCR verified).

### Layout commands

- `layout|plat|x0,y0,x1,y1[|<name>]` — platform segment (minimap px)
- `layout|plat|undo|clear[|<name>]`
- `layout|anchor|<x>,<y>[|<name>]`, `layout|anchor|del|<x>,<y>`,
  `layout|anchor|undo|clear[|<name>]`
- `layout|save|clear|reset[|<name>]`

## Host → client

Text, `dash|` + JSON:

| Event | Payload (beyond `event`) |
|---|---|
| `hello` | `{protocol: 1}` — first message on connect |
| `evt` | `{kind, level, msg, t}` — log lines |
| `history` | `{items: [evt…]}` — replay on connect |
| `maps` | `{maps, active, detected, via, title, score, reading, platforms_n, anchors_n}` |
| `class` | `{active, profiles: {name: {travel, air_attacks, teleport_key}}, policy, temp, measured}` |
| `skills` | `{source, skills}` — the book the Skills panel edits |
| `config` | full `BotConfig` snapshot |
| `host` | `{ports, windows, serial, window, serial_open}` |
| `bot` | chip state via `evt` (kind `bot`) |

Binary frame: `PBF1` magic + u32 BE JSON length + JSON meta + JPEG.
Meta carries `mode`, `w`, `h`, `ox`/`oy` (panel offset), and the
overlay geometry (platforms, ropes, anchors, player, nav edges) in
region space shifted by `ox`/`oy`.
