"""Session cover images, rendered with the OpenAI Images API."""

from __future__ import annotations

import base64
from typing import Any, Dict

import requests

from livestorm_app.config import (
    COVER_IMAGE_MODEL,
    COVER_IMAGE_QUALITY,
    COVER_IMAGE_SIZE,
    OPENAI_IMAGES_URL,
)

# Image models OpenAI has shut down (gpt-image-1 on 2026-10-23). A setting or
# env var still naming one is replaced, so covers keep working.
RETIRED_IMAGE_MODELS = {"gpt-image-1"}
REPLACEMENT_IMAGE_MODEL = "gpt-image-2"


def resolve_image_model(model: str) -> str:
    """The image model to send: a retired id becomes its replacement."""
    name = str(model or "").strip()
    if not name or name.lower() in RETIRED_IMAGE_MODELS:
        return REPLACEMENT_IMAGE_MODEL
    return name


def generate_cover_image_with_openai(
    api_key: str,
    *,
    prompt: str,
    model: str = "",
    size: str = COVER_IMAGE_SIZE,
    quality: str = COVER_IMAGE_QUALITY,
    timeout: int = 180,
) -> Dict[str, Any]:
    """Call the OpenAI Images API and return the raw image bytes + mime.

    Uses the same `requests` pattern as the chat-completion helpers
    above. Returns `{ "bytes": <raw png>, "mime": "image/png" }`. The
    bytes are decoded from the API's base64 response so the caller can
    push them straight into a Postgres BYTEA column.

    Raises a clear RuntimeError when the API key is missing, the
    response payload doesn't contain image data, or the upstream
    request fails — caller decides whether to log+skip or surface to
    the user.
    """
    key = str(api_key or "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is required for cover image generation.")

    if not model:
        try:
            from livestorm_app.db import get_setting
            model = get_setting("image_model") or COVER_IMAGE_MODEL
        except Exception:
            model = COVER_IMAGE_MODEL
    model = resolve_image_model(model)

    payload = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "quality": quality,
        "n": 1,
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    response = requests.post(OPENAI_IMAGES_URL, headers=headers, json=payload, timeout=timeout)
    response.raise_for_status()
    body = response.json()
    data = body.get("data") if isinstance(body, dict) else None
    first = data[0] if isinstance(data, list) and data else None
    b64 = str((first or {}).get("b64_json") or "").strip()
    if not b64:
        raise RuntimeError(
            "OpenAI Images response did not include `b64_json` (model="
            f"{model!r}). Inspect the raw response for upstream errors."
        )
    image_bytes = base64.b64decode(b64)
    # The OpenAI Images API returns PNG by default. The endpoint accepts
    # an `output_format` flag for jpeg/webp but we don't pass one — keep
    # the mime consistent with what's in the bytes.
    return {"bytes": image_bytes, "mime": "image/png"}
