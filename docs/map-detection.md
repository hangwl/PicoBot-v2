# Map detection

Map identity decides which saved layout (anchors, platforms, walls,
floor, region) is live. Three pieces, each built on a signal translucent
UI can't fake:

1. **Map change** — the loading blackout (`TransitionDetector` in
   `core/src/minimap.rs`), sampled by the `MapMonitor` thread
   (`host/src/feed.rs`).
2. **Where the minimap is** — the panel's opaque white frame
   (`find_frame` in `core/src/vision.rs`).
3. **Which map** — the OCR'd title, voted and fuzzy-matched
   (`core/src/identity.rs`, `core/src/title.rs`, `core/src/fuzzy.rs`,
   `io/src/ocr.rs`).

Why not pixel fingerprints: see [learnings.md](learnings.md).

## The map file model

```json
{
  "name": "WLOH",
  "map_name": "Lake of Oblivion Weathered Land of Happiness",
  "minimap_region": [7, 68, 216, 90],
  "rotation": { "anchors": [...], "legs": [...] },
  "skills": { ... },
  "platforms": [[x0, y0, x1, y1], ...],
  "ropes": [[x0, y0, x1, y1], ...],
}
```

- `name` — **your alias** (file name, dashboard selector label).
- `map_name` — **the OCR'd in-game title only**; this is what identity
  matches. Backfilled by **Save layout** when missing — so saving while
  standing on a *different* map records the wrong title. Setup → Map
  shows it as **Recorded** next to the title on screen; **Record title
  from screen** (`map|title|record[|<name>]`, refused if another map
  already has that title) and **Clear title** fix it. Never overwrite it
  with the alias.
- `minimap_region` — the panel rect at save. Re-applied once the title
  verifies the map, so normalized anchors/platforms line up with the
  layout they were recorded in.
- `fingerprint` — legacy, carried through unused.

## 1. Map change — loading blackout

Every transfer blacks out the whole client (all pixels 0) — for as
little as ~0.43s (Limina 1-1 ↔ 1-2). `TransitionDetector` watches
minimap captures: a frame is dark when its 99th-percentile value ≤ 12
(the panel's white frame keeps dark *scenes* well above that). States:
`normal` → `dark` (after 0.15s, emits `loading`) → `settling` (first lit
frame) → `normal` after 0.6s lit (emits `arrived`; waits out the
fade-in).

**`MapMonitor`** is the only caller: a dedicated thread with its own
screen grabber sampling the minimap at 20 Hz, independent of the bot's
cadence and the dashboard view. It also locates the panel (every 0.25s
while unknown), relocates a moved panel, and pumps title reads. The
feed and the bot only capture frames for their own use. The host's feed
owns the monitor and shares it with the bot; a standalone bot runs its
own.

- While loading, `MinimapAnalyzer.loading` is True: the bot's hazard
  check reports `map transfer (loading screen)` and aborts the leg; no
  title reads are attempted.
- On `arrived`: the region is dropped (unless `config`-pinned) so the
  new map's panel is re-detected, and identity requests a fresh title
  with the old title cleared.

## 2. Panel location — frame detection

`find_frame` scans the window's top-left quadrant for the frame: a top
and bottom edge (border-coloured runs ≥80px with matching x-extent) and
two solid sides, tolerating rounded corners. Largest rectangle wins.
Minimap size differs per map, so a global region pin is wrong on every
map but one.

**Panel moved without a transfer** (e.g. the title row toggled): each
frame checks the region's top rows still show the border. Missing for
≥1s → `relocate()` re-runs `find_frame` and switches only if a frame is
found elsewhere — an overlay hiding the edge (Inventory) keeps the
current region. Only `auto`/`stored` regions are tracked.

Region provenance: `config` (pinned in config.json, survives
everything), `manual` (drawn on the dashboard), `stored` (map file),
`auto` (detected).

## 3. Identity — title OCR

**Band**: `name_strip_region` spans the panel's width (±4px), from the
window top to `name_scan_px` into the frame. The client clips the title
at the panel edge, so nothing lies outside; a wider band only picks up
other UI text. `minimap_name_region` pins a band rect instead.

**Segmentation** (`title_scan`): near-white mask; cut at the divider
(the frame's top edge — first row with a ≥120px bright run); cut the
region-icon tile left of the title by its frame (unbroken white columns
≥70% of the two-line zone height — no glyph is that tall; the colourful
interior defeats a density test); row-group text lines; drop remaining
dense icon columns; extend over faded tails. `title_crop` pads the crop
with its median background colour (4px vertical, 8px margin) — the
recogniser misreads glyphs touching the crop edge.

**Reads** run on a `TitleOCR` worker thread (~2s each on CPU); the
monitor only captures the band (`MapIdentity.pump`). A request
starts a vote:

- a read matching a stored map with score ≥ 0.97 is accepted at once;
- otherwise two consecutive reads must agree (same map, or same text for
  an unknown map);
- after 5 reads the last readable one is accepted, else "unreadable".

Reads from a superseded request are discarded (generation counter).

**Matching** (`title_score` / `match_title`): best of substring
containment (weighted by coverage), prefix similarity for clipped titles
(`Happir` ↔ `Happiness`), and whole-string similarity for misreads.
Accept only when the best score ≥ 0.93 *and* leads the runner-up by
0.05 — so truncated reads stay unresolved rather than guessing. An
exact (normalized) title always wins, whatever its runner-up scored.

Sibling maps are *not* misreads (`looks_like_sibling`, score capped at
0.6): a different number (`Ramparts 2` ↔ `3`, `World 1-2` ↔ `2-6`), an
extra real word (`Storehouse Entrance` ↔ `Storehouse`), or a complete
read that stops where the stored title goes on. Still forgiven: short
junk tokens (region-icon residue), words before the stored title starts
(a region prefix), split or merged words, and a title clipped mid-word
(`Happir`). Without this, "Limina End of the World 1-2" resolved to a
stored 2-6 (0.952), and a map whose title contains another's matched
it.

**Requests**: startup, arrival, pin change, title-band change,
panel moved (only if no title yet). Never per-frame.

## Resolution (`MapIdentity`, shared by host and bot)

1. The title matches a stored map that differs from the pin → that map
   (`via: ocr`, logged as overriding the pin).
2. Else a pin → the pin (`via: ocr` if the title confirms it, else
   `pin`).
3. Else the title's map, or unknown.

Consumers poll `identity.version`. The bot applies changes on its own
thread (`_sync_map`); the host broadcasts a `maps` payload when the
version moves. Blank-name layout writes (walls, platforms, Save layout)
require `via: ocr` — a pin is not evidence of what's on screen.

## Debugging

`debug/frames/` captures every transition, OCR read, and
frame-not-found event — see
[development.md](development.md#debug-frame-captures).
