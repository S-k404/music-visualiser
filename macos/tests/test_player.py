"""Playlist/shuffle/repeat logic tests. Player.__init__ never touches
the filesystem or decodes audio, so these exercise the transport logic
directly without needing real tracks -- only _load_track()/next_track()'s
decode path (covered indirectly, with a stub engine) would need that.
"""

from __future__ import annotations

from types import SimpleNamespace

from ascii_visualizer.player import REPEAT_MODES, Player, _build_selector_entries, build_playlist


def test_shuffle_next_index_never_repeats_immediately():
    player = Player(["a.mp3", "b.mp3", "c.mp3"])
    player.shuffle = True
    player.index = 1
    for _ in range(50):
        nxt = player._pick_next_index()
        assert nxt != player.index
        assert 0 <= nxt < 3


def test_non_shuffle_next_index_wraps_around():
    player = Player(["a.mp3", "b.mp3", "c.mp3"])
    player.index = 2
    assert player._pick_next_index() == 0


def test_shuffle_single_track_playlist_returns_same_index():
    player = Player(["only.mp3"])
    player.shuffle = True
    assert player._pick_next_index() == 0


def test_repeat_mode_defaults_to_all_and_cycles():
    player = Player(["a.mp3"])
    assert player.repeat_mode == "all"
    player.repeat_index = (player.repeat_index + 1) % len(REPEAT_MODES)
    assert player.repeat_mode == "one"
    player.repeat_index = (player.repeat_index + 1) % len(REPEAT_MODES)
    assert player.repeat_mode == "off"
    player.repeat_index = (player.repeat_index + 1) % len(REPEAT_MODES)
    assert player.repeat_mode == "all"


class _StubEngine:
    """Just enough of AudioEngine's interface for _advance_on_track_end."""

    def __init__(self):
        self.playing = True
        self.restarted = False

    def restart(self):
        self.restarted = True

    def set_playing(self, value):
        self.playing = value

    def get_volume(self):
        return 1.0


def test_advance_on_track_end_repeat_one_restarts_same_track():
    player = Player(["a.mp3", "b.mp3"])
    player.index = 0
    player.repeat_index = REPEAT_MODES.index("one")
    player.engine = _StubEngine()

    player._advance_on_track_end()

    assert player.engine.restarted is True
    assert player.index == 0


def test_advance_on_track_end_repeat_off_stops_at_last_track():
    player = Player(["a.mp3", "b.mp3"])
    player.index = 1  # last track
    player.repeat_index = REPEAT_MODES.index("off")
    player.engine = _StubEngine()

    player._advance_on_track_end()

    assert player.engine.playing is False
    assert player.index == 1  # did not advance


def test_advance_on_track_end_repeat_all_advances_to_next(monkeypatch):
    player = Player(["a.mp3", "b.mp3"])
    player.index = 0
    player.repeat_index = REPEAT_MODES.index("all")
    player.engine = _StubEngine()

    calls = []
    monkeypatch.setattr(player, "next_track", lambda **kw: calls.append(kw))

    player._advance_on_track_end()

    assert calls == [{"user_initiated": False}]


def test_build_playlist_skips_appledouble_and_other_dotfiles(tmp_path):
    (tmp_path / "01 Real Track.flac").write_bytes(b"not real audio but a real filename")
    (tmp_path / "._01 Real Track.flac").write_bytes(b"appledouble resource fork junk")
    (tmp_path / ".DS_Store").write_bytes(b"junk")
    (tmp_path / "02 Another Track.mp3").write_bytes(b"also not real audio")

    playlist = build_playlist(str(tmp_path))

    names = {p.rsplit("/", 1)[-1] for p in playlist}
    assert names == {"01 Real Track.flac", "02 Another Track.mp3"}


def test_advance_with_retry_skips_unplayable_tracks(monkeypatch):
    player = Player(["bad_a.mp3", "bad_b.mp3", "good.mp3", "d.mp3"])

    def fake_load_track(self, index, resume_volume=1.0):
        if self.playlist[index] in ("bad_a.mp3", "bad_b.mp3"):
            raise RuntimeError(f"simulated decode failure: {self.playlist[index]}")
        self.index = index
        self.tags = SimpleNamespace(title=self.playlist[index])

    monkeypatch.setattr(Player, "_load_track", fake_load_track)

    player.next_track(user_initiated=False)  # index starts at -1 -> tries 0, 1, lands on 2

    assert player.index == 2
    assert player.playlist[player.index] == "good.mp3"


def test_build_selector_entries_groups_by_parent_folder():
    playlist = [
        "/lib/Artist A/Album One/01 Song.flac",
        "/lib/Artist A/Album One/02 Song.flac",
        "/lib/Artist B/Album Two/01 Other.flac",
    ]

    entries = _build_selector_entries(playlist)

    kinds = [(e.kind, e.label) for e in entries]
    assert kinds == [
        ("album", "Album One"),
        ("track", "01 Song"),
        ("track", "02 Song"),
        ("album", "Album Two"),
        ("track", "01 Other"),
    ]
    track_entries = [e for e in entries if e.kind == "track"]
    assert [e.track_index for e in track_entries] == [0, 1, 2]


def test_selector_navigation_skips_album_headers():
    player = Player([
        "/lib/Album One/a.mp3",
        "/lib/Album One/b.mp3",
        "/lib/Album Two/c.mp3",
    ])
    player._selector_cursor = player._entry_index_for_track(0)

    player._move_selector(1)
    assert player._selector_entries[player._selector_cursor].track_index == 1

    player._move_selector(1)  # crosses the "Album Two" header
    assert player._selector_entries[player._selector_cursor].track_index == 2

    player._move_selector(1)  # already at the end -- stays put
    assert player._selector_entries[player._selector_cursor].track_index == 2

    player._move_selector(-5)  # clamps at the start, doesn't wrap negative
    assert player._selector_entries[player._selector_cursor].track_index == 0


def test_activate_selector_plays_the_track_under_the_cursor(monkeypatch):
    player = Player(["a.mp3", "b.mp3"])
    player.show_queue = True
    player._selector_cursor = player._entry_index_for_track(1)

    calls = []
    monkeypatch.setattr(player, "play_index", lambda idx: calls.append(idx))

    player._activate_selector()

    assert calls == [1]
    assert player.show_queue is False, "picking a track should close the selector"


def test_play_index_falls_through_to_next_track_when_unplayable(monkeypatch):
    player = Player(["bad.mp3", "good.mp3"])
    player.index = 0

    def fake_load_track(self, index, resume_volume=1.0):
        if self.playlist[index] == "bad.mp3":
            raise RuntimeError("simulated decode failure")
        self.index = index
        self.tags = SimpleNamespace(title=self.playlist[index])

    monkeypatch.setattr(Player, "_load_track", fake_load_track)

    player.play_index(0)

    assert player.index == 1
    assert player.playlist[player.index] == "good.mp3"


def test_advance_with_retry_raises_once_when_everything_fails(monkeypatch):
    player = Player(["bad_a.mp3", "bad_b.mp3"])

    def always_fails(self, index, resume_volume=1.0):
        raise RuntimeError(f"simulated decode failure: {self.playlist[index]}")

    monkeypatch.setattr(Player, "_load_track", always_fails)

    try:
        player.next_track(user_initiated=False)
        raised = False
    except RuntimeError:
        raised = True

    assert raised, "an all-unplayable playlist should raise, not loop forever"
