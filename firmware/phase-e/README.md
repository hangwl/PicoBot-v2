# Phase E: TinyUSB firmware

`k75/` is the real thing: a Pico SDK / TinyUSB firmware that enumerates
byte-for-byte like the user's Sonix K75 — three HID interfaces, all
interrupt-IN at 1 ms, no serial number, no serial port, no MSC.

- if0: boot keyboard (stock report descriptor, LED output honored)
- if1: the K75's media interface — consumer/system control, a 120-bit
  bitmap keyboard, and the mouse collection (report ID 6) that carries
  `hid|mouse|*`, `hid|move`, `hid|scroll`
- if2: the K75's own vendor channel (usage page 0xFF13, 64-byte
  input/output/feature reports, no OUT pipe — host writes reach EP0 as
  SET_REPORT). This rides the whole `code.py` line protocol: newline-
  terminated text chunked into reports, `hello|handshake` →
  `PICO_READY v2`, numbered `ACK`/`NACK`, `hid|key|down|x|<ms>` leases,
  `hid|held`/`hid|keys`, `hid|release_all`. The 2 s silence watchdog and
  the 4 s hardware watchdog match `code.py`; unmount or suspend releases
  everything. `maintenance` ACKs and reboots into BOOTSEL — the board's
  only over-USB update path now.
- The one deliberate divergence from the captured device: EP3's packet
  size is 64, not 8, so a vendor report moves in one poll instead of
  eight.

`spike/` stays as the minimal reference (echo channel + raw keycodes).
`k75-descriptors.txt` has the captured report descriptors.
`k75.uf2` is the built artifact (not committed upstream — rebuild below).

## Build (WSL)

Arm GCC 14 and Pico SDK 2.2.0 as for the CircuitPython build:

```bash
wsl bash firmware/phase-e/k75/build.sh        # -> firmware/phase-e/k75.uf2
```

## Flash

Hold BOOTSEL while plugging the Pico in, drop `k75.uf2` on `RPI-RP2`.
With this firmware running, `maintenance` reboots to BOOTSEL for the next
update — there is no CIRCUITPY to preserve. To go back to CircuitPython,
flash `firmware/cp-10.3.1-pico_w-hid1ms.uf2` (or the stock UF2) the same
way; a nuked board takes BOOTSEL + `flash_nuke.uf2`.

## Host side

`serial_port: "hid"` in `config.json` (or `--port hid`) opens the vendor
channel (`io/src/hid_transport.rs`); the examples take it too:
`serial_latency -- hid`, `pico_maintenance -- hid`, `chord_probe -- hid`.

## Smoke test (host side)

```powershell
cd rust; cargo run --release -p picobot-io --example hid_echo -- list
cd rust; cargo run --release -p picobot-io --example hid_echo -- rtt
cd rust; cargo run --release -p picobot-io --example hid_echo -- type space
```
