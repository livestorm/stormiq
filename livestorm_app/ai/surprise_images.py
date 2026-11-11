"""Surprise me's illustrations: one playful picture per punchline, four per
video at most, from the director's prompts (`ai/surprise_director.py`,
`check_pictures`), by OpenAI Images, drawn full screen behind the
punchline's caption (`video/punch.py`).

- **What is sent** (`image_request`): the director's prompt, already checked
  (`surprise_director.image_prompt_ok`: no name, company, product or contact
  detail), after a fixed style (`STYLE`): a flat illustration, objects
  only, no text, no real people, no logos. Nothing else about the session.
- **The call** (`generate_image`): one POST per picture to
  `/v1/images/generations` (`IMAGE_MODEL`, `IMAGE_QUALITY`, `IMAGE_SIZE`)
  with a timeout, retried only for answers that mean nothing was made (429,
  5xx, a connection error), never after a read timeout. A failure raises
  `ImageFailed`; that punchline then gets the deadpan card.
- **The file** (`to_jpeg`): scaled to 1280 px wide, saved as a small JPEG,
  stored with the shot list (`surprise_show_images`), keyed by
  `image_key` (the model, quality, size, style and prompt).

No database, no web framework.
"""

from __future__ import annotations

import base64
import hashlib
import io
import time
from typing import Callable, Tuple

import requests

OPENAI_IMAGES_URL = "https://api.openai.com/v1/images/generations"
IMAGE_MODEL = "gpt-image-2"
IMAGE_QUALITY = "medium"
IMAGE_SIZE = "1536x1024"
IMAGE_MIME = "image/jpeg"
JPEG_WIDTH = 1280
JPEG_QUALITY = 78
IMAGE_TIMEOUT = (10, 240)  # connect, read: a medium picture takes up to a minute or two
IMAGE_ATTEMPTS = 2
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
IMAGE_KEY_PATTERN = r"^[0-9a-f]{64}$"

STYLE = (
    "Playful flat vector illustration, bold shapes, dark navy background (#0A1430) with teal (#2EE6C9) and "
    "electric blue (#3B7BFF) accents, soft glow, lots of empty space around a single clear visual gag, "
    "cartoon objects only. Absolutely no text, letters, numbers, logos, brands or real people. Scene: "
)


class ImageFailed(RuntimeError):
    """A picture could not be made. Its message never holds the key or OpenAI's answer."""


def image_request(prompt: str) -> dict:
    return {
        "model": IMAGE_MODEL,
        "prompt": STYLE + " ".join(str(prompt or "").split()),
        "size": IMAGE_SIZE,
        "quality": IMAGE_QUALITY,
        "n": 1,
    }


def image_key(prompt: str) -> str:
    """sha256 of exactly what is sent for the picture."""
    body = image_request(prompt)
    parts = [body["model"], body["quality"], body["size"], body["prompt"], str(JPEG_WIDTH), str(JPEG_QUALITY)]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def to_jpeg(png: bytes, *, width: int = JPEG_WIDTH, quality: int = JPEG_QUALITY) -> bytes:
    """The picture scaled to `width` (its ratio kept) as a JPEG."""
    from PIL import Image

    with Image.open(io.BytesIO(png)) as image:
        image = image.convert("RGB")
        if image.width > width:
            image = image.resize((width, round(image.height * width / image.width)), Image.LANCZOS)
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=quality, optimize=True, progressive=True)
        return out.getvalue()


def generate_image(
    api_key: str,
    prompt: str,
    *,
    timeout: Tuple[float, float] = IMAGE_TIMEOUT,
    attempts: int = IMAGE_ATTEMPTS,
    sleep: Callable[[float], None] = time.sleep,
) -> bytes:
    """One picture for `prompt`, as a JPEG (`to_jpeg`). Raises `ImageFailed`."""
    key = str(api_key or "").strip()
    if not key:
        raise ImageFailed("no OpenAI key")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    payload = image_request(prompt)
    for attempt in range(attempts):
        last = attempt == attempts - 1
        try:
            response = requests.post(OPENAI_IMAGES_URL, headers=headers, json=payload, timeout=timeout)
        except requests.exceptions.ReadTimeout:
            raise ImageFailed("read timeout") from None
        except requests.exceptions.ConnectionError:
            if last:
                raise ImageFailed("connection error") from None
            sleep(float(2 ** attempt))
            continue
        except requests.RequestException:
            raise ImageFailed("request failed") from None
        if response.status_code in RETRY_STATUSES and not last:
            sleep(float(2 ** attempt))
            continue
        if not response.ok:
            raise ImageFailed(f"HTTP {response.status_code}")
        try:
            encoded = response.json()["data"][0]["b64_json"]
            png = base64.b64decode(encoded)
            return to_jpeg(png)
        except Exception:
            raise ImageFailed("no picture in the answer") from None
    raise ImageFailed("no answer")
