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
    "stationary_seconds": 20,
    "wander_seconds": 15,
    "skill_gap_seconds": [0.5, 1.0],
    "nav_threshold_px": 5,
    "dwell_weave": true,
    "weave_range_px": 24,
    "wall_zone_px": 16,
    "stationary_mode": true,
    "enable_random_wander": true,
    "stop_when_players_appear": true,
    "stop_when_rune_appears": true,
    "pause_on_lie_detector": false,
    "minimap_region": [x, y, w, h],
    "minimap_colors": {
      "player": [12, 240, 239],
      "other_player": [118, 45, 253],
      "rune": [255, 102, 221],
      "border": [228, 228, 228],
      "ink": null
    },
    "flash_jump": {"enabled": true, "key": null},
    "travel_style": "mixed",
    "maps_dir": "maps",
    "active_map": null,
    "auto_select_map": true,
    "map_match_threshold": 15.0,
    "marker_inset_px": 4,
    "name_ocr": true,
    "name_scan_px": 160,
    "minimap_name_region": null,
    "debug_capture": true,
    "debug_capture_dir": "debug/frames",
    "debug_capture_max_events": 100
  }
}
```

## Notes

- `minimap_region`, `minimap_name_region` are client-area `(x,y,w,h)`
  rects — set via dashboard drags, not by hand.
- `name_ocr` reads the title band with RapidOCR — the preferred identity
  signal, event-gated (never per-frame). `name_scan_px` is how deep into
  the minimap region the scan reaches; segmentation cuts it back at the
  panel divider.
- `minimap_colors.ink` is an optional global platform-line colour that
  only feeds the fingerprint mask — it plays no role in movement.
- `active_map` is a pin, not identity: it's kept unless its own stored
  fingerprint verifiably mismatches, or OCR reads a different stored
  map's title. `auto_select_map` defers to live detection.
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
- `pause_on_lie_detector` is a stub seam — keep it off.
- `debug_capture` saves frames around detection events to
  `debug_capture_dir`, keeping the newest `debug_capture_max_events`
  folders. See [development.md](development.md#debug-frame-captures).
- Key timing is humanized host-side; the Pico firmware relays raw
  down/up events unchanged.
