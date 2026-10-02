# Progress log

Status snapshot, not a line-by-line changelog — see `git log` for that.
Updated 2026-10-02.

## In progress (this session, 2026-10-02)

Working on three asks: visual glitches, the song selector (album support),
and automatic lyric scrolling. `ruff` clean, all tests passing (52/52) at
each step so far.

**Done:**
- **Real glitch found and fixed**: every width/truncate/pad computation in
  `ui.py` used plain `len()`, which undercounts CJK/fullwidth characters
  (they take 2 terminal columns, not 1). Since this project explicitly
  supports CJK original lyrics (romanization/translation convention), any
  such line would overrun its column budget and desync the panel it's
  in — misaligned separators, ragged edges, or (in the final ANSI-aware
  safety truncate) spilling into a neighbouring panel. Added
  `_display_width`/`_char_width` (East-Asian-width aware, memoized) and
  routed `_truncate`, `_truncate_visible`, and new `_pad_left`/`_pad_center`
  helpers through it instead of `len()`/`str.center`/`str.ljust`.
- **Song selector rewritten with album support**: the old `render_queue`
  was a flat, read-only filename list. Replaced with `render_selector` +
  `QueueEntry` (album-header or track rows), fed by
  `player._build_selector_entries`, which groups the already
  folder-sorted playlist by parent directory (zero extra I/O — reading
  real album tags for a multi-thousand-file library on startup was
  already ruled out once, see below). It's now a real selector: ↑/↓ move
  a cursor between tracks (skipping headers), Enter plays the selected
  track and closes the panel, the playing track still shows ▶/bold. Also
  added the scrollbar this file's own "known limitations" called out as
  missing.
- **Lyrics now auto-scroll horizontally**: a current lyric line longer
  than the panel now scrolls as a ticker, driven by `position_seconds`
  (so it's automatic — no key needed, freezes on pause, resets cleanly on
  seek) instead of being truncated with an ellipsis. Vertical line
  advance (which line is "current") was already automatic/position-driven
  before this session — that part wasn't broken.
- Added regression tests for all of the above (`test_pipeline.py`,
  `test_player.py`); rewrote the `render_queue` tests for the new
  `render_selector` API since the old flat-list function no longer exists.

**Live pty testing (this session, continued):**
- **Second real glitch found and fixed, this one live**: `render_frame`'s
  final composition truncated every line to the terminal width but never
  padded shorter ones. The player repaints each frame with cursor-home +
  overwrite (no screen clear, to stay flicker-free), so a line shorter
  than the previous frame's line at that row left stale characters from
  the old, longer line sitting on screen. Reproduced live by opening/
  closing the song selector in a real pty: the short selector footer
  ("↑/↓ select  ↵ play  t close  q quit") left the tail of the old,
  longer transport footer visible right after it. Added `_pad_visible`
  (ANSI-aware, like `_truncate_visible`) and pad every composed line out
  to the full column width. Re-verified live after the fix: clean in
  both directions, no leftover characters. Regression coverage is via
  the existing render-frame tests; the bug was specifically about
  cross-frame terminal state, which a single-frame unit test can't catch
  directly — worth keeping in mind if a future glitch is similarly
  frame-to-frame rather than within-frame.
- **Hardened, but not confirmed as a real bug**: `keyboard.py`'s
  `read_key()` only waited 1ms after an ESC byte before giving up and
  treating it as a lone Esc keypress (which quits the app). While
  live-testing the selector, a `t` then arrow-key sequence sent via
  `tmux send-keys` reliably made the app quit cleanly (exit 0, no
  traceback) right after the ESC byte — consistent with that race.
  Raised the timeout to 25ms (matches common terminal/editor escape-time
  defaults) and added `tests/test_keyboard.py` covering the arrow-vs-
  lone-Esc disambiguation. **However**, deeper investigation points the
  other way: `tmux send-keys` (named keys, `-H` hex, `-l` literal, and
  `paste-buffer`) could not be made to reliably deliver an intact 3-byte
  CSI arrow sequence to a *detached* tmux pane in this sandbox — every
  method either delivered a lone ESC with nothing following (even after
  waiting a full 10s) or garbled bytes. A from-scratch `pty.fork()`
  harness that writes the real 3-byte sequence in one `os.write()` did
  **not** reproduce a crash (process stayed alive through `t`, two
  downs, and Enter). So the original "crash" very likely came from the
  test harness (tmux failing to deliver the full sequence to a headless
  pane), not from a real race in the app against a real terminal. The
  25ms fix is still correct/harmless hardening and stays in, but treat
  it as precautionary, not as a confirmed fix for a reproduced bug.
- Net effect: selector rendering, album headers, and the playing-track
  marker are confirmed live (pty capture, both toggle directions).
  Cursor movement via ↑/↓ and Enter-to-play are covered by existing unit
  tests (`_move_selector`, `_activate_selector`, `_entry_index_for_track`
  in `test_player.py`) but were **not** cleanly confirmed via live
  keypresses this session — every synthetic-input method tried (tmux
  send-keys in its various forms, direct pty-device writes, `expect`)
  had its own artifacts, documented above, that made the results
  untrustworthy one way or the other. Worth a real human keypress check
  next time, or a more carefully-built pty harness if automating it
  again.
- Lyric marquee: exercised offline (window contents across positions),
  not yet live. That found a real flaw: it scrolled on absolute song
  time, so a line becoming current at 90s opened mid-scroll. Now relative
  to the line's start, with a 1.5s hold before it moves; regression test
  added. Still worth watching once in a real terminal.
- Haven't swept for other visual glitches beyond the CJK width bug and
  the line-padding bug above.
- PROGRESS.md's "Known limitations" section below is now stale in two
  places (scrollbar, queue search) and needs updating once this work is
  confirmed working end-to-end rather than just test-passing.

Tests: 56/56 passing, `ruff` clean.

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
- Song selector has a scrollbar now, but no search/jump-to within a
  long library.
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
