"""Local, per-machine configuration -- currently just the default music
library path, so `musicvis` with no arguments has somewhere to look.

Deliberately stored outside the repo (~/.musicvis/, alongside the log
directory in applog.py) since it's a personal filesystem path with no
business being committed to what is now a public repo.
"""

from __future__ import annotations

from pathlib import Path

CONFIG_DIR = Path.home() / ".musicvis"
LIBRARY_FILE = CONFIG_DIR / "library"


def get_default_library() -> str | None:
    try:
        path = LIBRARY_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return path or None


def set_default_library(path: str) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    LIBRARY_FILE.write_text(path + "\n", encoding="utf-8")
