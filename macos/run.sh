#!/usr/bin/env bash
# Convenience launcher: creates/activates the venv, installs deps if
# needed, and runs the visualiser against the given file or folder.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "ffmpeg is required but was not found. Install it with: brew install ffmpeg" >&2
    exit 1
fi

if [ ! -d ".venv" ]; then
    echo "Setting up virtual environment (first run only)..."
    python3 -m venv .venv
    # shellcheck disable=SC1091
    source .venv/bin/activate
    pip install --upgrade pip -q
    pip install -r requirements.txt -q
else
    # shellcheck disable=SC1091
    source .venv/bin/activate
fi

# No args is fine -- ascii_visualizer falls back to the saved default
# library (musicvis --set-library <path>) and prints its own error if
# none is configured.
python -m ascii_visualizer "$@"
