# Development

## Setup

Python 3.14+. Install editable: `pip install -e .`

Development happens on Windows. Use the repo's venv interpreter:

```powershell
.venv\Scripts\python.exe -m picobot                 # run the host
.venv\Scripts\python.exe -m pytest tests/ -x -q     # the suite (~400 tests, ~2s)
cd web; npm install; npm run build                   # build the dashboard app (web/dist)
cd web; npm run dev                                  # hot-reload UI against a running host
```

`web/dist` is not committed — build it before the first run. Git should
use `core.autocrlf=true` (files are CRLF on disk, LF in the repo).

## Test layout

`tests/` mirrors `picobot/`:

- `test_minimap.py` — transfer detection, `find_frame`, panel-moved
  relocation, region provenance, marker/blob detection.
- `test_transition.py` — blackout detector states.
- `test_monitor.py` — 20 Hz monitor: short blackouts, locate rate limit,
  panel relocation, thread lifecycle.
- `test_identity.py` — title voting, pin resolution, stale-read discard,
  worker thread.
- `test_mapname.py` — title segmentation (divider cut, icon cut, sparse
  thresholds, region helpers).
- `test_bot_maps.py` — `MapStore` title matching on real OCR reads
  (clipped/noisy titles, sibling maps, ambiguity).
- `test_smart_nav.py` — patrol routing, weaving, stall guards, identity
  sync, arrival handling in `minimap_frame`.
- `test_serve.py` — host commands, frame payloads, panel assembly
  (`assemble_panel`/`_offset_meta`), layout drags.
- `test_framelog.py` — debug capture episodes, snapshots, pruning.
- `test_navgraph.py` — envelope-driven edges (up/rope/up-side/double
  flash), exploration + ceilings, jitter variety.
- `test_navigator.py` — one-move steps against a physics sim: landings,
  replans, rope-lift cooldown, reach learning.
- `test_reach.py` — grow/shrink/ceiling rules, persistence.
- `test_patrol.py` — full-loop plans, continuous motion, linger, bans.
- `test_remote.py` — command worker (ordering, loop never blocked),
  binary frame subscription/drop-if-busy, HTTP template.
- `test_screen.py` — per-thread mss instances.
- `test_measure.py`, `test_rotation.py`, `test_bot_skills.py`,
  `test_bot_machine.py`, `test_bot_travel.py`, `test_bot_inputs.py`,
  `test_serial_manager.py`, `test_config.py`.

## Conventions worth knowing

- **Two coordinate systems**: map files store 0–1 fractions of the
  minimap region; capture/region rects are client-area px. Overlay meta
  rides frames in minimap px; the Panel view reports `ox`/`oy` for the
  composite's offset.
- **`entry.name` is the user's alias; `entry.map_name` is the OCR'd
  in-game title.** Never conflate them.
- **Hand-drawn platforms are authoritative** for movement — don't
  reintroduce line auto-detection.
- **OCR is request-driven** and runs on the `TitleOCR` worker — never on
  the frame thread, never per-frame.
- **Map change = loading blackout only.** Don't reintroduce pixel-content
  change as a trigger — translucent UI defeats it (see learnings.md).
- Event levels: `hid`/serial chatter is `debug`; think before emitting
  chatty kinds at `info`.
- `CIRCUITPY/` is the Pico firmware (`code.py`). Changes take effect
  only once copied onto the Pico's CIRCUITPY drive.

## Debug frame captures

Off by default. Run with `--debug-frames`
(`python -m picobot --debug-frames`, or the same flag on
`python -m picobot.bot`) and the host / headless bot save
detection evidence to `debug/frames/<timestamp>_<reason>/` (gitignored,
oldest pruned past `debug_capture_max_events`). Every folder has a
`meta.json`.

| Reason | When | Contents |
|---|---|---|
| `transition` | a confirmed loading blackout through arrival | pre-roll + episode frames `NNN.png` (20 Hz), `window_arrived.png`; per-frame `dark`/`state`/`event` |
| `transition_flicker` | minimap went dark but never confirmed (near-miss) | same frames, `outcome: flicker` |
| `ocr` / `ocr_nocrop` / `ocr_error` | every title OCR read | `band.png`, `crop.png`, text + per-line scores |
| `frame_not_found` | `find_frame` failed on a lit window (≤ 1 per 30s) | `window.png` |

Captures are written on a background
thread. Implementation: `picobot/vision/framelog.py`.

## Verifying vision changes

Real captures beat fixtures. Save a screenshot, then exercise
`title_scan`/`assemble_panel`/`MapNameReader().read` directly — the
existing tests run fully offline against synthetic bands.
