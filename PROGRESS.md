# Progress log

Status snapshot, not a line-by-line changelog — see `git log` for that.
Updated 2026-10-05.

## macOS visualiser (`macos/`)

**Status: working, actively used against the real library.**

A terminal music player/visualiser: full media tags, quadrant-block
pixel-art album covers, FFT spectrum + braille waveform, synced lyrics
(including a multi-layer original/romanization/translation convention),
a queue view, shuffle/repeat, a default library + `musicvis` CLI alias,
and persistent logging to `~/.musicvis/logs/`.

115/115 tests passing, `ruff` clean, CI runs both on every push.

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

### Second pass (2026-10-05): bugs found by reproducing them, then fixed
Each of these was reproduced first (real pty + a terminal emulator, or a
deterministic unit test), fixed, and then shown to fail again when the fix
is reverted.
- **Arrow keys quit the app.** `Keyboard` mixed `select()` on the raw fd
  with buffered `sys.stdin.read(1)`: the first read swallowed all 3 bytes
  of `ESC [ C` into Python's buffer, `select` then saw nothing, and the
  arrow was reported as a lone `esc` — which is the quit key. So ←/→
  seek and ↑/↓ volume closed the player. Fast typing also lost keys
  (`ns` delivered `n`; the `s` waited for the next keypress). Now reads the
  fd directly and parses sequences itself, including Home/End/PgUp/PgDn,
  SS3-style arrows, modified arrows; unmapped keys (F-keys, Delete,
  Alt+x) are ignored rather than read as Esc. All pending keys are
  drained each frame so key-repeat can't build a backlog.
- **Stale text after a track change.** Frames overwrite the last one
  without clearing (flicker), but lines weren't padded, so a short title
  after a long one rendered as `Hi` + the tail of the old title. Every
  line is now fitted to exactly the terminal width, and the screen is
  wiped once on resize.
- **CJK / double-width text overflowed.** Truncation, centring and
  padding all counted characters, not terminal columns, so a wide-
  character title could run past the edge and wrap, scrolling the frame.
  Widths now come from Unicode East Asian Width (stdlib; no new
  dependency). Control characters in tags (a stray `\n`) are replaced
  with spaces so they can't split a line.
- **Seek/restart could be silently lost.** The audio callback read the
  position, released the lock, then wrote back `pos + frames`, so a seek
  landing in between was overwritten. Read-and-advance is now one
  critical section (reproduced deterministically; the old code ended at
  position 200 instead of 3200).
- **Volume drifted a percent low.** `1.0 - 0.05*3` is `0.8499…`, and the
  footer truncated it to `84%`. Rounded at the source and in the display.

### New: queue view navigation (was a known limitation)
Scrollbar, a selection cursor (↑/↓, PgUp/PgDn, Home/End), `enter` to play
the selected track, and a position indicator in the footer. `+`/`-` set
volume while the queue is open since the arrows are taken. Moving the
cursor never interrupts playback.

### Verification, and what it does *not* cover
Run end to end through a real pty with real keystrokes, output rendered
through a terminal emulator and cross-checked against the app's own log:
arrow seek, volume (burst of keys), queue open/navigate/page/home/end/
enter, jump playback, next-track with no leftover text, unmapped keys
ignored, clean quit — 18/18. **The container had no sound device**, so
PortAudio's stream was replaced by a stand-in that drives the real audio
callback in real time; actual audible output, and macOS Terminal/iTerm2
rendering quirks (this ran on Linux), are not verified here. Worth a
quick `musicvis` run on the Mac.

### Known limitations (real, not yet addressed)
- No text search within a long queue (you can page/jump, not type to
  filter).
- Lyrics panel is plain-text only.
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
