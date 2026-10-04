# Custom CircuitPython build (HID polling 1 ms)

Stock CircuitPython hardcodes `bInterval = 8` for its HID endpoints, so
every key event reaches Windows on an 8 ms grid (see `docs/learnings.md`,
"Key events arrive on an 8 ms comb"). `circuitpython-10.3.1-hid-interval.patch`
sets it to 1, like a normal 1 kHz keyboard.

The built UF2 (`cp-10.3.1-pico_w-hid1ms.uf2`, Pico W) is not committed.

## Build (WSL2 Ubuntu 22.04)

```bash
sudo apt-get install -y cmake build-essential git python3-pip gettext ninja-build
# GCC 14+ is required; Ubuntu 22.04's is too old. Arm GNU Toolchain 14.3.rel1:
#   https://developer.arm.com/downloads/-/arm-gnu-toolchain-downloads
git clone --depth 1 --branch 10.3.1 https://github.com/adafruit/circuitpython.git
cd circuitpython
pip3 install -r requirements-dev.txt
python3 tools/ci_fetch_deps.py raspberrypi
make -C mpy-cross -j
git apply ../path/to/circuitpython-10.3.1-hid-interval.patch
cd ports/raspberrypi
make BOARD=raspberry_pi_pico_w -j        # build-raspberry_pi_pico_w/firmware.uf2
```

## Flash

Hold BOOTSEL while plugging the Pico in, then drop the UF2 on `RPI-RP2`.
`CIRCUITPY` keeps its files. To go back, flash the stock 10.3.1 UF2.

Check with `chord_probe`: arrival gaps should follow the asked gaps down
to ~4-5 ms (the ACK round trip) instead of snapping to 8 ms.
