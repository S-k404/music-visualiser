"""Playlist/shuffle/repeat logic tests. Player.__init__ never touches
the filesystem or decodes audio, so these exercise the transport logic
directly without needing real tracks -- only _load_track()/next_track()'s
decode path (covered indirectly, with a stub engine) would need that.
"""

from __future__ import annotations

from types import SimpleNamespace

from ascii_visualizer.player import REPEAT_MODES, Player, build_playlist


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
