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

# How long to wait, after seeing ESC, for the rest of an arrow-key escape
# sequence before giving up and treating it as a lone Esc keypress. 1ms
# (the original value) is far too tight: the two bytes almost always
# arrive in a second read() rather than bundled with the ESC byte, and
# anything adding a hop -- tmux, SSH, a loaded system -- routinely pushes
# the gap past 1ms, which silently turned arrow-key presses into a quit
# (Esc -> quit). 25ms matches common terminal/editor escape-timeout
# defaults and is still well under human-perceptible input lag.
_ESCAPE_SEQUENCE_TIMEOUT = 0.025


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
        if not select.select([sys.stdin], [], [], _ESCAPE_SEQUENCE_TIMEOUT)[0]:
            return "esc"
        ch2 = sys.stdin.read(1)
        if ch2 != "[" or not select.select(
            [sys.stdin], [], [], _ESCAPE_SEQUENCE_TIMEOUT
        )[0]:
            return "esc"
        ch3 = sys.stdin.read(1)
        return ARROW_MAP.get(ch3, "esc")
