"""Tests for LRC synced-lyrics parsing, same-timestamp grouping (the
original/romanization/translation convention), and the current-group
lookup."""

from __future__ import annotations

from ascii_visualizer import lyrics


def test_parse_lrc_basic(tmp_path):
    lrc = tmp_path / "song.lrc"
    lrc.write_text(
        "[ar:Some Artist]\n"
        "[ti:Some Title]\n"
        "[00:00.43]Hontou ni utsukushii mono no tameni shinitainda\n"
        "[00:09.87]Datte ikiteiru dakede yogoreru kara\n"
        "[01:00.32]Samayoeru mama yuke\n",
        encoding="utf-8",
    )

    groups = lyrics.parse_lrc(str(lrc))

    assert len(groups) == 3
    assert groups[0].time_seconds == 0.43
    assert groups[0].texts == ["Hontou ni utsukushii mono no tameni shinitainda"]
    assert groups[2].time_seconds == 60.32


def test_parse_lrc_multiple_time_tags_on_one_line(tmp_path):
    lrc = tmp_path / "song.lrc"
    lrc.write_text("[00:10.00][00:20.00]Repeated chorus line\n", encoding="utf-8")

    groups = lyrics.parse_lrc(str(lrc))

    assert [g.time_seconds for g in groups] == [10.0, 20.0]
    assert all(g.texts == ["Repeated chorus line"] for g in groups)


def test_parse_lrc_groups_same_timestamp_lines_in_file_order(tmp_path):
    """The real-world case that prompted this: a separate lyrics tool in
    the user's workflow writes 3 consecutive lines at the identical
    timestamp -- original, romanization, translation. These must group
    into one LyricGroup, in that order, not scroll past as 3 unrelated
    lines."""
    lrc = tmp_path / "song.lrc"
    lrc.write_text(
        "[ti:Odo]\n"
        "[ar:Ado]\n"
        '[00:00.01]半端なら KO ふわふわ したいならどうぞ\n'
        "[00:00.01]Hanpa nara Ko fuwafuwa shitainara douzo\n"
        "[00:00.01]If you want something odd, go for KO.\n"
        "[00:06.29]second original\n"
        "[00:06.29]second romanization\n"
        "[00:06.29]second translation\n",
        encoding="utf-8",
    )

    groups = lyrics.parse_lrc(str(lrc))

    assert len(groups) == 2
    assert groups[0].texts == [
        "半端なら KO ふわふわ したいならどうぞ",
        "Hanpa nara Ko fuwafuwa shitainara douzo",
        "If you want something odd, go for KO.",
    ]
    assert groups[1].texts == ["second original", "second romanization", "second translation"]


def test_parse_lrc_groups_within_rounding_tolerance(tmp_path):
    lrc = tmp_path / "song.lrc"
    lrc.write_text("[00:10.00]original\n[00:10.01]romanization\n", encoding="utf-8")

    groups = lyrics.parse_lrc(str(lrc))

    assert len(groups) == 1
    assert groups[0].texts == ["original", "romanization"]


def test_parse_lrc_missing_file_returns_empty():
    assert lyrics.parse_lrc("/no/such/file.lrc") == []


def test_find_lrc_path_matches_sibling_file(tmp_path):
    audio = tmp_path / "01 Track.flac"
    audio.write_bytes(b"not real audio")
    lrc = tmp_path / "01 Track.lrc"
    lrc.write_text("[00:00.00]hi\n", encoding="utf-8")

    assert lyrics.find_lrc_path(str(audio)) == str(lrc)


def test_find_lrc_path_returns_none_when_absent(tmp_path):
    audio = tmp_path / "01 Track.flac"
    audio.write_bytes(b"not real audio")

    assert lyrics.find_lrc_path(str(audio)) is None


def test_load_lyrics_for_end_to_end(tmp_path):
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"not real audio")
    (tmp_path / "song.lrc").write_text("[00:05.00]hello\n[00:10.00]world\n", encoding="utf-8")

    result = lyrics.load_lyrics_for(str(audio))

    assert [g.texts[0] for g in result] == ["hello", "world"]


def test_current_group_index():
    groups = [
        lyrics.LyricGroup(0.0, ["a"]),
        lyrics.LyricGroup(10.0, ["b"]),
        lyrics.LyricGroup(20.0, ["c"]),
    ]
    assert lyrics.current_group_index(groups, -1.0) == -1  # before first group
    assert lyrics.current_group_index(groups, 0.0) == 0
    assert lyrics.current_group_index(groups, 15.0) == 1
    assert lyrics.current_group_index(groups, 999.0) == 2


def test_current_group_index_empty_list():
    assert lyrics.current_group_index([], 5.0) == -1
