# Enable both the default console (REPL) port and a dedicated data port
# After saving this file to CIRCUITPY, press reset (or power cycle) to apply.
import usb_cdc
import usb_hid

# Keep console=True so you can still use the REPL if needed.
# Enable data=True to get a second COM port for your host application.
usb_cdc.enable(console=True, data=True)
usb_hid.enable((usb_hid.Device.KEYBOARD, usb_hid.Device.MOUSE))