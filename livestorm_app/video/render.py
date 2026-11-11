"""Surprise me's renderer: the saved timeline, shot list, voice clips and
pictures in, one MP4 out (720p, H.264 and AAC, `+faststart`). Runs only in
the render worker (`livestorm_app/render_worker.py`), never in the web
process or the main worker.

1. A HyperFrames project in a temporary folder: `index.html` from
   `film.build_film`, the stylesheet and fonts from `templates/` and
   `frontend/public/fonts/`, GSAP from the image (`GSAP_JS_PATH`), the
   pictures as JPEGs. Everything is a local file: the render needs no network.
2. The pinned `hyperframes` CLI (`HYPERFRAMES_BIN`, 0.9.34 in
   `docker/Dockerfile.render`) renders the silent picture headless at
   1280x720, 24 fps, with `RENDER_WORKERS` capture workers (1 by default:
   one per CPU of the render worker's plan).
3. ffmpeg (`FFMPEG_BIN`) mixes the voice, the effects and the jingle and
   muxes the file (`mix.mix_command`).

Each step has a timeout. A failure raises `RenderFailed`, whose message is
what the page shows; the tools' own output is logged (tail only), never
returned.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

from livestorm_app.video.film import build_film
from livestorm_app.video.keys import FPS
from livestorm_app.video.mix import mix_command

logger = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE / "templates"
AUDIO_DIR = TEMPLATES / "audio"
REPO = HERE.parents[1]
SORA_FONT = REPO / "frontend" / "public" / "fonts" / "Sora-Variable-latin.woff2"
MARK_SVG = REPO / "frontend" / "public" / "favicon.svg"
DEFAULT_GSAP = "/opt/stormiq-render/node_modules/gsap/dist/gsap.min.js"
MAX_WORKERS = 4

PICTURE_TIMEOUT_S = 16 * 60
MIX_TIMEOUT_S = 3 * 60
# The picture's encode: about 10 MB for two minutes at 720p.
PICTURE_CRF = "22"
PICTURE_MAX_BITRATE = "1200k"

Runner = Callable[..., subprocess.CompletedProcess]


class RenderFailed(RuntimeError):
    """The video could not be made. The message is what the page shows."""


def hyperframes_command() -> list:
    return shlex.split(os.environ.get("HYPERFRAMES_BIN", "hyperframes")) or ["hyperframes"]


def ffmpeg_binary() -> str:
    return os.environ.get("FFMPEG_BIN", "ffmpeg") or "ffmpeg"


def capture_workers() -> int:
    """`RENDER_WORKERS`: how many headless Chrome workers capture frames at
    once. One per CPU of the render worker's plan; more only fight over it.
    1 when unset or not a number, at most `MAX_WORKERS`."""
    try:
        wanted = int(str(os.environ.get("RENDER_WORKERS", "1")).strip())
    except ValueError:
        return 1
    return max(1, min(wanted, MAX_WORKERS))


def gsap_path() -> Path:
    return Path(os.environ.get("GSAP_JS_PATH", DEFAULT_GSAP))


def write_project(
    folder: Path,
    timeline: Mapping[str, Any],
    shots: Sequence[Mapping[str, Any]],
    *,
    pulse: Sequence[Mapping[str, Any]],
    images: Mapping[int, bytes],
) -> list:
    """Writes the HyperFrames project; returns the sound effects to mix."""
    assets = folder / "assets"
    (assets / "fonts").mkdir(parents=True, exist_ok=True)
    gsap = gsap_path()
    if not gsap.is_file():
        raise RenderFailed("The video renderer isn't set up on this server (GSAP is missing).")
    shutil.copyfile(gsap, assets / "gsap.min.js")
    shutil.copyfile(SORA_FONT, assets / "fonts" / "Sora.woff2")
    shutil.copyfile(TEMPLATES / "fonts" / "LeagueGothic.ttf", assets / "fonts" / "LeagueGothic.ttf")
    names: Dict[int, str] = {}
    for shot_id, data in images.items():
        name = f"img_{int(shot_id):03d}.jpg"
        (assets / name).write_bytes(data)
        names[int(shot_id)] = name
    page, effects = build_film(
        timeline, shots, pulse=pulse, images=names,
        mark_svg=MARK_SVG.read_text(encoding="utf-8"), css=(TEMPLATES / "film.css").read_text(encoding="utf-8"),
    )
    (folder / "index.html").write_text(page, encoding="utf-8")
    (folder / "hyperframes.json").write_text(json.dumps({"name": "surprise-me"}), encoding="utf-8")
    return effects


def _run(run: Runner, args: list, timeout: float, what: str, cwd: Optional[Path] = None) -> None:
    env = {**os.environ, "HYPERFRAMES_NO_TELEMETRY": "1", "DO_NOT_TRACK": "1"}
    try:
        done = run(args, cwd=str(cwd) if cwd else None, env=env, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise RenderFailed(f"The video took too long to {what}. Please try again.") from None
    except OSError as exc:
        logger.error("[render] %s: could not start %s (%s)", what, args[0], type(exc).__name__)
        raise RenderFailed("The video renderer isn't set up on this server.") from None
    if done.returncode != 0:
        tail = (done.stderr or done.stdout or "")[-1500:]
        logger.error("[render] %s failed with exit %s: %s", what, done.returncode, tail)
        raise RenderFailed(f"The video could not {what}. Please try again.")


def render_video(
    timeline: Mapping[str, Any],
    shots: Sequence[Mapping[str, Any]],
    *,
    pulse: Sequence[Mapping[str, Any]],
    clips: Mapping[str, bytes],
    images: Mapping[int, bytes],
    run: Runner = subprocess.run,
    keep: Optional[Path] = None,
    on_stage: Callable[[str], None] = lambda stage: None,
) -> Dict[str, Any]:
    """`{mp4, duration_s, size}`. `clips`: each voice clip's mp3 by its key
    (every one the timeline places); `images`: each picture's JPEG by shot
    id. `keep`: a folder to copy the project into (local checks only)."""
    total = float(timeline["total"])
    with tempfile.TemporaryDirectory(prefix="surprise-video-") as raw:
        work = Path(raw)
        project = work / "project"
        project.mkdir()
        effects = write_project(project, timeline, shots, pulse=pulse, images=images)
        voice = []
        for index, placed in enumerate(timeline.get("voice") or []):
            data = clips.get(str(placed["clip"]))
            if not data:
                raise RenderFailed("A line of the voice is missing. Make the show again.")
            path = work / f"line_{index:03d}.mp3"
            path.write_bytes(data)
            voice.append((path, float(placed["start"])))
        picture = work / "picture.mp4"
        _run(run, [
            *hyperframes_command(), "render", str(project), "--output", str(picture),
            "--fps", str(FPS), "--crf", PICTURE_CRF, "--max-bitrate", PICTURE_MAX_BITRATE,
            "--workers", str(capture_workers()), "--quiet",
        ], PICTURE_TIMEOUT_S, "render its pictures", cwd=project)
        if not picture.is_file() or picture.stat().st_size == 0:
            raise RenderFailed("The video could not render its pictures. Please try again.")
        out = work / "surprise-me.mp4"
        on_stage("mixing")
        _run(run, mix_command(ffmpeg_binary(), picture, voice, effects, AUDIO_DIR, total, out), MIX_TIMEOUT_S, "mix its sound")
        if not out.is_file() or out.stat().st_size == 0:
            raise RenderFailed("The video could not mix its sound. Please try again.")
        if keep is not None:
            shutil.copytree(project, keep, dirs_exist_ok=True)
        data = out.read_bytes()
    return {"mp4": data, "duration_s": round(total, 3), "size": len(data)}
