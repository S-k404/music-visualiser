"""Keyboard escape-sequence parsing, driven through a pipe so the tests are
deterministic and need no real TTY. Bytes are written the way a terminal
sends them -- all of a sequence at once -- which is exactly the case the
old `sys.stdin.read(1)` implementation got wrong (every arrow key came
back as a lone Esc, i.e. "quit")."""

from __future__ import annotations

import os

import pytest

from ascii_visualizer.keyboard import UNKNOWN, Keyboard


@pytest.fixture
def pipe():
    r, w = os.pipe()
    yield r, w
    for fd in (r, w):
        try:
            os.close(fd)
        except OSError:
            pass


def _keys(pipe, payload: bytes) -> list[str]:
    r, w = pipe
    os.write(w, payload)
    kb = Keyboard(fd=r)
    out = []
    while (k := kb.read_key()) is not None:
        out.append(k)
    return out


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (b"\x1b[A", "up"),
        (b"\x1b[B", "down"),
        (b"\x1b[C", "right"),
        (b"\x1b[D", "left"),
        (b"\x1bOA", "up"),  # SS3 form, used in "application cursor" mode
        (b"\x1b[1;5C", "right"),  # Ctrl+right: modifier params are skipped
        (b"\x1b[H", "home"),
        (b"\x1b[F", "end"),
        (b"\x1b[5~", "pageup"),
        (b"\x1b[6~", "pagedown"),
    ],
)
def test_navigation_sequences_are_decoded_not_mistaken_for_esc(pipe, payload, expected):
    assert _keys(pipe, payload) == [expected]


def test_arrow_key_is_never_reported_as_esc(pipe):
    # The regression itself: Esc quits the player, so an arrow decoding to
    # "esc" meant pressing seek/volume closed the app.
    assert "esc" not in _keys(pipe, b"\x1b[C\x1b[D\x1b[A\x1b[B")


def test_lone_escape_is_esc(pipe):
    assert _keys(pipe, b"\x1b") == ["esc"]


def test_burst_of_keys_is_all_returned_in_order(pipe):
    # Previously only the first key surfaced; the rest sat in Python's
    # stdin buffer, invisible to select(), until the next keypress.
    assert _keys(pipe, b"ns\x1b[Cr ") == ["n", "s", "right", "r", " "]


def test_unused_sequences_are_swallowed_whole_and_never_quit(pipe):
    # F5, Delete, and Alt+q must neither leak stray bytes into the next
    # key nor read as Esc.
    keys = _keys(pipe, b"\x1b[15~\x1b[3~\x1bqn")
    assert keys == [UNKNOWN, UNKNOWN, UNKNOWN, "n"]
    assert "esc" not in keys


def test_enter_is_reported_for_cr_and_lf(pipe):
    assert _keys(pipe, b"\r\n") == ["enter", "enter"]


def test_multibyte_utf8_character_is_one_key(pipe):
    assert _keys(pipe, "é夫".encode()) == ["é", "夫"]


def test_no_input_returns_none(pipe):
    assert Keyboard(fd=pipe[0]).read_key() is None


def test_disabled_when_stdin_is_not_a_tty(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert Keyboard().read_key() is None
