# Dashboard

Served at `http://localhost:8000` (Tailscale `http://<host>.ts.net:8000`
for remote). Single-page WebSocket UI — all commands are `|`-delimited
strings; all pushes are `dash|{json}` frames or `evt` events.

## Panels

- **Connection** — Pico DATA serial port (or *Auto* probe) and game
  window title; persisted to `config.json`.
- **Map** — the single source of truth: selector pins a map
  (`active_map`), `auto-detect` defers to live identity, `+ new map…`
  names the next calibration save. `detected:` shows
  `alias (OCR "…") fp 87%` — alias, ground-truth title text, fingerprint
  corroboration.
- **View** — frame views + layout tools:
  - **Panel** (default) — the whole located minimap panel: title strip
    on top (green boxes = accepted text lines, orange = the OCR crop),
    a separator at the panel's divider row, then the annotated minimap
    (player dot, anchors, nav target, hazards, wall/floor zones,
    platforms). Its right edge extends to the title's end so long names
    aren't clipped. Platform drags land correctly — frames carry an
    `ox`/`oy` minimap offset that the client subtracts.
  - **Title** — the raw segmented band as OCR sees it (verification).
  - **Window** — the full client area (used for Draw minimap/title).
  - fps selector persists `view_fps` (default 10, 1–30).
- **Bot** — Start/Stop.
- **Calibrate** — Record / Mark anchor (F9) / Save. See
  [calibration.md](calibration.md).
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
                layout|plat|x0,y0,x1,y1[|<name>] | layout|wall|… | layout|floor|…
                dash|view|<minimap|window|title> | dash|fps|<n>
                skills|set|{json} | hid|… | host|window|<title> | host|serial|…
host → client:  dash|{"event":"frame","jpeg":…,"ox":…,"oy":…,"map":…}
                dash|{"event":"evt","kind":…,"level":…,"msg":…}
                dash|{"event":"maps"|"skills"|"history"|…}
```
