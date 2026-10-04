import time
import microcontroller
import supervisor
import usb_hid
import usb_cdc
from watchdog import WatchDogMode
from adafruit_hid.keyboard import Keyboard
from adafruit_hid.keycode import Keycode
from adafruit_hid.mouse import Mouse

# A file saved to CIRCUITPY must not restart this mid-run (keys held);
# reset the board to load an edit.
supervisor.runtime.autoreload = False

# Hardware watchdog: a hang resets the board, and the re-enumeration
# releases every key. In RESET mode it can't be stopped, so leaving to
# the REPL (Ctrl-C) resets the board too; set False while debugging.
HW_WATCHDOG = True
HW_WATCHDOG_S = 4.0

# Add a delay to give the USB host time to get ready.
# This helps prevent a race condition on startup.
time.sleep(1)


# A comprehensive mapping from the string names used by the 'keyboard' library
# to the Keycode objects that CircuitPython's HID library understands.
KEY_MAP = {
    # Letters (Lowercase)
    'a': Keycode.A, 'b': Keycode.B, 'c': Keycode.C, 'd': Keycode.D, 'e': Keycode.E,
    'f': Keycode.F, 'g': Keycode.G, 'h': Keycode.H, 'i': Keycode.I, 'j': Keycode.J,
    'k': Keycode.K, 'l': Keycode.L, 'm': Keycode.M, 'n': Keycode.N, 'o': Keycode.O,
    'p': Keycode.P, 'q': Keycode.Q, 'r': Keycode.R, 's': Keycode.S, 't': Keycode.T,
    'u': Keycode.U, 'v': Keycode.V, 'w': Keycode.W, 'x': Keycode.X, 'y': Keycode.Y,
    'z': Keycode.Z,

    # Numbers (Top Row)
    '1': Keycode.ONE, '2': Keycode.TWO, '3': Keycode.THREE, '4': Keycode.FOUR,
    '5': Keycode.FIVE, '6': Keycode.SIX, '7': Keycode.SEVEN, '8': Keycode.EIGHT,
    '9': Keycode.NINE, '0': Keycode.ZERO,

    # Function Keys
    'f1': Keycode.F1, 'f2': Keycode.F2, 'f3': Keycode.F3, 'f4': Keycode.F4,
    'f5': Keycode.F5, 'f6': Keycode.F6, 'f7': Keycode.F7, 'f8': Keycode.F8,
    'f9': Keycode.F9, 'f10': Keycode.F10, 'f11': Keycode.F11, 'f12': Keycode.F12,

    # Punctuation and Symbols
    'enter': Keycode.ENTER,
    'esc': Keycode.ESCAPE,
    'backspace': Keycode.BACKSPACE,
    'tab': Keycode.TAB,
    'space': Keycode.SPACE,
    '-': Keycode.MINUS,
    '=': Keycode.EQUALS,
    '[': Keycode.LEFT_BRACKET,
    ']': Keycode.RIGHT_BRACKET,
    '\\': Keycode.BACKSLASH,
    ';': Keycode.SEMICOLON,
    "'": Keycode.QUOTE,
    '`': Keycode.GRAVE_ACCENT,
    ',': Keycode.COMMA,
    '.': Keycode.PERIOD,
    '/': Keycode.FORWARD_SLASH,

    # Modifier Keys
    'caps lock': Keycode.CAPS_LOCK,
    'shift': Keycode.LEFT_SHIFT,
    'ctrl': Keycode.LEFT_CONTROL,
    'alt': Keycode.LEFT_ALT,
    'cmd': Keycode.LEFT_GUI,
    'windows': Keycode.LEFT_GUI,
    'right shift': Keycode.RIGHT_SHIFT,
    'right ctrl': Keycode.RIGHT_CONTROL,
    'right alt': Keycode.RIGHT_ALT,

    # Navigation and Control Keys
    'print screen': Keycode.PRINT_SCREEN,
    'scroll lock': Keycode.SCROLL_LOCK,
    'pause': Keycode.PAUSE,
    'insert': Keycode.INSERT,
    'home': Keycode.HOME,
    'page up': Keycode.PAGE_UP,
    'delete': Keycode.DELETE,
    'end': Keycode.END,
    'page down': Keycode.PAGE_DOWN,
    'right': Keycode.RIGHT_ARROW,
    'left': Keycode.LEFT_ARROW,
    'down': Keycode.DOWN_ARROW,
    'up': Keycode.UP_ARROW,
}

# Optional mouse button mapping for remote control
MOUSE_MAP = {
    'left': Mouse.LEFT_BUTTON,
    'right': Mouse.RIGHT_BUTTON,
    'middle': Mouse.MIDDLE_BUTTON,
}

print("Pico HID Command Executor")

try:
    keyboard = Keyboard(usb_hid.devices)
    print("HID Keyboard initialized. Ready for commands.")
except Exception as e:
    print(f"Error initializing HID Keyboard: {e}")
    while True: 
        pass

# Initialize HID mouse (optional)
try:
    mouse = Mouse(usb_hid.devices)
    print("HID Mouse initialized.")
except Exception as e:
    print(f"Error initializing HID Mouse: {e}")
    mouse = None

# Track DATA serial connection state to re-emit readiness on new connections
data_was_connected = usb_cdc.data.connected
# Buffer for assembling newline-terminated commands from DATA port
rx_buffer = b""
RX_LIMIT = 4096
# Periodic PICO_READY re-emit control
last_ready_sent = 0.0
commands_seen = False
# Failsafe: the host sends a line (a command or "ka") at least every
# ~0.5s. Silence this long while anything is held means the host is gone
# or hung — let go of everything rather than hold a key forever.
WATCHDOG_S = 2.0
last_rx = time.monotonic()
# Armed once the host shows it speaks v2 (a numbered command or "ka"):
# an older host sends no keepalives and may hold a key for seconds.
watchdog_armed = False
held = set()


def release_everything():
    held.clear()
    try:
        keyboard.release_all()
    except Exception:
        pass
    if mouse is not None:
        try:
            mouse.release_all()
        except Exception:
            pass


def reply(word, seq):
    try:
        usb_cdc.data.write((word + (" " + seq if seq else "") + "\n").encode())
    except Exception:
        pass


def run(parts):
    """Execute one command; True when handled."""
    if len(parts) == 2 and parts[0].lower() == 'hid' and parts[1].lower() == 'release_all':
        release_everything()
        return True
    if len(parts) >= 4 and parts[0].lower() == 'hid':
        kind = parts[1].lower()
        action = parts[2].lower()
        name = parts[3].strip().lower()
        if kind == 'key':
            kc = KEY_MAP.get(name)
            if kc is None:
                return False
            if action == 'down':
                keyboard.press(kc)
                held.add(kc)
                return True
            if action == 'up':
                keyboard.release(kc)
                held.discard(kc)
                return True
            return False
        if kind == 'mouse' and mouse is not None:
            btn = MOUSE_MAP.get(name)
            if btn is None:
                return False
            if action == 'down':
                mouse.press(btn)
                held.add(('mouse', btn))
                return True
            if action == 'up':
                mouse.release(btn)
                held.discard(('mouse', btn))
                return True
            return False
        if kind == 'move' and mouse is not None:
            # hid|move|dx|dy  (relative cursor movement)
            dx, dy = int(parts[2]), int(parts[3])
            if dx or dy:
                mouse.move(x=dx, y=dy)
            return True
        if kind == 'scroll' and mouse is not None:
            # hid|scroll|dx|dy  (only dy supported via wheel)
            dy = int(parts[3])
            if dy:
                mouse.move(wheel=-dy)
            return True
        return False
    # Legacy format: down|<name> or up|<name>
    if len(parts) == 2:
        kc = KEY_MAP.get(parts[1].strip().lower())
        cmd = parts[0].strip().lower()
        if kc is not None and cmd == 'down':
            keyboard.press(kc)
            held.add(kc)
            return True
        if kc is not None and cmd == 'up':
            keyboard.release(kc)
            held.discard(kc)
            return True
    return False


# --- Main Loop ---
def handle(command_line):
    global commands_seen, watchdog_armed
    if command_line == 'ka':
        watchdog_armed = True
        return
    if not command_line:
        return
    # "<seq>:<command>" — the reply echoes <seq>.
    seq = ""
    colon = command_line.find(':')
    if colon > 0 and command_line[:colon].isdigit():
        seq, command_line = command_line[:colon], command_line[colon + 1:]
        watchdog_armed = True
    parts = command_line.split('|')
    # Handshake compatibility: "hello" or "hello|handshake"
    if parts[0].lower() == 'hello' and len(parts) <= 2:
        try:
            usb_cdc.data.write(b"PICO_READY v2\n")
        except Exception:
            pass
        commands_seen = True
        return
    commands_seen = True
    try:
        handled = run(parts)
    except Exception as e:
        print(f"Command failed '{command_line}': {e}")
        handled = False
    if not handled:
        print(f"Warning: Unhandled command '{command_line}'")
    # Every numbered command gets an answer, so the host never waits out
    # its timeout on a command that failed here.
    reply("ACK" if handled else "NACK", seq)


def step():
    global data_was_connected, last_ready_sent, commands_seen
    global rx_buffer, last_rx, watchdog_armed
    now = time.monotonic()
    # Emit PICO_READY on new DATA port connection
    now_connected = usb_cdc.data.connected
    if now_connected and not data_was_connected:
        try:
            usb_cdc.data.write(b"PICO_READY v2\n")
            print("[console] DATA connected; sent PICO_READY on DATA")
            last_ready_sent = now
            commands_seen = False
        except Exception:
            pass
        last_rx = now
    elif (not now_connected) and data_was_connected:
        print("[console] DATA disconnected; releasing all keys")
        release_everything()
        rx_buffer = b""
        watchdog_armed = False
    data_was_connected = now_connected

    if watchdog_armed and held and now - last_rx > WATCHDOG_S:
        print("[console] host silent; releasing all keys")
        release_everything()

    # If still connected but host hasn't sent any command yet, periodically re-emit PICO_READY
    if now_connected and not commands_seen:
        if (now - last_ready_sent) >= 1.0:
            try:
                usb_cdc.data.write(b"PICO_READY v2\n")
                last_ready_sent = now
            except Exception:
                pass

    # Check if there's any data waiting in the DATA USB serial buffer.
    if usb_cdc.data.in_waiting > 0:
        try:
            chunk = usb_cdc.data.read(usb_cdc.data.in_waiting)
        except Exception:
            chunk = None
        if chunk:
            rx_buffer += chunk
            last_rx = time.monotonic()
        if len(rx_buffer) > RX_LIMIT and b"\n" not in rx_buffer:
            rx_buffer = b""

        # Process any complete lines
        while b"\n" in rx_buffer:
            line, rx_buffer = rx_buffer.split(b"\n", 1)
            try:
                command_line = line.decode("utf-8").strip()
            except Exception as e:
                print(f"Could not decode command: {line}. Error: {e}")
                continue
            handle(command_line)


release_everything()
if HW_WATCHDOG:
    wdt = microcontroller.watchdog
    wdt.timeout = HW_WATCHDOG_S
    wdt.mode = WatchDogMode.RESET
try:
    while True:
        if HW_WATCHDOG:
            wdt.feed()
        step()
        # 1ms keeps input timing fine-grained (10ms rounded every event).
        time.sleep(0.001)
finally:
    # A crash or Ctrl-C must not leave anything held.
    release_everything()
