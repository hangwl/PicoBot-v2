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

### Region-icon tile in the title (2026-09-27, third batch)

Colourful region icons (Chu Chu Island, Five-Color Hill, Skywhale)
survived the density-based icon cut — only their light frame is
near-white — and OCR read them as `QY`/`FQY`/`50`. White-heavy icons
(Limina, Lachelein) were already cut. Cutting by the frame's tall
unbroken side columns removes all of them, but the tighter crop then
caused edge misreads (`11-1`, `Pa ath`); a background-colour margin
fixed that — all 15 captured reads exact afterwards.

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
- **Single-anchor dwell timer and stationary attacks removed**
  (2026-09-29). One-anchor maps kept an 8–14s timer that handed off to
  TRAVEL — to the same anchor — and `dwell_weave: false` attacked in
  place. The timer's only real job was recovery after falling off the
  platform; a position check (another drawn platform, or another level
  with none drawn) now queues TRAVEL home directly, so GRIND leaves only
  on a plan handoff. Rotation `style` (loop/pingpong/shuffle) was dead
  too — order comes from the patrol planner.

## Dashboard and profiles

- **Synthesized key taps** (2026-09-29): the remote pad fired on `click`
  and sent `key|down` plus a timed `key|up` 90–150ms later. The game
  saw presses a whole touch late, nothing could be held or chorded, and
  a backgrounded tab throttled the timer into a stuck key. Keys now
  mirror the pointer (down on touch, up on lift/cancel) and release when
  the page hides or blurs.
- **Hazard lost in Window view** (2026-09-29): only Panel frames carried
  the bot's `state`/`hazard`, so switching to Window view to solve a rune
  cleared the alert. Every view carries them now.
- **Profile skill fallbacks** (2026-09-29): three paths disagreed on a
  profile's kit — an empty kit fell back to the global book, displaying
  skills seeded the profile, and the legacy `attack_keys` synthesis ran
  after the class was applied (wiping the kit at startup when there was
  no top-level `skills`). One rule now: a `skills` entry (even `{}`) is
  the kit; no entry inherits. Class switches also never re-sent
  `skills`/`config`, so the dashboard showed the previous profile.
- **Measuring the wrong moves** (2026-09-29): the measurer ran a fixed
  flash plan, so teleport classes measured flashes they never use and
  never measured teleport. The plan now follows the class kit.
- **"Measured" counted growth, not measurements** (2026-09-29): the
  count was moves whose reach grew >5% past the conservative guess, and
  measuring only ever raised reach (`max`). A move that measured at or
  below its guess changed nothing — the guess stayed, the planner kept
  attempting moves the character can't make, and Setup said "not
  measured". Measurements now calibrate (set) the envelope and are
  recorded per move. The same review found the ledge check inverted:
  rise is positive upward, so `rise > 8` rejected landing *higher* and
  accepted falls — an overshoot onto a lower platform was recorded as an
  inflated dx.
- **Live view dying for good** (2026-09-29): every stage had a way to
  stall permanently with nothing to restart it. `GameWindow` kept the
  handle it found at startup, so a restarted game broke every capture
  until the host restarted; `ScreenGrabber` reused an mss instance whose
  device context had gone bad (lock screen, sleep, UAC, display change);
  the streamer's viewer check sat outside its `try`; a frame send a
  frozen tab never drained starved that client while text still flowed;
  and the page had no heartbeat, so a half-open socket after sleep or a
  network switch looked "live" forever. Now the window is re-found by
  title, a failed grab replaces the instance, the streamer never exits
  and rebuilds the feed after 5s of failures (with a visible warning),
  stuck sends close the client after 10s, and the page pings and
  reconnects after 12s of silence.

## Movement and detection

- **Dot lost on ropes** (2026-09-30): markers were found with a 3x3
  erosion to drop specks. The real player dot is ~24px, which erodes to
  4px — a 1px rope or platform line through it split it into fragments
  the erosion erased, so the dot "vanished" on ropes. Grouping pixels
  within 2px and requiring 6px drops specks without that failure; 78/79
  real post-arrival frames still detect. The feet row is now the true
  bottom (1px lower than the eroded one).
- **Guessing ropes** (2026-09-30): a rope was learned (and a jump
  pressed) whenever the dot sat still off the drawn platforms for 2s or
  went missing for 2.5s — undrawn ground, UI over the minimap or a
  loading screen all produced ropes and blind jumps. A Down probe now
  confirms the hang, and a missing dot only waits.
- **Input timing** (2026-09-30): gaps inside moves were already
  lognormal, but nothing spaced consecutive key events (only the ~8ms
  serial round-trip, nearly constant), holds were Gaussian and the same
  for every key, there were no reaction times, and every session had
  identical statistics. Added a drifting session tempo, per-event
  spacing in `HidController`, key-dependent lognormal holds, and
  reactions at unexpected events. A virtual-clock simulation of the real
  move code showed the flash re-press unchanged and the up-flash within
  ~8ms of before once the Up lead sleep gives back its gap (without that
  it landed ~25ms late). Tempo differs per process, so timing medians
  must be compared in one process with `TEMPO` pinned.
- **Hand-drawn platforms** (2026-09-30): drags are never level and
  ledges get drawn as several overlapping pieces, and the planner's
  standing test is asymmetric (8px above a line, 2px below), so a line
  drawn just 3px too high makes the bot think it's off every platform.
  Near-flat drags are now levelled and same-row overlaps merged at save
  time; a fit diagnostic reports where the feet actually settle per
  platform. Auto-correcting from those samples was deferred, and so was
  drawing learned lines over the view — too cluttered on a map with many
  platforms. First cut bugs: an "under 8°" levelling rule flattened long
  gentle ramps (a 12px rise over 100px ended 6px off — hand wobble is a
  pixel amount, not an angle; now ≤4px only), merging pieces "within
  2px" bridged real gaps (now overlap or meet only), and undo restored
  anchors by name although names are reused (now only anchors the edit
  moved, and only while they're still where it left them).
- **Off-centre player box** (2026-09-30): `player_pos` returns the feet
  (bottom row) on purpose, but the overlay drew a 9x9 box *centred* on
  that point — on real 6x6 dots it missed the top row and hung 4px
  below. Python's banker's rounding also put an even-width dot's centre
  (x.5) on alternating sides. The overlay now frames the detected bounds;
  x rounds half-up.
- **Whole-app freeze** (2026-09-29): the stuck-frame-send close (added to
  revive dead streams) logged while holding `clients_lock`. Logging runs
  inline, emits on the event bus, and the bus broadcasts to dashboards —
  taking `clients_lock` again on the same thread. A plain `Lock` isn't
  re-entrant, so the streamer deadlocked itself; then the bot thread
  (every log line broadcasts) and the WS loop (connect/disconnect) blocked
  on the same lock. Symptoms: stream dead, bot silent mid-patrol, WS
  handshakes hang while HTTP still answers. Fixed by only doing
  bookkeeping under the lock; a test wires log → broadcast like
  `serve.py` and fails on a deadlock.
