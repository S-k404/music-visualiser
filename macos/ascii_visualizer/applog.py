"""File logging for the visualiser.

The TUI runs in the terminal's alt-screen and redraws every frame, so
stdout/stderr aren't a reliable place to surface problems while it's
live -- a print would just get overwritten or glitch the display. Skipped
tracks, decode failures, and crashes are logged to a persistent file
instead, so both the current session and past runs stay inspectable.

Logs intentionally live outside the project directory (~/.musicvis/logs/,
not inside the repo): they contain real file paths and listening data
from the user's own library, which has no business being anywhere near a
`git add` in what is now a public repo.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

LOG_DIR = Path.home() / ".musicvis" / "logs"


def setup_logging() -> tuple[logging.Logger, Path]:
    """Create (or reuse) the "musicvis" logger, writing to a fresh,
    timestamped file per run under LOG_DIR. Returns the logger and the
    path actually used, so the caller can mention it on a hard failure.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{datetime.now():%Y-%m-%d_%H-%M-%S}.log"

    logger = logging.getLogger("musicvis")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
        )
        logger.addHandler(handler)
    return logger, log_path
