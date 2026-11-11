"""Surprise me's punchline windows, drawn without Chrome: each frame a
Pillow picture, handed to ffmpeg as JPEGs and encoded with the renderer's
own settings (`video/render.py`), so the window joins the Chrome parts by
copy.

- **With a picture** (`ai/surprise_images.py`): the picture full screen,
  covering the frame, in a slow zoom (Ken Burns, `ZOOM` over the window,
  drifting a little up), a navy scrim on its lower part, and the
  punchline in big condensed capitals (League Gothic) over it, with a soft
  shadow, popping in over its first frames (`POP_FRAMES`).
- **Without one** (no prompt, a failed picture): the deadpan card, as
  before the pictures: the punchline in condensed capitals on black, a
  hard pop in and a slow push (`PUSH`).
- The progress bar runs on, as in the Chrome parts (`progress`).

The caption is laid out by `caption_lines` (upper case, wrapped to fit,
the size shrunk until it does); characters the font lacks (emoji) are
left out. Its text is drawn as pixels: nothing from the session reaches a
command line or a filter.

Pure: no database, no network, no subprocess.
"""

from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

from livestorm_app.video.keys import HEIGHT, WIDTH

FONT_PATH = Path(__file__).resolve().parent / "templates" / "fonts" / "LeagueGothic.ttf"
INK = (244, 247, 255)
NAVY = (10, 20, 48)
BLACK = (3, 5, 12)
TEAL = (46, 230, 201)
BLUE = (59, 123, 255)
ZOOM = 0.08  # the picture grows by this much over the window
DRIFT = 0.04  # and its centre rises by this share of the frame
PUSH = 0.04  # the deadpan card's push-in
POP_FRAMES = 3
POP_PICTURE = 1.12
POP_DEADPAN = 1.45
PROGRESS_H = 5  # the bar at the bottom, as in film.css (8 px at 1080p)
JPEG_QUALITY = 92


def _font(size: int):
    return _cached_font(int(size))


@lru_cache(maxsize=64)
def _cached_font(size: int):
    from PIL import ImageFont

    return ImageFont.truetype(str(FONT_PATH), size)


@lru_cache(maxsize=1)
def _missing_box() -> Optional[Tuple[int, int, int, int]]:
    return _font(100).getmask("￿").getbbox()


def drawable(text: str) -> str:
    """`text` in upper case, its spaces folded, without the characters the
    font has no glyph for (emoji)."""
    font = _font(100)
    missing = _missing_box()
    kept = []
    for char in " ".join(str(text or "").split()).upper():
        if char == " " or font.getmask(char).getbbox() != missing:
            kept.append(char)
    return " ".join("".join(kept).split())


def caption_height(lines: int, size: int) -> int:
    """The caption block's height: `lines` lines of `size` px, 0.92 apart."""
    return round(size * 0.92) * (lines - 1) + size


def caption_lines(
    text: str, *, max_width: int, sizes: range, max_lines: int, max_height: Optional[int] = None
) -> Tuple[List[str], int]:
    """The caption's lines and font size: the biggest size in `sizes` at
    which the words wrap into at most `max_lines` lines of at most
    `max_width` pixels, no taller than `max_height` (the smallest size
    otherwise, its lines as wrapped)."""
    words = drawable(text).split() or ["…"]
    lines: List[str] = []
    size = sizes[-1] if len(sizes) else 60
    for size in sizes:
        font = _font(size)
        lines = []
        for word in words:
            trial = f"{lines[-1]} {word}" if lines else word
            if lines and font.getlength(trial) <= max_width:
                lines[-1] = trial
            else:
                lines.append(word)
        tall = max_height is not None and caption_height(len(lines), size) > max_height
        if len(lines) <= max_lines and not tall and all(font.getlength(line) <= max_width for line in lines):
            return lines, size
    return lines, size


def caption_layer(text: str, *, deadpan: bool):
    """The caption over a transparent frame (RGBA), and its centre."""
    from PIL import Image, ImageDraw, ImageFilter

    if deadpan:
        # Narrower than the frame: the push-in grows it by `PUSH`.
        lines, size = caption_lines(text, max_width=1100, sizes=range(220, 59, -6), max_lines=4, max_height=HEIGHT - 160)
    else:
        lines, size = caption_lines(text, max_width=1160, sizes=range(150, 49, -6), max_lines=3, max_height=HEIGHT // 2 - 40)
    font = _font(size)
    step = round(size * 0.92)
    block = caption_height(len(lines), size)
    top = (HEIGHT - block) // 2 if deadpan else HEIGHT - 70 - block
    mask = Image.new("L", (WIDTH, HEIGHT), 0)
    draw = ImageDraw.Draw(mask)
    for k, line in enumerate(lines):
        width = font.getlength(line)
        draw.text(((WIDTH - width) / 2, top + k * step), line, font=font, fill=255)
    shadow = mask.filter(ImageFilter.GaussianBlur(10)).point(lambda v: int(v * 0.8))
    layer = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    layer.paste(Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 255)), (0, 6), shadow)
    layer.paste(Image.new("RGBA", (WIDTH, HEIGHT), (*INK, 255)), (0, 0), mask)
    return layer, (WIDTH / 2, top + block / 2)


def _scaled(layer, center: Tuple[float, float], scale: float):
    """`layer` scaled about `center`, on a frame of the same size."""
    from PIL import Image

    if abs(scale - 1.0) < 1e-4:
        return layer
    width, height = round(WIDTH * scale), round(HEIGHT * scale)
    big = layer.resize((width, height), Image.BICUBIC)
    out = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    out.paste(big, (round(center[0] - center[0] * scale), round(center[1] - center[1] * scale)), big)
    return out


def _cover(picture: bytes):
    """The picture as RGB, scaled so it covers the frame (its ratio kept)."""
    from PIL import Image

    with Image.open(io.BytesIO(picture)) as source:
        image = source.convert("RGB")
    scale = max(WIDTH / image.width, HEIGHT / image.height)
    if scale > 1.0:
        image = image.resize((round(image.width * scale), round(image.height * scale)), Image.LANCZOS)
    return image


def _scrim():
    """Navy, clear above 40% of the height, 85% opaque at the bottom."""
    from PIL import Image

    start = int(HEIGHT * 0.4)
    ramp = [0] * start + [round(217 * (y - start) / (HEIGHT - start)) for y in range(start, HEIGHT)]
    column = Image.new("L", (1, HEIGHT))
    column.putdata(ramp)
    alpha = column.resize((WIDTH, HEIGHT))
    scrim = Image.new("RGBA", (WIDTH, HEIGHT), (*NAVY, 0))
    scrim.putalpha(alpha)
    return scrim


def _bar():
    """The progress bar's gradient, blue to teal, full width."""
    from PIL import Image

    row = Image.new("RGB", (WIDTH, 1))
    row.putdata([tuple(round(BLUE[c] + (TEAL[c] - BLUE[c]) * x / (WIDTH - 1)) for c in range(3)) for x in range(WIDTH)])
    return row.resize((WIDTH, PROGRESS_H))


def _ease(x: float) -> float:
    return 1 - (1 - x) ** 3


def frames(
    picture: Optional[bytes],
    text: str,
    count: int,
    *,
    progress: Tuple[float, float] = (0.0, 1.0),
) -> Iterator[bytes]:
    """The window's `count` frames as JPEGs: the picture (or the deadpan
    card when `picture` is None or unreadable) with `text` as its caption.
    `progress`: the bar's share of the width at the first frame and after
    the last."""
    from PIL import Image

    count = max(1, int(count))
    base = None
    if picture:
        try:
            base = _cover(picture)
        except Exception:  # an unreadable picture: the deadpan card
            base = None
    deadpan = base is None
    layer, center = caption_layer(text, deadpan=deadpan)
    scrim = None if deadpan else _scrim()
    bar = _bar()
    black = Image.new("RGB", (WIDTH, HEIGHT), BLACK)
    for i in range(count):
        x = i / max(1, count - 1)
        if deadpan:
            frame = black.copy().convert("RGBA")
            pop = POP_DEADPAN + (1 - POP_DEADPAN) * _ease(min(1.0, i / POP_FRAMES)) if i < POP_FRAMES else 1.0
            scale = pop * (1 + PUSH * x)
        else:
            zoom = 1 + ZOOM * x
            box_w, box_h = WIDTH / zoom, HEIGHT / zoom
            cx = base.width / 2
            cy = base.height / 2 - DRIFT * HEIGHT * x
            cy = min(max(cy, box_h / 2), base.height - box_h / 2)
            box = (cx - box_w / 2, cy - box_h / 2, cx + box_w / 2, cy + box_h / 2)
            frame = base.resize((WIDTH, HEIGHT), Image.BICUBIC, box=box).convert("RGBA")
            frame.alpha_composite(scrim)
            scale = POP_PICTURE + (1 - POP_PICTURE) * _ease(min(1.0, i / POP_FRAMES)) if i < POP_FRAMES else 1.0
        frame.alpha_composite(_scaled(layer, center, scale))
        frame = frame.convert("RGB")
        share = progress[0] + (progress[1] - progress[0]) * i / count
        width = max(0, min(WIDTH, round(WIDTH * share)))
        if width:
            # As the page's bar: the whole gradient, scaled to its width.
            frame.paste(bar.resize((width, PROGRESS_H)), (0, HEIGHT - PROGRESS_H))
        out = io.BytesIO()
        frame.save(out, format="JPEG", quality=JPEG_QUALITY, subsampling=0)
        yield out.getvalue()
