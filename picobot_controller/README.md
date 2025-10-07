# picobot_controller

PicoBot Mobile Controller – a Flutter app to design and use custom on-screen keyboard templates and control PicoBot over WebSocket.

## Features

- **[Templates]** Create, edit, reorder, and delete templates stored locally via `SharedPreferences`.
- **[Template Editor]** Drag and resize keys with percent-based positions/sizes (`xPercent`, `yPercent`, `widthPercent`, `heightPercent`). Add keys from categorized palettes (letters, numbers, symbols, arrows, function, special, mouse) defined in `lib/utils/constants.dart`.
- **[Alt label (Shift) support]** Keys can define `shiftLabel`. The editor’s add-keys grid shows a top-right badge with the alt label when available. In the controller, holding any SHIFT key switches labels to the alt form.
- **[Multi-SHIFT]** Multiple SHIFT keys are supported. Shift stays active while at least one SHIFT is held.
- **[Controller Screen]** Uses the active template to render pressable keys. Press/release updates local UI instantly and sends commands to the server when connected.
- **[Macro controls]** START/STOP button with live macro state synced from the server (`macro|playing` / `macro|stopped`).
- **[Connection status]** Compact indicator in the UI header; server settings (host/port, auto-connect) via a Settings screen.
- **[Robust connection handling]** Single reconnection authority in `WebSocketService`. Cleans up on errors, auto-reconnects, and uses an app-level heartbeat (periodic `macro|query`) to detect stale connections.
- **[Default template]** Automatically loaded from `assets/templates/default_template.json` on first run.

## Architecture overview

- **[State management]**
  - `ConnectionProvider`: WebSocket connection state, key press logic, SHIFT state.
  - `TemplateProvider`: Active template selection, layout updates, add/remove/reorder keys.
- **[Services]**
  - `WebSocketService`: Connects to server, listens for messages, handles reconnects and heartbeat.
  - `StorageService`: Persists templates, active template ID, and server settings in `SharedPreferences`.
- **[Models]** `KeyConfig` (with optional `shiftLabel`), `LayoutConfig`, `Template` (JSON-serializable).
- **[Screens]** `HomeScreen`, `TemplateEditorScreen`, `ControllerScreen`, `SettingsScreen`, `ManageTemplatesScreen`.
- **[Widgets]** `KeyButton`, `DraggableKeyWidget`, `ConnectionStatusWidget`.
- **[Constants]** `AvailableKeys` categories (with `shiftLabel` for letters/numbers/symbols), screen breakpoints, default key sizes, and connection defaults.

## Server protocol (expected)

- **[Commands from client]**
  - HID: `key|down|<key>`, `key|up|<key>`, `mouse|down|<btn>`, `mouse|up|<btn>`, `hid|move|<x>|<y>`
  - Macro: `macro|start`, `macro|stop`, `macro|query`
- **[Server replies]**
  - Responds to `macro|query` with `macro|playing` or `macro|stopped` (and broadcasts state changes on start/stop).
- **[Heartbeat]**
  - The app sends periodic `macro|query` as a keepalive; any received message clears the heartbeat wait.

## Running the app

- **[Prerequisites]** Flutter SDK installed and configured.
- **[Install dependencies]**
  - `flutter pub get`
- **[Run]**
  - `flutter run`
- **[Server]** Ensure the PicoBot server is running and reachable at the configured host/port (set in Settings).

## Recommended improvements

- **[Exponential backoff]** Add backoff and jitter to reconnection attempts in `WebSocketService` to reduce thrash on long outages.
- **[Dedicated ping/pong]** Optionally add explicit `ping|<nonce>`/`pong|<nonce>` to the server and switch the client heartbeat from `macro|query` to a purpose-built ping.
- **[Template migration]** One-time backfill to populate `shiftLabel` for existing saved templates by matching `keyCode` against `AvailableKeys`.
- **[Gesture robustness]** Consider low-level pointer handling (`Listener`) for multi-touch hold scenarios to further minimize tap-cancel edge cases.
- **[Multiple layouts]** Support alternate layouts per template (e.g., pip/split/fullscreen/orientation variants) and quick switching.
- **[Template import/export]** JSON import/export and sharing.
- **[Theming]** Per-template styles and improved theme customization.
- **[Observability]** Centralized logging, error reporting, and analytics hooks.
- **[Testing]** Unit tests for providers/services and widget tests for editor/controller interactions.
