# Map detection

Map identity decides which saved layout (anchors, platforms, walls,
floor, region) is live. Three pieces, each built on a signal translucent
UI can't fake:

1. **Map change** — the loading blackout (`vision/transition.py`).
2. **Where the minimap is** — the panel's opaque white frame
   (`find_frame` in `vision/minimap.py`).
3. **Which map** — the OCR'd title, voted and fuzzy-matched
   (`bot/identity.py`, `vision/mapname.py`, `MapStore.match_title`).

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
  "walls": {"left": 0.05, "right": 0.95, "floor": 0.9}
}
```

- `name` — **your alias** (file name, dashboard selector label).
- `map_name` — **the OCR'd in-game title only**; this is what identity
  matches. Captured at calibration save, or backfilled by **Save
  layout** when missing. Never overwrite it with the alias.
- `minimap_region` — the panel rect at save. Re-applied once the title
  verifies the map, so normalized anchors/platforms line up with the
  layout they were recorded in.
- `fingerprint` — legacy, carried through unused.

## 1. Map change — loading blackout

Every transfer blacks out the whole client (all pixels 0) for ~1s.
`TransitionDetector` watches each minimap capture: a frame is dark when
its 99th-percentile value ≤ 12 (the panel's white frame keeps dark
*scenes* well above that). States: `normal` → `dark` (after 0.25s,
emits `loading`) → `settling` (first lit frame) → `normal` after 0.6s
lit (emits `arrived`; waits out the fade-in).

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
(the frame's top edge — first row with a ≥120px bright run); row-group
text lines; drop dense icon columns; extend over faded tails.

**Reads** run on a `TitleOCR` worker thread (~2s each on CPU); the
frame thread only captures the band (`MapIdentity.pump`). A request
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
0.05 — sibling maps on the same street score ~0.85–0.90 against each
other, so a lone sibling can't claim another's title and truncated reads
stay unresolved rather than guessing.

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
