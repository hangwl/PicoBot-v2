# Configuration

`config.json` in the project root. Top level: window/serial/ports/Telegram/
fps. The `"bot"` block is `BotConfig` (`picobot/bot/config.py`):

```json
{
  "bot": {
    "attack_keys": ["a"],
    "buff_keys": ["shift"],
    "buff_interval_seconds": 60,
    "jump_key": "alt",
    "up_jump_skill_key": null,
    "up_jump_skill_cooldown": 3.0,
    "skill_gap_seconds": [0.5, 1.0],
    "nav_threshold_px": 5,
    "nav_up_flash_px": 14,
    "nav_rope_lift_px": 20,
    "nav_up_side_dx_px": 16,
    "nav_jump_px": 8,
    "nav_gap_px": 20,
    "nav_double_gap_px": 32,
    "nav_reach_file": "nav_reach.json",
    "linger_hops": [0, 2],
    "dwell_weave": true,
    "weave_double_chance": 0.4,
    "weave_range_px": 24,
    "wall_zone_px": 16,
    "wall_pad_px": 6,
    "stop_when_players_appear": true,
    "stop_when_rune_appears": true,
    "pause_on_lie_detector": false,
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
- `up_jump_skill_key` is a rope-lift style skill used instead of the
  jump+up+jump combo; presses are gated by `up_jump_skill_cooldown` and
  nav rides out the cooldown rather than misreading suppression as
  "can't climb".
- Flash jump = jump-again-mid-air — always `jump_key` (`key` is a legacy
  override, leave null).
- Legacy `attack_keys`/`buff_keys` synthesize into skills when no
  explicit `"skills"` map exists — new configs should use named skills.
- `marker_inset_px` crops the minimap rim before marker detection — frame
  pixels can't register as markers; positions are largest-blob centroids.
- Vertical jumps that produce no progress twice in a row end the leg
  (x-aligned = arrived, misaligned = abort) — a target under the lowest
  platform can't loop the bot forever.
- `nav_*_px` are **starting** move reaches in minimap px (conservative);
  the bot learns real values from observed moves and stores them in
  `nav_reach_file` (gitignored, per character). Delete that file to
  relearn. See [bot-behavior.md](bot-behavior.md#moves--learned-reach-navgraphpy-reachpy-navigatorpy).
- `wall_pad_px` — buffer added to every wall/floor zone (left zone
  rightward, right zone leftward, floor upward); nothing is planned or
  weaved inside it.
- Removed keys (`stationary_mode`, `enable_random_wander`,
  `stationary_seconds`, `wander_seconds`, `wander_edge_margin_px`,
  rotation `wander_chance`/`rest_chance`) are ignored.
- `linger_hops` — weave hops at each anchor before moving on, random in
  `[lo, hi]`.
- `pause_on_lie_detector` is a stub seam — keep it off.
- Debug captures are enabled only by the `--debug-frames` CLI flag;
  they go to `debug_capture_dir`, keeping the newest
  `debug_capture_max_events` folders. See [development.md](development.md#debug-frame-captures).
- Key timing is humanized host-side; the Pico firmware relays raw
  down/up events unchanged.
