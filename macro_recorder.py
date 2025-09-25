import time

import keyboard

# --- Configuration ---
LOG_FILE = f"macro_recording_{int(time.time())}.txt"

print(f"High-fidelity keylogger started. Recording to '{LOG_FILE}'.")
print("Press and hold 'Esc' for a moment to stop the logger.")

# --- Main Program ---
# Record all keyboard events until the 'Esc' key is pressed.
# The list of events will include the 'esc' press itself.
events = keyboard.record(until="esc")

# --- NEW: Filter out the final 'esc' event ---
# The 'record' function stops on the key down event for 'esc',
# so the last item in the list is always the 'esc' press we want to remove.
if events:
    # This removes the last event from the list, which is the 'esc' press.
    events.pop()

# Write the filtered events to the log file
with open(LOG_FILE, "w") as f:
    for event in events:
        # Each line will be: timestamp event_type key_name
        # e.g., 16612345.678 down e
        f.write(f"{event.time} {event.event_type} {event.name}\n")

print(f"Logger stopped. Macro saved to '{LOG_FILE}'.")
