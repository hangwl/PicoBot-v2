# Phase E: TinyUSB firmware (spike)

`spike/` is a minimal Pico SDK / TinyUSB firmware shaped like the keyboard it
stands in for: a boot-keyboard HID interface and a vendor-defined 64-byte
HID interface, 1 ms polling, no serial-number string and no serial port. The
vendor channel echoes every report; `K <keycode>` / `U` press and release a
key on the keyboard interface.

Build (WSL; Arm GCC 14 and CMake as for the CircuitPython build, Pico SDK 2.2.0
with its TinyUSB submodule in /root/pico-sdk):

```bash
export PATH=/root/cpbuild/arm-gnu-toolchain/bin:$PATH PICO_SDK_PATH=/root/pico-sdk
cp -r firmware/phase-e/spike /root/spike && cp $PICO_SDK_PATH/external/pico_sdk_import.cmake /root/spike/
cd /root/spike && mkdir build && cd build && cmake .. -G Ninja && ninja   # spike.uf2
```

Flash by BOOTSEL; to go back to CircuitPython, flash its UF2 the same way.

Host side: `cargo run --release -p picobot-io --example hid_echo -- list | rtt | type`.
