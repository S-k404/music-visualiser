"""Non-blocking single-keypress reader for the controlling terminal.

Safely disables itself when stdin isn't a real TTY (e.g. output piped for
automated checks), so the rest of the app still runs headless."""

from __future__ import annotations

import select
import sys

try:
    import termios
    import tty
except ImportError:  # pragma: no cover - non-POSIX platform
    termios = None
    tty = None

ARROW_MAP = {"A": "up", "B": "down", "C": "right", "D": "left"}


class Keyboard:
    def __init__(self):
        self._enabled = bool(termios) and sys.stdin.isatty()
        self._old_settings = None

    def __enter__(self) -> Keyboard:  # noqa: PYI034 -- typing.Self needs
        # py3.11+; this project targets 3.10, not worth a typing_extensions
        # dependency for one annotation
        if self._enabled:
            fd = sys.stdin.fileno()
            self._old_settings = termios.tcgetattr(fd)
            tty.setcbreak(fd)
        return self

    def __exit__(self, *exc) -> None:
        if self._enabled and self._old_settings is not None:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._old_settings)

    def read_key(self) -> str | None:
        if not self._enabled:
            return None
        if not select.select([sys.stdin], [], [], 0)[0]:
            return None
        ch = sys.stdin.read(1)
        if ch != "\x1b":
            return ch
        if not select.select([sys.stdin], [], [], 0.001)[0]:
            return "esc"
        ch2 = sys.stdin.read(1)
        if ch2 != "[" or not select.select([sys.stdin], [], [], 0.001)[0]:
            return "esc"
        ch3 = sys.stdin.read(1)
        return ARROW_MAP.get(ch3, "esc")
