# Map detection

Map identity decides which saved layout (anchors, platforms, walls,
floor, region) is live. It uses **two independent signals**, a
self-consistent watchdog, and explicit user pins — designed to survive
the translucent minimap panel that made pixel-colour matching hopeless.

## The map file model

```json
{
  "name": "lake1f",
  "map_name": "Lake of Oblivion Weathered Land of Happiness",
  "fingerprint": "g2:<hex>",
  "minimap_region": [8, 56, 232, 169],
  "rotation": { "anchors": [...], "legs": [...] },
  "skills": { ... },
  "platforms": [[x0, y0, x1, y1], ...],
  "walls": {"left": 0.05, "right": 0.95, "floor": 0.9}
}
```

- `name` — **your alias** (file name, dashboard selector label).
- `map_name` — **the OCR'd in-game title only**, stored at calibration
  save. Never overwrite it with the alias.
- `fingerprint` — structural hash (`g2:` scheme), see below.
- `minimap_region` — the minimap rect remembered at save; restored when
  the map resolves so detection drift can't accumulate.

## Signal 1 — OCR title (`vision/mapname.py`)

The map title is exact, human-readable identity — immune to everything
that breaks fingerprints. It is **event-gated, never per-frame**: it runs
on startup, a watchdog-confirmed change, a pin change, a `map|list`
refresh, and a calibration save.

Capture band: full client width, window top → `name_scan_px` (default
160) into the minimap region. Generous on purpose — segmentation isolates
the text:

1. **Near-white mask** (`min channel > 170`): title text is white;
   toolbar icons are gray and the colored map icon mostly fails.
2. **Divider cut** — the first row with a *contiguous* bright run ≥120px.
   The panel's separator is one solid line (~200+px); a glyph's longest
   run is ~15px. This works whether `locate()` found the whole panel
   (divider inside it) or just the map frame (divider at its top).
3. **Row grouping** into text lines (`min_line_h=4` filters the 1–2px
   map lines and borders).
4. **Icon cut** — column runs within the text zone; runs denser than
   `icon_fill` (the droplet icon is a ~80%-fill block; text is sparse)
   are dropped, so titles at any panel width survive.
5. **Faded-tail recovery** — clients fade overflowing titles at the
   panel edge; trailing glyphs drop below the white threshold mid-word.
   The scan keeps extending while dimmer-but-bright columns (>80) appear
   (gaps >12 dark cols end the title), and `title_crop` extends to the
   divider's right edge — RapidOCR (a CNN) can read glyphs the threshold
   dropped.

Escape hatch: `minimap_name_region` pins an exact band rect (set it with
the dashboard's **Draw title** drag on the Window view). It wins over
auto detection entirely.

`match_name` normalizes (lowercase alnum) and substring-matches the OCR
text against **both** `map_name` and `name` — bidirectionally with a
6-char floor, so a truncated title (`"Lake of Obliv"`) still resolves.

## Signal 2 — structure fingerprint (`vision/minimap.py`)

`g2:` fingerprints hash the *structure mask* — platform/line geometry —
never pixel colour. Translucent backgrounds, panel art, and the moving
character sprite can't drift it. Scheme prefix is compared in distance:
legacy colour-hash fingerprints can never match `g2` ones (re-save maps
to refresh). `fingerprint_score` normalizes distance→0–1 confidence for
the dashboard.

## Resolution order (both serve-side and bot-side)

1. If the user pinned a map (`active_map`): keep it **unless** its own
   stored fingerprint verifiably mismatches the screen, or OCR reads a
   *different stored map's* title. A pin with no fingerprint stands —
   fuzzy evidence never silently overrides user intent.
2. OCR result → `match_name` → wins when present.
3. Fingerprint `match_scored` → best sub-threshold entry + confidence.
4. OCR-vs-fingerprint disagreements log a `map id conflict` event — you
   see the bad read rather than trust it silently.

## Watchdog (`note_frame`)

Each captured minimap frame is fingerprinted and compared to baseline.
A miss only counts when the new frame **matches the previous miss frame**
— the scene must be a stable *different* picture. Loading blanks/fades
neither match baseline nor each other, so they can never accumulate.
After `map_change_threshold` self-consistent misses the change is
confirmed: the region is dropped (unless `config`-sourced — provenance
is tracked: config / stored / manual / auto), map re-resolves, and the
map's remembered layout is reapplied.

Dashboard propagation: a resolved-identity change emits a `map` event
(`map resolved: lake1f (ocr) via OCR "…"`) and pushes a fresh `maps`
payload — the `detected:` readout and event log reflect transitions,
even while the bot is running (the bot's `_map` transitions mark the
host's resolution dirty, since the feed's watchdog is dormant then).
