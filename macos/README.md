# ASCII/Pixel Music Visualiser (macOS)

A terminal music player that reads full media tags (title, artist, album,
year, genre, embedded cover art, format/bitrate/sample rate) from your audio
files and renders an HD ASCII/pixel visualisation synced to playback:

- **Album art** rendered as true-colour terminal "pixel art" using quadrant
  block characters (4 independently-coloured sub-pixels per character cell).
- **Spectrum bars** with eighth-block sub-character resolution and a vivid
  colour gradient, driven by a real-time FFT of the audio.
- **Braille oscilloscope waveform** — braille dot cells pack a 2x4 sub-pixel
  grid per character, the highest resolution practical in a terminal.
- **Shuffle and repeat** (off / repeat-all / repeat-one), like any other
  music player.
- **Synced lyrics** (`l` to toggle) — reads a standard `.lrc` file sitting
  next to the track (same name, `.lrc` extension) and shows a karaoke-
  style view, current line highlighted. Also understands the common
  multi-layer convention (original / romanization / translation as three
  consecutive lines at the same timestamp) and shows all layers of the
  current line together, stacked, instead of scrolling past as separate
  unrelated lines.
- **Queue view** (`t` to toggle) — a scrollable list of the whole
  playlist with a scrollbar, current track marked and bold. Browse it with
  the arrow / page / home / end keys and press `enter` to play the
  selected track — useful for a big library/folder where "track
  1203/3846" alone doesn't tell you much. The footer shows your position
  in the list.

## Requirements

- macOS with a working audio output device
- Python 3.10+
- [ffmpeg](https://ffmpeg.org/) for decoding mp3/m4a/aac/wma/opus (flac/wav/
  aiff/ogg decode in-process via `soundfile`, no ffmpeg needed for those —
  see Performance below)

```bash
brew install ffmpeg
```

## Setup & run

```bash
cd "macos"
./run.sh "/path/to/song.mp3"
# or point it at a folder to build a playlist:
./run.sh "/path/to/Album Folder"
```

The first run creates a local virtual environment (`.venv`) and installs
dependencies automatically. Subsequent runs just reuse it.

### Default library

Save a folder once so you don't have to type the path every time:

```bash
musicvis --set-library "/path/to/your/Music"
musicvis                              # now just works, no path needed
```

Saved to `~/.musicvis/library` (outside the repo, not committed/synced —
it's a personal filesystem path). Overwrite it any time by running
`--set-library` again; pass an explicit path to play something else
without touching the saved default.

Manual setup, if you'd rather not use `run.sh`:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m ascii_visualizer "/path/to/song.mp3"
```

## Controls

| Key       | Action              |
|-----------|---------------------|
| `space`   | Play / pause        |
| `n` / `p` | Next / previous track |
| `←` / `→` | Seek -5s / +5s       |
| `↑` / `↓` or `+` / `-` | Volume up / down |
| `m`       | Cycle visualiser mode (bars+wave / bars / wave) |
| `s`       | Toggle shuffle      |
| `r`       | Cycle repeat (off / all / one) |
| `a`       | Toggle album art panel |
| `l`       | Toggle synced lyrics panel |
| `t`       | Toggle queue/playlist view |
| `q` / `esc` | Quit               |

While the queue view is open, the arrow keys move a selection instead of
changing the volume (use `+` / `-` for volume there):

| Key       | Action              |
|-----------|---------------------|
| `↑` / `↓` | Move selection      |
| `pgup` / `pgdn` | Move a page   |
| `home` / `end` | First / last track |
| `enter`   | Play the selected track |

Moving the selection never interrupts playback, and it stays where you put
it even as tracks auto-advance, until you press `enter` or close the view.

## Supported formats

mp3, m4a/mp4/aac, flac, wav, aiff, ogg/oga, opus, wma (anything ffmpeg can
decode). Cover art is read from ID3 APIC (mp3), FLAC pictures, MP4 `covr`
atoms, and Ogg `METADATA_BLOCK_PICTURE`.

## Folders, albums, and unplayable files

Point it at a folder (an album, an artist with several albums, or your
whole library) and it recursively queues every supported audio file
inside, sorted by path. AppleDouble sidecar files (`._Song.flac`, common
on exFAT/non-HFS+ drives) are skipped automatically. Any file that still
fails to load (corrupt, truncated, etc.) is skipped too — playback
continues with the next track instead of crashing the session.

## Logs

Every run writes a timestamped log to `~/.musicvis/logs/` (outside this
repo, since it records your real file paths and listening activity) —
track loads, skipped/unplayable files with the reason, shuffle/repeat
toggles, and any crash. Check there first if something seems off; old
logs aren't auto-deleted, so prune `~/.musicvis/logs/` occasionally if
you care about disk space.

## Performance

Profiled rather than guessed: the render loop was already fast (~1.6ms/
frame against a 33ms budget at 30fps — not a bottleneck). The real cost
was track-load latency, almost entirely from spawning an `ffmpeg`
subprocess to decode (~70-110ms per track). flac/wav/aiff/ogg now decode
via `soundfile` (libsndfile) in-process instead — no subprocess spawn,
measured consistently at least as fast and often faster (~85ms), and no
dependency on ffmpeg being installed for those formats at all. mp3/m4a/
aac/wma/opus still go through ffmpeg (soundfile doesn't support them);
if soundfile fails to open a file for any reason, it falls straight back
to the ffmpeg path rather than failing the track.

## Development

```bash
pip install -r requirements-dev.txt
ruff check .     # lint
pytest -v        # tests (synthesizes its own MP3/FLAC fixtures via ffmpeg)
```

CI (`.github/workflows/ci.yml`) runs both on every push.

## Notes

- Playback decodes the whole file into memory up front (fine for songs;
  not intended for hours-long files).
- The visualiser analyses the currently-audible portion of the track, not a
  separate system-audio capture, so it works for any file you point it at
  without extra virtual-audio-device setup.
- Text is laid out by terminal *columns*, not characters, so CJK
  (double-width) titles, filenames and lyrics truncate and align correctly.
  Width is approximated from Unicode East Asian Width data (no extra
  dependency); exotic emoji sequences may still be off by a column.
