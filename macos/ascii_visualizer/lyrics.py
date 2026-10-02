"""Synced lyrics (.lrc) support. Looks for a file with the same base name
as the track, `.lrc` extension, in the same folder -- the standard
convention these files are distributed under (and how they sit next to
the audio throughout the user's library).

Multi-layer lyrics (original + romanization + translation) are a common
real-world .lrc convention: three consecutive lines sharing the exact
same timestamp, one per layer. Those are grouped into a single
LyricGroup so they display together as a unit instead of scrolling past
as three separate, seemingly-unrelated lines.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

_TIME_TAG_RE = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")
_SAME_TIMESTAMP_TOLERANCE = 0.02  # seconds; absorbs rounding across layers


@dataclass
class LyricGroup:
    time_seconds: float
    # 1+ lines sharing this timestamp: original [, romanization [, translation]]
    texts: list[str] = field(default_factory=list)


def find_lrc_path(audio_path: str) -> str | None:
    candidate = os.path.splitext(audio_path)[0] + ".lrc"
    return candidate if os.path.isfile(candidate) else None


def parse_lrc(path: str) -> list[LyricGroup]:
    """Parse standard `[mm:ss.xx] text` synced-lyric lines, grouping
    consecutive lines that share a timestamp (within rounding) into one
    LyricGroup, preserving file order within the group (so a
    original/romanization/translation triple stays in that order).
    Metadata lines (e.g. `[ar:]`, `[ti:]`) don't match the time-tag
    pattern and are silently skipped.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            raw = f.read()
    except OSError:
        return []

    flat: list[tuple[float, str]] = []
    for raw_line in raw.splitlines():
        matches = list(_TIME_TAG_RE.finditer(raw_line))
        if not matches:
            continue
        text = _TIME_TAG_RE.sub("", raw_line).strip()
        for m in matches:
            minutes, seconds, frac = int(m.group(1)), int(m.group(2)), m.group(3) or "0"
            frac_seconds = int(frac) / (10 ** len(frac))
            flat.append((minutes * 60 + seconds + frac_seconds, text))

    flat.sort(key=lambda item: item[0])

    groups: list[LyricGroup] = []
    for time_seconds, text in flat:
        if groups and abs(time_seconds - groups[-1].time_seconds) <= _SAME_TIMESTAMP_TOLERANCE:
            groups[-1].texts.append(text)
        else:
            groups.append(LyricGroup(time_seconds=time_seconds, texts=[text]))
    return groups


def load_lyrics_for(audio_path: str) -> list[LyricGroup]:
    lrc_path = find_lrc_path(audio_path)
    return parse_lrc(lrc_path) if lrc_path else []


def current_group_index(groups: list[LyricGroup], position_seconds: float) -> int:
    """Index of the group that should be showing right now, or -1 if
    before the first group (or there are none at all)."""
    idx = -1
    for i, group in enumerate(groups):
        if group.time_seconds > position_seconds:
            break
        idx = i
    return idx
