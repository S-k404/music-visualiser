"""End-to-end pipeline tests: decode -> tags/artwork -> pixel art ->
spectrum/waveform -> full frame render. Generates its own tagged MP3/FLAC
fixtures via ffmpeg + mutagen so the suite has no external file
dependency, then exercises exactly the path a real track goes through.
"""

from __future__ import annotations

import io
import shutil
import subprocess

import numpy as np
import pytest
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, TALB, TIT2, TPE1, ID3NoHeaderError
from PIL import Image

from ascii_visualizer import decode, image_art, ui, visualizers
from ascii_visualizer import tags as tagsmod
from ascii_visualizer.lyrics import LyricGroup

FFMPEG = shutil.which("ffmpeg")
pytestmark = pytest.mark.skipif(
    FFMPEG is None, reason="ffmpeg is required to synthesize test audio"
)


def _make_cover_png() -> bytes:
    img = Image.new("RGB", (64, 64), (30, 140, 210))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(scope="module")
def cover_png() -> bytes:
    return _make_cover_png()


@pytest.fixture(scope="module")
def tagged_mp3(tmp_path_factory, cover_png) -> str:
    path = str(tmp_path_factory.mktemp("audio") / "test.mp3")
    subprocess.run(
        [
            FFMPEG, "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=44100:duration=2",
            "-codec:a", "libmp3lame", path,
        ],
        check=True,
    )
    try:
        id3 = ID3(path)
    except ID3NoHeaderError:
        id3 = ID3()
    id3.add(TIT2(encoding=3, text="Test Track"))
    id3.add(TPE1(encoding=3, text="Test Artist"))
    id3.add(TALB(encoding=3, text="Test Album"))
    id3.add(APIC(encoding=3, mime="image/png", type=3, desc="cover", data=cover_png))
    id3.save(path)
    return path


@pytest.fixture(scope="module")
def tagged_flac(tmp_path_factory, cover_png) -> str:
    path = str(tmp_path_factory.mktemp("audio") / "test.flac")
    subprocess.run(
        [
            FFMPEG, "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=44100:duration=2",
            "-codec:a", "flac", path,
        ],
        check=True,
    )
    flac = FLAC(path)
    flac["title"] = "Test Track FLAC"
    flac["artist"] = "Test Artist FLAC"
    flac["album"] = "Test Album FLAC"
    pic = Picture()
    pic.data = cover_png
    pic.type = 3
    pic.mime = "image/png"
    flac.add_picture(pic)
    flac.save()
    return path


@pytest.mark.parametrize("fixture_name", ["tagged_mp3", "tagged_flac"])
def test_decode_tags_artwork_roundtrip(fixture_name, request):
    path = request.getfixturevalue(fixture_name)

    audio = decode.decode_file(path)
    assert audio.samples.ndim == 2
    assert audio.sample_rate == 44100
    assert abs(audio.duration_seconds - 2.0) < 0.2

    track = tagsmod.read_tags(path)
    assert "Test" in track.title
    assert "Test Artist" in track.artist
    assert "Test Album" in track.album
    assert track.artwork and len(track.artwork) > 100

    rows = image_art.render_album_art(track.artwork, 24, 12)
    assert len(rows) == 12
    assert all("\x1b[" in row for row in rows)


def test_image_art_placeholder_when_no_artwork():
    rows = image_art.render_album_art(None, 24, 12)
    assert len(rows) == 12
    assert all("\x1b[" in row for row in rows)


def test_spectrum_analyzer_output_is_bounded():
    sample_rate = 44100
    t = np.linspace(0, 2048 / sample_rate, 2048, endpoint=False)
    tone = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32).reshape(-1, 1)

    analyzer = visualizers.SpectrumAnalyzer(n_bars=40, sample_rate=sample_rate)
    levels = analyzer.update(tone)

    assert levels.shape == (40,)
    assert (levels >= 0).all() and (levels <= 1).all()


def test_render_bars_shape():
    levels = np.linspace(0, 1, 16)
    lines = visualizers.render_bars(levels, rows=10)
    assert len(lines) == 10


def test_render_waveform_shape():
    samples = np.random.default_rng(0).uniform(-1, 1, size=(2205, 1)).astype(np.float32)
    lines = visualizers.render_waveform(samples, width_chars=30, height_chars=8)
    assert len(lines) == 8


def test_compute_layout_is_self_consistent():
    layout = ui.compute_layout(120, 40, mode="bars+wave", show_art=True)
    assert layout.art_w + layout.vis_w + 3 == layout.cols
    assert layout.bars_h + layout.wave_h == layout.body_h


def test_full_frame_render_contains_track_info(tagged_mp3):
    track = tagsmod.read_tags(tagged_mp3)
    audio = decode.decode_file(tagged_mp3)

    layout = ui.compute_layout(120, 40, mode="bars+wave", show_art=True)
    track_info = ui.TrackInfo(
        title=track.title, artist=track.artist, album=track.album, date=track.date,
        genre=track.genre, format=track.format, sample_rate=track.sample_rate,
        channels=track.channels, bitrate_kbps=track.bitrate_kbps,
        duration_seconds=audio.duration_seconds, artwork=track.artwork,
        track_index=1, track_count=1,
    )
    playback = ui.PlaybackState(position_seconds=1.0, is_playing=True, volume=0.8, mode="bars+wave")

    analyzer = visualizers.SpectrumAnalyzer(n_bars=layout.vis_w, sample_rate=audio.sample_rate)
    levels = analyzer.update(audio.samples[:2048])
    waveform_window = audio.samples[:2205]

    frame = ui.Renderer().render_frame(layout, track_info, playback, levels, waveform_window)
    frame_lines = frame.split("\n")

    assert len(frame_lines) == layout.rows
    assert track.title in frame
    assert track.artist in frame


def test_body_rows_survive_ansi_aware_truncation():
    """Regression test for a bug where the final per-line safety
    truncate counted ANSI escape bytes as visible characters. A single
    album-art row can carry ~1800 bytes of colour codes for only ~50
    visible pixels, so at a realistic terminal width that truncate cut
    every body line down to its first ~cols *raw* characters -- landing
    inside the art column's escape codes and silently discarding the
    separator and the entire spectrum/waveform panel every frame.
    """
    layout = ui.compute_layout(143, 58, mode="bars+wave", show_art=True)
    track_info = ui.TrackInfo(
        title="T", artist="A", album="Al", date="", genre="", format="FLAC",
        sample_rate=44100, channels=2, bitrate_kbps=1000, duration_seconds=180,
        artwork=None, track_index=1, track_count=1,
    )
    playback = ui.PlaybackState(
        position_seconds=17.0, is_playing=True, volume=1.0, mode="bars+wave"
    )

    levels = np.full(layout.vis_w, 0.8)
    waveform = np.sin(np.linspace(0, 20, 2205)).astype(np.float32).reshape(-1, 1)

    frame = ui.Renderer().render_frame(layout, track_info, playback, levels, waveform)
    body_row = frame.split("\n")[4 + layout.bars_h // 2]

    assert "│" in body_row, "separator between art and visualiser panel was truncated away"
    assert any(c in body_row for c in "▁▂▃▄▅▆▇█"), "spectrum bar content was truncated away"


def test_footer_status_text_survives_progress_bar_sizing():
    """Regression test: the progress-bar width was sized only against
    the timestamps, not the status/mode/shuffle/repeat text that follows
    it on the same line. Adding the shuffle/repeat labels pushed the
    whole line past `cols`, and the (correct, ANSI-aware) safety
    truncate then silently ate all of that trailing text every frame.
    """
    layout = ui.compute_layout(143, 58, mode="bars+wave", show_art=True)
    track_info = ui.TrackInfo(
        title="T", artist="A", album="Al", date="", genre="", format="FLAC",
        sample_rate=44100, channels=2, bitrate_kbps=1000, duration_seconds=180,
        artwork=None, track_index=1, track_count=1,
    )
    playback = ui.PlaybackState(
        position_seconds=17.0, is_playing=True, volume=1.0, mode="bars+wave",
        shuffle=True, repeat_mode="one",
    )
    levels = np.full(layout.vis_w, 0.8)
    waveform = np.zeros((2205, 1), dtype=np.float32)

    frame = ui.Renderer().render_frame(layout, track_info, playback, levels, waveform)

    assert "playing" in frame
    assert "shuffle:on" in frame
    assert "repeat:one" in frame


def test_lyrics_mode_shows_current_line_and_hides_visualiser():
    # show_art=False here: the album art panel legitimately reuses some
    # of the same block glyphs (e.g. "█") as the spectrum bars, so with
    # art on this test couldn't distinguish "bars rendered" from "art
    # happened to use the same character" -- the actual thing under test
    # is layout.show_bars/show_wave being forced off, not art.
    layout = ui.compute_layout(120, 40, mode="bars+wave", show_art=False, show_lyrics=True)
    assert layout.show_lyrics is True
    assert layout.show_bars is False
    assert layout.show_wave is False

    track_info = ui.TrackInfo(
        title="T", artist="A", album="Al", date="", genre="", format="FLAC",
        sample_rate=44100, channels=2, bitrate_kbps=1000, duration_seconds=180,
        artwork=None, track_index=1, track_count=1,
    )
    playback = ui.PlaybackState(
        position_seconds=12.0, is_playing=True, volume=1.0, mode="bars+wave"
    )
    groups = [
        LyricGroup(0.0, ["first line"]),
        LyricGroup(10.0, ["the current line"]),
        LyricGroup(20.0, ["a future line"]),
    ]

    frame = ui.Renderer().render_frame(
        layout, track_info, playback, np.zeros(layout.vis_w),
        np.zeros((2205, 1), dtype=np.float32), groups,
    )

    assert "the current line" in frame
    assert not any(c in frame for c in "▁▂▃▄▅▆▇█"), "bars should not render in lyrics mode"


def test_lyrics_mode_shows_all_layers_of_current_group():
    """Multi-layer .lrc convention (original/romanization/translation at
    the same timestamp, as produced by the user's separate lyrics tool):
    all layers of the *current* group should show together, not just
    the first one."""
    layout = ui.compute_layout(120, 40, show_art=False, show_lyrics=True)
    track_info = ui.TrackInfo(
        title="T", artist="A", album="Al", date="", genre="", format="FLAC",
        sample_rate=44100, channels=2, bitrate_kbps=1000, duration_seconds=180,
        artwork=None, track_index=1, track_count=1,
    )
    playback = ui.PlaybackState(
        position_seconds=10.0, is_playing=True, volume=1.0, mode="bars+wave"
    )
    groups = [
        LyricGroup(0.0, ["before"]),
        LyricGroup(10.0, ["original text", "romanized text", "translated text"]),
        LyricGroup(20.0, ["after"]),
    ]

    frame = ui.Renderer().render_frame(
        layout, track_info, playback, np.zeros(layout.vis_w),
        np.zeros((2205, 1), dtype=np.float32), groups,
    )

    assert "original text" in frame
    assert "romanized text" in frame
    assert "translated text" in frame


def test_lyrics_mode_with_no_lyrics_shows_fallback_message():
    layout = ui.compute_layout(120, 40, show_lyrics=True)
    track_info = ui.TrackInfo(
        title="T", artist="A", album="Al", date="", genre="", format="FLAC",
        sample_rate=44100, channels=2, bitrate_kbps=1000, duration_seconds=180,
        artwork=None, track_index=1, track_count=1,
    )
    playback = ui.PlaybackState(position_seconds=0.0, is_playing=True, volume=1.0, mode="bars+wave")

    frame = ui.Renderer().render_frame(
        layout, track_info, playback, np.zeros(layout.vis_w),
        np.zeros((2205, 1), dtype=np.float32), [],
    )

    assert "No synced lyrics found" in frame


def test_render_queue_marks_current_track_and_centres_window():
    names = [f"Track {i:02d}" for i in range(50)]

    rows = ui.render_queue(names, current_index=25, width=30, height=10)

    assert len(rows) == 10
    current_row = next(r for r in rows if "Track 25" in r)
    assert "▶" in current_row
    other_row = next(r for r in rows if "Track 24" in r)
    assert "▶" not in other_row
    # window should be roughly centred on the current track, not pinned
    # to the top or bottom of the full 50-item list
    visible_numbers = [int(r.split(".")[0].split()[-1]) for r in rows if r.strip()]
    assert min(visible_numbers) > 1
    assert max(visible_numbers) < 50


def test_render_queue_clamps_window_near_list_edges():
    names = [f"Track {i:02d}" for i in range(5)]

    rows = ui.render_queue(names, current_index=0, width=30, height=10)

    assert any("Track 00" in r for r in rows)
    assert len(rows) == 10  # padded with blank rows, not an error


def test_render_queue_empty_playlist_shows_message():
    rows = ui.render_queue([], current_index=0, width=30, height=5)
    assert any("Queue is empty" in r for r in rows)


def test_queue_mode_shows_in_full_frame_and_hides_visualiser():
    layout = ui.compute_layout(120, 40, show_art=False, show_queue=True)
    assert layout.show_queue is True
    assert layout.show_bars is False
    assert layout.show_wave is False

    track_info = ui.TrackInfo(
        title="T", artist="A", album="Al", date="", genre="", format="FLAC",
        sample_rate=44100, channels=2, bitrate_kbps=1000, duration_seconds=180,
        artwork=None, track_index=3, track_count=5,
    )
    playback = ui.PlaybackState(position_seconds=0.0, is_playing=True, volume=1.0, mode="bars+wave")
    names = [f"Song {i}" for i in range(1, 6)]

    frame = ui.Renderer().render_frame(
        layout, track_info, playback, np.zeros(layout.vis_w),
        np.zeros((2205, 1), dtype=np.float32), None, names,
    )

    assert "Song 3" in frame
