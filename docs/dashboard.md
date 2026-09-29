# Dashboard

Served at `http://localhost:8000` (Tailscale `http://<host>.ts.net:8000`
for remote). Single-page WebSocket UI — commands are `|`-delimited
strings; pushes are `dash|{json}` text messages plus binary view frames.
The page reconnects with backoff (0.5s → 10s) and uses `wss://` when
`ws_tls` is on. A heartbeat catches links that die without closing (a
sleeping phone, a network switch): 12s of silence — or no answer within
3s of coming back to the foreground — drops the socket and reconnects,
with the top bar showing *Reconnecting*.

## The app

The dashboard is a Preact + TypeScript app in `web/`, served from
`web/dist`. The build output is not committed: run
`cd web && npm install && npm run build` once (and after changes);
without it the host serves a page saying so. The protocol is versioned via the
`hello` handshake and documented in [protocol.md](protocol.md).

## Layout

Mobile-first. On a phone the app is four screens behind a bottom nav;
from 900px wide it becomes two columns — the live column (view, drawing
tools, status, Start/Stop) on the left and Control / Setup / Log tabbed
on the right. The screen lives in the URL hash (`#/home`, `#/control`,
`#/setup/skills`, `#/log`), so the phone's back button walks back through
it and a screen can be bookmarked.

A sticky top bar always shows the bot's state (Farming / Traveling /
Paused / Stopped / Offline, with a coloured dot), the map and the link.

- **Home** — the live view, view switcher (Panel / Window / Title) and
  fps, a status list (map + how it was identified, class kit, route
  size, last skill cast) and one large Start/Stop button.
- **Control** — rune solving on one screen: hazard banner, a compact
  view, the arrow pad, quick keys (jump key, ctrl, shift, enter) and a
  key-name field (limited to the Pico's `KEY_MAP` names). Keys mirror the
  finger: `key|down` on touch, `key|up` on lift, so a tap is a tap, a
  hold is a hold, and several fingers chord (hold left, tap jump). Held
  keys are released when the page is hidden or loses focus; the host
  releases a disconnected client's keys. A new hazard (rune, another player, verification
  prompt) jumps here from any screen and vibrates the phone where the
  browser allows it; the bot resumes by itself once it clears. The Control
  tab carries a red badge while a hazard is active.
- **Setup** — a readiness banner (map identified → platforms → anchors →
  moves measured; tap it for the next step) over a list of one-off tasks,
  each opening its own page:
  - **Map and layout** — pin a map, `Auto-detect` (live identity), or
    `New map…` (names the next layout save and pins it); detected map,
    how it was resolved, the accepted title and score; Save layout /
    Re-detect / Forget (confirmed); Tidy platforms / Undo platform; and
    the **platform fit** list — per platform, whether the feet settle on
    its line or it was drawn too high / too low, with **Move to feet**
    on flagged rows. Layout actions show the host's reply under the
    buttons.
  - **Class** — pick or create a profile (name, movement, air attacks,
    teleport key). Switching applies live and stops the bot first.
  - **Skills** — the active skill book (the profile's kit when one is
    active); add or remove (confirmed) attacks, buffs, summons.
  - **Move keys** — jump / rope lift / flash keys and arrival radius; only
    edited fields are saved.
  - **Measure moves** — the class's moves with each one's status (not
    measured / measuring / px result / skip reason) and one button that
    is *Measure moves* when idle and *Stop measuring* while running.
    While measuring, the top bar says so and Start bot is replaced.
  - **Patrol** (loop order + temperature),
    **Connection** (Pico serial port, *Find the Pico* auto-probe that
    skips the open port, game window — locked while the bot runs).
- **Log** — the event log with severity filters; while scrolled up it
  stops following and shows an "N new" chip that jumps back down.

### Drawing (desktop)

Layout drawing is desktop-only: the tools sit under the view in the live
column and act in the Panel view (arming one switches to it).

- **Draw platforms** — drag along each platform line; stays armed for
  successive drags. **Undo platform** pops the last one.
- **Place anchors** — click to add a patrol checkpoint (snaps to the
  drawn platform); **Undo anchor** removes the last one.
- **Preview route** — overlays the movement graph (greens: up flash /
  up-side flash / rope lift; oranges: down-jump / drop; purples: jump /
  flash / double flash); click to preview a route (yellow). While
  patrolling, the full loop plan is drawn olive and the current segment
  yellow.

Frames carry an `ox`/`oy` minimap offset that the client subtracts, and
the view is letterboxed to fit the screen, so drags land where drawn.
The **Panel** view is the whole located minimap panel (title strip with
green boxes = accepted text lines, orange = the OCR crop, then the
annotated minimap); **Title** is the raw segmented band as OCR sees it;
**Window** is the full client area with overlays moved onto the minimap.
All three carry the bot's state and hazard.

## Event log

Every event carries `debug < info < warn < error` (kind-based defaults,
overridable per emit). Defaults: `hid` and serial `TX:`/`RX:` chatter →
debug; `notify`/`safety` → warn; `error` → error; the rest → info.

Filters: **Info+ · Warn+ · Errors · All** (minimum severity). `Info+`
is the default — relay noise hidden, nav/fsm story visible; `All` is for
Pico link debugging. The client keeps the last 300 events; the log
follows new lines only while scrolled to the bottom.

Not yet in the app: rope/anchor delete by click.

## Protocol sketch

```
client → host:  map|set|<name> | map|list | class|list | class|use|<name>
                class|add|<name>|{travel,air_attacks,teleport_key,…}
                patrol|policy|<weighted|greedy> | patrol|temp|<v>
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
