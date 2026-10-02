# Dashboard

Served at `http://localhost:8000` (Tailscale `http://<host>.ts.net:8000`
or `http://100.x.y.z:8000` for remote — the host prints every reachable
address at startup, Tailscale first). Both servers listen on IPv4 and
IPv6, own their port exclusively (a stale host can't keep answering on
it), and move to the next free port if it's taken. Single-page WebSocket UI — commands are `|`-delimited
strings; pushes are `dash|{json}` text messages plus binary view frames.
The page reconnects with backoff (0.5s → 10s) and uses `wss://` when
`ws_tls` is on. A heartbeat catches links that die without closing (a
sleeping phone, a network switch): 12s of silence — or no answer within
3s of coming back to the foreground — drops the socket and reconnects,
with the top bar showing *Reconnecting*.

## Phone can't load the dashboard?

1. Open `http://<address>:8000/health` on the phone. JSON back (`"ok":
   true`) means the host is reachable — a page that still won't load is
   an app problem; no answer means the network.
2. Every page request is logged as an `http` event (Log → All): the
   phone's address, the request and the status. No line for the phone's
   attempt means it never reached the host — check Tailscale is on for
   both devices, and that Windows Firewall allows `picobot.exe` inbound
   on ports 8000 and 8765 for the network type Tailscale uses.
3. Try the `100.x.y.z` address the host printed instead of the MagicDNS
   name.

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
  fps, the **class picker** (one tap switches the profile; *Edit* opens
  Setup → Class) and one large Start/Stop button.
- **Control** — rune solving on one screen: hazard banner, a compact
  view, the arrow pad, the solve keys (space, enter, and `y` to interact
  with the rune), **Save window** (a lossless capture of the game window,
  for detection templates), and under **More keys** a key-name field
  (limited to the Pico's `KEY_MAP` names) and, when the map has
  platforms, **Align platform to feet** (walk onto a platform with the
  pad, tap it; the host's reply shows under it). Keys mirror the
  finger: `key|down` on touch, `key|up` on lift, so a tap is a tap, a
  hold is a hold, and several fingers chord (hold left, tap jump). Held
  keys are released when the page is hidden or loses focus; the host
  releases a disconnected client's keys. A new hazard (rune, another player, verification
  prompt) vibrates the phone where the
  browser allows it; the bot resumes by itself once it clears. The Control
  tab carries a red badge while a hazard is active.
- **Setup** — a readiness banner (map identified → platforms → anchors →
  moves measured; tap it for the next step) over a list of one-off tasks,
  each opening its own page:
  - **Map** — which map this is: pin a map, `Auto-detect` (live
    identity), or `New map…` (*Create map* saves and pins it); detected
    map, how it was resolved, the title on screen and the recorded one;
    Save map / Re-detect / Forget (confirmed); Record / Clear title.
    **Other players on this map** follows the global setting, ignores
    the markers (maps that draw monsters as players) or allows up to N.
  - **Layout** — the map's platforms, anchors and ropes: counts, Tidy
    platforms / Undo platform / Align platform to feet, the **platform
    fit** list (per platform, whether the feet settle on its line or it
    was drawn too high / too low, with **Move to feet** on flagged rows)
    and **learned ropes** (remove one, remove all, undo). Drawing itself
    is on the desktop view.
  - **Class** — pick or create a profile (name, movement, air attacks,
    teleport key), and its **move keys** (jump / rope lift / flash keys
    and arrival radius; only edited fields are saved). Switching applies
    live and stops the bot first.
  - **Skills** (a fold on the Class page) — the active skill book (the profile's kit when one is
    active); add or remove (confirmed) attacks, buffs, summons.
  - **Measure moves** — the class's moves with each one's status (not
    measured / measuring / px result / skip reason) and a **Measure**
    button per move (one move on its own), plus one button that
    is *Measure moves* when idle and *Stop measuring* while running.
    While measuring, the top bar says so and Start bot is replaced.
    Flash classes also get **Up-flash timing**: a sweep button and the
    per-delay peaks (live while sweeping, then the saved sweep).
    Every class also gets **Walk taps** (tap lengths, pace), and flash
    classes **Skill effects** (what each attack does to a flash in the air and to you on the ground;
    shown in the Skills fold too).
  - **Tuning** — three folds. **Patrol**: loop order + temperature, and per-anchor **stats**
    (visits, misses, skips by reason; Reset).
    **Attacks**: how often a move and a landing carry attacks, the
    second-attack chance and a target attacks-per-minute; the running
    rate shows here and in the Session row.
    **Safety**: the pause toggles (rune, other players, unrecognized
    map), what to do **on a rune** (walk over then pause, or pause
    here), **Record rune solves** (the solve dataset), how many other players are allowed and the smallest marker
    that counts (Status shows the live count), the log's status-line interval (0 = off; Telegram gets hazards only) and a **Send test
    alert** button. the Status fold on Control shows the running **Session**
    (uptime, visits, misses, skips, pauses); an unrecognized map sends
    you to the Map page, whose banner offers to name and save it.
  - **Connection** (Pico serial port, *Find the Pico* auto-probe that
    skips the open port, game window — locked while the bot runs).
- **Log** — the event log with severity filters (`notify` alerts stand out); while scrolled up it
  stops following and shows an "N new" chip that jumps back down.

Actions that change something show the host's reply right under their
buttons (`useReply`/`Reply` in `web/src/ui.tsx`).

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

- **Erase anchor/rope** — click an anchor (within 12px) or a learned rope
  to delete it; a stray click does nothing.

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
