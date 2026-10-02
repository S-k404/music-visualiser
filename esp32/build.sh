#!/usr/bin/env bash
# Convenience wrapper around PlatformIO: sets up its own isolated venv on
# first run, then builds (and optionally uploads/monitors) the firmware.
#
# Usage:
#   ./build.sh            # just compile
#   ./build.sh upload     # compile + flash (board must be connected via USB)
#   ./build.sh monitor    # open the serial monitor (115200 baud)
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

if [ ! -d ".venv-pio" ]; then
    echo "Setting up PlatformIO (first run only)..."
    python3 -m venv .venv-pio
    # shellcheck disable=SC1091
    source .venv-pio/bin/activate
    pip install --upgrade pip -q
    pip install -q platformio
else
    # shellcheck disable=SC1091
    source .venv-pio/bin/activate
fi

case "${1:-build}" in
    build)   pio run ;;
    upload)  pio run --target upload ;;
    monitor) pio device monitor ;;
    *)       echo "Usage: ./build.sh [build|upload|monitor]" >&2; exit 1 ;;
esac
