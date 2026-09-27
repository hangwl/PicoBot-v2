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
