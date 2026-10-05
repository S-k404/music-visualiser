"""Frame-composition tests: display-width handling (CJK), full-width line
fitting (the fix for text left over from the previous frame), and the
queue panel's selection cursor and scrollbar."""

from __future__ import annotations

import re
import unicodedata

import numpy as np
import pytest

from ascii_visualizer import ansi, ui

_ESC_RE = re.compile(r"\x1b\[[0-9;]*m")


def _cols(styled: str) -> int:
    """Terminal columns a styled line occupies, measured independently of
    ui.py's own width helpers -- tests that check the frame invariant with
    the code under test's own ruler can't notice that the ruler is wrong."""
    total = 0
    for ch in _ESC_RE.sub("", styled):
        if unicodedata.category(ch) in ("Mn", "Me", "Cf"):
            continue
        total += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return total


def _track(title="T", artist="A", album="Al", index=1, count=1):
    return ui.TrackInfo(
        title=title, artist=artist, album=album, date="", genre="", format="FLAC",
        sample_rate=44100, channels=2, bitrate_kbps=1000, duration_seconds=180,
        artwork=None, track_index=index, track_count=count,
    )


def _frame(layout, track, **kwargs):
    playback = ui.PlaybackState(
        position_seconds=17.0, is_playing=True, volume=1.0, mode="bars+wave"
    )
    return ui.Renderer().render_frame(
        layout, track, playback, np.full(layout.vis_w, 0.5),
        np.zeros((2205, 1), dtype=np.float32), **kwargs,
    )


# -- display width ----------------------------------------------------------


def test_display_width_counts_wide_and_zero_width_characters():
    assert ui.display_width("abc") == 3
    assert ui.display_width("夜に駆ける") == 10  # 5 fullwidth/wide chars x 2 columns
    assert ui.display_width("é") == 1  # base + combining acute accent
    assert ui.display_width("a‍b") == 2  # zero-width joiner


def test_truncate_measures_columns_not_characters():
    out = ui._truncate("夜に駆ける夜に駆ける", 7)
    assert out.endswith("…")
    assert ui.display_width(out) <= 7
    # 3 wide chars (6 cols) + the ellipsis (1 col) -- not 6 chars + ellipsis
    assert out == "夜に駆…"


def test_truncate_never_splits_a_wide_character():
    # Only 1 column of room beside the ellipsis: a 2-column glyph can't
    # fit, so it must be dropped rather than overflow the budget.
    out = ui._truncate("夜夜夜", 2)
    assert ui.display_width(out) <= 2


def test_truncate_leaves_text_that_fits_untouched():
    assert ui._truncate("short", 20) == "short"
    assert ui._truncate("夜に", 4) == "夜に"


def test_truncate_replaces_control_characters():
    # A newline inside a media tag would otherwise split the title across
    # two screen rows and shove the whole frame down.
    out = ui._truncate("Line one\nLine two\tend", 40)
    assert "\n" not in out
    assert "\t" not in out


def test_pad_and_center_are_column_aware():
    assert ui.display_width(ui._pad("夜に", 10)) == 10
    centred = ui._center("夜に", 10)
    assert ui.display_width(centred) == 10
    assert centred == "   夜に   "


# -- full-width line fitting (stale-text fix) -------------------------------


def test_fit_line_pads_short_lines_to_full_width():
    out = ui._fit_line(f"{ansi.BOLD}Hi{ansi.RESET}", 20)
    assert _cols(out) == 20


def test_fit_line_truncates_long_lines_to_full_width():
    out = ui._fit_line("x" * 100, 20)
    assert _cols(out) == 20
    assert "…" in out


def test_fit_line_truncates_wide_text_by_columns():
    out = ui._fit_line("夜" * 50, 21)
    assert _cols(out) == 21


@pytest.mark.parametrize(
    "title",
    [
        "Hi",
        "A Very Long Song Title " * 8,
        "夜に駆ける" * 12,  # 120 columns of CJK in a 100-column terminal
        "multi\nline\ttitle",
    ],
)
@pytest.mark.parametrize("opts", [{}, {"show_art": False}, {"show_queue": True}])
def test_every_frame_line_is_exactly_terminal_width(title, opts):
    """Regression test for text left over from the previous frame.

    Frames are drawn over the old one without clearing, so a line that
    doesn't cover its full width keeps the old frame's tail: switching from
    a long title to "Hi" rendered as "Hi" + the end of the old title.
    Guaranteeing every line spans exactly `cols` columns means each frame
    fully overwrites the last. It also pins the other half of the contract
    -- no line may exceed the width (wide chars counted as 2), or the
    terminal wraps it and the frame scrolls.
    """
    cols, rows = 100, 30
    layout = ui.compute_layout(cols, rows, **opts)
    frame = _frame(layout, _track(title=title, artist=title, album=title))

    lines = frame.split("\n")
    assert len(lines) == rows
    assert all(_cols(line) == cols for line in lines)


def test_every_frame_line_is_terminal_width_with_lyrics_and_cjk():
    from ascii_visualizer.lyrics import LyricGroup

    layout = ui.compute_layout(80, 24, show_lyrics=True)
    groups = [LyricGroup(0.0, ["夜に駆ける" * 10, "yoru ni kakeru", "Racing into the night"])]
    frame = _frame(layout, _track(), lyrics=groups)

    assert all(_cols(line) == 80 for line in frame.split("\n"))


# -- queue panel ------------------------------------------------------------


def _numbers(rows: list[str]) -> list[int]:
    out = []
    for r in rows:
        plain = ui._ANSI_RE.sub("", r).strip()
        head = plain.lstrip("▶ ").split(".")[0].strip()
        if head.isdigit():
            out.append(int(head))
    return out


def test_queue_rows_all_have_requested_width():
    names = [f"Track {i}" for i in range(200)]
    for rows in (
        ui.render_queue(names, 100, 40, 12),
        ui.render_queue(names, 100, 40, 12, cursor_index=3),
        ui.render_queue(names[:4], 0, 40, 12),  # short list: no scrollbar, still full width
    ):
        assert [_cols(r) for r in rows] == [40] * 12


def test_queue_window_follows_cursor_not_playing_track():
    names = [f"Track {i}" for i in range(500)]

    following = _numbers(ui.render_queue(names, current_index=10, width=40, height=11))
    browsing = _numbers(
        ui.render_queue(names, current_index=10, width=40, height=11, cursor_index=400)
    )

    assert 11 in following  # playing track (index 10 -> "11.") in view
    assert 401 in browsing  # cursor's row in view
    assert 11 not in browsing  # ...and the window really moved away from the playing track


def test_queue_cursor_row_is_highlighted_and_distinct_from_playing_row():
    names = [f"Track {i}" for i in range(20)]
    rows = ui.render_queue(names, current_index=5, width=40, height=20, cursor_index=8)

    cursor_row = next(r for r in rows if "Track 8" in r)
    playing_row = next(r for r in rows if "Track 5" in r)
    assert ansi.REVERSE in cursor_row
    assert ansi.REVERSE not in playing_row
    assert "▶" in playing_row  # playing marker survives while browsing elsewhere


def test_queue_no_cursor_means_no_highlight():
    rows = ui.render_queue([f"T{i}" for i in range(20)], 5, 40, 10)
    assert not any(ansi.REVERSE in r for r in rows)


def test_queue_cursor_out_of_range_is_clamped_not_an_error():
    names = [f"Track {i}" for i in range(10)]
    assert ui.render_queue(names, 0, 40, 5, cursor_index=99)
    assert ui.render_queue(names, 0, 40, 5, cursor_index=-4)


def test_queue_scrollbar_only_when_list_overflows():
    names = [f"Track {i}" for i in range(50)]
    assert any("┃" in r for r in ui.render_queue(names, 25, 40, 10))
    assert not any("┃" in r for r in ui.render_queue(names[:5], 2, 40, 10))


def test_queue_scrollbar_thumb_tracks_position():
    names = [f"Track {i}" for i in range(300)]
    height = 10

    def thumb_rows(cursor):
        rows = ui.render_queue(names, cursor, 40, height, cursor_index=cursor)
        return [i for i, r in enumerate(rows) if "┃" in r]

    at_top, middle, at_bottom = thumb_rows(0), thumb_rows(150), thumb_rows(299)
    assert at_top[0] == 0
    assert at_bottom[-1] == height - 1
    assert at_top[0] < middle[0] < at_bottom[0]


def test_queue_index_column_widens_for_large_libraries():
    names = [f"Track {i}" for i in range(12000)]
    rows = ui.render_queue(names, 11999, 60, 5)
    assert any("12000. Track 11999" in ui._ANSI_RE.sub("", r) for r in rows)


def test_queue_footer_shows_selection_position():
    layout = ui.compute_layout(120, 30, show_art=False, show_queue=True)
    names = [f"Song {i}" for i in range(1, 51)]

    frame = _frame(
        layout, _track(index=3, count=50), queue_names=names, queue_cursor=41
    )
    footer_hint = frame.split("\n")[-1]
    assert "(42/50)" in footer_hint
    assert "enter play" in footer_hint

    # No cursor yet: shows the playing track's position instead.
    frame = _frame(layout, _track(index=3, count=50), queue_names=names)
    assert "(3/50)" in frame.split("\n")[-1]


def test_footer_hint_outside_queue_mentions_volume_keys():
    layout = ui.compute_layout(160, 30, show_art=False)
    frame = _frame(layout, _track())
    assert "+/-" in frame.split("\n")[-1]


@pytest.mark.parametrize(
    ("volume", "shown"), [(0.85, " 85%"), (0.8499999999999999, " 85%"), (1.0, "100%")]
)
def test_footer_volume_is_rounded_not_truncated(volume, shown):
    layout = ui.compute_layout(160, 30, show_art=False)
    playback = ui.PlaybackState(position_seconds=1.0, is_playing=True, volume=volume, mode="wave")
    frame = ui.Renderer().render_frame(
        layout, _track(), playback, np.zeros(layout.vis_w), np.zeros((64, 1), dtype=np.float32)
    )
    assert f"vol {shown}" in frame
