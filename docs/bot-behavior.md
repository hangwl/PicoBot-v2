# Bot behavior

## Checkpoint patrol (the only multi-anchor mode)

With ≥2 anchors the bot plans a route and *weave-attacks* toward each
checkpoint — it never stands still:

- `_plan_route` orders anchors nearest-neighbour **from the player's
  current position** (not the declared order), excluding the anchor being
  stood on, sweeping the reachable level first.
- Every patrol tick attacks: arrival bookkeeping, blind frames, and
  stall-skips all fire an attack before returning — there are no dead
  ticks.
- Arriving pops the checkpoint, fires its `on_arrive` skills
  (summons/buffs registered in the map's `skills`), and keeps moving.
- Route exhausted → replan from wherever the player ended up.
- Checkpoints on another level — or on another drawn platform at the
  same height — hand off to TRAVEL (see Pathfinding below).
- **Stall guard**: a checkpoint unreachable for ~20s is skipped with a
  log line; failed legs and stalled heads are banned ~30–45s so one bad
  anchor can't hold the route.
- Single-anchor maps still weave in place; `dwell_weave: false` parks.

## Pathfinding (`navgraph.py`, `navigator.py`)

TRAVEL legs are planned over a movement graph built from the map's
**drawn platforms** (so drawing them is what enables pathfinding):

- Nodes: platform ends plus transfer points (inset 4px from overlap
  edges). Edges: `walk`; `down_jump` onto the next platform below;
  `up_jump` onto the next platform above within `nav_up_px`; `drop` off
  an end; `jump`/`flash` across gaps within `nav_jump_px`/`nav_gap_px`
  (target no more than 4px higher). Costs are rough seconds.
- Dijkstra with ±15% cost jitter per query, so near-equal routes vary.
- `Navigator` walks to each transfer point (±3px), performs the move,
  waits for the landing, and checks the player is on the expected
  platform. A miss replans from the actual position (up to 3 times).
  Rope-lift cooldowns are waited out rather than counted as failures.
- Hand-authored `legs` for a pair still win (ropes aren't in the graph
  yet); without drawn platforms the old leg/`walk_to` path runs.
- The active route is drawn on the Panel view in yellow; the dashboard's
  **Route** button shows the graph and previews routes on click.

Tune `nav_up_px`/`nav_jump_px`/`nav_gap_px` (minimap px) if routes
include moves your character can't make — the Route preview shows
exactly which edges exist.

## Attack weaving

Attacks fire during movement: `move_to_point` (walk legs, rope-alignment
walks, wander returns) fires a ready registered attack every ~0.4s of
nav polling — gated so a 0-cooldown key isn't a 20Hz spam loop. The rope
`climb` loop itself stays attack-free (attacking on a rope drops you).

During dwells the bot flash-hops (or walks) back and forth across the
anchor's platform — bounds from the drawn platform segment, falling back
to `weave_range_px` — pressing attack **after** the flash-jump re-press
(an earlier press eats the FJ input window). `wall_zone_px`/per-map
walls force inward facing near edges so weave can't wall-bang.

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
