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
platforms drawn): the bot performs each move type — flash, double flash,
jump, rope lift, up flash — in the safest direction and records the real
takeoff→landing into `nav_reach.json`. The Route overlay immediately
shows the connections the new reach unlocks. Farming keeps refining the
numbers (successes grow an envelope; two consecutive misses shrink it).

## Drawing layout (dashboard)

All layout tools act on the **Map** panel's selection — one source of
truth, no per-section map names.

- **Draw minimap** — drag the minimap rect on the Window view when
  frame detection fails. Persisted to `config.json` (`minimap_region`)
  as a hard pin that survives map changes — since minimap size differs
  per map, prefer fixing detection over pinning.
- **Draw title** — drag the title strip on the Window view; pins
  `minimap_name_region` when auto segmentation fails on a client.
- **Draw plats** — drag a segment along each platform line on the Panel
  view; stays armed for successive drags. **Undo** pops, **Clear** wipes.
  Platforms are the *authoritative* walkable geometry: anchor snapping,
  nav target projection (~8px snap radius), weave bounds, and Floor
  placement all consult them.
- **L wall / R wall** — stand at a wall, press the button: stores the
  boundary (normalized x) and shades the blocked side red. Overrides
  `wall_zone_px` on that side. A lighter line shows the `wall_pad_px`
  buffer — the bot never goes past it.
- **Floor** — stand on the lowest platform and click: the line is placed
  just below that platform (so it stays walkable) and everything below
  is forbidden; the lighter padded line shows the effective limit (see
  [bot-behavior.md](bot-behavior.md#wall-zones)).
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
flashes, rope lift, drops) — see
[bot-behavior.md](bot-behavior.md#moves--learned-reach-navgraphpy-reachpy-navigatorpy).
Author a `legs` entry manually only for moves the graph can't express
yet — e.g., a rope or ladder climb:

```json
"legs": [{"from": 0, "to": 1, "steps": [
  {"walk_to": [0.5, 0.55], "style": "flash"},
  {"climb": {"dir": "up", "until_y": 0.31, "x": 0.5}},
  {"walk_to": [0.72, 0.31]}
]}]
```

Step kinds: `walk_to` (`style`: walk|flash|mixed), `climb`
(`dir`, `until_y`, optional align `x`), `up_jump`, `down_jump`, `wait`.
