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

## Sweeps on Odium Road to the Castle's Gate 2 (2026-10-03)

- **Sweep ends at the edge**: entries were 3px inside a platform's end.
  Off-platform captures on this map showed what a walk to a point 3px
  from an edge does — the walk to x 66 on a platform ending at 69 stopped
  at 71, over the edge (a walk slides a few px after letting go). Sweeps
  made that every target; they now keep 8px inside both ends.
- **Lifting up to come down**: rope lift was priced 0.5s, under an up
  flash's 1.0, so from the floor the plan lifted to the top tier, walked,
  and double-flashed down onto the middle-tier platform an up flash
  reaches directly. Rope lift's wind-up alone is ~1.2s; priced 1.5s it
  stops winning those detours, and still beats an unproven up flash.
- **Weighted order costs loop time with sweeps**: over 40 seeds on this
  map a loop took 14.3s on average (12.0-19.1) under `weighted`, 12.1s
  (12.0-12.5) under `greedy` — every platform is swept once a loop anyway,
  so variety in the order is paid for in travel.
- **Map data**: on this map the top tier (drawn at y 42) is where the
  feet read at y 45-48 — off the drawn line by more than its 2px slack,
  so standing up there reads as off every platform, and up flashes
  planned onto the middle tier land on the real top tier instead.

## Live runs on Castle's Gate 2 (2026-10-03)

Each run was 3–5 minutes with the real character; misses are missed
landings per visit.

| Run | Change | Visits | Misses |
|---|---|---|---|
| 1 | sweeps as merged | 17 | 45% |
| 2 | settle before up flash/down jump, full-carry gap moves, sweep crossing as one walk | 30 | 25% |
| 3 | carried moves aimed at the landing platform's middle, "caught" check, cooldown fallback in the splice | 36 | 27% |
| 4 | rise learned between platform rows | 39 | 28% |
| 5 | median carry, zig-zag order | 22 | 33% |

- **Gap moves were planned as variable-length**: the graph linked a flash
  from the platform's edge to anywhere within reach, but a flash always
  flies its full carry — planned for 15px, it flew 37 and overshot.
  Carried moves now take off so they come down where their full carry
  takes them, aimed at the middle of the landing platform (the carry
  varies by several px either way), and aren't linked where a platform
  in between would catch them first.
- **The sweep crossing detoured**: routed through the graph, the walk
  across a platform was priced at the learned walk speed (~12px/s) and
  lost to hops along another tier. It's one walk leg now.
- **Up flash rise inflated 13 → 19**: a pause at the top of the arc read
  as the landing, so the rise learned was the apex. Rises are measured
  between platform rows now (dot rows only off the drawn platforms), and
  up flashes and down jumps settle 0.5s first so they don't take off
  moving. Rope lift misses dropped from 5/9 to 2/19.
- **Flash carry ratcheted 37 → 43 → 51**: the envelope grew to every
  longest success (a flight carried by walking momentum, or drifting
  down a tier), and with full-carry planning a too-long carry puts every
  takeoff too far back — flash missed 7/13 in run 4. A sideways move's dx
  is now the median of its last 9 carries: in run 5 the flash samples
  read 36–44 with a median of 37 and flash missed 1/6. Double flash
  carries spread 44–53 (5/17 missed); misses are now logged with the
  planned leg, the takeoff read and where it came down.
- **Zig-zag order**: with sweeps, greedy order still hopped between tiers
  when a far platform on another tier happened to be cheaper. `zigzag`
  finishes the nearest row first; ties between a sweep's ends go to the
  nearer one, so a row keeps its direction and the loop snakes.
- **The rotation's coverage was invisible**: per-anchor stats said how
  often an anchor was reached, not which stretches were attacked. Attacks
  now feed a decaying heat grid shown over the Panel view.

## Capture cadence and the Pico's USB identity (2026-10-04)

- **What the game PC can observe** of the host was already small: a cached
  window handle (`EnumWindows` only at startup or after the game
  restarts), GDI screen `BitBlt`s, and one `SetForegroundWindow` at bot
  start. The only periodic signal was the `MapMonitor`'s fixed 50ms tick,
  now drawn from 35–70ms (`timing::jittered`, independent of the session
  pace). The blackout detector is time-based (`min_dark_s` 0.15s), so
  70ms stays safe; a test pins that. `is_active` compares window handles
  rather than reading other windows' titles, and `auto_focus` (default on)
  turns off the start-up focus call. WGC/DXGI capture was judged no
  stealthier than GDI (a different handle, not a quieter one).
- **First `boot.py` missed an interface**: Device Manager showed the Pico
  as six interfaces — two serial ports, mass storage, HID, and a
  "CircuitPython Audio" one, which is the default USB MIDI. Dropping the
  REPL and drive wasn't enough; `usb_midi.disable()` was needed too.
- **CircuitPython can't read BOOTSEL**: the board definition exposes no
  button pin, so a no-jumper recovery needed another route — a
  `maintenance` command that sets an `nvm` byte and resets, which
  `boot.py` reads once. The first firmware to hide the drive shipped
  without it, and locking the board out meant BOOTSEL +
  `flash_nuke.uf2`; hidden-drive firmware must always carry the command.
- **A UF2 reflash keeps the filesystem**: dropping the CircuitPython UF2
  onto `RPI-RP2` left the old `boot.py` in place, so the board came
  straight back hidden — no `CIRCUITPY` drive, which looked like a failed
  flash. Only `flash_nuke.uf2` clears it.
- **Port numbers move**: the stock board is COM5 (REPL) / COM6 (data); the
  hardened one is a single COM7. A pinned `serial_port` needs updating
  after the switch.

- **Host process surface** (2026-10-04): the `dist` profile (`debug = 0`,
  thin LTO) was meant to strip symbols and source paths, but on MSVC the
  symbols already live in the `.pdb`, not the exe: both builds carry only
  a relative `picobot.pdb` name and no function names. What stays in
  either exe is ~40 relative source file names (`lie_detector.rs`,
  `rune_arrows.rs`, …) from panic locations, which only nightly's
  `-Zlocation-detail=none` removes; the exe is also ~1% bigger than
  release. So `dist` is a cheap hygiene profile, not a scrub. Listeners were wildcard `[::]`; they now bind the
  addresses `bind` names. `auto` takes loopback plus every detected
  Tailscale/LAN address rather than Tailscale only, so existing phone
  URLs keep working; `tailscale` and `loopback` are the tighter choices.
  `/health` gave its port and build state to anyone; only loopback sees
  them now. Evidence screenshots in `debug/frames/` are forensic
  residue on a deployed box, hence `evidence_captures`.

## Human reaction and session hygiene (2026-10-04)

- **A hazard stopped the bot in the same tick**: the machine went
  hazard → PAUSE → `release_all` with nothing between, so the gap from a
  player's dot appearing to every key coming up was only the detector's
  latency (tens of ms). Hazards a person would see (other players, the lie
  detector) now wait out a log-normal reaction first, with the keys still
  down; loading, map identity and focus loss stay immediate, since nobody
  watches those. The lie detector gets a longer, wider delay than a dot —
  noticing a popup takes longer than a glance.
- **No limit on a run, no rest**: nothing ended a run or paused it on its
  own. `session_max_minutes` and scheduled breaks are drawn per run, and a
  break reuses PAUSE (keys up, nothing else), muting the watchdog's
  "needs attention" line, which would otherwise fire on every rest.
- **Perfect execution is a tell**: re-press gaps are drawn inside the
  game's flash window, so the bot never missed a flash. `move_miss_chance`
  skips a re-press for real. The first guard test passed with the guard
  removed — a short sideways flash never shrank the model anyway (its dx
  is a median) — so it checks the up flash, where a short rise does. A
  second consumer needed the guard too: `hop_px`, the average hop length,
  would have dropped 30% on one slip.
- **The parity fixture counts**: adding a top-level `bind` key to
  `config.json` broke `config_round_trips_and_parses_like_python`, whose
  fixture the Python host wrote. A new top-level key is written only when
  it isn't the default.

## Input capture is blocked while the game is focused (2026-10-04)

- **No human baseline from inside the game**: `key_timing` (first a
  `WH_KEYBOARD_LL` hook, then Raw Input with `RIDEV_INPUTSINK`) saw 0 key
  events whenever the game window had the focus — even keys pressed in
  that window while the recorder watched another title — and worked
  normally (129 events, the real keyboard identified) with Discord in
  front and the game still running. The game isn't elevated, so UIPI
  isn't the cause: its protection withholds keyboard input from other
  processes while it is in front. An independent hook written in C# saw
  nothing either.
- **What it means**: a person's in-game timing can't be recorded from the
  PC, and the game's protection watches the input stack while it has the
  focus — worth weighing when deciding how much to invest in the Pico's
  USB identity. The recorder still measures any other window, and the
  Pico and a real keyboard show up as separate devices.
- **A rough human reference** (typing in Discord, not play): holds median
  120ms (p5–p95 54–267); different-key gaps median 386ms; 2 of 43 under
  10ms (4.7%).

## Key events arrive on an 8 ms comb (2026-10-04)

- **The sub-10 ms chord idea can't work on this firmware**: the host's
  ACK round trip to the Pico is a median 4.1 ms (p90 4.7, max 7.2), so
  gaps of 4–9 ms could be sent, but `chord_probe` (F8/F9 pairs read back
  with Raw Input) shows Windows receives them on a grid. Asked 0, 3, 5 or
  6 ms apart, the pair arrives 8.0 ms apart (min 7.7, max 8.2); asked 8,
  12, 20 ms it arrives 8, 16, 24. Pipelining the second command without
  waiting for the ACK changes nothing.
- **Why**: CircuitPython hardcodes `bInterval = 8` in the HID endpoint
  descriptors (`usb_hid_descriptor_template`, not a build macro), so the
  host polls the Pico every 8 ms and every report leaves on a poll. The
  K75 the Pico clones polls every 1 ms (`bInterval = 1` in its dump).
- **The bigger tell**: not just chords — *every* gap between key events,
  and every hold, lands on multiples of 8 ms with ~0.2 ms of jitter.
  Humanised delays inside the host are quantised on the way out, and a
  real 1 kHz keyboard's gaps are not. Anything that timestamps raw input
  at millisecond resolution can see the comb. Fixing it needs
  `bInterval = 1`: a custom CircuitPython build, or the TinyUSB firmware
  (Phase E); `boot.py` can't change it.
- **Fixed with a two-byte patch** (`firmware/`): a CircuitPython 10.3.1
  build with `bInterval = 1` (it needs Arm GCC 14; Ubuntu 22.04's is too
  old). `chord_probe` afterwards: asked 8/12/20 ms arrives 8.0/12.0/20.0
  (min–max within about ±2 ms), asked 0 or 5 ms arrives ~4.8 ms (the ACK
  round trip is the floor), and unacknowledged pairs reach 1.6–3 ms. The
  comb is gone. The USB identity and `boot.py`/`code.py` were untouched
  by the reflash.
- **Chord gaps** (`chord_gap_chance`): with polling at 1 ms the sub-10 ms
  idea became real. Through the real `HidController`, F8→F9 pairs drawn as
  chords arrive a median 8.8 ms apart (3.4–16.1; the same ±2 ms arrival
  jitter as every row). There is no in-game human data to size the odds
  from — 20% is a modest guess, with a 5 ms floor because nothing beats
  the ~4.5 ms ACK round trip.

## A walk that "reached" its target without arriving (2026-10-04)

- A navigator walk leg aimed at `x.round()` with the tolerance truncated,
  while the navigator judged arrival (and whether the walk still needed
  doing) against the unrounded `x`. Goals are anchors at fractional px,
  so the walk could stop within tolerance of the rounded target but just
  outside it of the real one — 96 for 100.4 with a 4px tolerance. Every
  step then re-walked a zero-length leg ("Navigating to … / Navigation
  target reached") without moving: TRAVEL burned its 40 steps and banned
  the anchor for 45s, and a rune detour could spin until its 90s timeout.
  The simulator's walk teleported onto the target, so no test saw it; it
  now has `edge_stop` (stop at the first whole pixel within tolerance,
  like the real primitive). The walk aims at the exact `x` with the same
  tolerance arrival uses.

## Rune detours and the lie detector (2026-10-03)

- **"No route" while rope lift cooled**: the navigator left cooling moves
  out of every route, so a rune on Castle's Gate 2's top tier — which
  only rope lift reaches from below once up flashes are learned at 13px
  (the tier is 14 up) — read as unreachable whenever the lift had just
  been used, and the detour paused for good. A cooling move is now routed
  through and waited out at its takeoff.
- **One failure paused until the rune vanished**: a missed landing or two
  on the way ended the detour for good. Failures on the way now go back
  to farming and retry (~15s, 3 tries); only an undrawable rune (nothing
  under it, no room beside it) pauses at once.
- **An unread puzzle left no trace**: the solver gave up without saving
  what it saw, so whether the reader or the activation failed was
  unknowable. An unread read now saves the window (`unread` rune event).
- **Lie detector**: three Save-window captures showed its countdown
  window at the same spot, titled in a yellow-green (G 211-254, R
  183-221, B < 25) no other UI on those screens uses. The title's 80x9
  pixel pattern is matched against text-coloured pixels anywhere in the
  window: it hits all three captures and none of the other saved windows
  (rune solves, snapshots, the MULTIKILL banner, chat).

## Jumps over rope lift (2026-10-03)

- Rope lift was the cheapest rise (cost 0.5 against an up flash's 1.0),
  so it was used even onto platforms an up flash or jump reaches. Now a
  proven jump-type link between two platforms removes the rope-lift edge
  between them; rope lift stays for rises nothing else reaches, and where
  only unproven (exploratory) jump reach covers the rise — there it's the
  safer move. The Python parity trace turns this off (`prefer_jumps:
  false`); cooldown-planning tests that need rope lift as a choice do too.

## Rune arrows (2026-10-03)

- **The puzzle**: "Tap the arrow keys in the correct order to activate
  the rune" over a strip of four arrows near the top of the window
  (around y 190-250 of 1366x768). The strip and the arrows' x positions
  move between runes, so slots can't be fixed.
- **The arrows read by colour**: each is a solid arrow shaded green at
  the tail through yellow to orange-red at the tip. Strongly coloured
  pixels with hue 12-160°, grouped into blobs, give the four largest
  blobs as the arrows; tip-hue (< 45°) centroid minus tail-hue (> 95°)
  centroid is the direction. A prototype read all 7 attempts in the
  first 5 recordings; the two that disagreed with the keys pressed were
  wrong presses (the retries that followed confirm it). Fixed RGB buckets
  first read only 1 of 7 — mid-tones like `99ff11` fell between them.
- **`rune_arrows::read_arrows`** (the Rust reader): band x 0.28-0.72, y
  0.22-0.38 of the window; arrows are blobs of 150-1000 px (coloured
  scenery below the strip makes much larger ones); the four *clear* blobs
  forming the tightest row (centres within 40px, sizes within 2.5x) are
  the strip — taking the four largest picked scenery instead. Over all
  ~550 recorded frames it read nothing before the puzzle showed or after
  the first arrow was pressed (the strip changes then — read all four
  first, then answer), and every attempt correctly from ~0.1-0.2s after
  the interact press, JPEG frames included.
- **Not every puzzle is green to red** (2026-10-03): two bot solves read
  "found 0 clear arrows" and "found 2" with the puzzle plainly up — its
  arrows were shaded magenta → blue → cyan and blue → cyan → green, hues
  the reader didn't accept. What all the shadings share is the direction:
  hue falls from tail to tip (magenta 300° … orange 30°). The reader now
  takes hues 12-330° and splits each blob at its own hue span (lowest 30%
  = tip, highest 30% = tail, span ≥ 30°). Accepting cyan let a cyan glow
  in the scenery swallow an arrow, so past 160° only near-pure pixels
  count (saturation ≥ 0.85, value ≥ 0.8); and a green glow level with the
  strip won the "tightest row" once its own direction became readable, so
  rows are scored on spread plus 25 × ln(size ratio). All 26 saved
  attempt windows read as before (the one disagreement was the new
  reader's, fixed by the size term), and both unread puzzles read.
- **A misread over busy scenery** (2026-10-03, strip k): read left,
  right, left, down for right, left, up, down — the rune failed once and
  read right on the retry. The "up" arrow sat over a saturated green bush
  and joined it into a 45x35 blob with no clear direction; the first
  arrow was half hidden behind a monster (~100 px, under the 150 minimum);
  and a monster's green glow (37x89) passed as an arrow. Now blobs must
  be arrow-shaped (10-36 px a side, at most 1.8:1), the minimum is 80 px,
  and where the looser colour rule gives no clean arrow a second pass
  over near-pure pixels (saturation ≥ 0.85, value ≥ 0.8) does — the
  arrow is purer than the scenery it runs into. The size ratio allowed in
  a row rose to 3.5 (a half-hidden arrow is small); the ln-ratio term
  still prefers even rows. All 13 strips and 30 saved attempts read
  right, the misread one included.
- **Reading by shape, over frames** (2026-10-04): to stop each new
  shading from needing a fix, direction is also read from the arrow's
  silhouette. A 1-D width profile along the arrow wasn't enough (27 of
  52 arrows read, the rest unsure, some leaning wrong) — sparkle and
  antialiased fringes stretch it, and per-slice width can't see whether
  the shape is centred. Opening the blob (1px erode, then dilate) and
  comparing it on a 12x12 grid with an ideal arrow of the same length
  and width read 39 of 52 with none wrong, so it's a safe second
  opinion: shading and silhouette must not disagree, and either decides
  alone. Shape alone reads 5 of 13 strips; with shading, 13 of 13.
  Locating the strip itself first was dropped: its 1px yellow outline
  is translucent (it vanishes on bright scenery) and the instruction
  text's orange is shared with other UI. The solver now watches frames
  (`ArrowWatch`): still arrows answer once two frames agree; an arrow
  whose reads keep changing is spinning, and the game makes a spinning
  arrow pause on or wiggle across its answer, so after 1.5s the
  direction it read most is pressed (tested on painted spins; no real
  spinning puzzle recorded yet).
- **Recordings mislabelled solves**: leaving PAUSE passes through a
  reaction delay published as a bare PAUSE, which the recorder took for
  an interruption; it now waits 1.5s before calling it that.

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
