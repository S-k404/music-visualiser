"""Non-blocking keypress reader tests.

read_key() has to tell a lone Esc press apart from the first byte of a
3-byte arrow-key escape sequence (ESC [ A/B/C/D) using only a short wait
for the rest of the sequence to show up -- there's no other way to
disambiguate a raw byte stream from the terminal.
"""

from __future__ import annotations

from unittest.mock import patch

from ascii_visualizer.keyboard import _ESCAPE_SEQUENCE_TIMEOUT, Keyboard


def _make_keyboard() -> Keyboard:
    kb = Keyboard()
    kb._enabled = True  # bypass the isatty() check -- stdin isn't a tty under pytest
    return kb


def test_arrow_key_sequence_resolves_to_direction():
    kb = _make_keyboard()
    with (
        patch("ascii_visualizer.keyboard.select.select", return_value=([True], [], [])),
        patch("sys.stdin") as mock_stdin,
    ):
        mock_stdin.read.side_effect = ["\x1b", "[", "B"]
        assert kb.read_key() == "down"


def test_lone_esc_with_no_more_input_returns_esc():
    kb = _make_keyboard()
    with (
        patch(
            "ascii_visualizer.keyboard.select.select",
            side_effect=[([True], [], []), ([], [], [])],
        ),
        patch("sys.stdin") as mock_stdin,
    ):
        mock_stdin.read.side_effect = ["\x1b"]
        assert kb.read_key() == "esc"


def test_escape_sequence_timeout_gives_real_terminals_room():
    """Regression: this was 1ms, tighter than the gap a tmux/SSH hop
    routinely puts between the ESC byte and the rest of an arrow-key
    sequence -- arrow presses would misread as a lone Esc and quit the
    app outright (Esc is mapped to quit). 25ms matches common
    terminal/editor escape-timeout defaults."""
    assert _ESCAPE_SEQUENCE_TIMEOUT >= 0.01
