# Dashboard

Served at `http://localhost:8000` (Tailscale `http://<host>.ts.net:8000`
for remote). Single-page WebSocket UI — commands are `|`-delimited
strings; pushes are `dash|{json}` text messages plus binary view frames.
The page reconnects with backoff (0.5s → 10s) and uses `wss://` when
`ws_tls` is on.

## Panels

- **Connection** — Pico DATA serial port (or *Auto* probe) and game
  window title; persisted to `config.json`.
- **Map** — the single source of truth: selector pins a map
  (`active_map`), `auto-detect` defers to live identity, `+ new map…`
  names the next layout save. `detected:` shows
  `alias (ocr|pin) · title "…" 98%` — the resolved map, how it was
  resolved, the accepted title and its match score, and `reading title…`
  while a vote is in progress.
- **View** — frame views + layout tools:
  - **Panel** (default) — the whole located minimap panel: title strip
    on top (green boxes = accepted text lines, orange = the OCR crop),
    a separator at the panel's divider row, then the annotated minimap
    (player dot, anchors, nav target, hazards, wall zones,
    platforms). Its right edge extends to the title's end so long names
    aren't clipped. Platform drags land correctly — frames carry an
    `ox`/`oy` minimap offset that the client subtracts.
  - **Title** — the raw segmented band as OCR sees it (verification).
  - **Window** — the full client area (used for Draw minimap/title).
  - **Place anchors** — click to add a patrol checkpoint (snaps to the
    drawn platform), shift-click to remove the nearest; Undo / Clear.
  - **Route** — overlays the movement graph (greens: up flash / up-side
    flash / rope lift; oranges: down-jump / drop; purples: jump / flash /
    double flash); click the Panel view to preview a route (yellow).
    While patrolling, the full loop plan is drawn olive and the current
    segment yellow.
  - fps selector persists `view_fps` (default 10, 1–30).
- **Bot** — Start/Stop.
- **Calibrate** — Record / Mark anchor (F9) / Save. See
  [layout.md](layout.md).
- **Skills** — registry for attacks, buffs, summons, movement keys.
- **Remote input** — arrow pad + key buttons (rune solving from a phone).
- **Events** — structured log with severity tabs.

## Event log

Every event carries `debug < info < warn < error` (kind-based defaults,
overridable per emit). Defaults: `hid` and serial `TX:`/`RX:` chatter →
debug; `notify`/`safety` → warn; `error` → error; the rest → info.

Tabs: **all · info · warn · error** (minimum severity). `info` is the
default view — relay noise hidden, nav/fsm story visible; `all` is for
Pico link debugging. The client buffers 600 events so tab switches don't
lose history; backlog entries render at their real timestamps.

## Protocol sketch

```
client → host:  map|set|<name> | map|list | cal|start|mark|finish|<name>
                layout|region|<minimap|title>|x,y,w,h
                layout|plat|… | layout|wall|left|right|clear[|<name>]
                dash|view|<minimap|window|title> | dash|fps|<n>
                skills|set|{json} | hid|… | host|window|<title> | host|serial|…
                dash|subscribe|frames            (opt in to view frames)
                nav|show|on|off | nav|preview|x,y   (minimap px)
                layout|anchor|x,y | layout|anchor|del|x,y | layout|anchor|undo|clear  [|<name>]
host → client:  binary frame: b"PBF1" | u32 BE json len | {"event":"frame","ox":…,"oy":…,"map":…} | JPEG
                dash|{"event":"evt","kind":…,"level":…,"msg":…}
                dash|{"event":"maps"|"skills"|"history"|…}
```
