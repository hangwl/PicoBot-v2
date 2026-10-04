#!/bin/bash
# Build the k75 firmware in WSL: wsl bash firmware/phase-e/k75/build.sh
# (Arm GCC 14+ and Pico SDK 2.2.0 in WSL; see firmware/phase-e/README.md.)
set -e
export PATH=/root/cpbuild/arm-gnu-toolchain/bin:/usr/bin:/bin
export PICO_SDK_PATH=${PICO_SDK_PATH:-/root/pico-sdk}
SRC="$(cd "$(dirname "$0")" && pwd)"
rm -rf /root/k75-build
cp -r "$SRC" /root/k75-build
cd /root/k75-build
mkdir -p build && cd build
cmake .. -G Ninja
ninja
cp k75.uf2 "$SRC/../k75.uf2"
echo "built: $SRC/../k75.uf2"
