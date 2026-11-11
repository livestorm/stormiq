"""Surprise me's renderer: the saved timeline, shot list, voice clips and
pictures in, one MP4 out (720p, H.264 and AAC, `+faststart`). Runs only in
the render worker (`livestorm_app/render_worker.py`), never in the web
process or the main worker.

The video is three parts joined end to end: the intro, the show's middle
and the outro. The intro and the outro hold nothing from a show, so each is
rendered once (`render_bumper`), stored by the render worker in
`surprise_bumpers` under `bumper_key` and reused; a show renders only its
middle.

1. A HyperFrames project in a temporary folder: `index.html` from
   `film.build_film` (or `film.build_bumper`), the stylesheet and fonts
   from `templates/` and `frontend/public/fonts/`, GSAP from the image
   (`GSAP_JS_PATH`), the pictures as JPEGs. Everything is a local file.
2. The pinned `hyperframes` CLI (`HYPERFRAMES_BIN`, `HYPERFRAMES_VERSION`
   in `docker/Dockerfile.render`) renders the silent picture headless at
   1280x720, 24 fps, with `RENDER_WORKERS` capture workers (1 by default:
   one per CPU of the render worker's plan). Every part is encoded with the
   same settings (`PICTURE_CRF`, `PICTURE_MAX_BITRATE`).
3. ffmpeg joins the three parts (`join_command`: the concat demuxer, the
   picture copied, never encoded again) once ffprobe has shown they share
   their codec parameters (`same_encoding`); when they don't, it encodes
   the join once with the same settings.
4. ffmpeg (`FFMPEG_BIN`) mixes the voice, the effects and the jingle on the
   whole video's clock and muxes the file (`mix.mix_command`).

Each step has a timeout; the picture's grows with the part's length
(`picture_timeout`). A failure raises `RenderFailed`, whose message is what
the page shows; the tools' own output is logged (tail only), never
returned. Each step's time is logged with the job's id only.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shlex
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

from livestorm_app.video.film import build_bumper, build_film, spans
from livestorm_app.video.keys import FPS, HEIGHT, WIDTH
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

# The CLI's version pinned in docker/Dockerfile.render (a test checks they
# agree): part of a bumper's key, so a new renderer makes new bumpers.
HYPERFRAMES_VERSION = "0.9.34"
# Bump when a bumper's look changes outside its page (fonts, GSAP, the
# capture): the page itself is in the key already.
BUMPER_VERSION = 1

# The picture step's timeout: a fixed start (Chrome, fonts) plus a budget
# per second of video, up to a ceiling the job's timeout stays above
# (`render_worker.RENDER_JOB_TIMEOUT_SECONDS`).
PICTURE_TIMEOUT_BASE_S = 3 * 60
PICTURE_TIMEOUT_PER_S = 8
PICTURE_TIMEOUT_MAX_S = 25 * 60
PROBE_TIMEOUT_S = 30
JOIN_TIMEOUT_S = 5 * 60
MIX_TIMEOUT_S = 3 * 60
# The picture's encode: about 10 MB for two minutes at 720p.
PICTURE_CRF = "22"
PICTURE_MAX_BITRATE = "1200k"
# What `same_encoding` compares: the parts join by copy only when all agree.
CODEC_FIELDS = ("codec_name", "profile", "level", "width", "height", "pix_fmt", "r_frame_rate", "time_base", "extradata_hash")

Runner = Callable[..., subprocess.CompletedProcess]


class RenderFailed(RuntimeError):
    """The video could not be made. The message is what the page shows."""


def hyperframes_command() -> list:
    return shlex.split(os.environ.get("HYPERFRAMES_BIN", "hyperframes")) or ["hyperframes"]


def ffmpeg_binary() -> str:
    return os.environ.get("FFMPEG_BIN", "ffmpeg") or "ffmpeg"


def ffprobe_binary() -> str:
    return os.environ.get("FFPROBE_BIN", "ffprobe") or "ffprobe"


def capture_workers() -> int:
    """`RENDER_WORKERS`: how many headless Chrome workers capture frames at
    once. One per CPU of the render worker's plan; more only fight over it.
    1 when unset or not a number, at most `MAX_WORKERS`."""
    try:
        wanted = int(str(os.environ.get("RENDER_WORKERS", "1")).strip())
    except ValueError:
        return 1
    return max(1, min(wanted, MAX_WORKERS))


def picture_timeout(seconds: float) -> int:
    """The picture step's timeout for `seconds` of video."""
    return int(min(PICTURE_TIMEOUT_MAX_S, PICTURE_TIMEOUT_BASE_S + PICTURE_TIMEOUT_PER_S * max(0.0, float(seconds))))


def gsap_path() -> Path:
    return Path(os.environ.get("GSAP_JS_PATH", DEFAULT_GSAP))


def _css() -> str:
    return (TEMPLATES / "film.css").read_text(encoding="utf-8")


def _mark() -> str:
    return MARK_SVG.read_text(encoding="utf-8")


def _write_assets(folder: Path) -> Path:
    assets = folder / "assets"
    (assets / "fonts").mkdir(parents=True, exist_ok=True)
    gsap = gsap_path()
    if not gsap.is_file():
        raise RenderFailed("The video renderer isn't set up on this server (GSAP is missing).")
    shutil.copyfile(gsap, assets / "gsap.min.js")
    shutil.copyfile(SORA_FONT, assets / "fonts" / "Sora.woff2")
    shutil.copyfile(TEMPLATES / "fonts" / "LeagueGothic.ttf", assets / "fonts" / "LeagueGothic.ttf")
    shutil.copyfile(TEMPLATES / "background.png", assets / "background.png")
    (folder / "hyperframes.json").write_text(json.dumps({"name": "surprise-me"}), encoding="utf-8")
    return assets


def write_project(
    folder: Path,
    timeline: Mapping[str, Any],
    shots: Sequence[Mapping[str, Any]],
    *,
    pulse: Sequence[Mapping[str, Any]],
    images: Mapping[int, bytes],
) -> list:
    """Writes the middle's HyperFrames project; returns the sound effects
    to mix (the whole video's)."""
    assets = _write_assets(folder)
    names: Dict[int, str] = {}
    for shot_id, data in images.items():
        name = f"img_{int(shot_id):03d}.jpg"
        (assets / name).write_bytes(data)
        names[int(shot_id)] = name
    page, effects = build_film(timeline, shots, pulse=pulse, images=names, css=_css())
    (folder / "index.html").write_text(page, encoding="utf-8")
    return effects


def bumper_page(kind: str, seconds: float) -> str:
    return build_bumper(kind, seconds, mark_svg=_mark(), css=_css())


def bumper_key(kind: str, seconds: float) -> str:
    """What a stored bumper was made from: `BUMPER_VERSION`, its kind and
    length, the encode and frame, the CLI's version, its page's bytes (the
    design, the stylesheet, the mark) and the files it draws (the
    background, the fonts). Any change makes a new key, and the render
    worker renders the new bumper on its next video."""
    page = bumper_page(kind, seconds)
    files = (TEMPLATES / "background.png", SORA_FONT, TEMPLATES / "fonts" / "LeagueGothic.ttf")
    parts = [
        str(BUMPER_VERSION), kind, f"{float(seconds):.3f}", f"{WIDTH}x{HEIGHT}@{FPS}",
        f"crf={PICTURE_CRF}", f"maxrate={PICTURE_MAX_BITRATE}", f"hyperframes={HYPERFRAMES_VERSION}",
        hashlib.sha256(page.encode("utf-8")).hexdigest(),
        *(hashlib.sha256(path.read_bytes()).hexdigest() for path in files),
    ]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _run(run: Runner, args: list, timeout: float, what: str, cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "HYPERFRAMES_NO_TELEMETRY": "1", "DO_NOT_TRACK": "1"}
    try:
        done = run(args, cwd=str(cwd) if cwd else None, env=env, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        logger.error("[render] %s: timed out after %ss", what, int(timeout))
        raise RenderFailed(f"The video took too long to {what}. Please try again.") from None
    except OSError as exc:
        logger.error("[render] %s: could not start %s (%s)", what, args[0], type(exc).__name__)
        raise RenderFailed("The video renderer isn't set up on this server.") from None
    if done.returncode != 0:
        tail = (done.stderr or done.stdout or "")[-1500:]
        logger.error("[render] %s failed with exit %s: %s", what, done.returncode, tail)
        raise RenderFailed(f"The video could not {what}. Please try again.")
    return done


def _render_picture(run: Runner, project: Path, out: Path, seconds: float, job: str, part: str) -> None:
    """HyperFrames renders `project` to `out`, silent, and logs how fast."""
    started = time.monotonic()
    _run(run, [
        *hyperframes_command(), "render", str(project), "--output", str(out),
        "--fps", str(FPS), "--crf", PICTURE_CRF, "--max-bitrate", PICTURE_MAX_BITRATE,
        "--workers", str(capture_workers()), "--quiet",
    ], picture_timeout(seconds), "render its pictures", cwd=project)
    if not out.is_file() or out.stat().st_size == 0:
        raise RenderFailed("The video could not render its pictures. Please try again.")
    elapsed = time.monotonic() - started
    count = round(seconds * FPS)
    logger.info("[render] %s %s: %s frames in %.1fs (%.2f fps)", job, part, count, elapsed, count / max(elapsed, 0.001))


def render_bumper(kind: str, seconds: float, *, run: Runner = subprocess.run, job: str = "") -> bytes:
    """The intro's or the outro's silent picture, `seconds` long, encoded
    as every middle is."""
    with tempfile.TemporaryDirectory(prefix="surprise-bumper-") as raw:
        project = Path(raw) / "project"
        project.mkdir()
        _write_assets(project)
        (project / "index.html").write_text(bumper_page(kind, seconds), encoding="utf-8")
        out = Path(raw) / f"{kind}.mp4"
        _render_picture(run, project, out, seconds, job, kind)
        return out.read_bytes()


def probe_encoding(run: Runner, path: Path) -> Dict[str, Any]:
    """The picture's codec parameters (`CODEC_FIELDS`), read by ffprobe."""
    done = _run(run, [
        ffprobe_binary(), "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=" + ",".join(CODEC_FIELDS), "-show_data_hash", "sha256", "-of", "json", str(path),
    ], PROBE_TIMEOUT_S, "check its pictures")
    try:
        stream = (json.loads(done.stdout or "{}").get("streams") or [{}])[0]
    except (ValueError, AttributeError, IndexError):
        stream = {}
    return {field: stream.get(field) for field in CODEC_FIELDS}


def same_encoding(probes: Sequence[Mapping[str, Any]]) -> bool:
    """True when every part has the first one's codec parameters, each one
    read (a missing field never matches)."""
    first = probes[0] if probes else {}
    return bool(probes) and all(first.get(field) is not None for field in CODEC_FIELDS) \
        and all(dict(probe) == dict(first) for probe in probes)


def join_command(ffmpeg: str, parts_list: Path, out: Path, *, copy: bool) -> list:
    """The three parts end to end with the concat demuxer: copied when they
    share their encoding (each starts on a keyframe), else encoded once with
    the picture's own settings."""
    args = [ffmpeg, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(parts_list), "-map", "0:v:0", "-an"]
    if copy:
        args += ["-c:v", "copy"]
    else:
        args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", PICTURE_CRF, "-maxrate", PICTURE_MAX_BITRATE,
                 "-bufsize", "2400k", "-pix_fmt", "yuv420p", "-r", str(FPS)]
    return [*args, str(out)]


def _join(run: Runner, work: Path, parts: Sequence[Path], job: str) -> Path:
    started = time.monotonic()
    probes = [probe_encoding(run, part) for part in parts]
    copy = same_encoding(probes)
    if not copy:
        differ = sorted({field for probe in probes for field in CODEC_FIELDS if probe.get(field) != probes[0].get(field)})
        logger.warning("[render] %s join: the parts' encodings differ (%s); encoding the join", job, ",".join(differ) or "unread")
    listing = work / "parts.txt"
    listing.write_text("".join(f"file '{part.name}'\n" for part in parts), encoding="utf-8")
    joined = work / "picture.mp4"
    _run(run, join_command(ffmpeg_binary(), listing, joined, copy=copy), JOIN_TIMEOUT_S, "join its parts", cwd=work)
    if not joined.is_file() or joined.stat().st_size == 0:
        raise RenderFailed("The video could not join its parts. Please try again.")
    logger.info("[render] %s join: %s in %.1fs", job, "copied" if copy else "encoded", time.monotonic() - started)
    return joined


def render_video(
    timeline: Mapping[str, Any],
    shots: Sequence[Mapping[str, Any]],
    *,
    pulse: Sequence[Mapping[str, Any]],
    clips: Mapping[str, bytes],
    images: Mapping[int, bytes],
    bumpers: Mapping[str, bytes],
    run: Runner = subprocess.run,
    keep: Optional[Path] = None,
    on_stage: Callable[[str], None] = lambda stage: None,
    job: str = "",
) -> Dict[str, Any]:
    """`{mp4, duration_s, size}`. `clips`: each voice clip's mp3 by its key
    (every one the timeline places); `images`: each picture's JPEG by shot
    id; `bumpers`: the intro's and the outro's MP4 (`render_bumper`, for
    `film.bumper_spans(timeline)`). `keep`: a folder to copy the project
    into (local checks only). `job`: an id for the logs."""
    if not bumpers.get("intro") or not bumpers.get("outro"):
        raise RenderFailed("The video's intro or outro is missing. Please try again.")
    parts = spans(timeline)
    total = parts["total"]
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
        intro, middle, outro = work / "intro.mp4", work / "middle.mp4", work / "outro.mp4"
        intro.write_bytes(bumpers["intro"])
        outro.write_bytes(bumpers["outro"])
        _render_picture(run, project, middle, parts["middle"], job, "middle")
        picture = _join(run, work, [intro, middle, outro], job)
        out = work / "surprise-me.mp4"
        on_stage("mixing")
        started = time.monotonic()
        _run(run, mix_command(ffmpeg_binary(), picture, voice, effects, AUDIO_DIR, total, out), MIX_TIMEOUT_S, "mix its sound")
        if not out.is_file() or out.stat().st_size == 0:
            raise RenderFailed("The video could not mix its sound. Please try again.")
        logger.info("[render] %s mix: %.1fs", job, time.monotonic() - started)
        if keep is not None:
            shutil.copytree(project, keep, dirs_exist_ok=True)
        data = out.read_bytes()
    return {"mp4": data, "duration_s": round(total, 3), "size": len(data)}
