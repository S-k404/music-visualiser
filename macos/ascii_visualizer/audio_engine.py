"""Playback of a fully-decoded in-memory PCM buffer via PortAudio
(sounddevice), with thread-safe transport controls and a window accessor
used by the visualiser to read the currently-audible samples."""

from __future__ import annotations

import threading

import numpy as np
import sounddevice as sd

from .decode import DecodedAudio


class AudioEngine:
    def __init__(self, audio: DecodedAudio, initial_volume: float = 1.0):
        self.audio = audio
        self.sample_rate = audio.sample_rate
        self.channels = audio.channels

        self._samples = audio.samples
        self._n_frames = self._samples.shape[0]
        self._position = 0
        self._lock = threading.Lock()
        self._playing = True
        self._volume = initial_volume
        self._reached_end = threading.Event()

        self._stream = sd.OutputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype="float32",
            callback=self._callback,
            blocksize=1024,
        )

    def start(self) -> None:
        self._stream.start()

    def close(self) -> None:
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:  # noqa: BLE001, S110 -- best-effort shutdown; a
            # PortAudio error here shouldn't block terminal cleanup on exit
            pass

    def _callback(self, outdata, frames, time_info, status):
        # Read the position and advance it in one critical section. Doing it
        # as read -> release -> write-back (as this used to) let a seek or
        # restart from the UI thread land in the gap and then be silently
        # overwritten with the stale `pos + frames`.
        with self._lock:
            playing = self._playing
            volume = self._volume
            pos = self._position
            if playing:
                self._position = min(pos + frames, self._n_frames)

        if not playing:
            outdata.fill(0)
            return

        chunk = self._samples[pos:pos + frames]  # read-only after construction
        n = chunk.shape[0]
        if n < frames:
            outdata[:n] = chunk * volume
            outdata[n:] = 0
            self._reached_end.set()
        else:
            outdata[:] = chunk * volume

    def toggle_pause(self) -> None:
        with self._lock:
            self._playing = not self._playing

    def set_playing(self, value: bool) -> None:
        with self._lock:
            self._playing = value

    def is_playing(self) -> bool:
        with self._lock:
            return self._playing

    def change_volume(self, delta: float) -> None:
        with self._lock:
            # Round off float drift: 1.0 - 0.05*3 is 0.8499999..., which the
            # UI used to truncate to "84%" after three presses of volume-down.
            self._volume = round(max(0.0, min(2.0, self._volume + delta)), 2)

    def get_volume(self) -> float:
        with self._lock:
            return self._volume

    def seek_seconds(self, delta: float) -> None:
        with self._lock:
            new_pos = self._position + int(delta * self.sample_rate)
            self._position = max(0, min(new_pos, self._n_frames - 1))
            if new_pos < self._n_frames:
                self._reached_end.clear()

    def restart(self) -> None:
        """Jump back to the start of the track (used for repeat-one)."""
        with self._lock:
            self._position = 0
            self._reached_end.clear()

    def position_seconds(self) -> float:
        with self._lock:
            return self._position / float(self.sample_rate)

    def is_finished(self) -> bool:
        with self._lock:
            pos = self._position
        return self._reached_end.is_set() and pos >= self._n_frames - 1

    def get_recent_window(self, n_samples: int) -> np.ndarray:
        with self._lock:
            pos = self._position
        start = max(0, pos - n_samples)
        window = self._samples[start:pos]
        if window.shape[0] < n_samples:
            pad = np.zeros((n_samples - window.shape[0], self.channels), dtype=np.float32)
            window = np.concatenate([pad, window], axis=0)
        return window
