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
keeps what it measured. An attempt is skipped — with the reason shown
next to the move — when the player dot is lost, there's no room or no
platform above, the key isn't bound, the character didn't move, or a
sideways move changed level (fell off or caught a ledge). The Route
overlay immediately shows the connections the new reach unlocks.
Farming keeps refining the numbers (successes grow an envelope; two
consecutive misses shrink it).

## Drawing layout (dashboard)

All layout tools act on the **Map** panel's selection — one source of
truth, no per-section map names.

- The minimap panel and title strip are found automatically. A
  `minimap_region` / `minimap_name_region` in `config.json` still pins
  them by hand — since minimap size differs per map, prefer fixing
  detection over pinning.
- **Draw plats** — drag a segment along each platform line on the Panel
  view; stays armed for successive drags. Hand drags are tidied as they
  are saved: a near-flat drag (ends within 3px, or under 8°) is levelled
  at its mean height — real slopes stay — and level segments on the same
  row (within 2px) that overlap or touch merge into one. **Tidy
  platforms** (Setup → Map) applies the same to a map drawn earlier.
  **Undo** restores the list as it was before the last edit (so undoing
  a merge brings back what was there), **Clear** wipes.
- **Platform fit** (Setup → Map) — while the bot runs, it records where
  the feet settle on each drawn platform (a standstill of ±1px for 0.3s,
  with exactly one platform within 8px). Each platform lists its median
  offset: feet *below* the line mean it was drawn too high — the bot
  then can't tell it stands there (the planner allows 8px above a line
  but only 2px below). It only reports; redraw the line at the feet.
  Samples live in host memory and reset when a platform is redrawn.
- Ropes are **not drawn** — they're learned: when the bot hangs stable
  at a spot that is on no drawn platform for over 2 seconds, it records
  a rope segment from that spot up to the platform above (persisted in
  the map file, drawn as brown lines) and leaps off. Learned rope climbs
  cost `rope_penalty` — a last resort, since platforms are normally
  reachable via jumps/rope lift/teleport.
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
