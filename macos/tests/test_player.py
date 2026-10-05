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
        self.volume_changes: list[float] = []

    def restart(self):
        self.restarted = True

    def set_playing(self, value):
        self.playing = value

    def get_volume(self):
        return 1.0

    def change_volume(self, delta):
        self.volume_changes.append(delta)


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


# -- queue view navigation ---------------------------------------------------


def _queue_player(n=30, index=5):
    player = Player([f"{i:02d}.mp3" for i in range(n)])
    player.index = index
    player.engine = _StubEngine()
    player.show_queue = True
    return player


def test_queue_cursor_starts_from_playing_track_and_moves_by_one():
    player = _queue_player(index=5)
    assert player.queue_cursor is None  # following the playing track

    player._handle_key("down")
    assert player.queue_cursor == 6
    player._handle_key("up")
    player._handle_key("up")
    assert player.queue_cursor == 4


def test_queue_cursor_clamps_at_both_ends():
    player = _queue_player(n=10, index=0)
    player._handle_key("up")
    assert player.queue_cursor == 0
    player._handle_key("end")
    assert player.queue_cursor == 9
    player._handle_key("down")
    assert player.queue_cursor == 9
    player._handle_key("home")
    assert player.queue_cursor == 0


def test_queue_page_keys_move_by_a_page_and_clamp():
    player = _queue_player(n=100, index=50)
    player._queue_page = 12

    player._handle_key("pagedown")
    assert player.queue_cursor == 62
    player._handle_key("pageup")
    player._handle_key("pageup")
    assert player.queue_cursor == 38
    for _ in range(10):
        player._handle_key("pageup")
    assert player.queue_cursor == 0


def test_queue_arrows_move_cursor_instead_of_changing_volume():
    player = _queue_player()
    player._handle_key("up")
    player._handle_key("down")
    assert player.engine.volume_changes == []


def test_volume_keys_still_work_inside_queue_view():
    player = _queue_player()
    player._handle_key("+")
    player._handle_key("-")
    assert player.engine.volume_changes == [0.05, -0.05]


def test_arrows_change_volume_when_queue_is_closed():
    player = _queue_player()
    player.show_queue = False
    player._handle_key("up")
    player._handle_key("down")
    assert player.engine.volume_changes == [0.05, -0.05]
    assert player.queue_cursor is None


def test_enter_plays_the_selected_track_and_resumes_following(monkeypatch):
    player = _queue_player(index=5)
    jumped = []
    monkeypatch.setattr(player, "jump_to", jumped.append)

    for _ in range(3):
        player._handle_key("down")
    player._handle_key("enter")

    assert jumped == [8]
    assert player.queue_cursor is None


def test_enter_without_browsing_does_nothing(monkeypatch):
    player = _queue_player()
    jumped = []
    monkeypatch.setattr(player, "jump_to", jumped.append)
    player._handle_key("enter")
    assert jumped == []


def test_toggling_queue_resets_the_cursor():
    player = _queue_player()
    player._handle_key("down")
    assert player.queue_cursor is not None
    player._handle_key("t")  # close
    assert player.queue_cursor is None
    player._handle_key("t")  # reopen
    assert player.queue_cursor is None


def test_navigation_keys_are_inert_when_queue_is_closed():
    player = _queue_player()
    player.show_queue = False
    for key in ("pagedown", "pageup", "home", "end", "enter"):
        assert player._handle_key(key) is False
    assert player.queue_cursor is None  # navigation only acts inside the queue view


def test_unknown_keys_are_ignored_and_never_quit():
    player = _queue_player()
    assert player._handle_key("unknown") is False
    assert player._handle_key("enter") is False


def test_jump_to_plays_target_and_records_history(monkeypatch):
    player = Player([f"{i}.mp3" for i in range(10)])
    player.index = 2
    loaded = []

    def fake_load_track(self, index, resume_volume=1.0):
        loaded.append(index)
        self.index = index
        self.tags = SimpleNamespace(title=str(index))

    monkeypatch.setattr(Player, "_load_track", fake_load_track)

    player.jump_to(7)

    assert loaded == [7]
    assert player.index == 7
    assert player._history == [2]  # 'p' can step back to where we were


def test_jump_to_unplayable_target_carries_on_forward_not_retrying_it(monkeypatch):
    player = Player([f"{i}.mp3" for i in range(6)])
    player.index = 0
    attempts = []

    def fake_load_track(self, index, resume_volume=1.0):
        attempts.append(index)
        if index in (3, 4):
            raise RuntimeError("corrupt")
        self.index = index
        self.tags = SimpleNamespace(title=str(index))

    monkeypatch.setattr(Player, "_load_track", fake_load_track)

    player.jump_to(3)

    assert attempts == [3, 4, 5]  # each bad file tried once, then moved on
    assert player.index == 5
