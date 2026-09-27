# Calibration & map layout

## Placing anchors (preferred)

Draw the platforms first, then on the Panel view press **Place anchors**
and click each patrol checkpoint. Clicks snap onto the drawn platform
under them (within 12px; a click with nothing under it stays put and the
log says so). Shift-click removes the nearest anchor; **Undo**/**Clear**
act on the Map selection. Anchors are named `a0`, `a1`, … (lowest free
number) and stored normalized in the map's `rotation.anchors`; removing
one drops and reindexes any hand-authored `legs`.

Placed anchors have no `on_arrive` list — at arrival the bot fires any
registered **summon** skill that is off cooldown instead. Order doesn't
matter: the patrol plans its own route.

## Recording anchors (optional)

Dashboard **Calibrate** panel (or headless
`python -m picobot.bot.calibrate --window "Eluna (x64)" --name my_map`,
F9 = mark, ESC = save):

1. **Record** starts a session.
2. Walk to each farming spot and press **Mark anchor** (or F9).
3. **Save** writes/updates the map file.

Recording captures **anchor positions only** — movement between
checkpoints is generated live at runtime, not replayed. What is inferred
for free at each mark:

- **Dwell range** — how long you stood there.
- **`on_arrive` skills** — keys pressed in the ~8s arrival window,
  resolved through registered skill bindings.
- **`minimap_region` + OCR title** (`map_name`) — identity data captured
  at save (the title comes from the identity's latest accepted read). If
  no title has been read a `notify` event warns (the map then can't be
  auto-identified until a save captures it).

Marks taken mid-air or off a platform warn immediately; saved anchors are
snapped onto the nearest drawn platform so targets stay reachable.

**Saving preserves.** `merge_recording` carries over everything recording
can't observe — walls, floor, platforms, `map_name`, tuned skills —
re-recording only replaces anchors.

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
  `wall_zone_px` on that side.
- **Floor** — stand on the lowest platform: shades below; the bot stops
  attempting down-jumps at/below it.
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

Recording no longer captures movement legs. Between checkpoints the bot
navigates directly with weave-attacking `walk_to` (+ `up_jump`/
`down_jump` for level gaps) and composes shortest paths over recorded
legs when a pair exists. Author a `legs` entry manually only for moves
the fallback can't manage — e.g., a rope climb:

```json
"legs": [{"from": 0, "to": 1, "steps": [
  {"walk_to": [0.5, 0.55], "style": "flash"},
  {"climb": {"dir": "up", "until_y": 0.31, "x": 0.5}},
  {"walk_to": [0.72, 0.31]}
]}]
```

Step kinds: `walk_to` (`style`: walk|flash|mixed), `climb`
(`dir`, `until_y`, optional align `x`), `up_jump`, `down_jump`, `wait`.
