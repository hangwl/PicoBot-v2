# Dashboard

Served at `http://localhost:8000` (Tailscale `http://<host>.ts.net:8000`
for remote). Single-page WebSocket UI — commands are `|`-delimited
strings; pushes are `dash|{json}` text messages plus binary view frames.
The page reconnects with backoff (0.5s → 10s) and uses `wss://` when
`ws_tls` is on.

## Setup flow

The left column walks the end-to-end setup as a checklist: **identify**
the map (title OCR) → **draw platforms** → **place anchors** →
**measure moves** → pick the **class profile** (or create one with
**+ new class…**: name, travel style, air attacks, teleport key —
created, persisted and applied live) and **patrol policy** → **Start**. Each row shows its state (green = done) with a one-click
jump to the tool. The status strip under the header shows the detected
map, the active class kit, and the patrol policy at a glance. Switching
the class profile applies **live** — the bot is stopped first (the kit
decides which moves exist), the profile is persisted to config, and the
planner rebuilds with the new kit.

## The app

The dashboard is a Preact + TypeScript app in `web/`, served from
`web/dist`. The build output is not committed: run
`cd web && npm install && npm run build` once (and after changes);
without it the host serves a page saying so. The protocol is versioned via the
`hello` handshake and documented in [protocol.md](protocol.md).

## Panels

- **Connection** — Pico DATA serial port (or *Auto* probe, which skips
  the port already open) and game window title; persisted to
  `config.json`. The window can't be switched while the bot runs.
- **Map** — the single source of truth: selector pins a map
  (`active_map`), `auto-detect` defers to live identity, `+ new map…`
  names the next layout save (which then pins the new map). `detected:` shows
  `alias (ocr|pin) · title "…" 98%` — the resolved map, how it was
  resolved, the accepted title and its match score, and `reading title…`
  while a vote is in progress.
- **View** — frame views + layout tools:
  - **Panel** (default) — the whole located minimap panel: title strip
    on top (green boxes = accepted text lines, orange = the OCR crop),
    a separator at the panel's divider row, then the annotated minimap
    (player dot, anchors, nav target, hazards,
    platforms). Its right edge extends to the title's end so long names
    aren't clipped. Platform drags land correctly — frames carry an
    `ox`/`oy` minimap offset that the client subtracts.
  - **Title** — the raw segmented band as OCR sees it (verification).
  - **Window** — the full client area, overlays moved onto the minimap.
  - Drawing tools (plats, anchors, route) only act in the Panel view;
    arming one switches to it.
  - **Place anchors** — click to add a patrol checkpoint (snaps to the
    drawn platform); Undo anchor removes the last one.
  - **Route** — overlays the movement graph (greens: up flash / up-side
    flash / rope lift; oranges: down-jump / drop; purples: jump / flash /
    double flash); click the Panel view to preview a route (yellow).
    While patrolling, the full loop plan is drawn olive and the current
    segment yellow.
  - fps selector persists `view_fps` (1–30).
- **Bot** — Start/Stop (enabled by the live running state).
- **Skills** — registry for attacks, buffs, summons; **Move keys** —
  jump / rope lift / flash keys and nav radius (only edited fields are
  saved; with a class profile active they go into that profile).
- **Remote input** — arrow pad + key buttons (rune solving from a phone).
- **Events** — structured log with severity tabs.

## Event log

Every event carries `debug < info < warn < error` (kind-based defaults,
overridable per emit). Defaults: `hid` and serial `TX:`/`RX:` chatter →
debug; `notify`/`safety` → warn; `error` → error; the rest → info.

Filters: **Info+ · Warn+ · Errors · All** (minimum severity). `Info+`
is the default — relay noise hidden, nav/fsm story visible; `All` is for
Pico link debugging. The client keeps the last 300 events; the log
follows new lines only while scrolled to the bottom.

Not yet in the app: Calibrate (Record / Mark anchor), Draw minimap /
Draw title (`layout|region|…`), rope/anchor delete by click.

## Protocol sketch

```
client → host:  map|set|<name> | map|list | class|list | class|use|<name>
                class|add|<name>|{travel,air_attacks,teleport_key,…}
                patrol|policy|<weighted|greedy> | patrol|temp|<v>
                layout|region|<minimap|title>|x,y,w,h
                layout|plat|… | layout|anchor|… [ |<name>]
                dash|view|<minimap|window|title> | dash|fps|<n>
                skills|set|{json} | hid|… | host|window|<title> | host|serial|…
                dash|subscribe|frames            (opt in to view frames)
                nav|show|on|off | nav|preview|x,y   (minimap px)
                layout|anchor|x,y | layout|anchor|del|x,y | layout|anchor|undo|clear  [|<name>]
host → client:  binary frame: b"PBF1" | u32 BE json len | {"event":"frame","ox":…,"oy":…,"map":…} | JPEG
                dash|{"event":"evt","kind":…,"level":…,"msg":…}
                dash|{"event":"maps"|"class"|"skills"|"history"|…}
```
