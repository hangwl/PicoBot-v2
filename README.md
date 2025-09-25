# PicoBot

PicoBot is a tool for macroing/botting. It uses a compatible microcontroller to act as a HID device to relay physical keyboard/mouse inputs to a computer.

Note that while I am using a Raspberry Pi Pico device, other microcontroller devices that support `Circuit Python` should still work. The `Adafruit HID` library is required to relay physical keyboard inputs.

## Disclaimer

This project is purely for learning purposes. Picobot was built for my personal botting needs in a Maplestory private server. Botting is a punishable offense, please use the program at your own risk. 

## Installation

1. Install Python 3.13 or later.
2. (Optional) Create and activate a virtual environment: `python -m venv .venv` then `.\.venv\Scripts\activate` on Windows.
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
