# PicoBot Refactor & Modularization Plan

## Current Structure Snapshot
- `picobot.py` bundles Telegram messaging, remote serial/WebSocket control, an embedded HTTP file server, macro playback logic, and the Tkinter GUI in a single ~2.2K line module.
- Supporting services live alongside the GUI: `TelegramHandler` wraps Telegram REST calls; `RemoteControlServer` mixes serial IO, asyncio WebSockets, and Tk callbacks; `EmbeddedHTTPServer` serves the web controller page.
- The GUI (`MacroControllerApp`) owns UI construction, config persistence, countdown logic, and even a second copy of the macro/serial routines that already exist inside `MacroController`.

## Pain Points & Risks
- **Severe duplication.** Macro parsing, port discovery, handshake/ACK helpers, and the macro playback thread are implemented twice (once in `MacroController`, once again in `MacroControllerApp`), making bug fixes error-prone.
- **Tightly coupled responsibilities.** The GUI calls deep into serial logic and vice versa (e.g., remote server threads manipulating Tk widgets), making it hard to reason about threading and to reuse components outside the UI.
- **Asynchronous complexity.** `RemoteControlServer` combines background threads, an asyncio loop, and shared queues/locks without clear lifecycle boundaries, complicating shutdown and error handling.
- **Configuration and state scattering.** Persistence, countdown timers, macro status, and remote server lifecycle logic all live inside the GUI class, obscuring the core application state model.

## Refactor & Modularization Plan
1. **Lay the groundwork for a package structure.** Create a `picobot/` package with submodules for messaging, transport, playback, and UI. Move the current `picobot.py` to `picobot/app.py` as an entry point that wires components together.
   - Extract `TelegramHandler` into `picobot/messaging.py`, exposing a narrow interface for sending notifications so both countdown code and any future alerts share the same client.
   - Centralize constants (e.g., `CONFIG_FILE`) and shared logging setup in a small `settings.py` module to avoid import-time side effects sprinkled throughout the code.
2. **Isolate hardware/transport concerns.** Design a serial transport layer that owns discovery, handshake, and ACK coordination, then let higher layers depend on it via an interface.
   - Move `find_data_port`, `_finalize_handshake`, `_wait_for_ack`, and related helpers into a new `transport/serial_manager.py`. Provide methods such as `discover_data_port()`, `open_session()`, and `send_hid()` so the rest of the app no longer manipulates `serial.Serial` directly.
   - Have `RemoteControlServer` depend on this serial manager instead of re-implementing its own queue/ACK handling. This allows reuse of the same handshake and ACK logic whether commands originate from the GUI or the WebSocket path.
   - Simplify RemoteControlServer’s lifecycle by encapsulating the asyncio loop in a dedicated runner class (e.g., `AsyncWebsocketBridge`) that exposes `start()`/`stop()` and hides threading details from the GUI.
3. **Consolidate macro playback logic.** Keep all macro sequencing in a single `MacroController` module (e.g., `picobot/macro.py`), and make the GUI talk to it through a clean API.
   - Preserve a single implementation of `parse_macro_file`, playlist construction, focus checking, and key-release cleanup inside `MacroController.play_macro_thread`. Remove the duplicate implementation from `MacroControllerApp`, replacing it with thin delegation methods like `controller.start_loop(config)` and `controller.stop()`.
   - Factor playlist creation, window activation, and event dispatch into smaller private methods (e.g., `_load_playlist`, `_ensure_window_focus`, `_dispatch_event`) to simplify unit testing and future enhancements.
4. **Untangle the UI from backend state.** Create a presentation/controller separation so Tk widgets only bind to high-level callbacks and observables.
   - Move port/window refresh and auto-selection logic from the GUI into the macro controller or transport layer, returning data structures rather than manipulating widgets directly.
   - Wrap countdown handling into a dedicated service (e.g., `picobot/countdown.py`) that the GUI starts/stops; inject the Telegram client so it can signal completion without knowing about Tk state.
   - Refactor `MacroControllerApp.__init__` to assemble UI sections using helper classes (e.g., `PortSelectorView`, `RemoteView`), each responsible for building widgets and binding to controller callbacks. This shortens the main class and clarifies dependencies.
5. **Improve configuration and dependency injection.** Introduce a lightweight application context or dataclass that collects user preferences, runtime state, and service instances.
   - Extract `load_config`/`save_config` into `picobot/config.py`, returning structured objects rather than mutating Tk variables directly. The GUI can then bind Tk variables to this config object, while other modules can read persisted settings without importing Tk.
   - Define a factory in `app.py` that wires together the Telegram handler, serial manager, macro controller, and remote servers, making future CLI or headless modes feasible without the GUI class.
6. **Testing and incremental rollout.** Before moving code, add smoke-level tests for macro parsing, playlist ordering, and handshake edge cases so refactors can be validated outside the GUI.
   - Use the new modular functions (e.g., `parse_macro_file`, `SerialManager._wait_for_ack`) to create unit tests covering typical and error flows that currently exist only in the threaded runtime.
   - After extraction, run an integration pass that starts the GUI but swaps in mocked serial transports to ensure event wiring works without hardware attached.

This plan replaces the monolithic `picobot.py` with well-scoped modules, eliminates duplicate logic, and makes the core macro/transport functionality reusable in both GUI and remote control contexts while clarifying threading and configuration lifecycles.

## Status Updates

### Step 1 – Package groundwork (completed)
- Introduced the `picobot` package with placeholder subpackages for `playback`, `transport`, and `ui` (`picobot/playback/__init__.py`, `picobot/transport/__init__.py`, `picobot/ui/__init__.py`) to stage future extractions.
- Relocated the monolithic GUI module to `picobot/app.py` while exposing CLI entry points via `picobot/__init__.py` and `picobot/__main__.py`.
- Extracted `TelegramHandler` into `picobot/messaging.py` and centralized config/logging defaults in `picobot/settings.py` for reuse beyond the GUI.

### Step 2 - Transport isolation mini plan (completed)
- Added `picobot/transport/serial_manager.py` with shared discovery/handshake helpers and a reusable `SerialManager`.
- Refactored `RemoteControlServer` to delegate serial I/O to the manager and remove duplicate ACK bookkeeping.
- Introduced `AsyncWebsocketBridge` to own the asyncio loop lifecycle and keep Tk interactions on the main thread.
- Added `tests/test_serial_manager.py`; `python -m unittest tests.test_serial_manager` passes.

### Step 3 - Macro playback consolidation mini plan (completed)
- Extracted the legacy `MacroController` into `picobot/playback/macro_controller.py` with reusable playlist and parsing helpers.
- Trimmed duplicate playback helpers from the Tk GUI so it now delegates to the shared controller.
- Added `tests/test_macro_controller.py` to cover macro parsing, playlist ordering, and the interruptible sleep helper.

### Step 4 - UI disentanglement mini plan (completed)
- Extracted port/window refresh logic into `PortService.build_selection` and `WindowService.build_selection`, returning plain data structures for the views.
- Introduced dedicated view classes (e.g., `PortSelectorView`, `RemoteView`) that build widgets and bind callbacks supplied by the controller layer.
- Created `CountdownService` in `picobot/countdown.py` to encapsulate timer state and Telegram notifications.
- Wired the GUI to the new services via `AppContext`, so backend state no longer depends on Tk variables.

### Step 5 - Configuration & Factory wiring (completed)
- Extracted config load/save helpers into `picobot/config.py`, exposing an `AppConfig` dataclass for GUI binding.
- Added an application factory via `create_application` in `picobot/app.py` to wire Telegram, transport, macro controller, countdown, and remote server dependencies.
- Updated the GUI to consume injected config/context so future CLI or headless modes can reuse services without Tk bindings.
- Added regression tests in `tests/test_config.py` covering config defaults, coercion, and persistence.

## Operational Notes

### PowerShell Notes
- Use `($text = Get-Content path -Raw).Replace('old','new')` for literal substitutions so PowerShell avoids regex side effects.
- Do not run `$ pwsh -NoLogo -Command 'python -m unittest discover -s tests'`; it hangs the terminal / loops.

### Targeted Test Commands
- Prefer `python -m unittest tests.test_macro_controller.MacroControllerPlaybackTests.test_build_playlist_prioritises_start_files -v` for focused playlist debugging or when the general suite format stalls.
