"""Frame composition: header (tags), body (album art + spectrum/waveform),
footer (transport + progress + keybindings). Built from raw strings for
speed -- one write() per frame keeps things flicker-free at 30fps."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from . import ansi
from .image_art import render_album_art
from .lyrics import LyricGroup, current_group_index
from .visualizers import render_bars, render_waveform

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

HEADER_LINES = 4
FOOTER_LINES = 3


@lru_cache(maxsize=4096)
def _char_width(ch: str) -> int:
    """Terminal display width of one character: 0 for combining marks, 2
    for East-Asian "wide"/"fullwidth" characters, 1 for everything else.

    Plain `len()` undercounts real display width for CJK text (each
    character occupies two terminal columns, not one) -- the lyrics
    panel explicitly supports CJK original lines alongside a
    romanization/translation, so every width/truncate/pad computation
    in this module has to go through this instead of `len()`, or CJK
    lines silently overflow their column budget and wreck the layout
    (misaligned separators, ragged panel edges, or -- in the final
    safety-net truncate -- chopping into a neighbouring panel).
    """
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def _display_width(s: str) -> int:
    return sum(_char_width(c) for c in s)


def _pad_right(s: str, width: int) -> str:
    return s + " " * max(width - _display_width(s), 0)


def _pad_center(s: str, width: int) -> str:
    total_pad = max(width - _display_width(s), 0)
    left = total_pad // 2
    return " " * left + s + " " * (total_pad - left)


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
    """Truncate plain text (no ANSI codes) to at most `width` display
    *columns* -- not codepoints, so a wide (CJK) character counts as 2."""
    if width <= 0:
        return ""
    if _display_width(s) <= width:
        return s
    budget = width - 1 if width > 1 else width
    out = []
    used = 0
    for ch in s:
        w = _char_width(ch)
        if used + w > budget:
            break
        out.append(ch)
        used += w
    if width > 1:
        out.append("…")
    return "".join(out)


def _truncate_visible(s: str, width: int) -> str:
    """Truncate a fully-styled (ANSI-coloured) line to `width` *visible
    display columns*, counting only non-escape-code characters toward
    the budget (at their real terminal width, wide CJK glyphs counting
    as 2) and never cutting inside an escape sequence.

    A plain len()-based truncate is wrong here for two independent
    reasons: a single pixel-art row can carry thousands of bytes of
    colour codes for only a few dozen visible characters (truncating on
    raw length chops the line far short of its actual on-screen width,
    discarding e.g. the entire spectrum/waveform panel next to the
    album art); and codepoint-counting undercounts any wide CJK text
    (lyrics, tags) by half, overrunning the budget instead of stopping
    at it. Both would otherwise land mid escape-sequence or mid-panel
    and render as garbage/misalignment on screen.
    """
    if width <= 0:
        return ""
    plain = _ANSI_RE.sub("", s)
    if _display_width(plain) <= width:
        return s

    budget = max(width - 1, 0) if width > 1 else width
    out = []
    visible_width = 0
    i = 0
    n = len(s)
    while i < n and visible_width < budget:
        m = _ANSI_RE.match(s, i)
        if m:
            out.append(m.group())
            i = m.end()
            continue
        ch = s[i]
        w = _char_width(ch)
        if visible_width + w > budget:
            break
        out.append(ch)
        visible_width += w
        i += 1
    if width > 1:
        out.append("…")
    out.append(ansi.RESET)
    return "".join(out)


def _pad_visible(s: str, width: int) -> str:
    """Pad a (possibly ANSI-styled) line with trailing spaces out to
    `width` display columns, measuring only the non-escape-code
    characters.

    The player repaints each frame with a cursor-home + overwrite (no
    screen clear, to stay flicker-free), so a line that's shorter than
    the previous frame's line at that row leaves stale characters from
    the old, longer line sitting on screen past the new text -- e.g.
    toggling the queue panel swaps the long keybindings footer for a
    short one and the tail of the old footer stayed visible. Every line
    has to be padded to the full row width, not just truncated, so a
    shorter line always overwrites the old one completely.
    """
    plain = _ANSI_RE.sub("", s)
    return s + " " * max(width - _display_width(plain), 0)


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
_MARQUEE_SEPARATOR = "   •   "
_MARQUEE_COLS_PER_SEC = 5.0  # ticker speed for lines too long to fit
_MARQUEE_HOLD_SECONDS = 1.5  # pause at the start so the line can be read first


def _slice_display(s: str, start_col: int, width: int) -> str:
    """Substring of `s` covering display columns [start_col, start_col +
    width), used to window into a doubled-up marquee string. Always
    returns exactly `width` columns (right-padded) so the caller never
    has to special-case a wide character landing on the boundary."""
    out = []
    col = 0
    used = 0
    for ch in s:
        w = _char_width(ch)
        if col < start_col:
            col += w
            continue
        if used + w > width:
            break
        out.append(ch)
        used += w
        col += w
    return _pad_right("".join(out), width)


def _marquee_window(text: str, width: int, position_seconds: float) -> tuple[str, bool]:
    """A horizontally auto-scrolling window into `text` when it's wider
    than `width`, advancing with playback position (`position_seconds` is
    time since the line became current, so it always starts at its
    beginning after a short hold) -- so a long current
    lyric line scrolls on its own as the song plays (no key needed,
    freezes with playback on pause, resets cleanly on seek) instead of
    just being truncated with an ellipsis. Returns (text_or_window,
    is_scrolling); when it already fits, the plain text is returned
    unchanged so the caller can still centre it."""
    if width <= 0 or _display_width(text) <= width:
        return text, False
    loop_text = text + _MARQUEE_SEPARATOR
    loop_width = _display_width(loop_text)
    scrolled = max(position_seconds - _MARQUEE_HOLD_SECONDS, 0.0)
    offset = int(scrolled * _MARQUEE_COLS_PER_SEC) % loop_width
    return _slice_display(loop_text + loop_text, offset, width), True


def _styled_lyric_row(
    text: str, width: int, style: str, *, marquee_position: float | None = None
) -> str:
    if not text:
        return " " * width
    if marquee_position is not None:
        window, scrolling = _marquee_window(text, width, marquee_position)
        body = window if scrolling else _pad_center(window, width)
    else:
        body = _pad_center(_truncate(text, width), width)
    return f"{style}{body}{ansi.RESET}"


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
        msg = _truncate("No synced lyrics found for this track", width)
        out[height // 2] = _pad_center(msg, width)
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
        _styled_lyric_row(
            t,
            width,
            _CURRENT_LAYER_STYLES[min(i, len(_CURRENT_LAYER_STYLES) - 1)],
            marquee_position=max(position_seconds - groups[current].time_seconds, 0.0),
        )
        for i, t in enumerate(block)
    ]
    out += [_styled_lyric_row(t, width, ansi.DIM) for t in after]
    return out[:height]


@dataclass
class QueueEntry:
    """One row of the song selector: either an album header (a grouping
    label only, not selectable) or a playable track."""

    kind: str  # "album" or "track"
    label: str
    track_index: int | None = None  # index into the playlist, for "track" entries


def _album_header_row(label: str, width: int) -> str:
    text = f" {label} "
    text = _truncate(text, width)
    pad = max(width - _display_width(text), 0)
    left = pad // 2
    line = "─" * left + text + "─" * (pad - left)
    return f"{ansi.BOLD}{line}{ansi.RESET}"


def _scrollbar_chars(n: int, top: int, height: int) -> list[str]:
    """One thumb/track character per visible row, proportional to how
    much of the full `n`-entry list the `height`-row window covers --
    the queue view previously had no indication of where you were in a
    long list beyond the names scrolling past."""
    if n <= height:
        return ["│"] * height
    thumb_size = max(round(height * height / n), 1)
    thumb_start = min(round(top * height / n), height - thumb_size)
    return ["┃" if thumb_start <= row < thumb_start + thumb_size else "│" for row in range(height)]


def render_selector(
    entries: list[QueueEntry], cursor: int, playing_index: int, width: int, height: int
) -> list[str]:
    """Song-selector panel: the playlist grouped under album headers
    (derived from each track's parent folder), windowed and scrolled to
    keep `cursor` in view. The playing track is bold with a ▶ marker;
    the cursor row (the one Enter would play) is shown in reverse video,
    combined with bold when it's also the playing track. Track labels
    are plain filenames (no tags) -- reading tags for every track in a
    library of thousands upfront just to list them would be slow for no
    real benefit here; the currently-playing track already shows full
    tag info in the header.
    """
    width = max(width, 1)
    height = max(height, 1)
    n = len(entries)

    if n == 0:
        out = [" " * width for _ in range(height)]
        out[height // 2] = _pad_center(_truncate("Queue is empty", width), width)
        return out

    top = cursor - height // 2
    top = max(0, min(top, max(n - height, 0)))

    show_scrollbar = width >= 12
    text_width = width - 2 if show_scrollbar else width
    bar_chars = _scrollbar_chars(n, top, height) if show_scrollbar else [""] * height

    out = []
    for row in range(height):
        idx = top + row
        if idx >= n:
            out.append(" " * width)
            continue
        entry = entries[idx]
        if entry.kind == "album":
            text = _album_header_row(entry.label, text_width)
        else:
            is_playing = entry.track_index == playing_index
            is_cursor = idx == cursor
            marker = "▶ " if is_playing else "  "
            label = f"{marker}{entry.track_index + 1:>4}. {entry.label}"
            body = _pad_right(_truncate(label, text_width), text_width)
            style = ""
            if is_cursor and is_playing:
                style = ansi.BOLD + ansi.REVERSE
            elif is_cursor:
                style = ansi.REVERSE
            elif is_playing:
                style = ansi.BOLD
            text = f"{style}{body}{ansi.RESET}" if style else body
        out.append(f"{text} {bar_chars[row]}" if show_scrollbar else text)
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
        selector_entries: list[QueueEntry] | None = None,
        selector_cursor: int = 0,
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
            render_selector(
                selector_entries or [], selector_cursor, track.track_index - 1,
                layout.vis_w, layout.body_h,
            )
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
        if layout.show_queue:
            lines.append(" ↑/↓ select  ↵ play  t close  q quit")
        else:
            lines.append(
                " space pause  n/p next/prev  ←/→ seek  ↑/↓ volume  "
                "m mode  s shuffle  r repeat  a art  l lyrics  t queue  q quit"
            )

        return "\n".join(
            _pad_visible(_truncate_visible(line, cols), cols) for line in lines
        )
