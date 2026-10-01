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

Both ban unreachable anchors for 30s, execute the plan strictly
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
- **One leg per tick**; safety checks run between legs. A failed leg
  splices a re-route from the player's actual position into the plan
  (rope lift excluded while cooling, without counting a failure); 2
  missed landings in a row ban the anchor for 30s and re-route to the next one.
- **Anchors are pure pass-through waypoints**: arriving fires the
  anchor's non-summon `on_arrive` skills and places at most one summon
  (see Summons) in passing, then the bot moves on — no linger, no dwell
  timers.
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
  leg has it banned); no anchors → weave around where
  grinding started.
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
| `jump` / `flash` / `double_flash` | jump; jump + re-press; + second re-press | horizontal gaps |
| `up_flash` | jump, then Up + jump mid-air | platform directly above |
| `up_side_flash` | up flash, then a sideways flash mid-air | higher platform across a gap |
| `rope_lift` | `up_jump_skill_key` | grabs the highest platform within `nav_rope_lift_px` (~90) of the takeoff column; preferred while ready |
| `down_jump` / `drop` | down + jump; walk off an end | lower platforms |
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
  Delete `nav_reach_file` to relearn.
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
  plan; one that overshot or came down elsewhere says nothing about reach.
- Re-press gaps are tunable: `flash_repress_seconds` (jump → flash) and
  `combo_repress_seconds` (between chained flashes).
- The **up flash** is timed from the first jump's key-down: the re-press
  lands inside the plateau of the class's up-flash timing sweep (the
  contiguous delays within 1 px of the highest peak), log-normally varied
  around its middle; without a sweep, 0.16–0.30 s around 0.22 s. Up goes
  down ~40 ms before the re-press.
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
- All gaps are log-normal (`timing.human_between`: clamped to the game's
  input windows, e.g. re-press 0.11–0.26s around 0.17s).

## Human input timing (`timing.rs`, `io/src/hid.rs`)

- **Session tempo**: each bot start draws a pace (±~7%) that also drifts
  slowly within the session (mean-reverting, minutes-scale). It scales
  every mean; clamps are applied after it, so input windows hold.
- **Key spacing**: `HidController` spaces consecutive key events (downs,
  ups, and `release_all`) by a drawn gap (~25ms, 10–70ms) — fingers never
  land at once. Time already spent (a deliberate sleep, the serial
  round-trip) counts toward it, so timed sequences barely shift; where a
  key leads the next press (Up before the up-flash re-press), the lead
  sleep gives the gap back so the jump still lands on time.
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
- `buff` — fires when ready, anywhere.
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
focus loss, rune marker on the minimap, more other players than
`allowed_other_players` (the alert says how many), a map transfer
(loading blackout) mid-leg, and a map no saved entry matches. Solve rune checks via the dashboard's
remote input pad. `pause_on_lie_detector` is a documented stub — keep it
off until template images exist.

Telegram hears about: a hazard pause, every stop (with its reason and
the run's summary), a failed start, a crash (caught; keys released), a
lost serial port, and the watchdog's three episodes — paused over 60 s,
no player dot for 30 s, standing still for 45 s while moving states run
(each once per episode). While running, a heartbeat message (map, state,
uptime, visits, misses, skips, pauses) goes out every `heartbeat_minutes`.
