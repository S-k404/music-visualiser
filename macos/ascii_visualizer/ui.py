"""Frame composition: header (tags), body (album art + spectrum/waveform),
footer (transport + progress + keybindings). Built from raw strings for
speed -- one write() per frame keeps things flicker-free at 30fps."""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from . import ansi
from .image_art import render_album_art
from .lyrics import LyricGroup, current_group_index
from .visualizers import render_bars, render_waveform

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

HEADER_LINES = 4
FOOTER_LINES = 3


@dataclass
class TrackInfo:
    title: str
    artist: str
    album: str
    date: str
    genre: str
    format: str
    sample_rate: int
    channels: int
    bitrate_kbps: int
    duration_seconds: float
    artwork: bytes | None
    track_index: int
    track_count: int


@dataclass
class PlaybackState:
    position_seconds: float
    is_playing: bool
    volume: float
    mode: str
    shuffle: bool = False
    repeat_mode: str = "all"


@dataclass
class Layout:
    cols: int
    rows: int
    body_h: int
    art_w: int
    vis_w: int
    bars_h: int
    wave_h: int
    show_art: bool
    show_bars: bool
    show_wave: bool
    show_lyrics: bool = False
    show_queue: bool = False


def compute_layout(
    cols: int,
    rows: int,
    mode: str = "bars+wave",
    show_art: bool = True,
    show_lyrics: bool = False,
    show_queue: bool = False,
) -> Layout:
    cols = max(cols, 40)
    rows = max(rows, 12)
    body_h = max(rows - HEADER_LINES - FOOTER_LINES, 4)

    takeover = show_lyrics or show_queue
    show_bars = not takeover and mode in ("bars+wave", "bars")
    show_wave = not takeover and mode in ("bars+wave", "wave")

    if show_art:
        # body_h * 2 keeps the panel visually square (terminal cells are
        # roughly half as wide as they are tall); the other two just cap
        # how big that's allowed to get on a wide terminal.
        art_w = max(min(int(cols * 0.40), body_h * 2, 90), 10)
        vis_w = max(cols - art_w - 3, 10)
    else:
        art_w = 0
        vis_w = cols

    if takeover:
        bars_h, wave_h = 0, 0
    elif show_bars and show_wave:
        bars_h = max(int(body_h * 0.62), 3)
        wave_h = max(body_h - bars_h, 2)
    elif show_bars:
        bars_h, wave_h = body_h, 0
    else:
        bars_h, wave_h = 0, body_h

    return Layout(
        cols, rows, body_h, art_w, vis_w, bars_h, wave_h,
        show_art, show_bars, show_wave, show_lyrics, show_queue,
    )


def _truncate(s: str, width: int) -> str:
    """Truncate plain text (no ANSI codes) to `width` characters."""
    if width <= 0:
        return ""
    if len(s) <= width:
        return s
    if width <= 1:
        return s[:width]
    return s[: width - 1] + "…"


def _truncate_visible(s: str, width: int) -> str:
    """Truncate a fully-styled (ANSI-coloured) line to `width` *visible*
    characters, counting only non-escape-code characters toward the
    budget and never cutting inside an escape sequence.

    A plain len()-based truncate is wrong here: a single pixel-art row
    can carry thousands of bytes of colour codes for only a few dozen
    visible characters, so truncating on raw length chops the line far
    short of its actual on-screen width -- discarding everything after
    the first heavily-coloured segment (e.g. the entire spectrum/
    waveform panel next to the album art) and sometimes cutting mid
    escape-sequence, which renders as garbage on screen.
    """
    if width <= 0:
        return ""
    if len(_ANSI_RE.sub("", s)) <= width:
        return s

    budget = max(width - 1, 0) if width > 1 else width
    out = []
    visible_count = 0
    i = 0
    n = len(s)
    while i < n and visible_count < budget:
        m = _ANSI_RE.match(s, i)
        if m:
            out.append(m.group())
            i = m.end()
            continue
        out.append(s[i])
        visible_count += 1
        i += 1
    if width > 1:
        out.append("…")
    out.append(ansi.RESET)
    return "".join(out)


def _format_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    m, s = divmod(seconds, 60)
    return f"{m:02d}:{s:02d}"


def _progress_bar(position: float, duration: float, width: int) -> str:
    if width <= 1 or duration <= 0:
        return " " * max(width, 0)
    filled = int(width * min(max(position / duration, 0.0), 1.0))
    filled = min(filled, width - 1)
    return "━" * filled + "╺" + "─" * max(width - filled - 1, 0)


_CURRENT_LAYER_STYLES = (ansi.BOLD, ansi.DIM, ansi.DIM)  # original, romanization, translation
_CURRENT_BLOCK_ROWS = 3  # reserved rows for the current group's layers


def _styled_lyric_row(text: str, width: int, style: str) -> str:
    if not text:
        return " " * width
    return f"{style}{_truncate(text, width).center(width)}{ansi.RESET}"


def render_lyrics(
    groups: list[LyricGroup], position_seconds: float, width: int, height: int
) -> list[str]:
    """Synced-lyrics panel. The current group's lines (original, and any
    extra layers -- romanization, translation -- a same-timestamp .lrc
    convention) all show together, original bold and the rest dim;
    single-line (original-only) context scrolls above/below. Stacked
    vertically rather than side by side: far more robust in a narrow
    terminal panel against CJK/variable-width text than column-splitting.
    """
    width = max(width, 1)
    height = max(height, 1)

    if not groups:
        out = [" " * width for _ in range(height)]
        out[height // 2] = _truncate("No synced lyrics found for this track", width).center(width)
        return out

    current = current_group_index(groups, position_seconds)
    current_texts = groups[current].texts if 0 <= current < len(groups) else []

    block = list(current_texts[:_CURRENT_BLOCK_ROWS])
    block += [""] * (_CURRENT_BLOCK_ROWS - len(block))

    remaining = max(height - len(block), 0)
    before_n, after_n = remaining // 2, remaining - remaining // 2

    before: list[str] = []
    gi = current - 1
    while gi >= 0 and len(before) < before_n:
        before.insert(0, groups[gi].texts[0])
        gi -= 1
    before = [""] * (before_n - len(before)) + before

    after: list[str] = []
    gi = current + 1
    while gi < len(groups) and len(after) < after_n:
        after.append(groups[gi].texts[0])
        gi += 1
    after += [""] * (after_n - len(after))

    out = [_styled_lyric_row(t, width, ansi.DIM) for t in before]
    out += [
        _styled_lyric_row(t, width, _CURRENT_LAYER_STYLES[min(i, len(_CURRENT_LAYER_STYLES) - 1)])
        for i, t in enumerate(block)
    ]
    out += [_styled_lyric_row(t, width, ansi.DIM) for t in after]
    return out[:height]


def render_queue(names: list[str], current_index: int, width: int, height: int) -> list[str]:
    """Scrolling playlist/queue panel: a window of `height` entries
    centred on the currently-playing track, which is marked and bold.
    Names are plain filenames (no tags) -- reading tags for every track
    in a library of thousands upfront just to list them would be slow
    for no real benefit here; the currently-playing track already shows
    full tag info in the header.
    """
    width = max(width, 1)
    height = max(height, 1)
    n = len(names)

    if n == 0:
        out = [" " * width for _ in range(height)]
        out[height // 2] = _truncate("Queue is empty", width).center(width)
        return out

    top = current_index - height // 2
    top = max(0, min(top, max(n - height, 0)))

    out = []
    for row in range(height):
        idx = top + row
        if idx >= n:
            out.append(" " * width)
            continue
        marker = "▶ " if idx == current_index else "  "
        label = f"{marker}{idx + 1:>4}. {names[idx]}"
        text = _truncate(label, width).ljust(width)
        style = ansi.BOLD if idx == current_index else ""
        out.append(f"{style}{text}{ansi.RESET}" if style else text)
    return out


class Renderer:
    def __init__(self):
        self._art_cache_key = None
        self._art_rows: list[str] = []

    def _get_art(self, artwork: bytes | None, w: int, h: int) -> list[str]:
        key = (id(artwork), w, h)
        if key != self._art_cache_key:
            self._art_rows = render_album_art(artwork, w, h)
            self._art_cache_key = key
        return self._art_rows

    def render_frame(
        self,
        layout: Layout,
        track: TrackInfo,
        playback: PlaybackState,
        spectrum_levels: np.ndarray,
        waveform_samples: np.ndarray,
        lyrics: list[LyricGroup] | None = None,
        queue_names: list[str] | None = None,
    ) -> str:
        cols = layout.cols
        lines: list[str] = []

        title_line = f" {ansi.BOLD}{_truncate(track.title, cols - 2)}{ansi.RESET}"
        artist_album = _truncate(f"{track.artist} — {track.album}", cols - 2)
        meta_bits = [track.format]
        if track.sample_rate:
            meta_bits.append(f"{track.sample_rate} Hz")
        if track.bitrate_kbps:
            meta_bits.append(f"{track.bitrate_kbps} kbps")
        if track.channels:
            meta_bits.append(f"{track.channels}ch")
        meta_bits.append(f"track {track.track_index}/{track.track_count}")
        meta = _truncate(" · ".join(meta_bits), cols - 2)

        lines.append(title_line)
        lines.append(f" {artist_album}")
        lines.append(f" {ansi.DIM}{meta}{ansi.RESET}")
        lines.append("─" * cols)

        art_rows = (
            self._get_art(track.artwork, layout.art_w, layout.body_h)
            if layout.show_art
            else []
        )
        bar_lines = render_bars(spectrum_levels, layout.bars_h) if layout.show_bars else []
        wave_lines = (
            render_waveform(waveform_samples, layout.vis_w, layout.wave_h)
            if layout.show_wave
            else []
        )
        lyric_lines = (
            render_lyrics(lyrics or [], playback.position_seconds, layout.vis_w, layout.body_h)
            if layout.show_lyrics
            else []
        )
        queue_lines = (
            render_queue(queue_names or [], track.track_index - 1, layout.vis_w, layout.body_h)
            if layout.show_queue
            else []
        )

        for i in range(layout.body_h):
            segments = []
            if layout.show_art:
                left = art_rows[i] if i < len(art_rows) else " " * layout.art_w
                segments.append(left)
                segments.append(" │ ")
            if layout.show_queue:
                segments.append(queue_lines[i] if i < len(queue_lines) else "")
            elif layout.show_lyrics:
                segments.append(lyric_lines[i] if i < len(lyric_lines) else "")
            elif i < layout.bars_h:
                segments.append(bar_lines[i] if i < len(bar_lines) else "")
            elif i - layout.bars_h < len(wave_lines):
                segments.append(wave_lines[i - layout.bars_h])
            lines.append("".join(segments))

        lines.append("─" * cols)
        status = "▶ playing" if playback.is_playing else "⏸ paused"
        vol = f"vol {int(playback.volume * 100):3d}%"
        shuffle_label = "on" if playback.shuffle else "off"
        # Build the trailing status text first so the progress bar can be
        # sized to what's actually left -- it previously assumed nothing
        # but the timestamps followed it on the line, so adding the
        # shuffle/repeat labels silently pushed the whole line (bar
        # included) past `cols` and the safety truncate ate all of it.
        suffix = (
            f"{status}   {vol}   mode:{playback.mode}"
            f"   shuffle:{shuffle_label}   repeat:{playback.repeat_mode}"
        )
        pos_s = _format_time(playback.position_seconds)
        dur_s = _format_time(track.duration_seconds)
        bar_width = max(cols - len(pos_s) - len(dur_s) - len(suffix) - 6, 4)
        prog = _progress_bar(playback.position_seconds, track.duration_seconds, bar_width)
        lines.append(f" {pos_s} {prog} {dur_s}   {suffix}")
        lines.append(
            " space pause  n/p next/prev  ←/→ seek  ↑/↓ volume  "
            "m mode  s shuffle  r repeat  a art  l lyrics  t queue  q quit"
        )

        return "\n".join(_truncate_visible(line, cols) for line in lines)
