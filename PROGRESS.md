# Progress log

Status snapshot, not a line-by-line changelog — see `git log` for that.
Updated 2026-10-02.

## macOS visualiser (`macos/`)

**Status: working, actively used against the real library.**

A terminal music player/visualiser: full media tags, quadrant-block
pixel-art album covers, FFT spectrum + braille waveform, synced lyrics
(including a multi-layer original/romanization/translation convention),
a queue view, shuffle/repeat, a default library + `musicvis` CLI alias,
and persistent logging to `~/.musicvis/logs/`.

40/40 tests passing, `ruff` clean, CI runs both on every push.

### Verified against the real ~4,200-file library
- Default-library launch (`musicvis`, no args) — loads and plays.
- Multi-album folder traversal (recursive, sorted).
- AppleDouble junk-file filtering (exFAT drive sidecar files) — the
  concrete cause of early crash reports, confirmed fixed against the
  real files that triggered it.
- Skip-and-continue when a track fails to decode, instead of crashing
  the session (verified with a forced real-file failure).
- Synced lyrics, including the multi-layer grouping — verified against
  a real file from the user's separate lyrics tool.
- Queue view, shuffle, repeat, art/lyrics/queue toggles — all exercised
  live through a real pty with actual keypresses (not just offline
  function calls), cross-checked against the app's own session log.
- Decode performance profiled (not guessed): render loop was already
  fast (~1.6ms/frame); track-load latency was the real cost, cut by
  switching flac/wav/aiff/ogg to an in-process decoder.

### Bugs found and fixed this session (real ones, not hypothetical)
- `os.get_terminal_size()` doesn't take a `fallback` kwarg — crashed on
  first real run.
- Layout math trusted `shutil.get_terminal_size()`'s env-var-first
  behavior; harmless in isolation but was a red herring for the next one.
- **The actual visualiser-is-blank bug**: the final per-line safety
  truncate counted ANSI escape *bytes* as visible characters, so at a
  real terminal width it chopped every body line down inside the album
  art's escape codes — discarding the entire spectrum/waveform panel,
  every frame. Now ANSI-aware, with a regression test.
- Footer status text (shuffle/repeat labels) could get silently
  truncated away because the progress-bar width was sized without
  accounting for the text that follows it on the same line.
- Album art aspect ratio: switching to quadrant-block rendering
  introduced a real ~2x vertical stretch (terminal cells aren't square);
  fixed the sampling math.
- Shell alias broke on the literal space in "Music Visualiser" (aliases
  re-split on whitespace at invocation; switched to a function).

### Known limitations (real, not yet addressed)
- No visible scrollbar/position indicator within the queue view beyond
  the centred window itself.
- Lyrics/queue panels are plain-text only — no search/jump-to within a
  long queue.
- Decode still loads a full track into memory up front (fine for songs,
  not for anything hours-long).

## ESP32 + SSD1306 visualiser (`esp32/`)

**Status: builds clean, not hardware-verified.**

Bluetooth A2DP sink, 16-band spectrum on the OLED, scrolling AVRCP
title/artist. `pio run` compiles clean against the real pinned library
versions (one real fix already applied: `pschatzmann/ESP32-A2DP` had to
be pulled from GitHub directly, not PlatformIO's registry, which no
longer lists it). One real logic bug was also found and fixed pre-
hardware: the OLED would freeze on the last frame instead of reverting
to "waiting for connection" after a Bluetooth disconnect.

**What's not verified, because it needs physical hardware not present in
this session:** flashing, Bluetooth pairing, whether the OLED actually
renders correctly, whether the I2S amp wiring produces audible output.
Bring back real hardware output (`./build.sh monitor`, or just what you
see on the board) and these get fixed for real instead of guessed at.
