"""Audio-reactive renderers: a log-frequency spectrum (eighth-block bars,
sub-character vertical resolution) and a braille-dot oscilloscope waveform
(2x4 sub-pixels per character -- the highest density ASCII can offer)."""

from __future__ import annotations

import colorsys
from functools import lru_cache

import numpy as np

from . import ansi

EIGHTHS = " ▁▂▃▄▅▆▇█"  # " ▁▂▃▄▅▆▇█"
BRAILLE_BASE = 0x2800
# Unicode braille dot bit positions for a 2 (col) x 4 (row) sub-grid.
_DOT_BITS_COL0 = (0, 1, 2, 6)
_DOT_BITS_COL1 = (3, 4, 5, 7)


# Row colour only depends on (row, total_rows), which is stable across
# frames (it only changes on terminal resize) -- caching avoids redoing
# the colorsys HSV->RGB conversion and ANSI string build on every one of
# the ~20-30 frames/sec this is called for.
@lru_cache(maxsize=256)
def _row_color_bars(row: int, rows: int) -> str:
    """Vivid bottom(blue/violet) -> top(orange/yellow) gradient."""
    row_frac = row / max(rows - 1, 1)
    hue = 0.78 - 0.68 * row_frac
    value = 0.75 + 0.25 * row_frac
    r, g, b = colorsys.hsv_to_rgb(hue % 1.0, 0.85, value)
    return ansi.fg(int(r * 255), int(g * 255), int(b * 255))


@lru_cache(maxsize=256)
def _row_color_wave(row: int, height_chars: int) -> str:
    """Cyan glow, brightest near the centre line."""
    center_dist = min(abs((row + 0.5) - height_chars / 2.0) / (height_chars / 2.0), 1.0)
    value = 1.0 - 0.55 * center_dist
    r, g, b = colorsys.hsv_to_rgb(0.5, 0.65, max(value, 0.25))
    return ansi.fg(int(r * 255), int(g * 255), int(b * 255))


class SpectrumAnalyzer:
    """Stateful log-frequency spectrum with auto-gain and asymmetric
    attack/decay smoothing, so bars rise fast and fall gracefully."""

    def __init__(self, n_bars: int, sample_rate: int, attack: float = 0.6, decay: float = 0.15):
        self.n_bars = max(n_bars, 1)
        self.sample_rate = sample_rate
        self.attack = attack
        self.decay = decay
        self.smoothed = np.zeros(self.n_bars, dtype=np.float64)
        self.running_max = 1e-6

    def resize(self, n_bars: int) -> None:
        n_bars = max(n_bars, 1)
        if n_bars != self.n_bars:
            self.n_bars = n_bars
            self.smoothed = np.zeros(self.n_bars, dtype=np.float64)

    def update(self, samples: np.ndarray) -> np.ndarray:
        mono = samples.mean(axis=1) if samples.ndim == 2 else samples
        raw = _compute_spectrum(mono, self.sample_rate, self.n_bars)

        peak = float(raw.max()) if raw.size else 0.0
        self.running_max = max(peak, self.running_max * 0.995, 1e-6)
        normalized = raw / self.running_max

        rising = normalized > self.smoothed
        self.smoothed[rising] += (normalized[rising] - self.smoothed[rising]) * self.attack
        self.smoothed[~rising] += (normalized[~rising] - self.smoothed[~rising]) * self.decay
        return np.clip(self.smoothed, 0.0, 1.0)


def _compute_spectrum(mono: np.ndarray, sample_rate: int, n_bars: int) -> np.ndarray:
    if mono.size < 8 or sample_rate <= 0:
        return np.zeros(n_bars)

    windowed = mono * np.hanning(len(mono))
    spectrum = np.abs(np.fft.rfft(windowed))
    freqs = np.fft.rfftfreq(len(mono), d=1.0 / sample_rate)

    min_freq, max_freq = 40.0, min(sample_rate / 2 * 0.92, 16000.0)
    edges = np.geomspace(min_freq, max_freq, n_bars + 1)

    levels = np.zeros(n_bars)
    idx = np.searchsorted(freqs, edges)
    for i in range(n_bars):
        lo, hi = idx[i], max(idx[i + 1], idx[i] + 1)
        band = spectrum[lo:hi]
        if band.size:
            levels[i] = band.mean()
    return np.log1p(levels * 40.0)


def render_bars(levels: np.ndarray, rows: int) -> list[str]:
    """levels: 1D array in [0, 1], one entry per output column."""
    n = len(levels)
    rows = max(rows, 1)
    total_units = rows * 8
    lit = np.clip(levels, 0.0, 1.0) * total_units

    out_lines: list[str] = []
    for r in range(rows - 1, -1, -1):
        base = r * 8
        color = _row_color_bars(r, rows)
        chars = []
        any_lit = False
        for amt in lit:
            amt_i = round(amt)
            if amt_i >= base + 8:
                chars.append(EIGHTHS[8])
                any_lit = True
            elif amt_i > base:
                chars.append(EIGHTHS[amt_i - base])
                any_lit = True
            else:
                chars.append(" ")
        line = f"{color}{''.join(chars)}{ansi.RESET}" if any_lit else " " * n
        out_lines.append(line)
    return out_lines


def render_waveform(samples: np.ndarray, width_chars: int, height_chars: int) -> list[str]:
    width_chars = max(width_chars, 1)
    height_chars = max(height_chars, 1)
    sub_w = width_chars * 2
    sub_h = height_chars * 4

    mono = samples.mean(axis=1) if samples.ndim == 2 else samples
    n = mono.shape[0]
    if n == 0:
        mono = np.zeros(sub_w, dtype=np.float32)
        n = sub_w

    edges = np.linspace(0, n, sub_w + 1).astype(int)
    mins = np.empty(sub_w)
    maxs = np.empty(sub_w)
    for i in range(sub_w):
        a, b = edges[i], max(edges[i + 1], edges[i] + 1)
        chunk = mono[a:min(b, n)]
        if chunk.size == 0:
            mins[i] = maxs[i] = 0.0
        else:
            mins[i] = float(chunk.min())
            maxs[i] = float(chunk.max())

    mid = sub_h / 2.0
    row_top = np.clip((mid - maxs * mid).astype(int), 0, sub_h - 1)
    row_bot = np.clip((mid - mins * mid).astype(int), 0, sub_h - 1)

    cells = [[0] * width_chars for _ in range(height_chars)]
    for x in range(sub_w):
        col = x // 2
        bits = _DOT_BITS_COL0 if x % 2 == 0 else _DOT_BITS_COL1
        lo, hi = (row_top[x], row_bot[x]) if row_top[x] <= row_bot[x] else (row_bot[x], row_top[x])
        for y in range(lo, hi + 1):
            row = y // 4
            sub_row = y % 4
            cells[row][col] |= 1 << bits[sub_row]

    lines: list[str] = []
    for r, row in enumerate(cells):
        color = _row_color_wave(r, height_chars)
        chars = "".join(chr(BRAILLE_BASE + v) if v else " " for v in row)
        lines.append(f"{color}{chars}{ansi.RESET}")
    return lines
