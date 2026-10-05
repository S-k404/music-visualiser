"""Non-blocking single-keypress reader for the controlling terminal.

Reads the raw file descriptor and does its own escape-sequence parsing.
Going through `sys.stdin.read(1)` instead is wrong here: the buffered
text wrapper slurps every byte the terminal has sent into its own buffer
on the first read, so a following `select()` on the fd sees nothing and a
3-byte arrow-key sequence (ESC [ C) is mistaken for a lone Esc -- which
quits the player -- and quickly-typed keys are held back until the next
keypress.

Safely disables itself when stdin isn't a real TTY (e.g. output piped for
automated checks), so the rest of the app still runs headless."""

from __future__ import annotations

import os
import select
import sys

try:
    import termios
    import tty
except ImportError:  # pragma: no cover - non-POSIX platform
    termios = None
    tty = None

ARROW_MAP = {"A": "up", "B": "down", "C": "right", "D": "left", "H": "home", "F": "end"}
# CSI "<n>~" sequences (ESC [ 5 ~ etc.)
TILDE_MAP = {"1": "home", "4": "end", "5": "pageup", "6": "pagedown", "7": "home", "8": "end"}

# A recognised-but-unmapped sequence (F-keys, Delete, Alt+key, ...). Returned
# rather than swallowed silently so callers can tell "nothing pressed" (None)
# from "something pressed that we don't use" -- and, crucially, so it is never
# mistaken for Esc, which quits.
UNKNOWN = "unknown"

# How long to wait for the rest of an escape sequence before deciding the
# ESC was a real, lone Esc keypress. Terminals send a sequence's bytes
# back-to-back, so this only needs to cover scheduling jitter.
_ESC_TIMEOUT = 0.02


class Keyboard:
    def __init__(self, fd: int | None = None):
        """`fd` overrides the descriptor to read (tests use a pipe); by
        default it's stdin, and only when stdin is a real TTY."""
        if fd is not None:
            self._fd: int | None = fd
            self._enabled = True
        else:
            self._enabled = bool(termios) and sys.stdin.isatty()
            self._fd = sys.stdin.fileno() if self._enabled else None
        self._old_settings = None
        self._pending = b""

    def __enter__(self) -> Keyboard:  # noqa: PYI034 -- typing.Self needs
        # py3.11+; this project targets 3.10, not worth a typing_extensions
        # dependency for one annotation
        if self._enabled and self._fd is not None and termios and os.isatty(self._fd):
            self._old_settings = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
        return self

    def __exit__(self, *exc) -> None:
        if self._old_settings is not None and self._fd is not None:
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old_settings)
            self._old_settings = None

    def _fill(self, timeout: float = 0.0) -> bool:
        """Pull whatever bytes are waiting on the fd into the pending
        buffer. Returns True if anything new arrived."""
        assert self._fd is not None
        got = False
        while select.select([self._fd], [], [], timeout)[0]:
            try:
                chunk = os.read(self._fd, 4096)
            except OSError:
                break
            if not chunk:  # EOF
                break
            self._pending += chunk
            got = True
            timeout = 0.0  # only the first wait may block; then just drain
        return got

    def read_key(self) -> str | None:
        """Return the next keypress, or None if there isn't one waiting.
        Call repeatedly to drain everything typed since the last frame."""
        if not self._enabled:
            return None
        self._fill()
        return self._pop_key()

    def _pop_key(self) -> str | None:
        buf = self._pending
        if not buf:
            return None

        first = buf[0]
        if first != 0x1B:
            return self._pop_plain(first)

        # Escape sequence. A bare ESC may just be the first byte of one that
        # hasn't fully arrived yet, so give the rest a moment.
        if len(buf) < 2 and self._fill(_ESC_TIMEOUT):
            buf = self._pending
        if len(buf) < 2:
            self._pending = buf[1:]
            return "esc"

        if buf[1] not in (ord("["), ord("O")):
            # Alt+<key>: ESC followed by the key itself. Not a binding here,
            # and must not be allowed to read as Esc (= quit).
            self._pending = buf[2:]
            return UNKNOWN

        # CSI (ESC [ ...) or SS3 (ESC O x, sent by some terminals in
        # "application cursor" mode). Consume through the final byte.
        end = 2
        while True:
            if end >= len(buf) and not self._fill(_ESC_TIMEOUT):
                break  # truncated sequence; consume what's there
            buf = self._pending
            if end >= len(buf):
                break
            final = buf[end]
            end += 1
            if 0x40 <= final <= 0x7E:  # final byte of a CSI/SS3 sequence
                break
        seq = buf[:end].decode("ascii", errors="replace")
        self._pending = buf[end:]
        return self._decode_sequence(seq)

    def _pop_plain(self, first: int) -> str:
        """A non-escape byte: ASCII, or the start of a UTF-8 character."""
        if first < 0x80:
            length = 1
        elif first >= 0xF0:
            length = 4
        elif first >= 0xE0:
            length = 3
        else:
            length = 2
        # Grab the continuation bytes if they're still in flight.
        if len(self._pending) < length:
            self._fill(_ESC_TIMEOUT)
        chunk, self._pending = self._pending[:length], self._pending[length:]
        ch = chunk.decode("utf-8", errors="replace")
        if ch in ("\r", "\n"):
            return "enter"
        return ch

    @staticmethod
    def _decode_sequence(seq: str) -> str:
        final = seq[-1]
        body = seq[2:-1]
        if final == "~":
            return TILDE_MAP.get(body.split(";")[0], UNKNOWN)
        # Modified arrows look like ESC [ 1 ; 5 C -- the final byte still
        # names the arrow, so treat Ctrl/Shift+arrow the same as the plain one.
        return ARROW_MAP.get(final, UNKNOWN)
