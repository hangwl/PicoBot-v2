# Bot behavior

## Continuous patrol (`patrol.py`)

With ≥2 anchors and drawn platforms the bot is always on the move:

- **Full traversal plan**: anchors are ordered greedily by *route cost*
  (seconds over the movement graph, ±20% jitter so loops vary), skipping
  the anchor being stood on. The whole loop's legs are published and
  drawn on the Panel view (olive); the current segment is yellow.
- **One move per tick**, re-planned from the player's actual position,
  so a missed landing is corrected on the next tick. Safety checks run
  between moves.
- **Anchors are waypoints**: arriving fires `on_arrive` skills (or, for
  placed anchors, any summon that is off cooldown), then lingers
  `linger_hops` (default 0–2, random) weave hops and moves on. No dwell
  timers, no breathers.
- Loop finished → re-plan from wherever the player is.
- **Bans**: no route → skipped at once; 3 missed landings → skipped.
  Banned anchors sit out 30s.
- Hand-authored `legs` for a pair still win (ropes aren't in the graph).
- Fallbacks: no drawn platforms (or standing off them) → the older
  straight-line patrol; one anchor → weave on its platform; no anchors →
  weave around where grinding started (`dwell_weave: false` attacks in
  place).

## Moves & learned reach (`navgraph.py`, `reach.py`, `navigator.py`)

The movement graph links drawn platforms with every move whose **reach**
covers the gap:

| Move | Input | Use |
|---|---|---|
| walk | flash weaves (walk near the goal) | along a platform |
| `jump` / `flash` / `double_flash` | jump; jump + re-press; + second re-press | horizontal gaps |
| `up_flash` | jump, then Up + jump mid-air | platform directly above |
| `up_side_flash` | up flash, then a sideways flash mid-air | higher platform across a gap |
| `rope_lift` | `up_jump_skill_key` | tall rises; cooldown-aware |
| `down_jump` / `drop` | down + jump; walk off an end | lower platforms |

- Reach = sideways `dx` and upward `rise` in minimap px. Starting values
  are conservative (`nav_*` config keys).
- Every executed jump-type move reports takeoff and landing: success
  grows the envelope to what was observed; a miss inside it shrinks it;
  a miss on an exploratory attempt sets a ceiling. The planner may try
  up to 1.15× the proven reach at a cost penalty, so reach grows from
  the conservative start. Learned values persist in `nav_reach_file`.
- Rope lift: a remaining cooldown ≤1.5s is waited out; longer excludes
  rope lift from that step's plan (up-flash chains instead).
- The dashboard's **Route** button shows every edge (colours per move)
  and previews routes on click.

## Movement rule & attack weaving

Bot-controlled movement looks like a player farming, not a macro:

- **Point to point = flash weaves** (`_flash_weave`): hold the direction,
  jump, re-press mid-air (the flash jump), then **1–2 attacks** once the
  flash has triggered (`weave_double_chance`, default 0.4 for two). An
  attack before the re-press eats its input window and the flash never
  fires.
- `move_to_point` flash-weaves while the target is farther than one hop
  and walks only the final approach. Hop distance is learned from
  observed hops (starts at 14 minimap px). Hops that don't move fall back
  to walking; with `flash_jump_enabled: false` attacks weave into walks.
- Dwell/patrol weaving uses the same primitive, bouncing across the
  anchor's drawn platform; walls force inward facing.
- Gap-crossing flashes in pathfinding stay attack-free.
- All gaps are log-normal (`timing.human_between`: clamped to the game's
  input windows, e.g. re-press 0.11–0.26s around 0.17s).

## Skills (`skills.py`, dashboard Skills panel)

Registered skills have `key`, `kind`, `cooldown`:

- `attack` — attack loop / travel weaving.
- `buff` — fires when ready, anywhere.
- `summon` — fires at anchors listing it via `on_arrive`;
  `wait_on_arrival` caps how long the bot waits for its cooldown.
- Movement keys (jump, flash, rope-lift) register the same way.

`dwell` bounds single-anchor parking and the rest-breather window;
`rest_chance`/`wander_chance`/`position_jitter_px` add entropy.

## Safety

Pauses the bot (and fires a Telegram alert if configured) on: window
focus loss, rune marker on the minimap, other players, a map transfer
(loading blackout) mid-leg. Solve rune checks via the dashboard's
remote input pad. `pause_on_lie_detector` is a documented stub — keep it
off until template images exist.
