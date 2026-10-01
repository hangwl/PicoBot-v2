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
- **Up flash measured the ledge, not the jump** (2026-09-29): vertical
  measurement ran once and recorded the rise to wherever it landed — the
  gap to the platform above the measuring spot. That became the
  calibrated reach (13 px on one class vs a 26 px guess), so the planner
  found no route to taller tiers while rope lift cooled. The up flash is
  now measured by its recorded peak. Its re-press delay was also random
  (~0.08–0.18 s after the first tap's release, plus the tap's own hold),
  so outcomes varied with nothing recording why; the timing sweep
  records peak vs delay before the planner is taught to choose one.
- **Per-class reach only after a switch** (2026-09-29): the host built its
  reach model from `nav_reach_file` at startup and only moved to
  `nav_reach_<class>.json` on a class switch, so a session started with
  a class active learned and measured into the shared `nav_reach.json`.
  Startup now uses `reach_path()`.
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

## Pre-migration review (2026-09-30)

A whole-codebase pass before the Rust port found, among others:

- **Keys could stay held for good**: the firmware never released on
  disconnect or host silence; late ACKs were credited to the next command
  (positional matching, NACK ignored); and a key-down that timed out in the
  host's queue still went out later, untracked, so `release_all` skipped
  it. Fixed with numbered commands, a keepalive + 2s firmware watchdog,
  dropping stale key-downs, and tracking a key from the first attempt.
- **Rope-lift landings were judged before takeoff**: the landing wait
  took two still reads as "landed" — during the rope's grapple the
  character stands still, so a good lift read as a miss (the Limina 2-6
  pattern: miss → "not on any drawn platform" → walking on the upper
  tier). Landing now needs a takeoff and a steady read on a platform.
- **Up flashes overshot their target**: the graph aimed them at the
  nearest platform above, but the character lands on the highest one its
  peak clears. Those misses also shrank the reach model — misses now only
  count when the move fell short.
- **Cross-thread mutation**: dashboard skill edits cleared the bot's
  skill dict mid-iteration; anchor deletes shrank the list the patrol
  indexed. Skill books are now handed over and swapped on the bot thread
  (cooldowns carried over); anchor lists are replaced, never mutated, and
  the patrol replans when they change. A running bot also read a stale
  copy of the map's anchors after any layout save.

## Bot-logic review (2026-10-01)

- **Re-routes started from the takeoff, not the landing**: after a miss
  the patrol spliced a route from the position read *before* the leg, so
  the next legs ran from a platform the player had left — usually a
  second miss and a ban. The re-route now waits for the next tick and
  reads the player afresh.
- **Moves that never happened taught reach**: a key eaten by focus loss
  or a stun reads as "landed where it started", which scored as falling
  short — one capped exploration, two shrank the envelope. Reach now
  learns only from moves seen leaving the takeoff spot, and interrupted
  legs are aborted rather than counted as misses.
- **Stale anchor indices**: arrival skills, TRAVEL targets and bans are
  indices into the anchor list. A dashboard delete right after an arrival
  panicked the bot thread, and bans and the roam origin carried over to
  the next map. Indices are checked on use and cleared on map change.
- **Unbounded walks**: `move_to_point` had no deadline — a lost dot or a
  target the arrow kept overshooting held the tick forever, and the
  watchdog (which runs between ticks) never fired. It now has a
  distance-based budget.
- **Cooldowns checked late**: a cooling rope lift or teleport was only
  noticed after walking to its takeoff, and the patrol's re-route didn't
  exclude a cooling teleport, so it re-picked it every tick.
- **Loops planned moves that couldn't fire**: the planner ignored
  cooldowns, so a loop chained rope lifts seconds apart (each later one
  hit its cooldown and was re-routed at run time), and planned them even
  with no rope-lift key bound. It now runs a clock along the loop. Plain
  route cost underestimates real time (attack windows, landing waits,
  summons), which would exclude moves that are in fact ready — so the
  clock runs at a pace learned from how long segments really take.
- **Pauses banned TRAVEL targets**: a hazard or focus loss mid-TRAVEL
  held the target out of routes for 45s as if it were unreachable.
- **Clamped timing had spikes**: `human_between` clamped log-normal draws,
  so tight windows put up to ~25% of delays on the exact bound — the
  kind of repeated value the timing module exists to avoid. Draws are
  now redrawn inside the window.
- **Teleport weaves ignored the cooldown**: each weave pressed teleport
  and restarted the bot's cooldown clock, so a teleport leg planned
  after a weave always read as cooling. A cooling teleport now weaves on
  foot.
- **The approach jump ignored the platform's end**: the short plain jump
  that closes the last hop only checked the distance to the target, so
  near an edge it could carry the character off. It now needs a jump's
  worth of platform ahead, else it walks.

## Rope hugs and the top rim (2026-10-01)

- **Down jumps onto a rope top**: a down jump's takeoff columns (the
  overlap's ends and middle) ignored ropes. Standing on a rope's top,
  Down grabs the rope, so the character hung under the platform ("not on
  any drawn platform") until the off-graph probe learned the rope and
  leapt off. Takeoffs now keep clear of learned ropes off that platform;
  the first hug on an unlearned rope still happens, and learns it.
- **The dot vanished under the top rim on rope lifts**: every one of 53
  dot-lost captures was a rope lift to a platform near the minimap's top,
  last seen at y 5-8. The dot was still there — its lower rows showing
  just under a 2px frame — but the 4px `marker_inset` crop left 2 of its
  pixels, under the 6px marker minimum. The capture stats made it look
  like a colour problem (max-channel distance 15 against a sum-tolerance
  detector); they now use the detector's metric. A dot missed inside the
  crop is now looked for in the rim, but only within 12px of its last
  sighting — the rim is frame everywhere else. All 53 captures read.

## Movement and detection

- **Dot lost on ropes** (2026-09-30): markers were found with a 3x3
  erosion to drop specks. The real player dot is ~24px, which erodes to
  4px — a 1px rope or platform line through it split it into fragments
  the erosion erased, so the dot "vanished" on ropes. Grouping pixels
  within 2px and requiring 6px drops specks without that failure; 78/79
  real post-arrival frames still detect. The feet row is now the true
  bottom (1px lower than the eroded one).
- **Rope tops under their platform** (2026-10-01): the graph found a
  rope's upper platform with a point test that accepts only 2px *below*
  the row. Saved ropes on EZFZ and Slurpy ended 2.5–5px under it, so
  they got no climb edges, and a hang at their top read as off every
  platform (2s wait, Down probe, leap). Ropes are now lifted onto the row
  at graph build (up to 6px) and learned tops stand 3px above it. A climb
  that stalls within 6px of the top (after rising) counts as arrival and
  keeps Up held to mount, instead of failing "stalled" 1.5s later and
  leaping; a climb now rides out 3s of unreadable dot, not 1.5s. Why the
  dot goes unreadable on rope grabs is still open: lost-dot frames are now
  saved to `debug/frames/*_dotlost/`.
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
- **Rope grab misses** (2026-09-29): climb edges took off directly under
  the rope (takeoff clamped to its column), so almost every grab was a
  standing jump that only latches if the character is within a pixel or
  two of the rope, and Up went down only ~25ms (one key gap) before the
  jump. Grabs are now hops toward the rope from 6px beside it (flash off
  a platform end further out) with Up held ~0.15–0.25s before takeoff.
  Up is *not* held during the walk to the takeoff: Up over a portal
  changes maps. Learned ropes between stacked tiers could run down onto
  the lower platform (a merge kept the lowest end); the bottom now stays
  5px above it, both when learning and when the graph is built.
- **Dashboard not loading on the phone** (2026-09-29): impossible to
  diagnose — the HTTP handler swallowed every error and logged nothing.
  Looking closer: it bound IPv4 only (a Tailscale name resolves to an
  IPv6 address too, and IPv6 attempts hung rather than failing fast),
  sent no Content-Length (a cut-off transfer looked complete), and —
  worst — `HTTPServer` sets SO_REUSEADDR, which on Windows lets a second
  process bind a port already in use: a stale or hung host could keep
  receiving some of the connections, and the "next free port" fallback
  could never trigger. Now: dual-stack, exclusive bind with port
  fallback, Content-Length, per-connection timeout, every request and
  error logged as an `http` event, `/health`, and real URLs at startup.
- **Wrong map from a right read** (2026-09-29): standing in "Identisk
  Tisk Food Storehouse Entrance", the bot resolved a stored map aliased
  "Tisk Food Storehouse" at 100% — that file's recorded title *was* the
  Entrance's (saved while standing there). Behind it, the fuzzy matcher
  treated sibling maps as misreads: "…Storehouse" vs "…Storehouse
  Entrance" and "Ramparts 2" vs "3" scored ~0.96 (above the 0.93 bar),
  real captures show "Limina End of the World 1-2" resolving to a stored
  2-6, and with both siblings stored the exact title failed the 0.05
  margin. Siblings (different number, extra real word, complete read
  that stops early) are now capped at 0.6, exact titles win outright,
  and the recorded title is visible and fixable on the Map page.
- **Summons as positional, charged skills** (2026-09-29): every
  checkpoint re-cast every off-cooldown summon, with no memory of where
  one was or how long it lasted — casts stacked at one anchor while
  others stayed empty, and a cast could fire mid-air. Summons now have
  charges (one back per cooldown) and an uptime, placements are tracked
  per anchor (one live summon each; a skill's oldest instance goes when
  it has `charges` out), and a cast needs the character standing on a
  platform. The tracker is timing only — the bot can't see summons.
- **"Far anchors get visited less"** (2026-09-29): the loop visits every
  reachable anchor once, so the roulette temperature can't change visit
  frequency — only order. Simulated on EZFZ: temp 3 gives 15s loops and
  24–29s worst revisit gaps, temp 0.05 gives 20s and 34–36s — *raising*
  it revisits far anchors sooner. The logs pointed at skips (no route,
  missed landings) instead, so per-anchor stats went on the Map page
  rather than a heat-map policy.
- **A pin outlived the map it named** (2026-10-01): walking into
  "Identisk Tisk Food Storehouse Entrance" from the pinned "Identisk Tisk
  Food Storehouse", the title read matched no stored map, so the pin
  stood (`(pin)` in the log) and the bot farmed the wrong map — the pause
  also ended before the title read finished, because the pin was trusted
  in the gap after the arrival cleared the old title. The stored map had
  no recorded title, so only its alias could vouch for it; the sibling
  title scores 0.6 against it. Now an accepted read that fits neither the
  recorded title nor the alias (0.7) contradicts the pin, and an arrival
  holds the bot until the read is settled.
- **A region with the right corner and the wrong size** (2026-10-01): the
  panel's size differs per map (stored regions span 170–236 px wide and
  61–105 tall, all at the same corner), but tracking only watched the frame's
  top edge, which a wrong-size region still satisfies — and a title-verified
  arrival installed the stored region over what had just been located. The
  overlay then sat shifted until the next map change. Now the whole frame
  is verified against the live window (two agreeing checks, so one stray
  rectangle can't flip it) and a located frame wins over a stored one.
