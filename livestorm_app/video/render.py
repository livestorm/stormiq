"""Surprise me's renderer: the saved timeline, shot list, voice clips and
punchline pictures in, one MP4 out (720p, 24 fps, H.264 and AAC,
`+faststart`). Runs only in the render worker
(`livestorm_app/render_worker.py`), never in the web process or the main
worker.

The video is parts joined end to end: the intro, the show's middle (its
kinetic parts and its punchline windows in turn, `film.layout`) and the
outro. The intro and the outro hold nothing from a show, so each is
rendered once (`render_bumper`), stored by the render worker in
`surprise_bumpers` under `bumper_key` and reused.

1. Chrome, once: a HyperFrames project in a temporary folder, `index.html`
   from `film.build_film` (every kinetic part on one page, each starting on
   a keyframe), the stylesheet and fonts from `templates/` and
   `frontend/public/fonts/`, GSAP from the image (`GSAP_JS_PATH`).
   Everything is a local file. The pinned `hyperframes` CLI
   (`HYPERFRAMES_BIN`, `HYPERFRAMES_VERSION` in `docker/Dockerfile.render`)
   renders the silent picture headless at 1280x720, 24 fps, a keyframe
   every `KEYFRAME_INTERVAL_S`, with `RENDER_WORKERS` capture workers (1 by
   default: one per CPU of the render worker's plan). Starting Chrome costs
   about as much as 250 frames, so it starts once, never once per part.
2. ffmpeg, once per punchline: its window's frames, drawn by Pillow
   (`video/punch.py`: the picture, its slow zoom, the caption), piped as
   JPEGs into the very encode HyperFrames runs (`encode_command`: its x264
   settings, read from 0.9.34), so every part has the same codec
   parameters.
3. ffmpeg joins the parts (`join_command`: the concat demuxer, each kinetic
   part cut out of Chrome's file by its in and out points, the picture
   copied, never encoded again) once ffprobe has shown they share their
   codec parameters, the SPS and PPS included (`same_encoding`), and checks
   the joined file has every frame (`count_frames`). When the parameters
   differ or a frame is missing, it encodes the join once with the same
   settings instead.
4. ffmpeg (`FFMPEG_BIN`) mixes the voice, the effects and the jingle on the
   whole video's clock and muxes the file (`mix.mix_command`).

Each step has a timeout; the Chrome step's grows with its page's length
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
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from livestorm_app.video import punch
from livestorm_app.video.film import build_bumper, build_film, layout, spans
from livestorm_app.video.keys import FPS, HEIGHT, KEYFRAME_FRAMES, KEYFRAME_INTERVAL_S, WIDTH
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
# agree): part of a bumper's key, so a new renderer makes new bumpers. When
# it changes, read its encode again (`encode_command`): a run whose parts
# differ still works, joined by one encode, but slower.
HYPERFRAMES_VERSION = "0.9.34"
# Bump when a bumper's look changes outside its page (fonts, GSAP, the
# capture, the encode): the page itself is in the key already.
BUMPER_VERSION = 2

# The Chrome step's timeout: a fixed start (Chrome, fonts) plus a budget
# per second of its page, up to a ceiling the job's timeout stays above
# (`render_worker.RENDER_JOB_TIMEOUT_SECONDS`).
PICTURE_TIMEOUT_BASE_S = 3 * 60
PICTURE_TIMEOUT_PER_S = 8
PICTURE_TIMEOUT_MAX_S = 15 * 60
PUNCH_TIMEOUT_S = 2 * 60
PROBE_TIMEOUT_S = 30
JOIN_TIMEOUT_S = 5 * 60
MIX_TIMEOUT_S = 3 * 60
# The picture's encode: about 9 MB for a minute at 720p. HyperFrames'
# `draft` quality (x264 `ultrafast`, Chrome's frames as JPEG at 80) makes the
# Chrome step about a fifth faster than `standard` (`medium`) for files about
# 60% bigger, with the same CRF and bitrate cap (owner: speed over quality).
PICTURE_QUALITY = "draft"
PICTURE_CRF = "22"
PICTURE_MAX_BITRATE = "1200k"
# HyperFrames 0.9.34's x264 preset and its own x264 settings, per quality,
# read from its ffmpeg call (`encode_command` repeats them).
X264_BY_QUALITY = {
    "draft": ("ultrafast", "aq-mode=3"),
    "standard": ("medium", "aq-mode=3:aq-strength=0.8:deblock=1,1"),
}
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
    """The Chrome step's timeout for `seconds` of page."""
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
    plan: Optional[Mapping[str, Any]] = None,
) -> Tuple[bool, list]:
    """Writes the kinetic parts' HyperFrames project: `(has a page, the
    whole video's sound effects to mix)`. No page when the middle is only
    punchlines."""
    page, effects = build_film(timeline, shots, pulse=pulse, css=_css(), plan=plan)
    if page is None:
        return False, effects
    _write_assets(folder)
    (folder / "index.html").write_text(page, encoding="utf-8")
    return True, effects


def bumper_page(kind: str, seconds: float) -> str:
    return build_bumper(kind, seconds, mark_svg=_mark(), css=_css())


def bumper_key(kind: str, seconds: float) -> str:
    """What a stored bumper was made from: `BUMPER_VERSION`, its kind and
    length, the encode and frame, the keyframe interval, the CLI's
    version, its page's bytes (the design, the stylesheet, the mark) and
    the files it draws (the background, the fonts). Any change makes a new
    key, and the render worker renders the new bumper on its next video."""
    page = bumper_page(kind, seconds)
    files = (TEMPLATES / "background.png", SORA_FONT, TEMPLATES / "fonts" / "LeagueGothic.ttf")
    parts = [
        str(BUMPER_VERSION), kind, f"{float(seconds):.3f}", f"{WIDTH}x{HEIGHT}@{FPS}",
        f"quality={PICTURE_QUALITY}", f"crf={PICTURE_CRF}", f"maxrate={PICTURE_MAX_BITRATE}", f"keyframes={KEYFRAME_INTERVAL_S}",
        f"hyperframes={HYPERFRAMES_VERSION}",
        hashlib.sha256(page.encode("utf-8")).hexdigest(),
        *(hashlib.sha256(path.read_bytes()).hexdigest() for path in files),
    ]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _run(
    run: Runner, args: list, timeout: float, what: str, cwd: Optional[Path] = None, stdin: Optional[bytes] = None
) -> subprocess.CompletedProcess:
    env = {**os.environ, "HYPERFRAMES_NO_TELEMETRY": "1", "DO_NOT_TRACK": "1"}
    extra: Dict[str, Any] = {"text": True} if stdin is None else {"input": stdin}
    try:
        done = run(args, cwd=str(cwd) if cwd else None, env=env, capture_output=True, timeout=timeout, check=False, **extra)
    except subprocess.TimeoutExpired:
        logger.error("[render] %s: timed out after %ss", what, int(timeout))
        raise RenderFailed(f"The video took too long to {what}. Please try again.") from None
    except OSError as exc:
        logger.error("[render] %s: could not start %s (%s)", what, args[0], type(exc).__name__)
        raise RenderFailed("The video renderer isn't set up on this server.") from None
    if done.returncode != 0:
        output = done.stderr or done.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", "replace")
        logger.error("[render] %s failed with exit %s: %s", what, done.returncode, output[-1500:])
        raise RenderFailed(f"The video could not {what}. Please try again.")
    return done


def _render_picture(run: Runner, project: Path, out: Path, seconds: float, job: str, part: str) -> None:
    """HyperFrames renders `project` to `out`, silent, and logs how fast."""
    started = time.monotonic()
    _run(run, [
        *hyperframes_command(), "render", str(project), "--output", str(out),
        "--fps", str(FPS), "--quality", PICTURE_QUALITY, "--crf", PICTURE_CRF, "--max-bitrate", PICTURE_MAX_BITRATE,
        "--keyframe-interval", KEYFRAME_INTERVAL_S, "--workers", str(capture_workers()), "--quiet",
    ], picture_timeout(seconds), "render its pictures", cwd=project)
    if not out.is_file() or out.stat().st_size == 0:
        raise RenderFailed("The video could not render its pictures. Please try again.")
    elapsed = time.monotonic() - started
    count = round(seconds * FPS)
    logger.info("[render] %s %s: %s frames in %.1fs (%.2f fps)", job, part, count, elapsed, count / max(elapsed, 0.001))


def render_bumper(kind: str, seconds: float, *, run: Runner = subprocess.run, job: str = "") -> bytes:
    """The intro's or the outro's silent picture, `seconds` long, encoded
    as every other part is."""
    with tempfile.TemporaryDirectory(prefix="surprise-bumper-") as raw:
        project = Path(raw) / "project"
        project.mkdir()
        _write_assets(project)
        (project / "index.html").write_text(bumper_page(kind, seconds), encoding="utf-8")
        out = Path(raw) / f"{kind}.mp4"
        _render_picture(run, project, out, seconds, job, kind)
        return out.read_bytes()


def encode_command(ffmpeg: str, out: Path) -> list:
    """The encode HyperFrames 0.9.34 runs on its captured frames (read from
    its own ffmpeg call, with `--quality`, `--crf`, `--max-bitrate` and
    `--keyframe-interval` as `_render_picture` passes them), fed JPEG
    frames on stdin, then written with the timescale its final file has. The
    same x264 settings give the same SPS and PPS, so the parts join by copy."""
    every = str(KEYFRAME_FRAMES)
    rate = int(PICTURE_MAX_BITRATE.rstrip("k")) * 1000
    preset, tuning = X264_BY_QUALITY[PICTURE_QUALITY]
    return [
        ffmpeg, "-v", "error", "-f", "image2pipe", "-vcodec", "mjpeg", "-framerate", str(FPS), "-i", "-",
        "-r", str(FPS), "-maxrate", str(rate), "-bufsize", str(2 * rate),
        "-c:v", "libx264", "-preset", preset, "-threads", "1", "-crf", PICTURE_CRF,
        "-g", every, "-keyint_min", every, "-sc_threshold", "0", "-force_key_frames", f"expr:eq(mod(n,{every}),0)",
        "-bf", "0",
        "-x264-params", f"{tuning}:colorprim=bt709:transfer=bt709:colormatrix=bt709:scenecut=0:open-gop=0:repeat-headers=1",
        "-colorspace:v", "bt709", "-color_primaries:v", "bt709", "-color_trc:v", "bt709", "-color_range", "tv",
        "-vf", "scale=in_color_matrix=bt601:in_range=pc:flags=neighbor+accurate_rnd,format=gbrp,"
               "scale=out_color_matrix=bt709:out_range=tv:flags=neighbor,"
               "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv",
        "-video_track_timescale", str(FPS * 512), "-pix_fmt", "yuv420p", "-avoid_negative_ts", "make_zero",
        "-y", str(out),
    ]


def render_punch(
    run: Runner, out: Path, picture: Optional[bytes], text: str, count: int, progress: Tuple[float, float], job: str, n: int
) -> None:
    """One punchline window, `count` frames (`video/punch.py`), encoded by
    `encode_command` into `out`."""
    started = time.monotonic()
    data = b"".join(punch.frames(picture, text, count, progress=progress))
    drawn = time.monotonic()
    _run(run, encode_command(ffmpeg_binary(), out), PUNCH_TIMEOUT_S, "draw its punchlines", stdin=data)
    if not out.is_file() or out.stat().st_size == 0:
        raise RenderFailed("The video could not draw its punchlines. Please try again.")
    logger.info("[render] %s punchline %s: %s frames (%s), drawn in %.1fs, encoded in %.1fs", job, n, count,
                "picture" if picture else "deadpan", drawn - started, time.monotonic() - drawn)


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


def count_frames(run: Runner, path: Path) -> Optional[int]:
    """How many pictures `path` holds (its packets, counted by ffprobe), or None."""
    done = _run(run, [
        ffprobe_binary(), "-v", "error", "-select_streams", "v:0", "-count_packets",
        "-show_entries", "stream=nb_read_packets", "-of", "json", str(path),
    ], PROBE_TIMEOUT_S, "check its pictures")
    try:
        return int((json.loads(done.stdout or "{}").get("streams") or [{}])[0]["nb_read_packets"])
    except (ValueError, AttributeError, IndexError, KeyError, TypeError):
        return None


def same_encoding(probes: Sequence[Mapping[str, Any]]) -> bool:
    """True when every part has the first one's codec parameters, each one
    read (a missing field never matches)."""
    first = probes[0] if probes else {}
    return bool(probes) and all(first.get(field) is not None for field in CODEC_FIELDS) \
        and all(dict(probe) == dict(first) for probe in probes)


# A part of the joined picture: a file, and for a piece of Chrome's file
# its first frame and its length in frames.
Piece = Tuple[str, Optional[int], Optional[int]]


def _seconds(frame: int) -> str:
    """A frame's time for the concat list, rounded down to the microsecond:
    the frame there is the first one out."""
    return f"{(frame * 1_000_000 // FPS) / 1_000_000:.6f}"


def concat_list(pieces: Sequence[Piece]) -> str:
    """The concat demuxer's list: each file, and for a piece of Chrome's
    file its in point (a keyframe) and out point."""
    lines = []
    for name, first, count in pieces:
        lines.append(f"file '{name}'")
        if first is not None and count is not None:
            if first:
                lines.append(f"inpoint {_seconds(first)}")
            lines.append(f"outpoint {_seconds(first + count)}")
    return "".join(f"{line}\n" for line in lines)


def join_command(ffmpeg: str, parts_list: Path, out: Path, *, copy: bool) -> list:
    """The parts end to end with the concat demuxer: copied when they share
    their encoding (each starts on a keyframe), else encoded once with the
    picture's own settings."""
    args = [ffmpeg, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(parts_list), "-map", "0:v:0", "-an"]
    if copy:
        args += ["-c:v", "copy"]
    else:
        args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", PICTURE_CRF, "-maxrate", PICTURE_MAX_BITRATE,
                 "-bufsize", "2400k", "-pix_fmt", "yuv420p", "-r", str(FPS)]
    return [*args, str(out)]


def _join(run: Runner, work: Path, pieces: Sequence[Piece], expected: int, job: str) -> Path:
    started = time.monotonic()
    files = list(dict.fromkeys(name for name, _, _ in pieces))
    probes = [probe_encoding(run, work / name) for name in files]
    copy = same_encoding(probes)
    if not copy:
        differ = sorted({field for probe in probes for field in CODEC_FIELDS if probe.get(field) != probes[0].get(field)})
        logger.warning("[render] %s join: the parts' encodings differ (%s); encoding the join", job, ",".join(differ) or "unread")
    listing = work / "parts.txt"
    listing.write_text(concat_list(pieces), encoding="utf-8")
    joined = work / "picture.mp4"
    _run(run, join_command(ffmpeg_binary(), listing, joined, copy=copy), JOIN_TIMEOUT_S, "join its parts", cwd=work)
    if not joined.is_file() or joined.stat().st_size == 0:
        raise RenderFailed("The video could not join its parts. Please try again.")
    if copy:
        found = count_frames(run, joined)
        if found != expected:
            logger.warning("[render] %s join: %s frames by copy, %s expected; encoding the join", job, found, expected)
            copy = False
            _run(run, join_command(ffmpeg_binary(), listing, joined, copy=False), JOIN_TIMEOUT_S, "join its parts", cwd=work)
            if not joined.is_file() or joined.stat().st_size == 0:
                raise RenderFailed("The video could not join its parts. Please try again.")
    logger.info("[render] %s join: %s parts %s in %.1fs", job, len(pieces), "copied" if copy else "encoded",
                time.monotonic() - started)
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
    (every one the timeline places); `images`: each punchline's picture
    (JPEG) by its number, a missing one drawn as the deadpan card;
    `bumpers`: the intro's and the outro's MP4 (`render_bumper`, for
    `film.bumper_spans(timeline)`). `keep`: a folder to copy the project
    and the parts into (local checks only). `job`: an id for the logs."""
    if not bumpers.get("intro") or not bumpers.get("outro"):
        raise RenderFailed("The video's intro or outro is missing. Please try again.")
    parts = spans(timeline)
    total = parts["total"]
    plan = layout(timeline)
    punches = {int(p["n"]): p for p in timeline.get("punches") or []}
    with tempfile.TemporaryDirectory(prefix="surprise-video-") as raw:
        work = Path(raw)
        project = work / "project"
        project.mkdir()
        has_page, effects = write_project(project, timeline, shots, pulse=pulse, plan=plan)
        voice = []
        for index, placed in enumerate(timeline.get("voice") or []):
            data = clips.get(str(placed["clip"]))
            if not data:
                raise RenderFailed("A line of the voice is missing. Make the show again.")
            path = work / f"line_{index:03d}.mp3"
            path.write_bytes(data)
            voice.append((path, float(placed["start"])))
        (work / "intro.mp4").write_bytes(bumpers["intro"])
        (work / "outro.mp4").write_bytes(bumpers["outro"])
        if has_page:
            _render_picture(run, project, work / "kinetic.mp4", plan["page_frames"] / FPS, job, "kinetic")
        middle = max(1, plan["middle_frames"])
        pieces: List[Piece] = [("intro.mp4", None, None)]
        for seg in plan["segments"]:
            if seg["kind"] == "kinetic":
                pieces.append(("kinetic.mp4", seg["c0"], seg["frames"]))
                continue
            name = f"punch_{seg['n']}.mp4"
            text = str((punches.get(seg["n"]) or {}).get("text") or "")
            render_punch(run, work / name, images.get(seg["n"]), text, seg["frames"],
                         (seg["f0"] / middle, seg["f1"] / middle), job, seg["n"])
            pieces.append((name, None, None))
        pieces.append(("outro.mp4", None, None))
        expected = round(parts["total"] * FPS)
        picture = _join(run, work, pieces, expected, job)
        out = work / "surprise-me.mp4"
        on_stage("mixing")
        started = time.monotonic()
        _run(run, mix_command(ffmpeg_binary(), picture, voice, effects, AUDIO_DIR, total, out), MIX_TIMEOUT_S, "mix its sound")
        if not out.is_file() or out.stat().st_size == 0:
            raise RenderFailed("The video could not mix its sound. Please try again.")
        logger.info("[render] %s mix: %.1fs", job, time.monotonic() - started)
        if keep is not None:
            shutil.copytree(project, keep, dirs_exist_ok=True)
            for name in dict.fromkeys(piece[0] for piece in pieces):
                if (work / name).is_file():
                    shutil.copyfile(work / name, keep / name)
            shutil.copyfile(work / "parts.txt", keep / "parts.txt")
        data = out.read_bytes()
    return {"mp4": data, "duration_s": round(total, 3), "size": len(data)}
