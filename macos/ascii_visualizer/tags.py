"""Media tag + embedded artwork extraction for common audio formats,
using mutagen. Falls back to sane defaults when tags are missing."""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass

import mutagen
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3
from mutagen.mp4 import MP4


@dataclass
class TrackTags:
    title: str
    artist: str
    album: str
    albumartist: str
    date: str
    genre: str
    track_number: str
    duration_seconds: float
    sample_rate: int
    channels: int
    bitrate_kbps: int
    format: str
    filesize_bytes: int
    artwork: bytes | None = None


def _first(value) -> str:
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value) if value is not None else ""


def _extract_artwork(path: str) -> bytes | None:
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".mp3":
            id3 = ID3(path)
            for tag in id3.values():
                if isinstance(tag, APIC):
                    return tag.data
        elif ext == ".flac":
            audio = FLAC(path)
            if audio.pictures:
                return audio.pictures[0].data
        elif ext in (".m4a", ".mp4", ".aac"):
            audio = MP4(path)
            covr = audio.tags.get("covr") if audio.tags else None
            if covr:
                return bytes(covr[0])
        elif ext in (".ogg", ".oga", ".opus"):
            raw = mutagen.File(path)
            pics = raw.get("metadata_block_picture") if raw else None
            if pics:
                data = base64.b64decode(pics[0])
                return Picture(data).data
    except Exception:  # noqa: BLE001 -- malformed/unexpected tag data in a
        # user's file shouldn't crash the visualiser, just skip the art
        return None
    return None


def read_tags(path: str) -> TrackTags:
    easy = None
    raw = None
    try:
        easy = mutagen.File(path, easy=True)
    except Exception:  # noqa: BLE001 -- mutagen can raise many format-
        # specific exception types on malformed files; fall back to defaults
        easy = None
    try:
        raw = mutagen.File(path)
    except Exception:  # noqa: BLE001 -- same reasoning as above
        raw = None

    def get(key: str) -> str:
        if not easy:
            return ""
        val = easy.get(key)
        return _first(val)

    fallback_title = os.path.splitext(os.path.basename(path))[0]
    title = get("title") or fallback_title
    artist = get("artist") or "Unknown Artist"
    album = get("album") or "Unknown Album"
    albumartist = get("albumartist") or artist
    date = get("date") or get("year")
    genre = get("genre")
    track_number = get("tracknumber")

    info = raw.info if raw is not None else None
    duration = float(getattr(info, "length", 0.0) or 0.0)
    sample_rate = int(getattr(info, "sample_rate", 0) or 0)
    channels = int(getattr(info, "channels", 0) or 0)
    bitrate_bps = getattr(info, "bitrate", None)
    bitrate_kbps = int(bitrate_bps / 1000) if bitrate_bps else 0

    return TrackTags(
        title=title,
        artist=artist,
        album=album,
        albumartist=albumartist,
        date=date,
        genre=genre,
        track_number=track_number,
        duration_seconds=duration,
        sample_rate=sample_rate,
        channels=channels,
        bitrate_kbps=bitrate_kbps,
        format=os.path.splitext(path)[1].lstrip(".").upper() or "?",
        filesize_bytes=os.path.getsize(path),
        artwork=_extract_artwork(path),
    )
