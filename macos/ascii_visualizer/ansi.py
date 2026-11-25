"""Small collection of raw ANSI escape sequence helpers. No external
dependency is used for rendering so the hot render loop stays cheap."""

from __future__ import annotations

import contextlib
import sys

RESET = "\x1b[0m"
HIDE_CURSOR = "\x1b[?25l"
SHOW_CURSOR = "\x1b[?25h"
CLEAR_SCREEN = "\x1b[2J"
HOME = "\x1b[H"
ALT_SCREEN_ON = "\x1b[?1049h"
ALT_SCREEN_OFF = "\x1b[?1049l"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
REVERSE = "\x1b[7m"


def enable_vt_console() -> None:
    """Enable UTF-8 output and ANSI Virtual Terminal sequences on Windows."""
    if not sys.platform.startswith("win"):
        return
    with contextlib.suppress(OSError, AttributeError):
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleOutputCP(65001)
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            # 0x0004 == ENABLE_VIRTUAL_TERMINAL_PROCESSING
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(OSError, AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")


def fg(r: int, g: int, b: int) -> str:
    return f"\x1b[38;2;{r};{g};{b}m"


def bg(r: int, g: int, b: int) -> str:
    return f"\x1b[48;2;{r};{g};{b}m"
