"""Simple generated cover art for playlist folders (used by the "playlist" layout)."""

from __future__ import annotations

import colorsys
import hashlib
import io
import unicodedata
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_FILE = Path(__file__).parent / "assets" / "Inter-Bold.otf"


@lru_cache(maxsize=8)
def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_FILE), size)


def _printable(text: str) -> str:
    """Drop emoji and other symbols the font cannot draw."""
    kept = "".join(c for c in text if unicodedata.category(c)[0] not in ("S", "C") or c in "&+")
    return " ".join(kept.split()) or "Playlist"


def _colors(title: str) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    hue = int(hashlib.sha1(title.encode("utf-8")).hexdigest()[:4], 16) / 0xFFFF

    def rgb(h: float, lightness: float, saturation: float) -> tuple[int, int, int]:
        r, g, b = colorsys.hls_to_rgb(h % 1.0, lightness, saturation)
        return int(r * 255), int(g * 255), int(b * 255)

    return rgb(hue, 0.42, 0.65), rgb(hue + 0.12, 0.22, 0.55)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if draw.textlength(candidate, font=font) <= width or not line:
            line = candidate
        else:
            lines.append(line)
            line = word
    lines.append(line)
    return lines


def playlist_cover(title: str, size: int = 1000) -> bytes:
    """A minimal square cover: colour gradient, small equalizer motif and the playlist name."""
    text = _printable(title)
    top, bottom = _colors(title)
    img = Image.new("RGB", (size, size), top)
    # diagonal gradient: rotate a twice-as-large ramp and keep its centre, so no corner is left empty
    gradient = Image.linear_gradient("L").resize((size * 2, size * 2)).rotate(45)
    gradient = gradient.crop((size // 2, size // 2, size // 2 + size, size // 2 + size))
    img = Image.composite(Image.new("RGB", (size, size), bottom), img, gradient)
    draw = ImageDraw.Draw(img)

    margin = size // 10
    # equalizer bars (the app's logo motif) in the top-left corner
    bar_w, gap = size // 40, size // 55
    for i, h in enumerate((0.45, 0.8, 1.0, 0.65, 0.35)):
        x = margin + i * (bar_w + gap)
        bar_h = int(size * 0.09 * h)
        y = margin + int(size * 0.09) - bar_h
        draw.rounded_rectangle((x, y, x + bar_w, y + bar_h), radius=bar_w // 2, fill=(255, 255, 255, 230))

    # title: as large as fits in at most three lines
    max_width = size - 2 * margin
    for font_size in range(size // 7, size // 22, -4):
        font = _font(font_size)
        lines = _wrap(draw, text, font, max_width)
        if len(lines) <= 3 and all(draw.textlength(line, font=font) <= max_width for line in lines):
            break
    line_h = int(font.size * 1.12)
    y = size - margin - line_h * len(lines)
    for line in lines:
        draw.text((margin, y), line, font=font, fill=(255, 255, 255))
        y += line_h

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90, optimize=True)
    return buf.getvalue()
