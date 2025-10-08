# PicoBot

PicoBot is a tool for macroing/botting. It uses a compatible microcontroller to act as a HID device to relay physical keyboard/mouse inputs to a computer.

Note that while I am using a Raspberry Pi Pico device, other microcontroller devices that support `Circuit Python` should still work. The `Adafruit HID` library is required to relay physical keyboard inputs.

## Disclaimer
This project is purely for learning purposes. Picobot was built for my personal botting needs in a Maplestory private server. Botting is a punishable offense, please use the program at your own risk. 

## Installation

1. Install Python 3.13 or later.
2. (Optional) Create and activate a virtual environment: `python -m venv .venv` then `\.venv\Scripts\activate` on Windows.
3. From the project root, install dependencies in editable mode: `pip install -e .`

## Usage Guide

To run the app, launch the GUI after installation with:

```bash
python -m picobot
```

The application will start the Tk interface, where you can select a COM port, target window, macro folder. 

PicoBot automatically starts a WebSocket-based remote control server on port 8765 when a COM port is selected. During macro playback, if the remote server is active (which it is by default), all HID events (key inputs) are relayed via the remote server to the microcontroller device via serial communication.  

Additionally, the server embeds a HTML remote controller interface that can be accessed via the specified HTTP port. Using [Tailscale](https://github.com/tailscale/tailscale), you can access this interface on a remote device. Paired with [Sunshine/Moonlight](https://github.com/LizardByte/Sunshine) streaming, you can bot/play your game from anywhere (note that some players in GMS have been reportedly banned for using Sunshine/Moonlight).

To record a macro, run `macro_recorder.py` script in administrator mode. The `ESC` key stops the recording and saves the recorded macro as a text file. In the selected macro folder, macro files form a randomized playlist that loops continuously until the playback is interrupted. Files prefixed with `START_` are prioritized to start first.

To use the Telegram notifier, please refer to [BotFather](https://core.telegram.org/bots/tutorial) to get your own `Bot Token` and `Chat ID`.

## Picobot Directory Tree

```
picobot/
├── playback/
│   ├── __init__.py
│   └── macro_controller.py
├── remote/
│   ├── __init__.py
│   ├── control.py
│   └── http.py
├── services/
│   ├── __init__.py
│   └── system.py
├── transport/
│   ├── __init__.py
│   └── serial_manager.py
├── ui/
│   ├── __init__.py
│   └── views.py
├── __init__.py
├── __main__.py
├── app.py
├── config.py
├── context.py
├── countdown.py
├── messaging.py
└── settings.py
```

---

## PicoBot Server (Desktop GUI)

- **Launch**
  - From the repo root: `python -m picobot`
  - The Tk app opens (`picobot/app.py`).

- **Setup steps in the GUI**
  - **1. Select Pico DATA COM port** in “Select Pico DATA Port”. This auto-starts the Remote server.
  - **2. Select Target Window** to which keystrokes will be sent.
  - **3. Select Macro Folder** (root folder that holds your macros/playlists).
  - Optional: configure **Telegram & Countdown**.

- **Macro folders and playlists**
  - Put `.txt` macro files inside folders. Each subfolder under your macro root is treated as a distinct “playlist”.
  - If you pick a specific playlist folder as the macro folder, the server will list sibling playlists by looking one level up (a heuristic added for convenience).
  - Record new macros with `macro_recorder.py` (run as admin). Press `ESC` to stop and save.

- **Remote server**
  - A WebSocket server is started when the COM port is selected. Default port comes from config (e.g., 8765).
  - An embedded HTTP server serves a basic remote UI on the configured HTTP port.
  - TLS is supported for WS if `ws_tls`, `ws_certfile`, and `ws_keyfile` are set and valid (see `AppConfig` in `picobot/config.py`).

- **During playback**
  - HID events are relayed over serial to the Pico. You can stop via the GUI, a remote command, or by switching windows.

- **Troubleshooting (server)**
  - “No COM ports found”: ensure drivers/cable are OK and the Pico DATA port is selected (not the CDC-only port).
  - Playlists don’t appear: confirm your macro root has subfolders. If you selected a playlist folder directly, the server now lists siblings from its parent.
  - Port conflicts: adjust WS/HTTP ports in the GUI; check firewall rules.

---

## PicoBot Controller (Flutter App)

The mobile/desktop controller UI lives under `picobot_controller/`.

- **Prerequisites**
  - Install Flutter (stable channel).
  - Android Studio / Xcode as needed for your platform.

- **Run locally**
  - In `picobot_controller/`: `flutter pub get`
  - Then: `flutter run -d <device>`

- **Build**
  - Android: `flutter build apk`
  - iOS: open the iOS project and build via Xcode (signing required).
  - Web/Desktop: use `flutter run`/`flutter build` with appropriate targets.

- **Connect to the server**
  - Open Settings → “Server Profiles”. Add a profile with the desktop’s IP and WS port (e.g., `192.168.1.100:8765`).
  - Use “Reconnect” to force a connect. The status dot shows: green (connected), orange (reconnecting), red (disconnected).

- **Playlists in the AppBar**
  - When connected and playlists are available, a playlist dropdown appears inline in the AppBar next to the connection indicator.
  - Selecting a playlist tells the server which subfolder under your macro root to use.

- **Background and multitasking**
  - The controller keeps the WS open while backgrounded/in split-screen where possible. On resume, it avoids reconnecting if the link is still alive and just refreshes playlists.
  - If the OS drops the socket, the app automatically retries with an orange “reconnecting” indicator.

- **Troubleshooting (controller)**
  - Cannot connect: verify the desktop server is running, IP/port are correct, and firewall allows the port.
  - No playlists: ensure the desktop macro root has subfolders; the server responds with `macroPlaylists` only when it finds any.
  - After device lock/unlock: the app now handles resume more gracefully; use the “Reconnect” button if needed.

---

## Remote connections over mobile data (Tailscale MagicDNS)

- **Why Tailscale**
  - Works behind CGNAT and firewalls without port forwarding.
  - End-to-end encrypted (WireGuard). MagicDNS gives a stable hostname.

- **Setup**
  - Install Tailscale on the desktop host running the PicoBot Server and sign in.
  - In the Tailscale admin console, enable **MagicDNS** (Settings → DNS → MagicDNS On).
  - Optionally enable **HTTPS certificates** (not required for ws://, but useful for wss://).
  - Install Tailscale on your phone and sign in to the same tailnet.

- **Connect from the controller**
  - In the controller’s Server Profile host field, use either:
    - The host’s MagicDNS name (as shown in the Tailscale admin page or `tailscale status`), e.g. `myhost.tail-1234.ts.net`.
    - Or the Tailscale IP (100.x.y.z) of the desktop.
  - Keep the same WebSocket port as shown by the desktop app (default 8765).
  - TLS is optional because Tailscale already encrypts traffic. If you still prefer wss://, configure cert/key in `picobot/config.py` and enable WS TLS in the GUI.