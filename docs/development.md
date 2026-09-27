# Development

## Setup

Python 3.14+. Install editable: `pip install -e .`

The repo pins `.python-version` to `3.14`; if the pyenv shim isn't
installed the repo's own venv interpreter works:

```bash
.venv/bin/python -m picobot          # run the host
.venv/bin/python -m pytest tests/ -x -q   # the suite (~280 tests, ~1s)
```

## Test layout

`tests/` mirrors `picobot/`:

- `test_minimap.py` — fingerprint geometry, watchdog self-consistency,
  region provenance, marker/blob detection.
- `test_mapname.py` — title segmentation (divider cut, icon cut, sparse
  thresholds, region helpers).
- `test_bot_maps.py` — `MapStore` matching incl. alias/title OCR keys and
  truncated-title reverse matching, `match_scored`.
- `test_smart_nav.py` — patrol routing, weaving, stall guards, map
  resolution + pin verification.
- `test_serve.py` — host commands, frame payloads, panel assembly
  (`assemble_panel`/`_offset_meta`), layout drags.
- `test_calibrate.py`, `test_rotation.py`, `test_bot_skills.py`,
  `test_bot_machine.py`, `test_bot_travel.py`, `test_bot_inputs.py`,
  `test_serial_manager.py`, `test_config.py`.

## Conventions worth knowing

- **Two coordinate systems**: map files store 0–1 fractions of the
  minimap region; capture/region rects are client-area px. Overlay meta
  rides frames in minimap px; the Panel view reports `ox`/`oy` for the
  composite's offset.
- **`entry.name` is the user's alias; `entry.map_name` is the OCR'd
  in-game title.** Never conflate them.
- **Hand-drawn platforms are authoritative** for movement. Structural
  detection exists only for fingerprints/identity — don't reintroduce
  ink-based navigation.
- **OCR is event-gated**, never per-frame. Fingerprint per-frame is cheap
  and drives `map_conf`.
- **Self-consistent watchdog**: don't relax "miss must match the previous
  miss" — translucent panels produce constant one-off misses.
- Event levels: `hid`/serial chatter is `debug`; think before emitting
  chatty kinds at `info`.
- `picobot_controller/` is a separate Flutter remote app; `CIRCUITPY/` is
  the Pico firmware (`code.py`).

## Verifying vision changes

Real captures beat fixtures. Save a screenshot, then exercise
`title_scan`/`assemble_panel`/`MapNameReader().read` directly — the
existing tests run fully offline against synthetic bands.
