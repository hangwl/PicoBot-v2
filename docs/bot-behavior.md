# Bot behavior

## Continuous patrol

**Loop ordering policy** (`patrol_policy`):

- `weighted` (default) — the next anchor is drawn by roulette with
  probability ∝ 1/route-cost^`patrol_weight_temp` (temp 1 = 1/cost).
  Near anchors are likely, far anchors keep a real chance — nothing is
  neglected, and the loop never becomes a metronome. Lower the temp
  toward uniform randomness; raise it toward greedy.
- `greedy` — always the cheapest next anchor, with ±20% cost jitter for
  variety.
- `zigzag` — row by row: the next anchor comes from the nearest row
  (platform rows within 6px share one) that still has anchors, cheapest
  first within it. With sweeps the loop snakes — a row one way, the next
  row back — instead of hopping between tiers.

All three ban unreachable anchors for 30s, execute the plan strictly
(splice-on-fail, 2 misses → ban), and pipeline the next loop before the
current one ends. (`core/src/bot/patrol.rs`, `core/src/planner.rs`)

With ≥2 anchors and drawn platforms the bot strictly follows a planned
loop:

- **The plan is a fixed loop of anchor-to-anchor segments** over the
  movement graph, ordered greedily by route cost (±20% jitter so loops
  vary). Legs execute in order — no re-planning between legs. The plan is
  drawn on the Panel view (olive), the current segment yellow.
- **The next loop is planned before the current one ends** (when the bot
  enters the last segment), so a planned path always exists. If planning
  ever yields nothing, the bot **halts and takes a break** — no movement,
  no attacks — and retries every few seconds.
- **Planning respects cooldowns**: the planner runs a clock along the
  loop (route cost × a learned pace, the real seconds a segment takes
  per second of cost) and only plans rope lift or teleport where it will
  be ready again; a cooling one is replaced by the next-cheapest move.
  An unbound one is never planned. When only a cooling move gets
  somewhere, it's planned anyway (the run re-routes if it's still
  cooling) — a cooldown never skips an anchor. The next loop starts its
  clock after the current segment's legs.
- **One leg per tick**; safety checks run between legs. A failed leg
  splices a re-route into the plan on the next tick, from where the
  player ended up (after any time off the platforms); rope lift and
  teleport are excluded while cooling, without counting a failure. 2
  missed landings in a row ban the anchor for 30s and re-route to the next one.
- A leg **interrupted** by focus loss, a hazard or a stop is aborted, not
  missed: no miss counted, nothing learned, and it's re-routed on resume.
- **Anchors are pure pass-through waypoints**: arriving fires the
  anchor's non-summon `on_arrive` skills and places at most one summon
  (see Summons) in passing, then the bot moves on — no linger, no dwell
  timers.
- **Platform sweeps** (`patrol_mode: sweep`, the default): an anchor marks
  a spawn platform, and the loop **sweeps** it rather than visiting the
  point. A sweep goes to whichever end of the platform is cheaper to
  reach, then crosses from 8px inside that end to `sweep_reach_px`
  (default 12, at least 8) short of the other — never nearer an edge than
  8px, since a walk slides a few px past where it lets go — on flash hops with an attack window
  each, so the last attack, facing the far end, covers it. Several
  anchors on one platform make one sweep (the first anchor's; the others
  aren't targets). A platform too short for that is visited at its
  middle. The loop orders sweeps by the cost of getting to them only —
  every sweep is made once a loop, so a long platform isn't put off for
  its length — and goes on from each sweep's far end. Arrival skills,
  buffs and the summon happen at the end of the sweep. A missed landing
  re-routes to the sweep's entry, or straight on to its exit when already
  on the platform. The crossing is one walk leg along the platform, never
  a route through other tiers; when both ends cost about the same to
  reach, the nearer one is entered, so a row keeps its direction.
  `patrol_mode: anchors` passes through anchor points as before.
- **Attack heatmap**: every attack adds to a 4px cell at the player's
  dot; cells fade with a 5-minute half-life and reset on a map change.
  The Panel view's **Heat** toggle paints them (blue = rarely, red =
  often) while the bot runs — drawn platforms with no colour on them are
  the stretches the rotation neglects. (`core/src/heat.rs`)
- **Loop policy and temperature**: every reachable anchor is visited
  exactly once per loop; `patrol_weight_temp` only orders the loop.
  Higher temperature → nearer-first sweeps → shorter loops, so *every*
  anchor (far ones too) comes around sooner; lower → more random,
  longer loops. An anchor that falls behind is being skipped or missed —
  Setup → Tuning → Patrol → **Anchors** lists visits, last visit, missed landings
  and skips by reason per anchor (this host session; **Reset anchor
  stats** after fixing geometry).
- **Bans**: no route, unreachable-at-plan-time (including anchors not
  on any drawn platform — "re-place it"), or 2 missed landings → skipped
  for 30s, so one bad anchor can't shrink or stall the patrol.
- Hand-authored `legs` for a pair still win (ropes aren't in the graph).
- Fallbacks: no drawn platforms → the older straight-line patrol;
  platforms drawn but the player off all of them → wait with a throttled
  "not on any drawn platform" diagnostic (the legacy patrol would plan
  routes from an off-graph start and ban every anchor); one anchor →
  weave on its platform indefinitely (standing on another platform or
  level hands the anchor to TRAVEL, or takes a break while a failed
  leg has it banned — a leg interrupted by a pause keeps its target and
  is retried, unbanned); no anchors → weave around where
  grinding started.
- A walk (`move_to_point`) gives up after 8s plus three times the
  straight walk to its target — blind, or bouncing around the target —
  so a tick can't run on long enough to starve the watchdog.
- Platform matching is asymmetric: the player glyph floats above the
  drawn row, so a point matches its platform when it sits up to
  `snap_px` above the row (and up to 2px below) — stacked tiers stay
  unambiguous.

## Anchors

Anchors float `anchor_float_px` (default 4) above the drawn platform
line — the same height as the player icon — instead of sitting on it.
Placement snaps the height to the platform under the click and raises it;
the runtime checks arrival by platform membership, so older on-row
anchors keep working.

## Moves & learned reach (`navgraph.rs`, `reach.rs`, `bot/navigator.rs`)

The movement graph links drawn platforms (plus **learned** ropes — see
below) with every move whose **reach** covers the gap:

| Move | Input | Use |
|---|---|---|
| walk | flash weaves (walk near the goal) | along a platform |
| `jump` / `flash` / `double_flash` | jump; jump + re-press; + second re-press | horizontal gaps — a carried move always flies its full learned distance, so it's planned aimed at the middle of the platform it lands on (taking off as far back as needed, at least 3px inside its own platform), only where it comes down on that platform and no platform in between catches it |
| `up_flash` | jump, then Up + jump mid-air | platform directly above |
| `up_side_flash` | up flash, then a sideways flash mid-air | higher platform across a gap |
| `rope_lift` | `up_jump_skill_key` | grabs the highest platform within `nav_rope_lift_px` (~90) of the takeoff column — only where no proven jump, flash or up flash reaches that platform (those come first); still used to skip a tier no single jump reaches. Costs `rope_lift_cost` (1.5s: its wind-up alone is ~1.2s), so a route doesn't lift to a higher tier just to drop back down |
| `down_jump` / `drop` | down + jump; walk off an end | lower platforms; a down jump takes off more than `rope_clear_px` (8px) from any learned rope hanging off its platform — Down on a rope's top grabs the rope instead |
| `climb_up` | a **moving grab**: Up held ~0.15–0.25s before takeoff, then a hop toward the rope from 6px beside it (or a flash jump off a platform end up to 20px out, flash kits) — then climb and mount the top platform. Straight-up only when the platform is too narrow to step aside | boards from any platform within jump reach below the rope's bottom end, which stays ≥5px above the platform under it |
| `climb_down` | grab at the top (hold down), descend past the rope's bottom end, land below | lands on the nearest platform under the rope's end |

There is no direct release from a rope: after any failed climb the bot
leaps off (hold direction + jump) toward the nearest platform it can
land on — at or below it, never one above — and replans from where it
lands. Climb checks ignore 1px of dot jitter (it is neither progress nor
a fall).

## Player dot

The dot is found by its colour (`minimap_colors.player`); pixels within
2px of each other count as one marker, so a rope or platform line
through the dot (which splits it) doesn't hide it. Groups under 6px are
specks. The reported point is the marker's true bottom row (its feet);
x rounds half-up so an even-width dot's centre always lands on the same
side. The overlay frames the dot itself — its detected bounds with a 1px
margin — not a box centred on the feet; anchors (also feet points) are
framed where a 6x6 dot standing on them would be, so an arrived player
lines up with its anchor.
Detection tracks the dot per thread: when several yellow markers
qualify, the one nearest the last position wins, and one missed frame
repeats the last position before the dot counts as lost. With the dot
lost the bot only waits — it never learns ropes or jumps blind. Each loss
saves the minimap crop for later study (see `development.md`, Debug frame
captures).

Rope climbs cost `rope_penalty` (default 5s) on top of climb time —
**ropes are a last resort**: platforms are normally reachable via jumps,
rope lift, or (future) teleport. Set `rope_penalty: 0` to let the
planner use them freely.

## Learning ropes from stuck events

Ropes are not drawn. When the bot is off every drawn platform at a
stable position for over 2 seconds it holds **Down** briefly: on a rope
the character slides down, on ground it only crouches (Down, not Up —
Up on a portal changes maps). A confirmed hang records a rope segment
from that spot up to the platform above (persisted in the map file,
drawn brown on the overlay); later hangs on the same column extend that
segment instead of adding another. A learned rope's top stands 3px above
its platform's row so the climb ends on the platform; older ropes ending
up to 6px under the row are lifted onto it when the graph is built. Then it leaps off and replans. Ground
that isn't drawn is logged ("not a rope; draw the platform there") and
the bot hops back toward drawn ground. Learned climbs carry the same
`rope_penalty`, so they're used only when nothing else connects.

- Reach = sideways `dx` and upward `rise` in minimap px. Starting values
  are realistic guesses (`nav_*` config keys); the **Measure moves**
  dashboard button replaces them with real numbers — the bot performs
  each move a few times and records the observed takeoff→landing into
  `nav_reach_file` (see [layout.md](layout.md#measuring-moves)).
- Farming keeps learning: success grows the envelope to what was
  observed; the planner may explore up to 1.3× the proven reach at a
  cost penalty; the **second consecutive** miss shrinks the envelope
  (one miss may be input timing) and an exploratory miss sets a ceiling.
  Sideways moves (jump, flash, double flash) are the exception: their dx
  is the **median of the last 9 carries** (`"carry"` in the file, 3 make
  a median) — a flight that came down on its own row, or one that fell
  short to a lower row (an upper bound). The planner flies them their
  full distance, so a furthest-ever carry would put every takeoff too
  far back. Rises are measured between platform rows, not dot reads (an
  apex read isn't a landing). Up flashes and down jumps settle first
  (0.5s) so they don't take off moving. Delete `nav_reach_file` to relearn.
- Rope lift is **preferred over up-flash whenever it's ready** (cheapest
  rise). While it's cooling down it's left out of the plan and the bot
  up-flashes instead — it never waits on the cooldown. A lift that
  starts cooling during the approach re-routes without counting a
  failure.
- The dashboard's **Route** button shows every edge (colours per move)
  and previews routes on click.

## Class profiles

`config.json` holds named class profiles with an active selector — the
kit decides which moves exist and where attacks fire:

```json
"class": {
  "active": "mage",
  "profiles": {
    "mage":  {"travel": "teleport", "air_attacks": false,
              "teleport_key": "shift", "teleport_cooldown": 0.8},
    "hero":  {"travel": "flash"}
  }
}
```

- `travel: flash` — jump + mid-air re-press (the default kit); the graph
  generates flash/double-flash/up-flash/up-side edges.
- `travel: teleport` — blink (gains height): the graph generates
  **teleport edges** instead (horizontal gaps and up-teleports), with a
  learned reach envelope (`nav_teleport_dx`/`nav_teleport_rise`,
  refined like every move) and cooldown-aware planning (excluded while
  cooling, never waited on). The weave is blink → attack → blink.
- `travel: walk` — no air movement; attacks weave into walks.
- `air_attacks: false` — the attack tail fires **after landing**
  (mages can't attack suspended); `true` weaves mid-air as usual.
- A teleport without a bound key falls back to `flash`.
- **Per-profile skills:** a profile may carry its own `skills` dict —
  the character's kit (attack, buffs, summons, movement). When active,
  it **replaces** the global skill book; per-map overrides still merge
  on top. The dashboard's Skills panel edits the active profile's kit
  (seeded from the global book on first edit) and shows which book it's
  editing.
- Measure-moves covers teleport when a key is bound.
- **Reach is per profile:** measurement writes
  `nav_reach_<profile>.json` (e.g. `nav_reach_mage.json`) — each class
  measures its own movement ranges, and switching profiles swaps the
  learned envelopes.

## Movement rule & attack weaving

Bot-controlled movement looks like a player farming, not a macro:

- **Point to point = flash weaves** (`_flash_weave`): hold the direction,
  jump, re-press mid-air (the flash jump), then an **attack window**
  once the flash has triggered (see below). An attack before the
  re-press eats its input window and the flash never fires.
- `move_to_point` **travels by flash weaves** whenever the target is
  farther than `walk_band_px` (default 8) **and the platform leaves a hop
  of room ahead**; walking is only the final precise approach (and future
  precise destinations like rune solving). Hop distance is learned from
  observed hops (starts at 14 minimap px). Hops that don't move fall back
  to walking; with `flash_jump_enabled: false` attacks weave into walks.
- Walk legs are horizontal (`flat`): a few px between the drawn row and
  the real standing line never triggers vertical jumps.
- A gap flash that never triggers releases the direction as soon as the
  character drops below the takeoff row (or shows no progress for 0.5s)
  instead of walking into the gap.
- Landings are scored tolerantly (platform x-span + row slack): a move
  that succeeded never shrinks the reach model — only a real miss does.
- After each move the bot waits for the landing: first for the
  character to leave its takeoff spot (up to 1.2s for a rope lift, whose
  rope grapples while the character stands still), then for two steady
  reads **on a drawn platform** (≤2.5s) — steadiness alone is not enough,
  since the top of a jump is steady too, in the air.
- An up flash comes down on the **highest** platform its peak (the
  learned rise) clears, so the graph only plans it onto that one — never
  onto a lower tier it would fly past.
- A miss only counts against a move's reach when it fell short of the
  plan; one that overshot or came down elsewhere says nothing about reach,
  and neither does one never seen leaving its takeoff spot (an eaten key,
  a stun).
- Re-press gaps are tunable: `flash_repress_seconds` (jump → flash) and
  `combo_repress_seconds` (between chained flashes).
- The **up flash** is timed from the first jump's key-down: the re-press
  lands inside the plateau of the class's up-flash timing sweep (the
  contiguous delays within 1 px of the highest peak), log-normally varied
  around its middle; without a sweep, 0.16–0.30 s around 0.22 s. Up goes
  down ~40 ms before the re-press.
- **Slips** (`move_miss_chance`, default 0 — try 1–3%): a flash move can
  skip its last mid-air re-press (`Body::slip`), so the hop really falls
  short and the navigator treats it like any missed leg (logged "Slip:
  skipping the … re-press", then "Missed …"). Slips are marked
  (`BotState::injected_miss`) and never taught to the planner: no
  `ReachModel` update, no `hop_px` update. The session's move counts
  still record the miss. Rope grabs never slip, and a move measurement
  (`BotState::measuring`) never does.
- **Attack windows** (`Body::attack_window`): an *air* window opens once
  a flash has triggered (gap, double, up and up-then-side flashes; a
  blink or a class without air attacks gets a *ground* window after
  landing instead), and a *ground* window opens after every successful
  landing. A window fires with odds — `move_attack_chance` (default 1)
  after a move, `ground_attack_chance` (default 0) after a landing — and
  a firing window casts one attack, or two with `weave_double_chance`.
  Each cast picks among ready attacks legal in the window (`stance`),
  cooldown skills before spam, by `weight`. A ready skill tagged for
  only that window's stance fires at least 85% of the time, so
  ground-only skills get cast on landings. `target_attacks_per_min`
  (off at 0) adds the shortfall to each window's odds and subtracts the
  excess, over a one-minute count. With no player dot the bot waits
  instead of attacking in place; there is no stationary mode. The
  defaults reproduce the old behaviour: every move attacks, landings
  don't.
- Single-anchor and roam weaving use the same primitive, bouncing
  between the drawn platform's ends.
- All gaps are log-normal (`timing.human_between`: truncated to the game's
  input windows, e.g. re-press 0.11–0.26s around 0.17s).

## Human input timing (`timing.rs`, `io/src/hid.rs`)

- **Session tempo**: each bot start draws a pace (±~7%) that also drifts
  slowly within the session (mean-reverting, minutes-scale). It scales
  every median; bounds are applied after it, so input windows hold. A
  draw outside its bounds is redrawn rather than clamped — clamping
  piled up to a quarter of the samples on the exact bound.
- **Key spacing**: `HidController` spaces consecutive key events (downs,
  ups, and `release_all`) by a drawn gap (~25ms, 10–70ms) — fingers never
  land at once. Time already spent (a deliberate sleep, the serial
  round-trip) counts toward it, so timed sequences barely shift; where a
  key leads the next press (Up before the up-flash re-press), the lead
  sleep gives the gap back so the jump still lands on time. A press by a
  *different* key than the last event follows within 5–20ms (~8ms)
  instead, with odds `chord_gap_chance` (default 0.2) — chords and quick
  rolls; the same key never does. The floor is the Pico's ACK round trip
  (~4.5ms), and the gap is read when the bot starts.
- **Holds**: lognormal around ~85ms (a bit longer for arrows and
  modifiers), 45–220ms.
- **Reactions** (~0.22s, 0.13–0.55s) only where a person reacts to
  something unexpected: resuming after a pause clears, and noticing a
  missed landing before re-routing. Planned move chains flow without
  one. Walking lets go of the direction a moment (~40ms) after the
  target is seen.
- The Pico firmware polls every 1ms, so it doesn't round every key event
  to 10ms.

## Skills (`skills.rs`, dashboard Skills panel)

Registered skills have `key`, `kind`, `cooldown`:

- `attack` — attack loop / travel weaving.
- `movement` — an attack that also moves the character (a rush, a
  hold-and-dash). It is cast exactly like an `attack` (same windows, odds,
  stance, weight, rate count); the difference is that its effect is
  **measured** (Skill effects) so it can be kept off platform ends. Plain
  attacks aren't measured. Traversal keys (jump, flash, rope lift,
  teleport) are config keys, not skills.
- `buff` — a ground cast, given **priority at anchor checkpoints**:
  arriving, the bot settles (two steady reads on a drawn line, up to 1s)
  and casts every due buff first — ~0.45-0.9s apart so each cast's
  animation finishes — then the anchor's `on_arrive` skills and summon.
  Not standing (still moving, off the drawn lines, no dot) holds the
  buffs for the next checkpoint. A buff is due once off cooldown *and*
  worn off: with a `duration`, it lasts that long from its last cast.
  With fewer than two anchors (no checkpoints) due buffs go out whenever
  the character stands.
- `summon` — placed at anchors (below). Allowed at an anchor that lists
  it in `on_arrive`, or at any anchor that lists none.

Skills also carry a `stance` (`ground`, `air`, or `any` — the default)
and a `weight` (default 1), stored only when not default. The attack
scheduler (windows, above) uses them.

### Walking and ropes

Ropes are a last resort: a climb edge costs `rope_penalty` extra seconds
(default 5), so it is planned only when no hop path is within that much
cheaper — 30 or more all but bans ropes. `walk_cost_factor` (default 1)
multiplies every walking leg's cost, so routes and loop orders that walk
less win. Both are on Setup → Tuning → Patrol (`patrol|rope_penalty`,
`patrol|walk_factor`). The Home status list and the stop/heartbeat
summaries count the legs run by kind (`flash 41 · walk 12 · climb up 3 (1
missed)`), so you can see what the bot is really doing.

### Skill effects

Some attacks change a flash — they hold the character up or shift where
it lands — and some move the character on the ground.
`measure|profile|effects` measures both for the kit's `movement` skills and saves `profiles.skill_effects`
(rows carry `where`: `air`, or `ground`):

- **In the air** (flash-jump classes, with flight recording): plain
  flashes a few times, then with each skill that isn't ground-only cast
  just after the re-press — where an attack window casts it. Per skill:
  the landing shift toward the jump (`dx`, negative = pulled back), the
  airtime added (`hang`) and the peak change (`rise`).
- **On the ground** (every class): each skill that isn't air-only, cast
  standing still after two short taps to face a direction, nothing held —
  how a landing window casts it. Per skill: how far it moves the
  character the way it faces (`dx`).

A skill with a long cooldown is measured once; one on cooldown over 40 s
is skipped. `measure|effect|<skill>` (a **Measure** button per skill on the
Skills fold on Setup → Class and the Measure page) measures one skill and keeps the other
skills' rows; a whole run saves each skill as it finishes, so a stopped
run keeps what it finished. The plain-flash baseline is measured once per
run and shared by its skills.

While a flash is planned onto a platform, an air window only picks
skills whose measured air shift fits the room left: the navigator sets
`air_slack` (px the landing may move back and forward, from the target
platform's ends with a 6 px margin) for each flash leg, and the weave
sets it from the weave bounds. A ground window does the same with the
skills' ground shifts: inside a leg it uses that landing's room, else the
platform room either side of the character (one extra capture, only when
a skill with a measured ground shift is in the kit). Unmeasured skills
aren't held back.

### Walk taps

`measure|profile|walk` (Setup → Measure moves → Walk taps) sweeps
keypresses of 30–260 ms on one drawn platform and saves how far each
carries (`profiles.walk_taps`: `{ms, n, dx, sd}`), then times the walking
pace and slide (`profiles.walk_speed`). Each direction change spends one
discarded tap: the first only turns the character. With a tap table,
`Body::nudge_to` closes in on a target with the longest tap that doesn't
overshoot, re-reading the dot after each (8 taps at most). Only a **rope
grab's takeoff** finishes that way — every other leg's takeoff alignment
stays a plain walk, so the taps don't slow the loop. The planner's walking
cost uses the measured pace. Without a sweep, nothing changes.

## Summons (`summons.rs`)

- **Charges**: a skill stores up to `charges` uses (default 1); while
  below the maximum, one returns every `cooldown` seconds (the timer
  restarts for the next). With one charge it's a plain cooldown.
- **Uptime**: each cast lasts `duration` seconds (0 = until its cooldown
  ends). Up to `charges` instances of a skill can be out at once; casting
  one more removes that skill's oldest (as the game does).
- **One summon per anchor**, of any kind: an anchor with a live summon
  gets none. Otherwise, on arrival, the summon allowed there with the
  most charges banked (by fraction) is cast.
- **Standing only**: two position reads a moment apart must agree within
  1px and be on a drawn platform (feet up to 6px under a line drawn a
  little high still count) — never mid-air or on a rope. A character
  still moving (a landing slide, a movement skill's carry) gets up to 1s
  to settle; the route is untouched. If it doesn't, the visit is skipped
  and logged ("still moving", "not on a drawn platform at (x, y)",
  "player dot not visible"); the anchor stays free, so the next loop's
  visit tries again.
- The bot can't see summons: placements are timed from casts, cleared
  on a map change and when the bot starts. Home shows them ("fountain at
  a2 · 38s left · 1/2"). `wait_on_arrival` is ignored.
- Movement keys (jump, flash, rope-lift) register the same way.

`position_jitter_px` adds entropy to hand-authored walk legs. There is
no wander state, dwell timer or idle breather — the bot keeps moving.

## Boundaries (no wall zones)

There are no wall zones: **the drawn platforms are the only geometry**,
and their ends are the boundaries. The graph's walk edges run along
drawn platforms, travel flashes measure room against the platform span,
and the weave bounces within platform bounds — drawn geometry alone
prevents wall-banging. Down-jumps only exist toward drawn platforms
below, so the lowest drawn platform is the map's bottom.

## Safety

Pauses the bot (and fires a Telegram alert if configured) on: window
focus loss, more other players than
`allowed_other_players` (the alert says how many), a map transfer
(loading blackout) mid-leg, a map no saved entry matches, and the **lie
detector** (`pause_on_lie_detector`, default on): once a second the
whole game window is searched for the window's "LIE DETECTOR" title (its
yellow-green header text, matched pixel for pixel against a template
from real captures, anywhere on screen; ~3ms). The bot pauses and
alerts; the mini-game is the player's to solve, and farming resumes once
the window closes. The first sighting is saved to
`debug/frames/*_liedetector/`. A rune is handled separately (below).

**Reaction time**: hazards a person at the keyboard would see — other
players and the lie detector — are acted on after a human delay (~0.22s
for a player's dot, ~0.7s for the lie-detector window, log-normal and
bounded). The alert goes out at once; the bot keeps playing until the
delay is up, then releases every key and pauses. Hazards that are the
bot's own business (loading screens, map identity, focus loss) stop it
immediately.

**Session limits** (Setup → Safety → Session): `session_max_minutes`
ends the run on its own (alert: "Session limit reached"), the limit drawn
per run within ±15% of the setting. `break_every_minutes` with
`break_minutes` schedules rests: after about that much farming (±30%)
the bot goes to PAUSE for about the break length (±40%) — keys released,
shown as "scheduled break", no alert and no long-pause warning — then
resumes where it was. A rune detour takes priority and a break waits
for it. All three are 0 (off) by default.

### Runes

With `stop_when_rune_appears` on, a rune marker on the minimap
interrupts farming:

- `rune_action: solve` (default) — **RUNE** detours onto the rune (the
  player's glyph centred over it, within 1px) and solves it:
  1. **Activate**: after a short settle, press `rune_key` (default `y`).
  2. **Read**: watch the game window (a frame every 0.05s, up to 2.5s)
     with `rune_arrows::ArrowWatch`. Each arrow's direction is read by
     its shading (down the hue circle from tail to tip — green to red,
     magenta to cyan, blue to green) and by its silhouette (head, tip,
     shaft); the two must not disagree, and either decides alone when
     the other can't tell. Still arrows answer once two frames agree. An
     arrow whose reads keep changing is **spinning**: it pauses on, or
     wiggles across, its answer, so after 1.5s of watching it's answered
     with the direction it read most. At the deadline the bot answers
     what it has; a puzzle with no four arrows counts as a failed try
     (its window saved as an `unread` rune event); two of those give up.
  3. **Answer** like a person: ~0.4-1.4s to take in the puzzle, then the
     four arrows with uneven ~0.17-0.7s gaps (log-normal, tempo-scaled).
     Keys stop at once if the game window loses focus.
  4. **Step off to verify**: tap toward the side of the platform with more
     room until the dots no longer overlap — standing on the rune hides
     it, so only stepping off shows whether it's gone. A rune that shows
     again failed; one that stays gone (the tracker's 5 reads) was solved
     and farming resumes, the patrol re-routing from there.
  5. **Failed**: the rune is locked for 3s. The bot waits that out plus a
     human margin with small wiggles (short taps either way, human
     pauses), walks back on, and tries again — at most 3 tries, then it
     pauses with an alert.
- `rune_action: approach` — **RUNE** detours to a spot beside
  the rune and **pauses** there for the solve (dashboard Control page).
  The spot is on the rune's platform (the drawn row up to 12px under its
  marker), on whichever side is cheaper to reach, with the player's glyph
  touching the rune's (`TARGET_GAP` 0px). The
  route is the navigator's (flash hops, attack windows, one leg per
  tick); on the rune's platform the bot places itself (tap nudges when a
  tap table is measured, else a precise walk), then a short tap toward
  the rune turns it to face it. Arrival is checked on the minimap: the
  glyphs must touch (0px gap) or overlap — standing on it counts.
- `rune_action: pause` — pauses where it stands.
- **On the way**: a move that's cooling (rope lift, teleport) and the
  only way there is waited out at its takeoff, not counted as "no route";
  a route check that finds none is retried twice (a read mid-move or off
  the drawn lines finds none for a moment).
- **Fallback**: a detour that fails on the way (no route, 3 missed
  landings, 90s) goes back to farming and tries again after ~15s
  (log-normal, 9–30s); the third failure pauses with an alert. No drawn
  platform under the rune or no room beside it can't be fixed by trying
  again: those pause at once.

The rune is **remembered**, not re-read per frame: the player's dot is
drawn over the rune's, so standing on it hides it. A rune that vanishes
while the player's glyph covers it is still there; it counts as gone
after 5 reads without it with the player clearly off it (or once it has
stayed covered 30s). Then the bot resumes farming, and the patrol
re-routes from wherever the detour ended. A map change forgets the rune.

Telegram hears about hazards only: a hazard pause (other players, an
unrecognized map, a loading screen) and runes (spotted with `rune_action:
pause`, reached, or out of reach). Health checks only reach the log:
every stop (with its reason and the run's summary), a failed start, a
crash (caught; keys released), a lost serial port (which stops the run;
the host reconnects on its own), the watchdog's three
episodes — paused over 60 s, no player dot for 30 s, standing still for
45 s while moving states run (each once per episode) — and a heartbeat
line (map, state, uptime, visits, misses, skips, pauses) every
`heartbeat_minutes`.
