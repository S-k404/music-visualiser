"""Render album artwork as HD terminal 'pixel art' using quadrant block
characters with 24-bit truecolor. Each character cell is split into a
2x2 sub-pixel grid; we pick the best 2-colour (foreground/background)
approximation per cell and the matching Unicode quadrant glyph, giving
4 independently-coloured sub-pixels per cell -- 2x the resolution in
*both* directions versus a plain one-pixel-per-glyph or half-block-only
renderer."""

from __future__ import annotations

import colorsys
import io

from PIL import Image

from . import ansi

RGB = tuple[int, int, int]
_UNSET = object()  # distinct sentinel; no real RGB tuple can equal it

# Quadrant bit order: bit0=top-left, bit1=top-right, bit2=bottom-left,
# bit3=bottom-right. Value = the glyph showing exactly those quadrants
# "on" (foreground); the rest are background.
_QUADRANT_GLYPHS = {
    0b0000: " ",
    0b0001: "▘",
    0b0010: "▝",
    0b0011: "▀",
    0b0100: "▖",
    0b0101: "▌",
    0b0110: "▞",
    0b0111: "▛",
    0b1000: "▗",
    0b1001: "▚",
    0b1010: "▐",
    0b1011: "▜",
    0b1100: "▄",
    0b1101: "▙",
    0b1110: "▟",
    0b1111: "█",
}


def _luminance(rgb: RGB) -> float:
    r, g, b = rgb
    return 0.299 * r + 0.587 * g + 0.114 * b


def _avg_color(colors: list[RGB]) -> RGB:
    n = len(colors)
    return (
        sum(c[0] for c in colors) // n,
        sum(c[1] for c in colors) // n,
        sum(c[2] for c in colors) // n,
    )


def _quadrant_cell(tl: RGB, tr: RGB, bl: RGB, br: RGB) -> tuple[str, RGB | None, RGB]:
    """Pick a glyph + (fg, bg) pair approximating this 2x2 pixel block.

    Splits the 4 sub-pixels into a "lighter" and "darker" group by
    luminance (relative to their own mean, so it adapts per-cell rather
    than using a fixed global threshold), then averages each group's
    colour. Returns (glyph, fg_or_None, bg) -- fg is None when the glyph
    is a plain space (flat region reduced to background only).
    """
    quad = [tl, tr, bl, br]
    lums = [_luminance(c) for c in quad]
    mean_lum = sum(lums) / 4

    mask = 0
    fg_group: list[RGB] = []
    bg_group: list[RGB] = []
    for i, (color, lum) in enumerate(zip(quad, lums, strict=True)):
        if lum >= mean_lum:
            mask |= 1 << i
            fg_group.append(color)
        else:
            bg_group.append(color)

    glyph = _QUADRANT_GLYPHS[mask]
    bg = _avg_color(bg_group) if bg_group else _avg_color(fg_group)
    if glyph == " ":
        return glyph, None, bg
    fg = _avg_color(fg_group)
    return glyph, fg, bg


def _placeholder_image(size: tuple[int, int]) -> Image.Image:
    """Generate a pleasant gradient when a track has no embedded artwork."""
    w, h = max(size[0], 1), max(size[1], 1)
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            t = (x / max(w - 1, 1) + y / max(h - 1, 1)) / 2
            hue = 0.70 - 0.45 * t
            r, g, b = colorsys.hsv_to_rgb(hue, 0.55, 0.22 + 0.5 * (1 - t))
            px[x, y] = (int(r * 255), int(g * 255), int(b * 255))
    return img


def render_album_art(artwork: bytes | None, cell_width: int, cell_height: int) -> list[str]:
    """Return `cell_height` ANSI-coloured strings, each `cell_width`
    characters wide, depicting the artwork (or a generated placeholder)
    at quadrant-block resolution (2x2 sub-pixels per character).

    Terminal character cells are roughly twice as tall as they are wide
    (e.g. ~9x18px), not square. A quadrant cell's two *columns* already
    line up 1:1 with that -- one source sample each -- but its two *rows*
    don't: naively sampling cell_height*2 source rows (one per quadrant
    row) stretches the image vertically by ~2x on screen, since each
    quadrant row only covers half the cell's height but the cell itself
    is twice as tall as it is wide. Sampling cell_height*4 rows instead,
    and averaging each adjacent pair into one quadrant row, keeps the
    source image's proportions intact (confirmed against the real
    stretching this produced before the fix).
    """
    cell_width = max(cell_width, 1)
    cell_height = max(cell_height, 1)
    px_w, px_h = cell_width * 2, cell_height * 4

    img: Image.Image | None = None
    if artwork:
        try:
            img = Image.open(io.BytesIO(artwork)).convert("RGB")
        except Exception:  # noqa: BLE001 -- arbitrary embedded artwork from
            # user files; any decode failure should fall back to the
            # placeholder gradient, not crash the visualiser
            img = None
    if img is None:
        img = _placeholder_image((px_w, px_h))

    img = img.resize((px_w, px_h), Image.LANCZOS)
    pixels = img.load()

    rows: list[str] = []
    for cy in range(cell_height):
        top_y0, top_y1 = cy * 4, min(cy * 4 + 1, px_h - 1)
        bot_y0, bot_y1 = min(cy * 4 + 2, px_h - 1), min(cy * 4 + 3, px_h - 1)
        parts: list[str] = []
        last_fg: object = _UNSET
        last_bg: object = _UNSET
        for cx in range(cell_width):
            left_x, right_x = cx * 2, min(cx * 2 + 1, px_w - 1)
            tl = _avg_color([pixels[left_x, top_y0], pixels[left_x, top_y1]])
            tr = _avg_color([pixels[right_x, top_y0], pixels[right_x, top_y1]])
            bl = _avg_color([pixels[left_x, bot_y0], pixels[left_x, bot_y1]])
            br = _avg_color([pixels[right_x, bot_y0], pixels[right_x, bot_y1]])

            glyph, fg, bg = _quadrant_cell(tl, tr, bl, br)
            if bg != last_bg:
                parts.append(ansi.bg(*bg))
                last_bg = bg
            if fg is not None and fg != last_fg:
                parts.append(ansi.fg(*fg))
                last_fg = fg
            parts.append(glyph)
        parts.append(ansi.RESET)
        rows.append("".join(parts))
    return rows
