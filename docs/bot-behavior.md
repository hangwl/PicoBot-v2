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
(splice-on-fail, 3 misses → ban), and pipeline the next loop before the
current one ends. (`patrol.py`)

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
  (rope lift excluded while cooling, without counting a failure); 3
  missed landings ban the anchor for 30s and re-route to the next one.
- **Anchors are pure pass-through waypoints**: arriving fires `on_arrive`
  skills (or, for placed anchors, any summon that is off cooldown) and
  the bot moves on immediately — no linger, no dwell timers.
- **Bans**: no route, unreachable-at-plan-time (including anchors not
  on any drawn platform — "re-place it"), or 3 missed landings → skipped
  for 30s, so one bad anchor can't shrink or stall the patrol.
- Hand-authored `legs` for a pair still win (ropes aren't in the graph).
- Fallbacks: no drawn platforms → the older straight-line patrol;
  platforms drawn but the player off all of them → wait with a throttled
  "not on any drawn platform" diagnostic (the legacy patrol would plan
  routes from an off-graph start and ban every anchor); one anchor →
  weave on its platform; no anchors → weave around where grinding
  started (`dwell_weave: false` attacks in place).
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

## Moves & learned reach (`navgraph.py`, `reach.py`, `navigator.py`)

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
| `climb_up` | **jump-grab** a learned rope (hold direction + up, jump), climb, mount the top platform | boards from any platform within jump reach below the rope's bottom end |
| `climb_down` | grab at the top (hold down), descend past the rope's bottom end, land below | lands on the nearest platform under the rope's end |

There is no direct release from a rope: after any failed climb the bot
leaps off (hold direction + jump) toward the nearest platform and
replans from where it lands. The same exit fires when the player dot
stays invisible for ~2.5s — a fragmented glyph over a rope line is the
usual cause (marker detection retries without erosion first).

Rope climbs cost `rope_penalty` (default 5s) on top of climb time —
**ropes are a last resort**: platforms are normally reachable via jumps,
rope lift, or (future) teleport. Set `rope_penalty: 0` to let the
planner use them freely.

## Learning ropes from stuck events

Ropes are not drawn. When the bot is off every drawn platform at a
stable position for over 2 seconds — the signature of hanging on an
(undrawn) game rope — it records a rope segment from that spot up to the
platform above (persisted in the map file, drawn brown on the overlay),
leaps off, and replans. Learned climbs carry the same `rope_penalty`, so
they're used only when nothing else connects. The same learning fires
when the player dot stays invisible for ~2.5s (a fragmented glyph over a
rope line).

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

## Movement rule & attack weaving

Bot-controlled movement looks like a player farming, not a macro:

- **Point to point = flash weaves** (`_flash_weave`): hold the direction,
  jump, re-press mid-air (the flash jump), then **1–2 attacks** once the
  flash has triggered (`weave_double_chance`, default 0.4 for two). An
  attack before the re-press eats its input window and the flash never
  fires.
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
- After each move the bot waits for the player dot to be stable within
  ~1.5px (≤0.7s), not for exact-equal readings — detection jitter no
  longer adds a fixed stall to every move.
- Re-press gaps are tunable: `flash_repress_seconds` (jump → flash) and
  `combo_repress_seconds` (between chained flashes).
- **Every flash move attacks**: gap flashes, double flashes, up flashes
  and up-then-side flashes all weave 1–2 attacks once the flash has
  triggered (`_after_flash`). Attacks never fire outside a flash move in
  the moving paths — with no player dot the bot waits instead of
  attacking in place. (`dwell_weave: false` is the one stationary mode.)
- Linger/roam weaving uses the same primitive, bouncing across the
  platform; it never starts a hop that would land inside a (padded) wall
  zone.
- All gaps are log-normal (`timing.human_between`: clamped to the game's
  input windows, e.g. re-press 0.11–0.26s around 0.17s).

## Skills (`skills.py`, dashboard Skills panel)

Registered skills have `key`, `kind`, `cooldown`:

- `attack` — attack loop / travel weaving.
- `buff` — fires when ready, anywhere.
- `summon` — fires at anchors listing it via `on_arrive`;
  `wait_on_arrival` caps how long the bot waits for its cooldown.
- Movement keys (jump, flash, rope-lift) register the same way.

`dwell` bounds single-anchor parking; `position_jitter_px` adds entropy
to hand-authored walk legs. There is no wander state or idle breather —
the bot keeps moving.

## Boundaries (no wall zones)

There are no wall zones: **the drawn platforms are the only geometry**,
and their ends are the boundaries. The graph's walk edges run along
drawn platforms, travel flashes measure room against the platform span,
and the weave bounces within platform bounds — drawn geometry alone
prevents wall-banging. Down-jumps only exist toward drawn platforms
below, so the lowest drawn platform is the map's bottom.

## Safety

Pauses the bot (and fires a Telegram alert if configured) on: window
focus loss, rune marker on the minimap, other players, a map transfer
(loading blackout) mid-leg. Solve rune checks via the dashboard's
remote input pad. `pause_on_lie_detector` is a documented stub — keep it
off until template images exist.
