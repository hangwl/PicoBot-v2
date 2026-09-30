# Dashboard protocol (v1)

The host speaks one WebSocket protocol; the Preact dashboard (`web/`) is
its client.
Versioned by the `hello` handshake: the host sends
`dash|{"event":"hello","protocol":1}` immediately after a client
connects. A client that doesn't understand the version should warn and
reconnect.

Transport: one WebSocket (`/`), text frames for JSON, binary frames for
the view stream. No compression. The WS port is normally 8765 but moves
to the next free port if that one is taken; the host writes the live
port into the served `index.html` (`<meta name="pb-ws-port">`).

Replies to requests (`map|list`, `config|get`, `events|history`, …) and
the stream view/fps are **shared**: every connected client receives them
and follows the same view.

## Client → host (text, `|`-separated)

| Message | Meaning |
|---|---|
| `ping|<nonce>` | heartbeat — the host echoes `pong|<nonce>`. The dashboard pings every 5s and treats 12s without *any* message as a dead link (drops the socket, reconnects); it also probes on returning to the foreground |
| `bot|start` / `bot|stop` / `bot|query` | bot lifecycle; `query` replies with a `bot` event |
| `map|list` | re-send the maps payload |
| `map|set|<name>` | pin a map ("" = auto-detect) |
| `map|stats|reset[|<name>]` | clear the map's anchor stats |
| `map|players|<follow\|ignore\|allow:N>[\|<name>]` | this map's other-player rule (see configuration); the `maps` event carries it as `players_rule` |
| `map|title|record\|clear[|<name>]` | set a map's recorded title to the title on screen now (refused if another map has it), or clear it; blank name = the resolved map |
| `dash|view|<minimap\|window\|title>` | switch the streamed view |
| `dash|fps|<n>` | stream rate (1–30) |
| `dash|subscribe|frames` | opt in to binary view frames |
| `class|list` | re-send the class/policy state |
| `class|use|<name>` | apply a profile live (stops the bot and any move measurement first); re-sends `class`, `skills` and `config` |
| `class|add|<name>\|{spec}` | create a profile (travel, air_attacks, double_flash, teleport_key, teleport_cooldown, skills) and apply it |
| `class|caps|{json}` | edit the active profile's abilities (`double_flash`, `air_attacks`: bool); re-sends `class` and `config` |
| `patrol|policy|<weighted\|greedy>` | set the loop-ordering policy |
| `patrol|rope_penalty|<s>` / `patrol|walk_factor|<x>` | planner costs: seconds added to every rope climb (0–600) and the multiplier on walking legs (0.1–10); saved to `config.json`, then re-sends `config` |
| `patrol|temp|<v>` | set the weighted-roulette temperature |
| `skills|set\|{json}` (`name, key, kind, cooldown`, attacks also `stance` (ground/air/any) and `weight`, summons also `charges, duration`) / `skills|del\|<name>` / `skills|list` | edit the active skill book (profile kit when a profile is active; the first edit gives an inheriting profile its own kit) |
| `safety|set|{json}` | edit the pause toggles (`stop_when_players_appear`, `stop_when_rune_appears`, `stop_when_map_unrecognized`: bool), `allowed_other_players` (≥ 0), `other_player_min_px` (≥ 1) and `heartbeat_minutes` (≥ 0); saved to `config.json`, then re-sends `config` |
| `attacks|set|{json}` | `move_attack_chance`, `ground_attack_chance`, `weave_double_chance` (0–1) and `target_attacks_per_min` (≥ 0); saved to `config.json`, then re-sends `config` |
| `notify|test` | send one Telegram test alert; the host answers with a `notify` (sent) or `error` event |
| `movekeys|set|{json}` | movement keybinds (jump/rope-lift/flash) + nav radius |
| `layout|save\|clear\|reset[|<name>]` | layout lifecycle |
| `layout|plat\|anchor\|…[|<name>]` | drawn geometry (see below) |
| `nav|show|on\|off` / `nav|preview|x,y` | graph overlay + route preview (minimap px) |
| `measure|start` / `measure|stop` / `measure|status` | move measurement (the moves the active class can use); `status` re-sends the `measure` event |
| `measure|start|<move>` | measure one move of the class's plan (e.g. `rope_lift`); others keep their reach |
| `measure|profile|effects` | skill-effect sweep: how far each attack shifts a flash's landing and how long it holds (flash classes), and how far each ground-castable attack moves a standing character; saves `profiles.skill_effects` (rows tagged `where: air\|ground`) in the reach file |
| `measure|effect|<skill>` | the skill-effect sweep for one skill (keeps the other skills' rows); each skill's rows are saved as soon as it is done |
| `measure|profile|walk` | walk-tap sweep then walking pace (stopped with `measure|stop`); saves `profiles.walk_taps` and `profiles.walk_speed` in the reach file |
| `measure|profile|up_flash` | up-flash timing sweep (stopped with `measure|stop`); saves `profiles.up_flash` in the reach file |
| `host|serial\|<port\|auto>` / `host|window|<title>` | connection |
| `key|down\|<k>` / `key|up\|<k>` | remote input pad (Pico HID): a real press and release — the dashboard sends them on touch and lift. `<k>` is a Pico `KEY_MAP` name. Keys a client still holds when it disconnects are released |
| `config|get` | full config snapshot |

Commands with a trailing `[|<name>]` target the named map; blank
resolves through map identity (title-OCR verified).

### Layout commands

- `layout|plat|x0,y0,x1,y1[|<name>]` — platform segment (minimap px)
- `layout|plat|undo|clear|tidy[|<name>]` — undo restores the list before
  the last edit; tidy levels near-flat segments and merges same-row
  overlaps (new drags are tidied automatically)
- `layout|rope|del|<x0,y0,x1,y1>[|<name>]` — remove one learned rope
  (the `maps` event's rope `key`); `layout|rope|clear|undo[|<name>]`
- `layout|plat|here[|<name>]` — move the drawn line under the standing
  player (nearest row within 10px at their x) onto their feet; anchors
  follow; refused while the dot moves or with no line near
- `layout|plat|feet|<x0,y0,x1,y1>[|<name>]` — move that stored line (the
  platform-fit row's `key`) by its median feet offset; anchors follow;
  refused with too few samples or when the line changed since
- `layout|erase|x,y[|<name>]` — delete the anchor within 12px of the click, else the learned rope within 6px of its column and span; a stray click deletes nothing
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
| `maps` | `{maps, active, detected, via, title, score, reading, platforms_n, anchors_n, platform_fit, ropes, recorded_title, anchor_stats}` — `recorded_title`: the resolved map's stored title; `anchor_stats`: per anchor `{name, visits, last, misses, skips: {why: n}}`; — `ropes`: learned ropes `{key, x, top, bottom}` (minimap px); — `platform_fit`: per drawn platform `{x0, x1, row, n, key, offset?, spread?, coverage?}` (minimap px; offset = median feet − row, + = drawn too high); re-sent at most every 5s while samples arrive |
| `class` | `{active, profiles: {name: {travel, air_attacks, teleport_key}}, policy, temp, measured, measure_plan, measured_moves}` — `measure_plan` is the class's measurement moves, `measured_moves` those measured, `measured` their count; re-sent when a measurement ends |
| `measure` | `{running, move, plan, results: {move: {dx} \| {rise} \| {skipped}}, mode, profile, profiles}` — `mode` is `moves` or `up_flash_profile`; `only` the single move being measured (null = whole plan); `profile` the live sweep rows `{delay, n, gap, rise, sd, min, max, peak_t, air}` (`delay` null = plain jump); `profiles` the saved sweeps `{move: {at, rows}}`. Sent on every change of a run, on `measure|status`, and on class switches |
| `skills` | `{source, inherited, skills}` — the book the bot uses: `source` is the active profile or `global`; `inherited` = the profile has no kit of its own and uses the global book |
| `config` | full `BotConfig` snapshot |
| `host` | `{ports, windows, serial, window, serial_open}` |
| `bot` | `{running}` — on `bot|query`, and whenever the bot starts or stops |

Binary frame: `PBF1` magic + u32 BE JSON length + JSON meta + JPEG.
Meta carries `mode`, `w`, `h`, `ox`/`oy` (panel offset), `state` (bot
FSM state), `hazard`, `summons` (`{placed: [{skill, anchor, left}],
charges: {skill: [have, max]}}`) and `patrol` (`{target, leg, legs, move,
misses, next: [anchor], arrived, halted}` — the loop's current target,
the leg being run, misses toward it, the next anchors, anchors reached
this run, and whether it's halted with no plan) in every view,
`player`, `others` (other-player markers on the minimap), `session` (the run's `{up, visits, misses, skips, pauses, paused, apm, moves: {kind: {ok, failed}}}` in seconds and counts; only while the bot runs), and map identity (`map`, `map_via`,
`map_conf`, `map_title`, `layout`, `no_rotation`). Overlays (platforms,
ropes, anchors, routes) are drawn host-side into the JPEG: in the Panel
view shifted by `ox`/`oy`, in the Window view shifted to where the
minimap sits in the client area, and left out of the Title view.
Frames are only captured while at least one client is subscribed.
A client whose previous frame is still unsent after 10s is closed
(code 1011) so it reconnects instead of silently starving. If captures
fail for 5s straight, the host logs a warning and rebuilds the vision
feed (fresh window lookup, capture handles, map monitor) unless a bot or
measurement is running on it.
