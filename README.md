# PicoBot

PicoBot is a desktop automation toolkit that pairs a Raspberry Pi Pico (or compatible firmware) with a Tkinter GUI, remote WebSocket bridge, and Telegram alerts to run repeatable macro playlists.

## Installation

1. Install Python 3.13 or later.
2. (Optional) Create and activate a virtual environment: `python -m venv .venv` then `.\.venv\Scripts\activate` on Windows.
3. From the project root, install dependencies in editable mode: `pip install -e .`

## Running the App

Launch the GUI after installation with:

```bash
python -m picobot
```

The application will start the Tk interface, where you can select the Pico data port, target window, macro folder, and optional remote-control services.
