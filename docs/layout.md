# Map layout, anchors & move measurement

## Placing anchors (preferred)

Draw the platforms first, then on the Panel view press **Place anchors**
and click each patrol checkpoint. Clicks snap to the drawn platform under
them and float `anchor_float_px` above it — the same height as the
player icon (within 12px; a click with nothing under it stays put and the
log says so). Shift-click removes the nearest anchor; **Undo**/**Clear**
act on the Map selection. Anchors are named `a0`, `a1`, … (lowest free
number) and stored normalized in the map's `rotation.anchors`; removing
one drops and reindexes any hand-authored `legs`.

Placed anchors have no `on_arrive` list — at arrival the bot fires any
registered **summon** skill that is off cooldown instead. Order doesn't
matter: the patrol plans its own route.

## Placing anchors

Draw the platforms first, then on the Panel view press **Place anchors**
and click each patrol checkpoint. Clicks snap to the drawn platform under
them and float `anchor_float_px` above it — the same height as the
player icon (within 12px; a click with nothing under it stays put and the
log says so). Shift-click removes the nearest anchor; **Undo**/**Clear**
act on the Map selection. Anchors are named `a0`, `a1`, … (lowest free
number) and stored normalized in the map's `rotation.anchors`; removing
one drops and reindexes any hand-authored `legs`.

Placed anchors have no `on_arrive` list — at arrival the bot fires any
registered **summon** skill that is off cooldown instead. Order doesn't
matter: the patrol plans its own route.

## Measuring moves

The movement graph starts from conservative reach guesses. Press
**Measure moves** (bot stopped, character on an open platform with the
platforms drawn, ideally mid-way along a long platform with another one
above it): the bot performs each move its class can use, in the safest
direction, and **calibrates** that move to the best of its attempts —
a measurement is ground truth, so it can also lower a guess that was too
generous. Results go to the class's reach file
(`nav_reach_<profile>.json`), which also records which moves have been
measured:

- flash classes — flash, double flash, jump, rope lift, up flash;
- teleport classes — jump, sideways teleport, rope lift, up-teleport
  (waiting out the teleport cooldown; the sideways one sets the teleport
  envelope's reach, the upward one its height);
- walk classes, or flash jump disabled — jump and rope lift.

Each move calibrates as soon as its attempts finish, so a stopped run
keeps what it measured. Each move also has its own **Measure** button,
for when one spot doesn't suit every move (rope lift needs a platform
above, sideways moves need room). An attempt is skipped — with the reason shown
next to the move — when the player dot is lost, there's no room or no
platform above, the key isn't bound, the character didn't move, or a
sideways move changed level (fell off or caught a ledge). The Route
overlay immediately shows the connections the new reach unlocks.
Farming keeps refining the numbers (successes grow an envelope; two
consecutive misses shrink it).

The **up flash** is measured by its arc, not its landing: a flight
recorder (`bot/flight.py`) samples the player dot at ~60 Hz on its own
thread while the move runs, and the rise is the recorded peak — so it
needs no platform above and isn't capped by whatever ledge happens to be
there. The patrol's re-press timing varies, so it runs a couple of times
and keeps the **lowest** peak. Other upward moves still measure the
landing (a rope lift's height depends on the platform it grapples).

### Up-flash timing sweep

How high an up flash goes depends on when the second jump is pressed.
**Sweep up-flash timing** (Measure page, flash classes) records that
curve: standing on a drawn platform with at least 45 px of open space
overhead, the character does a plain jump (the baseline) and up flashes
re-pressed at fixed delays from the first key-down (0.08–0.44 s), three
rounds interleaved. Each delay gets its mean peak ± spread, time to the
top, airtime and the actual re-press gap. It's saved under `profiles` in
the reach file. Patrol up flashes then re-press inside its plateau (the
delays whose peak is within 1 px of the best); the sweep itself doesn't
change the up flash's envelope — run **Measure moves** afterwards to
measure the peak at the new timing. Landing on a platform stops the
sweep (the start point would drift); a stopped sweep keeps its rows.

## Drawing layout (dashboard)

All layout tools act on the **Map** panel's selection — one source of
truth, no per-section map names.

- The minimap panel and title strip are found automatically. A
  `minimap_region` / `minimap_name_region` in `config.json` still pins
  them by hand — since minimap size differs per map, prefer fixing
  detection over pinning.
- **Draw plats** — drag a segment along each platform line on the Panel
  view; stays armed for successive drags. Hand drags are tidied as they
  are saved: a near-flat drag (ends within 4px) is levelled at its mean
  height — anything steeper is a real slope and stays, however gentle —
  and level segments on the same row (within 2px) that overlap or meet
  merge into one; pieces with a gap stay apart (the gap may be real). **Tidy
  platforms** (Setup → Map) applies the same to a map drawn earlier.
  Anchors move with their line: when a platform is levelled, merged or
  tidied, each anchor standing on it keeps its x and its float above the
  line. **Undo** restores the list — and the anchors that edit moved,
  if they're still where it left them — as they were before the last
  edit (so undoing a merge brings back what was there),
  **Clear** wipes. Tidying works while the bot runs: the bot reads the
  new lines on its next graph lookup (the loop in progress keeps its
  already-planned legs).
- **Platform fit** (Setup → Map) — while the bot runs, it records where
  the feet settle on each drawn platform (a standstill of ±1px for 0.3s,
  with exactly one platform within 8px). Each platform lists its median
  offset: feet *below* the line mean it was drawn too high — the bot
  then can't tell it stands there (the planner allows 8px above a line
  but only 2px below). A flagged row (5+ samples, 1.5px+ off) has
  **Move to feet**: the line shifts by the median offset (slope kept),
  its anchors follow, the samples carry over (so it reads as fitting
  right away), and **Undo platform** reverts it. Nothing moves on its
  own. Samples live in host memory and reset when a platform is
  redrawn.
- **Align platform to feet** (Control pad, and the Map page) does the
  same from one reading, without waiting for samples: stand still on a
  platform and tap it. The host takes three dot reads 0.1 s apart (all
  within 1px, else "stand still"), picks the drawn line spanning the
  feet whose row is nearest and within 10px, and shifts it onto the
  feet — slope kept, anchors and fit samples following, undoable.
- Ropes are **not drawn** — they're learned: when the bot hangs stable
  at a spot that is on no drawn platform for over 2 seconds (confirmed by
  a Down probe), it records a rope segment from that spot up to the
  platform above (persisted in the map file, drawn as brown lines) and
  leaps off. A rope's bottom end never reaches the platform under it — it
  stops 5px above (stacked tiers stay separate, and boarding is a grab).
  Learned rope climbs cost `rope_penalty` — a last resort, since
  platforms are normally reachable via jumps/rope lift/teleport.
- **Learned ropes** (Setup → Map) lists each rope by column and span;
  **Remove** drops one learned by mistake (it's re-learned if the bot
  really hangs there again), **Remove all** clears them (confirmed),
  **Undo rope** restores.
  Platforms are the *authoritative* walkable geometry: anchor snapping,
  nav target projection (~8px snap radius) and weave bounds consult
  them. Platform ends are the bot's boundaries — no wall zones needed.
- **Save layout / Forget / Re-detect** — commit the located region to
  the map file (backfilling `map_name` from the current title when it's
  missing), drop it, or force re-detection. With no map named, writes
  only act when the title OCR verifies the map — a pin can't redirect a
  write to the wrong file.

## Coordinate conventions

- Map-file coordinates are **0–1 fractions of the minimap region**
  (values >1 are treated as raw px — legacy).
- Capture/region coordinates are **client-area px** (below the OS title
  bar).
- Player positions are **feet-anchored**: `player_pos` reports the marker
  icon's bottom row — the point touching the platform.

## Legs (hand-authored, optional)

The movement graph covers platform moves (walks, flashes, up/double
flashes, rope lift, drops) and learned ropes — see
[bot-behavior.md](bot-behavior.md#moves--learned-reach-navgraphpy-reachpy-navigatorpy).
Author a `legs` entry manually only for exotic moves the graph can't
express:

```json
"legs": [{"from": 0, "to": 1, "steps": [
  {"walk_to": [0.5, 0.55], "style": "flash"},
  {"climb": {"dir": "up", "until_y": 0.31, "x": 0.5}},
  {"walk_to": [0.72, 0.31]}
]}]
```

Step kinds: `walk_to` (`style`: walk|flash|mixed), `climb`
(`dir`, `until_y`, optional align `x`), `up_jump`, `down_jump`, `wait`.
