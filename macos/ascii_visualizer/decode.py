"""Decode arbitrary audio files into a normalised float32 numpy PCM
buffer.

Uses soundfile (libsndfile, in-process -- no subprocess spawn) for the
formats it supports (flac/wav/aiff/ogg), falling back to ffmpeg (via
pydub) for everything else (mp3/m4a/aac/wma/opus, or anything soundfile
fails to open, e.g. an .ogg that's actually Opus-coded). Measured on a
real ~20MB FLAC from the user's library: soundfile ~86ms vs ffmpeg
~70-110ms, with the added benefit of no subprocess spawn and no
dependency on ffmpeg being installed for the common case -- 4,024 of the
user's 4,168 real library files are FLAC.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass

import numpy as np
import soundfile as sf
from pydub import AudioSegment


class DecodeError(RuntimeError):
    pass


@dataclass
class DecodedAudio:
    samples: np.ndarray  # shape (n_frames, n_channels), float32 in [-1, 1]
    sample_rate: int
    channels: int
    duration_seconds: float
    bitrate_kbps: int | None = None


_SOUNDFILE_EXTENSIONS = {".flac", ".wav", ".aiff", ".aif", ".ogg", ".oga"}


def check_ffmpeg_available() -> None:
    if shutil.which("ffmpeg") is None:
        raise DecodeError(
            "ffmpeg was not found on PATH. Install it with:\n"
            "  brew install ffmpeg"
        )


def _decode_with_soundfile(path: str) -> DecodedAudio:
    samples, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    duration = samples.shape[0] / float(sample_rate)
    return DecodedAudio(
        samples=samples,
        sample_rate=sample_rate,
        channels=samples.shape[1],
        duration_seconds=duration,
    )


def _decode_with_ffmpeg(path: str) -> DecodedAudio:
    check_ffmpeg_available()
    try:
        segment = AudioSegment.from_file(path)
    except Exception as exc:
        # pydub raises assorted/bare exception types depending on how the
        # ffmpeg subprocess fails; all mean "could not decode this file"
        raise DecodeError(f"Could not decode '{path}': {exc}") from exc

    channels = segment.channels
    sample_rate = segment.frame_rate
    raw = np.array(segment.get_array_of_samples())

    # Normalise integer PCM -> float32 [-1, 1] based on sample width.
    max_val = float(1 << (8 * segment.sample_width - 1))
    samples = raw.astype(np.float32) / max_val

    if channels > 1:
        samples = samples.reshape((-1, channels))
    else:
        samples = samples.reshape((-1, 1))

    duration = samples.shape[0] / float(sample_rate)

    return DecodedAudio(
        samples=samples,
        sample_rate=sample_rate,
        channels=channels,
        duration_seconds=duration,
    )


def decode_file(path: str) -> DecodedAudio:
    """Fully decode an audio file to PCM. Simple and robust; the files this
    app targets (individual songs) are small enough to hold in memory."""
    ext = os.path.splitext(path)[1].lower()
    if ext in _SOUNDFILE_EXTENSIONS:
        try:
            return _decode_with_soundfile(path)
        except Exception:  # noqa: BLE001, S110 -- soundfile raises assorted
            # errors for anything it can't open (e.g. Opus-coded .ogg,
            # which libsndfile doesn't support); fall back to ffmpeg,
            # which can handle it, rather than failing the whole track
            pass
    return _decode_with_ffmpeg(path)
