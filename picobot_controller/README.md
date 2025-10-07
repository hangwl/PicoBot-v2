# picobot_controller

PicoBot Mobile Controller – a Flutter app to design and use custom on-screen keyboard templates and control PicoBot over WebSocket.

## Features

- **[Templates]** Create, edit, reorder, and delete templates stored locally via `SharedPreferences`.
- **[Template Editor]** Drag and resize keys with percent-based positions/sizes (`xPercent`, `yPercent`, `widthPercent`, `heightPercent`). Add keys from categorized palettes (letters, numbers, symbols, arrows, function, special, mouse) defined in `lib/utils/constants.dart`.
- **[Alt label (Shift) support]** Keys can define `shiftLabel`. The editor’s add-keys grid shows a top-right badge with the alt label when available. In the controller, holding any SHIFT key switches labels to the alt form.
- **[Multi-SHIFT]** Multiple SHIFT keys are supported. Shift stays active while at least one SHIFT is held.
- **[Controller Screen]** Uses the active template to render pressable keys. Press/release updates local UI instantly and sends commands to the server when connected.
- **[Macro controls]** START/STOP button with live macro state synced from the server (`macro|playing` / `macro|stopped`).
- **[Controller input model]** Central canvas `Listener` for raw pointer events enables robust multi-touch holds. Keys press on finger down and release on finger up, minimizing latency and avoiding gesture arena cancellations.
- **[Connection & latency]** Compact connection dot plus RTT (ms) indicator in the AppBar.
- **[Server Profiles]** Create/select server profiles in Settings. The last selected profile auto-connects on startup. If no profile is selected, the app does not auto-connect.
- **[Robust connection handling]** `WebSocketService` is a singleton. It cleans up on errors, uses exponential backoff with jitter for reconnects, and a dedicated heartbeat (`ping|<nonce>`/`pong|<nonce>`) to detect stale connections.
- **[Default template]** Automatically loaded from `assets/templates/default_template.json` on first run.
- **[Dev logs]** Debug-only in-app log console (Settings → View Logs) backed by centralized `LoggerService`.

## Architecture overview

- **[State management]**
  - `ConnectionProvider`: WebSocket connection state, key press logic, SHIFT state.
  - `TemplateProvider`: Active template selection, layout updates, add/remove/reorder keys.
- **[Services]**
  - `WebSocketService`: Singleton that connects to the server, listens for messages, handles reconnects with exponential backoff + jitter, and runs a dedicated ping/pong heartbeat.
  - `StorageService`: Persists templates, active template ID, server profiles, and selected profile ID in `SharedPreferences`.
  - `LoggerService`: Centralized logging with levels, ring buffer, and optional in-app viewer.
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
  - Client sends periodic `ping|<nonce>` and the server replies `pong|<same-nonce>`. Any non-pong message also clears the heartbeat wait for backward compatibility.

## Server configuration (Tailscale + ws)

- **[Prerequisites]**
  - Install Tailscale on the server (where PicoBot desktop app runs) and on mobile devices.
  - Ensure both are connected to the same tailnet and MagicDNS is enabled.

- **[Choose a hostname]**
  - Use the server’s MagicDNS hostname (e.g., `device-name.<tailnet>.ts.net`). Avoid using 100.x IPs because Android’s network security config whitelists hostnames, not IPs.

- **[Start the PicoBot server]**
  - Launch the desktop PicoBot app (`picobot/app.py`).
  - Start the Remote Control WebSocket server (default port `8765`).
  - The server listens on cleartext ws by default (`ws_tls` defaults to false in `picobot/config.json`).

- **[Configure the mobile app]**
  - In Settings → Server Profiles, add a profile with Host = `your-device.ts.net` and Port = `8765`.
  - Select the profile to connect. The app uses `ws://` and shows RTT in the AppBar when connected.

- **[Platform notes]**
  - Android allows cleartext only for `*.ts.net` via `android/app/src/main/res/xml/network_security_config.xml`.
  - iOS allows cleartext only for `*.ts.net` via `ios/Runner/Info.plist` ATS exceptions.
  - If you use bare IPs (100.x), platform policies will block `ws://` by default. Prefer MagicDNS hostnames.

- **[Firewall]**
  - Allow inbound TCP `8765` on the server host (Windows Defender Firewall, etc.).

- **[Future: switch to TLS]**
  - If desired later, mint a Tailscale certificate for your MagicDNS name (`tailscale cert <host>`) and set `ws_tls=true`, `ws_certfile`, `ws_keyfile` in `picobot/config.json`, then switch the client to `wss://`.

## Running the app

- **[Prerequisites]** Flutter SDK installed and configured.
- **[Install dependencies]**
  - `flutter pub get`
- **[Run]**
  - `flutter run`
- **[Server]** Ensure the PicoBot server is running and reachable at the configured host/port (set in Settings).
  - In Settings → Server Profiles, add/select a profile (host/port). The last selected profile auto-connects on startup; clear selection to disable auto-connect.

## Latency and performance tuning

- **[Direct vs DERP]** Over mobile data, Tailscale may relay via DERP, adding tens–hundreds of ms. On the server, use `tailscale status` and `tailscale ping <phone>` to see if the path is "direct" or "via DERP(...)". Same‑LAN Wi‑Fi is usually single‑digit ms.
- **[Disable WebSocket compression]** For tiny, frequent frames (e.g., `key|down|a`), compression adds CPU/buffering with negligible size savings. This project disables compression on both ends:
  - Server: `websockets.serve(..., compression=None)` in `picobot/remote/control.py`.
  - Client: `dart:io` `WebSocket.connect(..., compression: CompressionOptions(enabled: false))` in `lib/services/websocket_service.dart`.
- **[Build mode]** Test on Profile/Release builds on mobile; Debug adds overhead that can affect perceived latency.
- **[UI path]** Trigger sends directly in pointer handlers; keep rebuilds minimal and wrap the canvas in `RepaintBoundary` to reduce invalidation cost.
- **[Server/serial]** Avoid sleeps/batching for key events in `_writer_loop()`; keep serial `write_timeout` low and ACK waits off for immediate actions.