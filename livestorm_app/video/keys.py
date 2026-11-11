"""The cache key of Surprise me's video: what it was made from. Imported by
the web process (the show's status) and both workers; no renderer here."""

from __future__ import annotations

import hashlib
from typing import Iterable

# Bump when the templates, the film's design, the mix or the encode change:
# every video key changes with it, and the next click renders again.
RENDER_VERSION = 2

VIDEO_MIME = "video/mp4"
# 24 fps: a fifth fewer frames to capture than 30, and kinetic type and
# stills look the same.
WIDTH, HEIGHT, FPS = 1280, 720, 24


def video_key(timeline_key: str, shots_key: str, image_keys: Iterable[str]) -> str:
    """sha256 of `RENDER_VERSION`, the timeline's key (which covers every
    clip), the shot list's key and its pictures' keys."""
    parts = [str(RENDER_VERSION), str(timeline_key), str(shots_key), *sorted(str(key) for key in image_keys)]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
