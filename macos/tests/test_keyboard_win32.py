from __future__ import annotations

from ascii_visualizer import ansi
from ascii_visualizer.decode import DecodeError, check_ffmpeg_available
from ascii_visualizer.keyboard import Keyboard


class _FakeMsvcrt:
    def __init__(self, seq):
        self._items = list(seq)

    def kbhit(self):
        return bool(self._items)

    def getwch(self):
        return self._items.pop(0)


def test_windows_msvcrt_keyboard_mapping():
    fake = _FakeMsvcrt(["\xe0", "H", "\xe0", "P", "\xe0", "I", "\xe0", "Q", "\r", "\x1b", " "])
    with Keyboard(win_msvcrt=fake) as kb:
        assert kb.read_key() == "up"
        assert kb.read_key() == "down"
        assert kb.read_key() == "pageup"
        assert kb.read_key() == "pagedown"
        assert kb.read_key() == "enter"
        assert kb.read_key() == "esc"
        assert kb.read_key() == " "
        assert kb.read_key() is None


def test_enable_vt_console_safe_everywhere():
    ansi.enable_vt_console()


def test_win32_ffmpeg_hint(monkeypatch):
    import shutil

    import pytest

    from ascii_visualizer import decode

    monkeypatch.setattr(decode.sys, "platform", "win32")
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(DecodeError, match=r"winget install Gyan\.FFmpeg"):
        check_ffmpeg_available()
