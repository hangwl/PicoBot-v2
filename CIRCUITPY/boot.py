"""Runs once at power-up, before USB starts. Reset the board to apply.

Normal boot: one data port (no REPL), no CIRCUITPY drive, no MIDI,
keyboard (+ mouse) HID, and the USB identity from usb_ids.py when present.

Maintenance boot: ground RECOVERY_PIN while plugging in (or resetting) and
the board comes up stock - CIRCUITPY drive, REPL, default IDs - so code.py
and usb_ids.py can be edited. See docs/development.md.
"""
import board
import digitalio
import storage
import supervisor
import usb_cdc
import usb_hid
import usb_midi

# Jumper this pin (GP15, physical pin 20) to any GND pin for maintenance.
RECOVERY_PIN = board.GP15
# The dashboard's remote pad can click and scroll; False drops the mouse
# (code.py then runs keyboard-only).
ENABLE_MOUSE = True


def recovery_requested():
    pin = digitalio.DigitalInOut(RECOVERY_PIN)
    try:
        pin.switch_to_input(pull=digitalio.Pull.UP)
        return not pin.value
    finally:
        pin.deinit()


devices = [usb_hid.Device.KEYBOARD]
if ENABLE_MOUSE:
    devices.append(usb_hid.Device.MOUSE)

if recovery_requested():
    usb_cdc.enable(console=True, data=True)
    usb_hid.enable(tuple(devices))
else:
    usb_cdc.enable(console=False, data=True)
    usb_hid.enable(tuple(devices))
    usb_midi.disable()
    storage.disable_usb_drive()
    try:
        import usb_ids

        supervisor.set_usb_identification(
            manufacturer=usb_ids.MANUFACTURER,
            product=usb_ids.PRODUCT,
            vid=usb_ids.VID,
            pid=usb_ids.PID,
        )
    except Exception:
        pass  # no usb_ids.py (or a bad one): keep the stock identity
