# Configuration

`config.json` in the project root. Top level: window/serial/ports/Telegram/
fps. The `"bot"` block is `BotConfig` (`rust/crates/core/src/config.rs`):

```json
{
  "bot": {
    "attack_keys": ["a"],
    "buff_keys": ["shift"],
    "buff_interval_seconds": 60,
    "jump_key": "alt",
    "up_jump_skill_key": null,
    "up_jump_skill_cooldown": 3.0,
    "walk_band_px": 8,
    "class": {
      "active": "default",
      "profiles": {
        "default": {"travel": "flash"},
        "mage": {"travel": "teleport", "air_attacks": false,
                 "teleport_key": "shift", "teleport_cooldown": 0.8,
                 "jump_key": "space", "up_jump_skill_key": "alt",
                 "flash_jump": {"key": null, "enabled": false},
                 "skills": {"rush": {"key": "shift", "kind": "movement"},
                            "main": {"key": "a", "kind": "attack"}}}
      }
    },
    "air_attacks": true,
    "teleport_key": null,
    "teleport_cooldown": 1.0,
    "nav_teleport_dx": 25,
    "nav_teleport_rise": 12,
    "nav_threshold_px": 5,
    "nav_up_flash_px": 26,
    "nav_rope_lift_px": 90,
    "nav_up_side_dx_px": 30,
    "nav_jump_px": 10,
    "nav_gap_px": 30,
    "nav_double_gap_px": 48,
    "rope_penalty": 5,
    "patrol_policy": "weighted",
    "patrol_mode": "sweep",
    "sweep_reach_px": 12,
    "patrol_weight_temp": 1.0,
    "nav_reach_file": "nav_reach.json",
    "anchor_float_px": 4,
    "flash_repress_seconds": 0.15,
    "combo_repress_seconds": 0.16,
    "vert_jump_interval": 0.9,
    "weave_double_chance": 0.4,
    "weave_range_px": 24,
    "stop_when_players_appear": true,
    "stop_when_rune_appears": true,
    "rune_action": "solve",
    "rune_key": "y",
    "record_rune_solves": true,
    "stop_when_map_unrecognized": true,
    "pause_on_lie_detector": true,
    "auto_focus": true,
    "minimap_colors": {
      "player": [12, 240, 239],
      "other_player": [118, 45, 253],
      "rune": [255, 102, 221],
      "border": [228, 228, 228]
    },
    "flash_jump": {"enabled": true, "key": null},
    "travel_style": "mixed",
    "maps_dir": "maps",
    "active_map": null,
    "auto_select_map": true,
    "marker_inset_px": 4,
    "name_ocr": true,
    "name_scan_px": 160,
    "debug_capture_dir": "debug/frames",
    "debug_capture_max_events": 100
  }
}
```

## Notes

- `minimap_region` / `minimap_name_region` (client-area `(x,y,w,h)`)
  are **hard pins** — leave them unset. The panel is found by its frame
  on every map (sizes differ per map), and the title band follows it.
  Set them only via the dashboard drags when detection fails.
- `minimap_colors.border` is the frame colour `find_frame` looks for.
- `name_ocr` reads the title band with RapidOCR on a worker thread — the
  identity signal, requested on startup/arrival/pin change (never
  per-frame). `name_scan_px` is how deep into the frame the band reaches.
- `active_map` is a pin: it stands unless the title matches a different
  stored map. `auto_select_map` defers to live detection.
- Removed keys (`map_match_threshold`, `minimap_colors.ink`) are ignored.
- `up_jump_skill_key` is the rope-lift skill, preferred for rises when
  ready; `up_jump_skill_cooldown` is its re-cast delay. While it's
  cooling down the bot up-flashes (jump, then Up + jump) instead of
  waiting.
- Flash jump = jump-again-mid-air — always `jump_key` (`key` is a legacy
  override, leave null).
- Legacy `attack_keys`/`buff_keys` synthesize into skills when no
  explicit `"skills"` map exists — new configs should use named skills.
- `marker_inset_px` crops the minimap rim before marker detection — frame
  pixels can't register as markers; positions are largest-blob centroids.
  The one exception is the player dot passing under the rim (a rope lift
  to the map's top edge): a dot missed inside the crop is looked for in
  the rim within 12px of its last sighting.
- Vertical jumps that produce no progress twice in a row end the leg
  (x-aligned = arrived, misaligned = abort) — a target under the lowest
  platform can't loop the bot forever.
- `patrol_mode` — `sweep` (default: sweep each anchor's platform end to
  end, one sweep per platform) or `anchors` (pass through anchor points);
  `sweep_reach_px` (default 12) is how far ahead one attack reaches on
  the minimap — a sweep stops that short of the far end.
- `patrol_policy` — loop ordering: `weighted` (roulette ∝ 1/cost, far
  anchors stay in the draw), `greedy` (cheapest next, ±20% jitter) or
  `zigzag` (row by row, nearest row first, greedy within it).
  `patrol_weight_temp` tunes the roulette: lower → more uniform,
  higher → more greedy.
- `rope_penalty` — extra seconds added to every rope-climb edge; ropes
  are planned only when no jump/rope-lift path is within this much
  cheaper. Set 0 to allow ropes freely.
- `class` — named class profiles with an active selector; the active
  profile overlays `travel` (flash|teleport|walk), `air_attacks`,
  `double_flash` (default true; false drops double-flash edges and its
  measurement), the
  teleport keys, **its own movement keys** (`jump_key`,
  `up_jump_skill_key`, `flash_jump`) and **its own `skills` kit**. When
  every profile carries its kit, the top-level `skills`/`jump_key`/
  `up_jump_skill_key`/`flash_jump` keys are optional fallbacks (the
  dashboard's movekeys/skills editors write into the active profile
  once one is active). A profile **without** a `skills` entry uses the
  global book; one **with** a `skills` entry — even `{}` — uses exactly
  that kit (empty = no attacks). The first dashboard skill edit copies
  the global book into an inheriting profile. See
  [bot-behavior.md](bot-behavior.md#class-profiles).
- `walk_band_px` — walking distance: the bot flash-travels until inside
  this band, then walks the last stretch for a precise stop.
- `nav_*_px` are **starting** move reaches in minimap px (conservative);
  the bot learns real values from observed moves and stores them in
  `nav_reach_file` (gitignored) — with a class profile active the file
  is `nav_reach_<profile>.json` (per character). Delete that file to
  relearn. See [bot-behavior.md](bot-behavior.md#moves--learned-reach-navgraphpy-reachpy-navigatorpy).
- Removed keys (`stationary_mode`, `enable_random_wander`,
  `stationary_seconds`, `wander_seconds`, `wander_edge_margin_px`,
  `linger_hops`, `wall_zone_px`, `wall_pad_px`, `dwell_weave`,
  `skill_gap_seconds`, rotation `style`/`wander_chance`/`rest_chance`,
  anchor `dwell`) are ignored.
- `anchor_float_px` — how far placed anchors hover above the drawn
  platform line (matching the player icon).
- `flash_repress_seconds` / `combo_repress_seconds` — mid-air re-press
  gaps (jump → flash; between chained flashes). Raise them if an
  up-side or double flash rarely chains on your server.
- `vert_jump_interval` — minimum gap between vertical jump attempts in
  non-graph movement.
- `stop_when_map_unrecognized` — pause (and alert) when the title read
  names no saved map and none is pinned. Turn it off only to farm an
  unsaved map with the global rotation.
- `move_attack_chance` / `ground_attack_chance` (0–1; default 1 / 0) —
  the odds a move or a landing opens an attack window;
  `weave_double_chance` is the chance a firing window casts two;
  `target_attacks_per_min` (default 0 = off) steers those odds toward a
  rate. Setup → Tuning → Attacks edits them (`attacks|set`).
- `move_miss_chance` (0–1; default 0) — the odds a flash move skips its
  last mid-air re-press so it genuinely misses (try 0.01–0.03). Never
  taught to the move-reach model. Setup → Tuning → Attacks edits it.
- `chord_gap_chance` (0–1; default 0.2) — the odds a key event by a
  different key than the last follows within 5–20 ms instead of the
  usual 10–70 ms. Read when the bot starts; 0 keeps the old spacing.
  Setup → Tuning → Attacks edits it.
- `allowed_other_players` (default 0) — other players tolerated on the
  minimap before the bot pauses (`stop_when_players_appear`);
  `other_player_min_px` (default 6) — the smallest marker that counts.
  Markers within 2px merge into one, so players standing on top of each
  other count once. Every change in the count is logged as a warning.
- A map file may carry `"other_players": {"mode": "ignore"}` or
  `{"mode": "allow", "allowed": N}` — that map ignores other-player
  markers (monsters drawn as players) or tolerates N, instead of the
  global setting; absent follows it. Set on the Map page
  (`map|players`). On an ignoring map the count is logged at debug level.
- `rope_penalty` (default 5 s) and `walk_cost_factor` (default 1) — how
  strongly the planner avoids ropes and walking (see bot-behavior.md);
  Setup → Tuning → Patrol edits them.
- `heartbeat_minutes` — a status line in the log every N minutes while
  the bot runs (default 30; 0 turns it off). Telegram gets hazards only.
- `pause_on_lie_detector` (default true) — pause and alert while the
  lie detector's window shows (see bot-behavior.md, Safety).
- `session_max_minutes` (default 0 = off) — end the run after about this
  long; `break_every_minutes` and `break_minutes` (0 = off, both needed)
  — rest that long every so often. Each is jittered per run; see
  bot-behavior.md, Safety. Setup → Safety → Session edits them.
- `auto_focus` (default true) — bring the game to the front when the
  bot starts; off waits for you to focus it (the bot pauses until then).
- `evidence_captures` (default true) — save game screenshots to
  `debug/frames/` (dot lost, off platform, rune, lie detector). Turn it
  off on a deployed machine; `record_rune_solves` and the dashboard's
  explicit snapshot are separate.

Top-level (not in `"bot"`):

- `bind` (default `"auto"`) — where the dashboard listens: `auto`
  (loopback plus this machine's Tailscale and LAN addresses),
  `loopback`, `tailscale` (loopback plus the Tailscale address), `all`
  (every interface), or one IPv4 address (plus loopback). The addresses
  are read at start-up; restart after the network changes.
  `/health` returns the full status JSON to loopback callers and only
  `{"ok": true}` to others.
- `serial_port` — `auto` probes every COM port (open, DTR toggle,
  handshake); once the Pico's port is known, pin it (a number in
  `config.json`, e.g. `COM7`) so a run doesn't. Don't pass `--window`
  from launchers: the title already lives in `default_target_window`.
- Debug captures are enabled only by the `--debug-frames` CLI flag;
  they go to `debug_capture_dir`, keeping the newest
  `debug_capture_max_events` folders. See [development.md](development.md#debug-frame-captures).
- Key timing is humanized host-side; the Pico firmware relays raw
  down/up events unchanged.
