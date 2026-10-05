"""Frame composition: header (tags), body (album art + spectrum/waveform),
footer (transport + progress + keybindings). Built from raw strings for
speed -- one write() per frame keeps things flicker-free at 30fps."""

from __future__ import annotations

import re
import unicodedata
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


def _char_width(ch: str) -> int:
    """Terminal columns a character occupies: 0 for combining marks and
    format characters (e.g. zero-width joiner), 2 for East Asian wide /
    fullwidth characters (CJK, kana, hangul, most emoji), else 1.

    Stdlib-only approximation of wcwidth -- good for the CJK titles and
    lyrics this app actually meets, without adding a dependency.
    """
    if unicodedata.category(ch) in ("Mn", "Me", "Cf"):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def display_width(s: str) -> int:
    """Width of plain text (no ANSI codes) in terminal columns."""
    return sum(_char_width(ch) for ch in s)


def _visible_width(s: str) -> int:
    """Width of a styled line in terminal columns, ignoring escape codes."""
    return display_width(_ANSI_RE.sub("", s))


def _sanitize(s: str) -> str:
    """Replace control characters (a stray newline or tab in a media tag
    or lyric line) with spaces -- left alone they'd break the line grid."""
    return "".join(" " if unicodedata.category(ch) == "Cc" else ch for ch in s)


def _truncate(s: str, width: int) -> str:
    """Truncate plain text (no ANSI codes) to at most `width` terminal
    columns, ending in an ellipsis when anything was cut."""
    if width <= 0:
        return ""
    s = _sanitize(s)
    if display_width(s) <= width:
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


def _pad(s: str, width: int) -> str:
    """Left-justify plain text to `width` columns (str.ljust counts
    characters, which is wrong for double-width text)."""
    return s + " " * max(width - display_width(s), 0)


def _center(s: str, width: int) -> str:
    gap = max(width - display_width(s), 0)
    left = gap // 2
    return " " * left + s + " " * (gap - left)


def _truncate_visible(s: str, width: int) -> str:
    """Truncate a fully-styled (ANSI-coloured) line to `width` *visible*
    terminal columns, counting only non-escape-code characters toward the
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
    if _visible_width(s) <= width:
        return s

    budget = max(width - 1, 0) if width > 1 else width
    out = []
    used = 0
    i = 0
    n = len(s)
    while i < n:
        m = _ANSI_RE.match(s, i)
        if m:
            out.append(m.group())
            i = m.end()
            continue
        w = _char_width(s[i])
        if used + w > budget:
            break
        out.append(s[i])
        used += w
        i += 1
    if width > 1:
        out.append("…")
    out.append(ansi.RESET)
    return "".join(out)


def _fit_line(s: str, cols: int) -> str:
    """Make a styled line exactly `cols` columns: truncate if too long,
    pad with spaces if short.

    The padding is what stops leftovers: frames are drawn over the
    previous one without clearing the screen (that flickers), so any
    column a line doesn't explicitly overwrite keeps whatever the last
    frame put there -- e.g. a short title after a long one came out as
    "Short" + the tail of the old title.
    """
    s = _truncate_visible(s, cols)
    short = cols - _visible_width(s)
    return s + ansi.RESET + " " * short if short > 0 else s


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
    return f"{style}{_center(_truncate(text, width), width)}{ansi.RESET}"


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
        out[height // 2] = _center(_truncate("No synced lyrics found for this track", width), width)
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


def _queue_scrollbar(n: int, top: int, height: int) -> list[str]:
    """One glyph per row: a thumb sized and positioned to show which slice
    of the `n`-entry list the `height`-row window is looking at."""
    thumb = max(1, round(height * height / n))
    span = max(n - height, 1)
    thumb_top = round(top / span * (height - thumb))
    return [
        "┃" if thumb_top <= row < thumb_top + thumb else f"{ansi.DIM}│{ansi.RESET}"
        for row in range(height)
    ]


def render_queue(
    names: list[str],
    current_index: int,
    width: int,
    height: int,
    cursor_index: int | None = None,
) -> list[str]:
    """Scrolling playlist/queue panel: a window of `height` entries with the
    currently-playing track marked and bold, and -- once the user starts
    browsing -- a highlighted selection cursor. The window centres on the
    cursor, or on the playing track while there is none. A scrollbar
    column appears when the list doesn't fit.

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
        out[height // 2] = _center(_truncate("Queue is empty", width), width)
        return out

    focus = current_index if cursor_index is None else cursor_index
    focus = max(0, min(focus, n - 1))
    top = max(0, min(focus - height // 2, max(n - height, 0)))

    scrollbar = _queue_scrollbar(n, top, height) if n > height and width > 4 else None
    text_w = width - 2 if scrollbar else width  # 1 col scrollbar + 1 col gap
    num_w = len(str(n))

    out = []
    for row in range(height):
        idx = top + row
        if idx >= n:
            line = " " * text_w
        else:
            marker = "▶ " if idx == current_index else "  "
            label = f"{marker}{idx + 1:>{num_w}}. {names[idx]}"
            line = _pad(_truncate(label, text_w), text_w)
            if idx == cursor_index:
                line = f"{ansi.REVERSE}{line}{ansi.RESET}"
            elif idx == current_index:
                line = f"{ansi.BOLD}{line}{ansi.RESET}"
        out.append(line + " " + scrollbar[row] if scrollbar else line)
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
        queue_cursor: int | None = None,
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
            render_queue(
                queue_names or [],
                track.track_index - 1,
                layout.vis_w,
                layout.body_h,
                cursor_index=queue_cursor,
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
        vol = f"vol {round(playback.volume * 100):3d}%"
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
            selected = track.track_index if queue_cursor is None else queue_cursor + 1
            lines.append(
                " ↑/↓ select  pgup/pgdn page  home/end  enter play  t close  q quit"
                f"   ({selected}/{track.track_count})"
            )
        else:
            lines.append(
                " space pause  n/p next/prev  ←/→ seek  ↑/↓ or +/- volume  "
                "m mode  s shuffle  r repeat  a art  l lyrics  t queue  q quit"
            )

        return "\n".join(_fit_line(line, cols) for line in lines)
