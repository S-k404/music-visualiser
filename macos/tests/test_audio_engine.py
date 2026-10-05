"""AudioEngine transport tests. The PortAudio stream is stubbed out and the
audio callback is driven by hand, so no sound device is needed."""

from __future__ import annotations

import numpy as np
import pytest

from ascii_visualizer import audio_engine
from ascii_visualizer.decode import DecodedAudio

SAMPLE_RATE = 1000  # tiny rate so "seconds" are cheap to reason about
BLOCK = 100


class _StubStream:
    def __init__(self, **_kwargs):
        pass

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass


@pytest.fixture
def make_engine(monkeypatch):
    monkeypatch.setattr(audio_engine.sd, "OutputStream", _StubStream)

    def make(n_frames: int = 10_000, volume: float = 1.0) -> audio_engine.AudioEngine:
        # Each frame's value is its own index / n, so output reveals exactly
        # which slice of the track the callback read.
        ramp = (np.arange(n_frames, dtype=np.float32) / n_frames).reshape(-1, 1)
        audio = DecodedAudio(
            samples=ramp,
            sample_rate=SAMPLE_RATE,
            channels=1,
            duration_seconds=n_frames / SAMPLE_RATE,
        )
        return audio_engine.AudioEngine(audio, initial_volume=volume)

    return make


def _pull(engine: audio_engine.AudioEngine, frames: int = BLOCK) -> np.ndarray:
    out = np.zeros((frames, 1), dtype=np.float32)
    engine._callback(out, frames, None, None)
    return out


def test_callback_streams_consecutive_blocks_and_advances_position(make_engine):
    engine = make_engine()
    first, second = _pull(engine), _pull(engine)

    assert engine._position == 2 * BLOCK
    np.testing.assert_allclose(first[:, 0], engine._samples[0:BLOCK, 0])
    np.testing.assert_allclose(second[:, 0], engine._samples[BLOCK : 2 * BLOCK, 0])


def test_callback_applies_volume(make_engine):
    engine = make_engine(volume=0.5)
    out = _pull(engine)
    np.testing.assert_allclose(out[:, 0], engine._samples[:BLOCK, 0] * 0.5)


def test_paused_outputs_silence_and_does_not_advance(make_engine):
    engine = make_engine()
    _pull(engine)
    engine.toggle_pause()

    out = _pull(engine)

    assert not out.any()
    assert engine._position == BLOCK


def test_end_of_track_zero_pads_and_flags_finished(make_engine):
    engine = make_engine(n_frames=150)
    _pull(engine)  # frames 0-99
    out = _pull(engine)  # only 50 frames left

    np.testing.assert_allclose(out[:50, 0], engine._samples[100:150, 0])
    assert not out[50:].any()
    assert engine.is_finished()


def test_seek_moves_position_and_clamps_to_track_bounds(make_engine):
    engine = make_engine(n_frames=10_000)
    engine.seek_seconds(2)
    assert engine._position == 2 * SAMPLE_RATE
    engine.seek_seconds(-999)
    assert engine._position == 0
    engine.seek_seconds(999)
    assert engine._position == engine._n_frames - 1


def test_restart_rewinds_and_clears_finished(make_engine):
    engine = make_engine(n_frames=150)
    _pull(engine)
    _pull(engine)
    assert engine.is_finished()

    engine.restart()

    assert engine._position == 0
    assert not engine.is_finished()


class _ActAfterFirstRelease:
    """Lock wrapper that runs `action` right after the first time the lock
    is released -- i.e. at the moment another thread could slip in."""

    def __init__(self, inner, action):
        self._inner = inner
        self._action = action

    def __enter__(self):
        return self._inner.__enter__()

    def __exit__(self, *exc):
        result = self._inner.__exit__(*exc)
        if self._action is not None:
            action, self._action = self._action, None
            action()
        return result


def test_seek_during_callback_is_not_overwritten(make_engine):
    """Regression test: the callback used to read the position, release the
    lock, then write back `pos + frames` -- so a seek (or restart) landing
    between the two was silently discarded and playback carried on from the
    old place. The seek must apply on top of the callback's advance."""
    engine = make_engine(n_frames=10_000)
    _pull(engine)  # position = BLOCK
    seek = 3  # seconds -> 3000 frames

    engine._lock = _ActAfterFirstRelease(engine._lock, lambda: engine.seek_seconds(seek))
    _pull(engine)

    assert engine._position == BLOCK + BLOCK + seek * SAMPLE_RATE


def test_volume_steps_do_not_drift(make_engine):
    # Repeated 0.05 steps used to accumulate float error (1.0 - 3*0.05 ==
    # 0.8499999...), which the UI then truncated to "84%".
    engine = make_engine()
    for _ in range(3):
        engine.change_volume(-0.05)
    assert engine.get_volume() == 0.85
    for _ in range(3):
        engine.change_volume(0.05)
    assert engine.get_volume() == 1.0


def test_volume_is_clamped_to_range(make_engine):
    engine = make_engine()
    for _ in range(100):
        engine.change_volume(0.05)
    assert engine.get_volume() == 2.0
    for _ in range(100):
        engine.change_volume(-0.05)
    assert engine.get_volume() == 0.0
