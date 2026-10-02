"""Playlist + main loop: loads tracks, drives the ~30fps render loop,
and dispatches keyboard transport controls."""

from __future__ import annotations

import logging
import os
import random
import sys
import time

from . import ansi, ui
from .audio_engine import AudioEngine
from .decode import decode_file
from .keyboard import Keyboard
from .lyrics import LyricGroup, load_lyrics_for
from .tags import TrackTags, read_tags
from .visualizers import SpectrumAnalyzer

SUPPORTED_EXTENSIONS = {
    ".mp3", ".m4a", ".mp4", ".flac", ".wav", ".aiff", ".aif",
    ".ogg", ".oga", ".opus", ".aac", ".wma",
}

TARGET_FPS = 30
WAVEFORM_WINDOW_SECONDS = 0.05
SPECTRUM_WINDOW_SAMPLES = 2048
MODES = ["bars+wave", "bars", "wave"]
REPEAT_MODES = ["off", "all", "one"]  # "all" is the default -- loop the playlist


def _live_terminal_size(fallback: tuple[int, int] = (100, 32)) -> tuple[int, int]:
    """Query the real, current pty size via the TIOCGWINSZ ioctl.

    Deliberately not `shutil.get_terminal_size()`: that checks the
    COLUMNS/LINES environment variables first, and falls back to the
    ioctl only if they're unset. Some shell setups export a stale
    COLUMNS that never updates on resize, which silently wrecks the
    layout for a redrawing-every-frame TUI like this one -- we always
    want the live value, never a cached env var.
    """
    try:
        size = os.get_terminal_size(sys.stdout.fileno())
        return size.columns, size.lines
    except OSError:
        return fallback


def build_playlist(path: str) -> list[str]:
    if os.path.isfile(path):
        return [path]
    if not os.path.isdir(path):
        raise FileNotFoundError(f"No such file or directory: {path}")
    files = []
    for root, _dirs, names in os.walk(path):
        for name in names:
            # Skip dotfiles -- in particular AppleDouble resource-fork
            # sidecars ("._Song.flac") that exFAT/non-HFS+ volumes litter
            # next to real files. They carry a real audio extension but
            # are ~4KB of metadata, not audio, and fail to decode.
            if name.startswith("."):
                continue
            if os.path.splitext(name)[1].lower() in SUPPORTED_EXTENSIONS:
                files.append(os.path.join(root, name))
    files.sort()
    if not files:
        raise FileNotFoundError(f"No supported audio files found under: {path}")
    return files


class Player:
    def __init__(self, playlist: list[str], logger: logging.Logger | None = None):
        self.playlist = playlist
        self.index = -1  # so the first next_track() call tries index 0
        self.mode_index = 0
        self.show_art = True
        self.show_lyrics = False
        self.show_queue = False
        self.shuffle = False
        self.repeat_index = 1  # "all"
        self.renderer = ui.Renderer()
        self.engine: AudioEngine | None = None
        self.tags: TrackTags | None = None
        self.analyzer: SpectrumAnalyzer | None = None
        self.lyrics: list[LyricGroup] = []
        self._last_vis_w = -1
        self._history: list[int] = []
        self.logger = logger or logging.getLogger("musicvis")
        self._queue_names = [os.path.splitext(os.path.basename(p))[0] for p in playlist]

    @property
    def mode(self) -> str:
        return MODES[self.mode_index]

    @property
    def repeat_mode(self) -> str:
        return REPEAT_MODES[self.repeat_index]

    def _load_track(self, index: int, resume_volume: float = 1.0) -> None:
        if self.engine is not None:
            self.engine.close()

        path = self.playlist[index]
        audio = decode_file(path)
        self.tags = read_tags(path)
        self.lyrics = load_lyrics_for(path)
        self.engine = AudioEngine(audio, initial_volume=resume_volume)
        self.engine.start()
        self.analyzer = None
        self._last_vis_w = -1
        self.index = index

    def _ensure_analyzer(self, vis_w: int) -> None:
        assert self.engine is not None
        if self.analyzer is None:
            self.analyzer = SpectrumAnalyzer(max(vis_w, 1), self.engine.sample_rate)
            self._last_vis_w = vis_w
        elif vis_w != self._last_vis_w:
            self.analyzer.resize(max(vis_w, 1))
            self._last_vis_w = vis_w

    def _pick_next_index(self) -> int:
        if self.shuffle and len(self.playlist) > 1:
            choices = [i for i in range(len(self.playlist)) if i != self.index]
            return random.choice(choices)  # noqa: S311 -- playlist shuffle, not crypto
        return (self.index + 1) % len(self.playlist)

    def _advance_with_retry(self, pick_index, volume: float) -> None:
        """Try candidates from `pick_index()` until one decodes, skipping
        unplayable files (e.g. corrupt downloads, or anything else that
        slips past build_playlist's filters) instead of crashing the
        whole session. Bounded to the playlist size so an all-bad
        playlist fails once, cleanly, rather than looping forever.

        Deliberately silent on *screen* for each skip (writes nothing to
        stdout/stderr, which would risk a one-frame glitch in the live
        alt-screen display -- the track title changing is signal enough
        there); every skip is still recorded to the log file so it's
        visible after the fact, not just inferred from a gap in a
        shuffled session.
        """
        last_error: Exception | None = None
        for _ in range(len(self.playlist)):
            idx = pick_index()
            try:
                self._load_track(idx, resume_volume=volume)
                self.logger.info(
                    "loaded track %d/%d: %s", idx + 1, len(self.playlist), self.tags.title
                )
                return
            except Exception as exc:  # noqa: BLE001 -- "one bad file must
                # never take down the session" covers decode failures,
                # tag-read failures, and AudioEngine/PortAudio stream
                # construction failures for a malformed file alike, not
                # just the one exception type seen in practice so far
                last_error = exc
                self.logger.warning("skipping unplayable track: %s (%s)", self.playlist[idx], exc)
                self.index = idx  # so the next pick_index() call advances past it
        self.logger.error("no playable track found in playlist of %d", len(self.playlist))
        raise last_error or RuntimeError("No playable audio files in the playlist.")

    def next_track(self, *, user_initiated: bool = True) -> None:
        if user_initiated:
            self._history.append(self.index)
        volume = self.engine.get_volume() if self.engine else 1.0
        self._advance_with_retry(self._pick_next_index, volume)

    def prev_track(self) -> None:
        volume = self.engine.get_volume() if self.engine else 1.0

        def pick() -> int:
            return self._history.pop() if self._history else (self.index - 1) % len(self.playlist)

        self._advance_with_retry(pick, volume)

    def run(self) -> None:
        print(f"Loading {os.path.basename(self.playlist[0])} ...", flush=True)
        self.next_track(user_initiated=False)
        sys.stdout.write(ansi.ALT_SCREEN_ON + ansi.HIDE_CURSOR)
        sys.stdout.flush()
        try:
            with Keyboard() as kb:
                frame_time = 1.0 / TARGET_FPS
                while True:
                    start = time.monotonic()
                    try:
                        key = kb.read_key()
                        if key is not None and self._handle_key(key):
                            break
                        if self.engine.is_finished():
                            self._advance_on_track_end()
                        self._render_once()
                    except KeyboardInterrupt:
                        break

                    elapsed = time.monotonic() - start
                    remaining = frame_time - elapsed
                    if remaining > 0:
                        time.sleep(remaining)
        finally:
            if self.engine is not None:
                self.engine.close()
            sys.stdout.write(ansi.SHOW_CURSOR + ansi.ALT_SCREEN_OFF)
            sys.stdout.flush()

    def _advance_on_track_end(self) -> None:
        """Called when the current track finishes playing on its own
        (not via n/p) -- respects repeat mode rather than always
        advancing."""
        assert self.engine is not None
        at_last_track = self.index == len(self.playlist) - 1
        if self.repeat_mode == "one":
            self.engine.restart()
        elif self.repeat_mode == "off" and not self.shuffle and at_last_track:
            self.engine.set_playing(False)  # stay on the last track, paused
        else:
            self.next_track(user_initiated=False)

    def _handle_key(self, key: str) -> bool:
        assert self.engine is not None
        if key in ("q", "Q", "esc"):
            self.logger.info("quit requested by user")
            return True
        if key == " ":
            self.engine.toggle_pause()
        elif key in ("n", "N"):
            self.next_track()
        elif key in ("p", "P"):
            self.prev_track()
        elif key == "right":
            self.engine.seek_seconds(5)
        elif key == "left":
            self.engine.seek_seconds(-5)
        elif key == "up":
            self.engine.change_volume(0.05)
        elif key == "down":
            self.engine.change_volume(-0.05)
        elif key in ("m", "M"):
            self.mode_index = (self.mode_index + 1) % len(MODES)
        elif key in ("s", "S"):
            self.shuffle = not self.shuffle
            self.logger.info("shuffle: %s", "on" if self.shuffle else "off")
        elif key in ("r", "R"):
            self.repeat_index = (self.repeat_index + 1) % len(REPEAT_MODES)
            self.logger.info("repeat: %s", self.repeat_mode)
        elif key in ("a", "A"):
            self.show_art = not self.show_art
        elif key in ("l", "L"):
            self.show_lyrics = not self.show_lyrics
            if self.show_lyrics:
                self.show_queue = False
                if not self.lyrics:
                    self.logger.info("lyrics toggled on, but no .lrc file found for this track")
        elif key in ("t", "T"):
            self.show_queue = not self.show_queue
            if self.show_queue:
                self.show_lyrics = False
        return False

    def _render_once(self) -> None:
        assert self.engine is not None and self.tags is not None
        cols, rows = _live_terminal_size()
        layout = ui.compute_layout(
            cols, rows, mode=self.mode, show_art=self.show_art,
            show_lyrics=self.show_lyrics, show_queue=self.show_queue,
        )
        self._ensure_analyzer(layout.vis_w)

        window_n = max(int(self.engine.sample_rate * WAVEFORM_WINDOW_SECONDS), 64)
        waveform_samples = self.engine.get_recent_window(window_n)
        spectrum_samples = self.engine.get_recent_window(SPECTRUM_WINDOW_SAMPLES)
        levels = self.analyzer.update(spectrum_samples)

        track_info = ui.TrackInfo(
            title=self.tags.title,
            artist=self.tags.artist,
            album=self.tags.album,
            date=self.tags.date,
            genre=self.tags.genre,
            format=self.tags.format,
            sample_rate=self.tags.sample_rate or self.engine.sample_rate,
            channels=self.tags.channels or self.engine.channels,
            bitrate_kbps=self.tags.bitrate_kbps,
            duration_seconds=self.engine.audio.duration_seconds,
            artwork=self.tags.artwork,
            track_index=self.index + 1,
            track_count=len(self.playlist),
        )
        playback = ui.PlaybackState(
            position_seconds=self.engine.position_seconds(),
            is_playing=self.engine.is_playing(),
            volume=self.engine.get_volume(),
            mode=self.mode,
            shuffle=self.shuffle,
            repeat_mode=self.repeat_mode,
        )

        frame = self.renderer.render_frame(
            layout, track_info, playback, levels, waveform_samples, self.lyrics, self._queue_names
        )
        sys.stdout.write(ansi.HOME + frame)
        sys.stdout.flush()
