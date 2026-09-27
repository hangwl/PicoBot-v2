# Learnings

What has been tried, what happened, and what we took from it. Code
comments stay short; history lives here.

## Map identity

- **Colour-hash fingerprints** (per-cell mean/max grayscale of the
  minimap) — abandoned. The modern-UI minimap panel is translucent, so
  the live scene bleeds through and the hash drifts constantly.
- **`g2:` structural fingerprints** (hash of a thin-horizontal-line
  mask) — current, but the watchdog false-triggers. Translucent UI
  overlays (popups, toasts, fades) both hide real platform lines and add
  new line-like edges, and a *stable* overlay passes the
  self-consistency check. Open problem; see `debug/frames/` captures.
- **OCR of the map title** — removed once (translucent strip made reads
  unreliable), restored with segmentation: near-white mask, divider cut,
  icon cut, faded-tail recovery. Much better than the unsegmented band.
  Still fragile on bright backgrounds behind the panel.

### Diagnosis from debug captures (2026-09-27)

29 captures over ~50s walking Nameless Town → Happiness → Rage → Sorrow
→ Chu Chu Village. 15 watchdog confirms for 5 real changes.

- **A global `minimap_region` pin is wrong on every other map.** The
  pinned `[7, 66, 304, 72]` fit Nameless Town's wide minimap; elsewhere
  ~90px of the region is live game world and the minimap is cut off.
  Minimap size is per-map.
- **The structure mask barely sees real platforms.** Minimap platform
  lines are dotted, so they fail the ≥8px uniform-run test. The mask is
  dominated by the panel's white border (constant, no identity) plus
  scene edges — snow platforms seen through the translucent panel and
  in the out-of-panel strip. Camera movement alone produces misses of
  20–30 (threshold 15).
- **Opening/closing a window (Inventory) confirms a change twice** —
  once on open, once on close — in 0.3s (3 frames at ~10fps).
- **Loading transitions are pure black** (every pixel 0, whole window)
  for ≥1s. Raw-hash fallback made each fade confirm twice (into and
  out of black).
- **OCR takes ~1.5s and blocks the frame thread**; every run left a
  matching gap in capture timestamps. OCR ran 15 times, often
  back-to-back without any watchdog confirm.
- **OCR text quality is good**, but the pinned title band clips long
  names mid-glyph: `Happiness` → `Happir`. `Happir` is not a substring
  of `happiness`, so substring matching fails.

### Redesign (2026-09-27): blackout trigger + frame detection + voted OCR

Replaced the fingerprint watchdog entirely. Validated by replaying the
captures above:

- **Blackout trigger**: 3 loading/arrived pairs for the 3 captured
  blackouts; 0 triggers from the Inventory and scenery episodes that
  produced 12 false confirms before.
- **`find_frame`**: exact panel rects on every lit window (216×90,
  185×82, 194×82, 213×109, 170×82); `None` only mid-fade or with
  Inventory covering the frame's side. The old density-based `_detect`
  returned 185×297 and 323×82 on the same captures.
- **Panel-width title band** + fuzzy matching: every lit capture
  resolved to the right sibling (Happiness/Rage/Sorrow, scores ≥0.985);
  Chu Chu Village (not stored) stayed unknown. Siblings score 0.84–0.90
  against each other, hence `min_score` 0.93 + 0.05 margin.
- **Panel with the title row collapsed** (seen at 180339) has no title to
  read — identity falls back to the pin there. The region moves without
  a blackout, so the frame edge is tracked to relocate.
- The saved `WLOH` map had `map_name: null`, so title matching could
  never identify it; the stored region `[7,68,204,75]` is smaller than
  the real panel (216×90).

### Missed transfers (2026-09-27, second capture batch)

11/11 transfers in the batch were caught (idle, ~9 Hz feed), but live
misses happened elsewhere: detection only ran when something captured
the minimap — the bot between actions (0.3–1s gaps; PAUSE sleeps 1s)
and the dashboard only in Panel view. Blackouts can be as short as
0.43s. Replaying the 11 real transfers at bot cadence caught 22% (1% for
the shortest); a dedicated 20 Hz `MapMonitor` catches 100%. Lesson: a
trigger must own its sampling rate — never piggyback on consumers.

## Geometry

- **Platform auto-detection** by ink colour, then colour-free
  structural line detection — both abandoned for navigation. Alpha
  blending makes lines unreliable. Platforms, walls, and floor are now
  hand-drawn in the dashboard and authoritative; detection only feeds
  fingerprints.
- **Marker centroid → feet**: positions anchor to the marker's bottom
  row so they sit on platform lines rather than half an icon above.

## Behaviour

- **Dwell-parking between anchors** replaced by planned checkpoint
  routes with attack weaving during travel — parking looked robotic and
  blind travel legs wasted time.
