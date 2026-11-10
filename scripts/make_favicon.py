"""Build StormIQ's "IQ" monogram from the wordmark's own letters.

The monogram (the favicon, and the square spots of the app) is the "I" and
"Q" of the StormIQ wordmark, Sora at weight 600, in white on the mark's
blue disc (docs/brand.md). This script draws them from the font the app
ships, so the favicon and the wordmark are the same letters:

- `frontend/public/favicon.svg`: the disc and the two letters as outlines
  (no font needed to show it);
- `frontend/public/favicon.ico` (16, 32, 48 px) and
  `frontend/public/apple-touch-icon.png` (180 px, on white);
- it prints the letters' path for `frontend/src/components/ui/StormIqMark.vue`,
  which draws the same monogram in the app.

Not part of the app or its dependencies. Run it in a throwaway environment:

    python3 -m venv /tmp/fontvenv && /tmp/fontvenv/bin/pip install fonttools brotli pillow
    /tmp/fontvenv/bin/python scripts/make_favicon.py

It reads only the font in `frontend/public/fonts/` and writes the three
files above. Nothing is sent anywhere.
"""

from __future__ import annotations

import io
from pathlib import Path

from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "frontend" / "public"
FONT = PUBLIC / "fonts" / "Sora-Variable-latin.woff2"

BOX = 28.0  # the monogram's units (viewBox 0 0 28 28)
DISC = "#0B42C3"  # `--siq-mark-fill`, light theme
INK = "#FFFFFF"  # `--siq-mark-ink`
WEIGHT = 600  # the wordmark's weight
CAP_HEIGHT = 11.0  # the letters' cap height, in monogram units
TEXT = "IQ"


def semibold() -> TTFont:
    font = TTFont(str(FONT))
    font.flavor = None
    return instantiateVariableFont(font, {"wght": WEIGHT})


def layout(font: TTFont):
    """Each letter's glyph name and x offset in font units (advance widths,
    no kerning), the scale to monogram units, and the offsets that centre
    the letters in the box: across by their outline, up and down by the
    cap height, so the Q's tail hangs below as in the wordmark."""
    cmap = font.getBestCmap()
    glyphs = font.getGlyphSet()
    hmtx = font["hmtx"]
    x, placed = 0, []
    for char in TEXT:
        name = cmap[ord(char)]
        placed.append((name, x))
        x += hmtx[name][0]
    bounds = BoundsPen(glyphs)
    for name, offset in placed:
        glyphs[name].draw(TransformPen(bounds, (1, 0, 0, 1, offset, 0)))
    x_min, _y_min, x_max, _y_max = bounds.bounds
    scale = CAP_HEIGHT / font["OS/2"].sCapHeight
    dx = (BOX - (x_max - x_min) * scale) / 2 - x_min * scale
    baseline = (BOX + CAP_HEIGHT) / 2  # y grows downwards in the box
    return glyphs, placed, scale, dx, baseline


def letters_path(font: TTFont) -> str:
    glyphs, placed, scale, dx, baseline = layout(font)
    pen = SVGPathPen(glyphs, ntos=lambda value: f"{value:.2f}".rstrip("0").rstrip("."))
    for name, offset in placed:
        # Font units, y up → box units, y down.
        glyphs[name].draw(TransformPen(pen, (scale, 0, 0, -scale, dx + offset * scale, baseline)))
    return pen.getCommands()


def svg(path: str) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 28 28">'
        "<title>StormIQ</title>"
        f'<circle cx="14" cy="14" r="14" fill="{DISC}"/>'
        f'<path d="{path}" fill="{INK}"/>'
        "</svg>\n"
    )


def raster(font: TTFont, size: int, *, background: str | None = None, inset: float = 0.0) -> Image.Image:
    """The monogram at `size` px, drawn 8× larger then reduced, so its edges
    are smooth. `inset` shrinks the disc inside the square (for the
    home-screen icon on white)."""
    big = size * 8
    image = Image.new("RGBA", (big, big), background or (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    pad = big * inset
    draw.ellipse((pad, pad, big - pad, big - pad), fill=DISC)
    data = io.BytesIO()
    font.save(data)
    _glyphs, placed, scale, dx, baseline = layout(font)
    unit = (big - 2 * pad) / BOX
    em = font["head"].unitsPerEm
    typeface = ImageFont.truetype(io.BytesIO(data.getvalue()), size=round(em * scale * unit))
    for (_name, offset), char in zip(placed, TEXT, strict=True):
        x = pad + (dx + offset * scale) * unit
        draw.text((x, pad + baseline * unit), char, font=typeface, fill=INK, anchor="ls")
    return image.resize((size, size), Image.LANCZOS)


def main() -> None:
    font = semibold()
    path = letters_path(font)
    (PUBLIC / "favicon.svg").write_text(svg(path), encoding="utf-8")
    icons = [raster(font, size) for size in (16, 32, 48)]
    icons[-1].save(PUBLIC / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)], append_images=icons[:-1])
    raster(font, 180, background="#FFFFFF", inset=0.08).convert("RGB").save(PUBLIC / "apple-touch-icon.png")
    print("wrote favicon.svg, favicon.ico, apple-touch-icon.png")
    print("letters path for StormIqMark.vue:")
    print(path)


if __name__ == "__main__":
    main()
