from __future__ import annotations

import hashlib
import http.client
import json
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

try:
    import imageio_ffmpeg
except ModuleNotFoundError:
    imageio_ffmpeg = None
from dotenv import load_dotenv

DEFAULT_MODEL = "gladia-v2-pre-recorded"
DEFAULT_AUDIO_SAMPLE_RATE = 16000
GLADIA_API_BASE = "https://api.gladia.io"
GLADIA_UPLOAD_PATH = "/v2/upload"
GLADIA_PRE_RECORDED_PATH = "/v2/pre-recorded"
GLADIA_POLL_INTERVAL_SECONDS = 3
GLADIA_POLL_TIMEOUT_SECONDS = 30 * 60
# Re-report progress at least this often while Gladia's status is unchanged
# (keeps the worker's job heartbeat fresh; see _poll_gladia_transcription).
PROGRESS_HEARTBEAT_SECONDS = 60
# Gladia standard plan supports up to 135 min per request; enterprise up to 255 min.
GLADIA_MAX_AUDIO_CHUNK_SECONDS = 135 * 60
LIVESTORM_API_BASE = "https://api.livestorm.co/v1"
# Socket timeout for every JSON call (Gladia and Livestorm). Without one, a
# stalled connection would hang the worker thread forever.
HTTP_TIMEOUT_SECONDS = 60
# A paid transcription can poll for 30+ minutes; a brief network or Gladia
# hiccup must not throw it away. Give up after this many failed polls in a row.
GLADIA_POLL_MAX_CONSECUTIVE_ERRORS = 5
# GETs to Livestorm (recording list) are retried this many times in total.
LIVESTORM_GET_MAX_ATTEMPTS = 3
# ffprobe or `ffmpeg -i` reading the recording's header. Local work, but a
# stuck probe must not hold a transcription slot.
MEDIA_PROBE_TIMEOUT_SECONDS = 60

# The speaker cap of a new transcript (docs/product.md §13, "Speaker cap"):
# the people on stage plus a margin, never under the floor nor over the
# ceiling. The ceiling is also the cap when nobody is known to be on stage.
MAX_SPEAKERS_MARGIN = 2
MAX_SPEAKERS_FLOOR = 3
MAX_SPEAKERS_CEILING = 20

# What a Gladia job carries to be found again (`custom_metadata`): ids, counts
# and labels only, never a key, token, connection id or URL.
GLADIA_METADATA_KEYS = (
    "stormiq_session_id",
    "recording_id",
    "file_size",
    "env",
    "chunk",
    "chunks",
    "offset_s",
    "config",
)
GLADIA_METADATA_MAX_CHARS = 2000
# `config` is this many hex characters of the request's SHA-256.
CONFIG_FINGERPRINT_CHARS = 12
# The `stormiq` provenance block of a stored transcript. 2: the block exists.
PIPELINE_VERSION = 2


class TransientHTTPError(RuntimeError):
    """A failure worth retrying: timeout, dropped connection, HTTP 429 or 5xx."""


DEFAULT_GLADIA_OPTIONS: dict[str, Any] = {
    "diarization": True,
    "diarization_config": {
        # Gladia recommendation for meeting/webinar recorders: supply a speaker
        # range so the model adapts instead of having to auto-detect from scratch.
        # The worker narrows the range to the people on stage (`speaker_options`).
        "min_speakers": 1,
        "max_speakers": MAX_SPEAKERS_CEILING,
    },
    "named_entity_recognition": True,
    "sentences": True,
    "subtitles": True,
    "subtitles_config": {
        "formats": ["srt", "vtt"],
    },
}


def _resolve_api_key(explicit_api_key: str | None = None) -> str:
    api_key = str(explicit_api_key or "").strip()
    if api_key:
        return api_key
    load_dotenv()
    api_key = os.getenv("GLADIA_KEY")
    if not api_key:
        raise RuntimeError("Missing Gladia API key. Set GLADIA_KEY in your environment or .env file.")
    return api_key


def _resolve_livestorm_api_key(explicit_api_key: str | None = None) -> str:
    api_key = str(explicit_api_key or "").strip()
    if api_key:
        return api_key
    load_dotenv()
    api_key = os.getenv("LS_API_KEY")
    if not api_key:
        raise RuntimeError("Missing Livestorm API key. Set LS_API_KEY in your environment or .env file.")
    return api_key


def _ffmpeg_executable() -> str:
    if imageio_ffmpeg is not None:
        return imageio_ffmpeg.get_ffmpeg_exe()

    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg

    raise RuntimeError(
        "FFmpeg is required but not available. Install the Python package "
        "`imageio-ffmpeg` with `pip install -r requirements.txt` or install "
        "a system `ffmpeg` binary and make sure it is on your PATH."
    )


def _extract_audio(video_path: Path, audio_path: Path) -> None:
    # WAV PCM (pcm_s16le) at 16 kHz mono — Gladia's recommended format for
    # reliable uploads. Larger than MP3 but avoids decode overhead on Gladia's side.
    ffmpeg_path = _ffmpeg_executable()
    command = [
        ffmpeg_path,
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(DEFAULT_AUDIO_SAMPLE_RATE),
        "-c:a",
        "pcm_s16le",
        str(audio_path),
    ]

    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() if exc.stderr else "Unknown ffmpeg error"
        raise RuntimeError(f"Audio extraction failed: {stderr}") from exc


def _ffprobe_executable() -> str | None:
    ffmpeg_path = Path(_ffmpeg_executable())
    sibling_ffprobe = ffmpeg_path.with_name("ffprobe")
    if sibling_ffprobe.exists():
        return str(sibling_ffprobe)
    return shutil.which("ffprobe")


def _parse_ffmpeg_duration(stderr: str) -> float | None:
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", stderr)
    if not match:
        return None

    hours = int(match.group(1))
    minutes = int(match.group(2))
    seconds = float(match.group(3))
    return (hours * 60 * 60) + (minutes * 60) + seconds


def _probe_media_duration_seconds(media_path: Path) -> float | None:
    ffprobe_path = _ffprobe_executable()
    if ffprobe_path:
        command = [
            ffprobe_path,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(media_path),
        ]
        try:
            completed = subprocess.run(command, check=True, capture_output=True, text=True)
            payload = json.loads(completed.stdout or "{}")
            duration = payload.get("format", {}).get("duration")
            if isinstance(duration, str) and duration.strip():
                return float(duration)
            if isinstance(duration, (int, float)):
                return float(duration)
        except (subprocess.CalledProcessError, json.JSONDecodeError, ValueError):
            pass

    ffmpeg_command = [
        _ffmpeg_executable(),
        "-i",
        str(media_path),
        "-f",
        "null",
        "-",
    ]
    try:
        completed = subprocess.run(ffmpeg_command, check=False, capture_output=True, text=True)
    except subprocess.CalledProcessError:
        return None
    return _parse_ffmpeg_duration(completed.stderr or "")


def _media_seconds(value: Any) -> float | None:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return round(seconds, 3) if seconds >= 0 and seconds != float("inf") else None


def _media_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text[:64] if text else None


def _ffprobe_media_info(media_path: Path) -> dict[str, Any] | None:
    ffprobe_path = _ffprobe_executable()
    if not ffprobe_path:
        return None
    command = [
        ffprobe_path,
        "-v",
        "error",
        "-show_entries",
        "format=duration:format_tags=creation_time",
        "-of",
        "json",
        str(media_path),
    ]
    try:
        completed = subprocess.run(
            command, check=True, capture_output=True, text=True, timeout=MEDIA_PROBE_TIMEOUT_SECONDS
        )
        payload = json.loads(completed.stdout or "{}")
    except (subprocess.SubprocessError, OSError, ValueError):
        return None
    media_format = payload.get("format") if isinstance(payload, dict) else None
    if not isinstance(media_format, dict):
        return None
    tags = media_format.get("tags")
    tags = tags if isinstance(tags, dict) else {}
    return {
        "duration_s": _media_seconds(media_format.get("duration")),
        "creation_time": _media_text(tags.get("creation_time")),
    }


def _ffmpeg_media_info(media_path: Path) -> dict[str, Any] | None:
    """The same facts from the header `ffmpeg -i` prints. It names no output,
    so ffmpeg reads the header, exits with an error and decodes nothing."""
    try:
        completed = subprocess.run(
            [_ffmpeg_executable(), "-hide_banner", "-i", str(media_path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=MEDIA_PROBE_TIMEOUT_SECONDS,
        )
    except (subprocess.SubprocessError, OSError, RuntimeError):
        return None
    stderr = completed.stderr or ""
    # The container's own tags come before its first `Duration:` line; the
    # streams' tags come after it.
    container_header = stderr.split("Duration:", 1)[0]
    creation_time = re.search(r"^\s*creation_time\s*:\s*(\S+)", container_header, re.MULTILINE)
    return {
        "duration_s": _media_seconds(_parse_ffmpeg_duration(stderr)),
        "creation_time": _media_text(creation_time.group(1)) if creation_time else None,
    }


def _probe_media_info(video_path: Path) -> dict[str, Any] | None:
    """The recording file's own duration and `creation_time` tag, read from its
    container before the audio is extracted: `{duration_s, creation_time}`.

    ffprobe when it is installed, otherwise the header `ffmpeg -i` prints (the
    Docker images ship only imageio-ffmpeg's ffmpeg). `creation_time` is kept
    as written: whether it marks the recording's start is not confirmed, so
    nothing reads it yet. None when neither fact was found. Never raises: this
    is provenance, not a reason to fail a paid transcription.
    """
    try:
        info = _ffprobe_media_info(video_path) or _ffmpeg_media_info(video_path)
    except Exception:  # noqa: BLE001 - provenance must never fail the job
        return None
    if not info or all(value is None for value in info.values()):
        return None
    return info


def _format_ffmpeg_segment_timestamp(total_seconds: int) -> str:
    hours, remainder = divmod(total_seconds, 60 * 60)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _split_audio_file(audio_path: Path, chunk_dir: Path) -> list[tuple[Path, float]]:
    duration_seconds = _probe_media_duration_seconds(audio_path)
    if duration_seconds is None or duration_seconds <= GLADIA_MAX_AUDIO_CHUNK_SECONDS:
        return [(audio_path, 0.0)]

    ffmpeg_path = _ffmpeg_executable()
    chunk_dir.mkdir(parents=True, exist_ok=True)
    chunk_pattern = chunk_dir / f"{audio_path.stem}.chunk-%03d{audio_path.suffix}"
    command = [
        ffmpeg_path,
        "-y",
        "-i",
        str(audio_path),
        "-f",
        "segment",
        "-segment_time",
        _format_ffmpeg_segment_timestamp(GLADIA_MAX_AUDIO_CHUNK_SECONDS),
        "-c",
        "copy",
        "-reset_timestamps",
        "1",
        str(chunk_pattern),
    ]

    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() if exc.stderr else "Unknown ffmpeg error"
        raise RuntimeError(f"Audio splitting failed: {stderr}") from exc

    chunk_paths = sorted(chunk_dir.glob(f"{audio_path.stem}.chunk-*{audio_path.suffix}"))
    if not chunk_paths:
        raise RuntimeError("Audio splitting failed: no chunk files were created.")

    chunk_specs: list[tuple[Path, float]] = []
    next_offset_seconds = 0.0
    for chunk_path in chunk_paths:
        chunk_specs.append((chunk_path, next_offset_seconds))
        chunk_duration = _probe_media_duration_seconds(chunk_path)
        if chunk_duration is None or chunk_duration <= 0:
            raise RuntimeError(f"Audio splitting failed: could not determine duration for chunk {chunk_path.name}.")
        next_offset_seconds += chunk_duration

    return chunk_specs


def _json_request(
    *,
    method: str,
    url: str,
    api_key: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data: bytes | None = None
    headers = {
        "accept": "application/json",
        "x-gladia-key": api_key,
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["content-type"] = "application/json"

    request = urllib.request.Request(url=url, data=data, headers=headers, method=method)
    return _urlopen_json(request, "Gladia API request")


def _urlopen_json(request: urllib.request.Request, label: str) -> dict[str, Any]:
    """Send `request` with a timeout and decode the JSON body.

    Raises TransientHTTPError for failures worth retrying (timeouts, dropped
    connections, HTTP 429/5xx) and RuntimeError for everything else.
    """
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        error_class = TransientHTTPError if exc.code == 429 or exc.code >= 500 else RuntimeError
        raise error_class(f"{label} failed with HTTP {exc.code}: {details}") from exc
    except urllib.error.URLError as exc:
        raise TransientHTTPError(f"{label} failed: {exc.reason}") from exc
    except (TimeoutError, ConnectionError) as exc:
        raise TransientHTTPError(f"{label} failed: {type(exc).__name__}") from exc


def _upload_audio_file(audio_path: Path, api_key: str) -> dict[str, Any]:
    mime_type = mimetypes.guess_type(audio_path.name)[0] or "application/octet-stream"
    boundary = f"gladia-{uuid.uuid4().hex}"
    preamble = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="audio"; filename="{audio_path.name}"\r\n'
        f"Content-Type: {mime_type}\r\n\r\n"
    ).encode("utf-8")
    epilogue = f"\r\n--{boundary}--\r\n".encode("utf-8")
    content_length = len(preamble) + audio_path.stat().st_size + len(epilogue)

    connection = http.client.HTTPSConnection("api.gladia.io", timeout=120)
    try:
        connection.putrequest("POST", GLADIA_UPLOAD_PATH)
        connection.putheader("x-gladia-key", api_key)
        connection.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
        connection.putheader("Accept", "application/json")
        connection.putheader("Content-Length", str(content_length))
        connection.endheaders()
        connection.send(preamble)
        with audio_path.open("rb") as audio_handle:
            while True:
                chunk = audio_handle.read(1024 * 1024)
                if not chunk:
                    break
                connection.send(chunk)
        connection.send(epilogue)

        response = connection.getresponse()
        body = response.read().decode("utf-8", errors="replace")
        if response.status >= 400:
            raise RuntimeError(f"Gladia upload failed with HTTP {response.status}: {body}")
        return json.loads(body) if body else {}
    except OSError as exc:
        raise RuntimeError(f"Gladia upload failed: {exc}") from exc
    finally:
        connection.close()


def _deep_merge(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(dict(merged[key]), value)
            continue
        merged[key] = value
    return merged


def max_speakers_for(people_on_stage: int | None) -> int:
    """Gladia's `max_speakers` for a recording with `people_on_stage` people on
    stage: that count plus `MAX_SPEAKERS_MARGIN`, between `MAX_SPEAKERS_FLOOR`
    and `MAX_SPEAKERS_CEILING`. Unknown (None, or not a positive count) keeps
    the ceiling, as before the cap existed (docs/product.md §13). With a
    margin of 2 the floor never binds (1 + 2 = 3); it matters only if the
    margin drops."""
    if isinstance(people_on_stage, bool) or not isinstance(people_on_stage, int) or people_on_stage <= 0:
        return MAX_SPEAKERS_CEILING
    return min(MAX_SPEAKERS_CEILING, max(MAX_SPEAKERS_FLOOR, people_on_stage + MAX_SPEAKERS_MARGIN))


def speaker_options(people_on_stage: int | None) -> dict[str, Any]:
    """The Gladia options that set the speaker range for `people_on_stage`."""
    return {"diarization_config": {"min_speakers": 1, "max_speakers": max_speakers_for(people_on_stage)}}


def environment_tag() -> str:
    """Where this process runs: `render` (Render sets `RENDER` on every
    service) or `local`. A label for job metadata, never a secret."""
    return "render" if str(os.environ.get("RENDER") or "").strip() else "local"


def gladia_config_fingerprint(gladia_request: Mapping[str, Any]) -> str:
    """The first `CONFIG_FINGERPRINT_CHARS` hex characters of the SHA-256 of a
    Gladia request without its `audio_url` and `custom_metadata`: the same
    settings always give the same fingerprint, whatever the file."""
    settings = {key: value for key, value in gladia_request.items() if key not in ("audio_url", "custom_metadata")}
    canonical = json.dumps(settings, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:CONFIG_FINGERPRINT_CHARS]


def _custom_metadata(metadata: Mapping[str, Any], config: str) -> dict[str, Any]:
    """The `custom_metadata` a Gladia job carries: the keys of
    `GLADIA_METADATA_KEYS` only, as strings or numbers, plus the request's
    `config` fingerprint. A None value is left out. Raises ValueError, before
    anything is sent, on any other key or value, or when the metadata runs
    over `GLADIA_METADATA_MAX_CHARS` as JSON."""
    allowed = set(GLADIA_METADATA_KEYS) - {"config"}
    unknown = sorted(str(key) for key in metadata if key not in allowed)
    if unknown:
        raise ValueError(f"Gladia job metadata takes no {unknown}.")
    custom = {key: metadata[key] for key in GLADIA_METADATA_KEYS if key in allowed and metadata.get(key) is not None}
    custom["config"] = config
    for key, value in custom.items():
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ValueError(f"Gladia job metadata {key} must be a string or a number.")
    if len(json.dumps(custom, separators=(",", ":"), ensure_ascii=True)) > GLADIA_METADATA_MAX_CHARS:
        raise ValueError(f"Gladia job metadata is longer than {GLADIA_METADATA_MAX_CHARS} characters.")
    return custom


def _build_gladia_request(
    audio_url: str,
    gladia_options: dict[str, Any] | None = None,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The request that starts a Gladia job for `audio_url`.

    `DEFAULT_GLADIA_OPTIONS` deep-merged with `gladia_options`, with
    diarization, named entities and sentences forced on. With `metadata`, the
    job also carries `custom_metadata` (`_custom_metadata`), whose `config` is
    the fingerprint of these settings (`gladia_config_fingerprint`). Only
    StormIQ's own metadata is sent: any in `gladia_options` is dropped.
    """
    request_payload: dict[str, Any] = _deep_merge({}, DEFAULT_GLADIA_OPTIONS)
    if gladia_options:
        request_payload = _deep_merge(request_payload, gladia_options)
    request_payload["diarization"] = True
    request_payload["named_entity_recognition"] = True
    request_payload["sentences"] = True
    request_payload.pop("audio_to_llm", None)
    request_payload.pop("audio_to_llm_config", None)
    request_payload.pop("audio_url", None)
    request_payload.pop("custom_metadata", None)
    if metadata is not None:
        request_payload["custom_metadata"] = _custom_metadata(metadata, gladia_config_fingerprint(request_payload))
    request_payload["audio_url"] = audio_url
    return request_payload


def _transcribe_audio_file(
    audio_path: Path,
    *,
    api_key: str,
    gladia_options: dict[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    chunk_index: int = 1,
    chunk_total: int = 1,
) -> dict[str, Any]:
    if on_progress:
        chunk_label = f"chunk {chunk_index} of {chunk_total}" if chunk_total > 1 else "recording"
        on_progress({
            "step": "uploading",
            "message": f"Uploading {chunk_label} to Gladia…",
            "chunk": chunk_index,
            "total": chunk_total,
        })
    upload_payload = _upload_audio_file(audio_path, api_key)
    audio_url = upload_payload.get("audio_url")
    if not isinstance(audio_url, str) or not audio_url.strip():
        raise RuntimeError("Gladia upload succeeded but no audio_url was returned.")

    # Built once: the request we record is exactly the one we send.
    gladia_request = _build_gladia_request(audio_url, gladia_options, metadata=metadata)
    started_job = _start_gladia_transcription(gladia_request, api_key)
    job_id = started_job.get("id")
    if not isinstance(job_id, str) or not job_id.strip():
        raise RuntimeError("Gladia transcription request succeeded but no job id was returned.")

    gladia_result = _poll_gladia_transcription(
        job_id, api_key,
        on_progress=on_progress,
        chunk_index=chunk_index,
        chunk_total=chunk_total,
    )
    return {
        "upload_payload": upload_payload,
        "gladia_request": gladia_request,
        "started_job": started_job,
        "gladia_result": gladia_result,
    }


def _start_gladia_transcription(gladia_request: dict[str, Any], api_key: str) -> dict[str, Any]:
    """POST a request built by `_build_gladia_request`. Never retried: it starts paid work."""
    return _json_request(
        method="POST",
        url=f"{GLADIA_API_BASE}{GLADIA_PRE_RECORDED_PATH}",
        api_key=api_key,
        payload=gladia_request,
    )


def _poll_gladia_transcription(
    job_id: str,
    api_key: str,
    *,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    chunk_index: int = 1,
    chunk_total: int = 1,
) -> dict[str, Any]:
    deadline = time.monotonic() + GLADIA_POLL_TIMEOUT_SECONDS
    last_payload: dict[str, Any] | None = None
    last_gladia_status: str | None = None
    last_reported_at = 0.0
    consecutive_errors = 0

    while time.monotonic() < deadline:
        try:
            payload = _json_request(
                method="GET",
                url=f"{GLADIA_API_BASE}{GLADIA_PRE_RECORDED_PATH}/{job_id}",
                api_key=api_key,
            )
        except TransientHTTPError:
            # The Gladia job keeps running server-side; just poll again.
            consecutive_errors += 1
            if consecutive_errors >= GLADIA_POLL_MAX_CONSECUTIVE_ERRORS:
                raise
            time.sleep(GLADIA_POLL_INTERVAL_SECONDS * (2 ** consecutive_errors))
            continue
        consecutive_errors = 0
        last_payload = payload
        status = str(payload.get("status") or "").strip().lower()

        # Report on every status change, and at least every
        # PROGRESS_HEARTBEAT_SECONDS while the status stays the same: the
        # worker's progress callback refreshes transcript_jobs.updated_at,
        # which the stuck-job sweeper uses to tell slow jobs from dead ones.
        heartbeat_due = time.monotonic() - last_reported_at >= PROGRESS_HEARTBEAT_SECONDS
        if on_progress and (status != last_gladia_status or heartbeat_due):
            last_gladia_status = status
            last_reported_at = time.monotonic()
            chunk_label = f"chunk {chunk_index} of {chunk_total}" if chunk_total > 1 else "recording"
            on_progress({
                "step": "transcribing",
                "message": f"Transcribing {chunk_label} ({status})…",
                "chunk": chunk_index,
                "total": chunk_total,
                "gladia_status": status,
            })

        if status == "done":
            return payload
        if status == "error":
            error_code = payload.get("error_code")
            raise RuntimeError(f"Gladia transcription failed with status=error and error_code={error_code}.")
        time.sleep(GLADIA_POLL_INTERVAL_SECONDS)

    raise RuntimeError(
        f"Timed out while waiting for Gladia transcription job {job_id}. "
        f"Last status: {last_payload.get('status') if last_payload else 'unknown'}."
    )


def _extract_text_segments(result_payload: dict[str, Any]) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    result = result_payload.get("result")
    if not isinstance(result, dict):
        return "", [], []

    transcription = result.get("transcription")
    if not isinstance(transcription, dict):
        return "", [], []

    full_transcript = str(transcription.get("full_transcript") or "").strip()
    utterances = transcription.get("utterances")
    words = transcription.get("words")

    segments: list[dict[str, Any]] = []
    if isinstance(utterances, list):
        for index, utterance in enumerate(utterances, start=1):
            if not isinstance(utterance, dict):
                continue
            segments.append(
                {
                    "id": utterance.get("id", index),
                    "start": utterance.get("start"),
                    "end": utterance.get("end"),
                    "speaker": utterance.get("speaker"),
                    "confidence": utterance.get("confidence"),
                    "text": str(utterance.get("text") or "").strip(),
                }
            )

    normalized_words: list[dict[str, Any]] = []
    if isinstance(words, list):
        for word in words:
            if not isinstance(word, dict):
                continue
            normalized_words.append(
                {
                    "word": word.get("word"),
                    "start": word.get("start"),
                    "end": word.get("end"),
                    "speaker": word.get("speaker"),
                    "confidence": word.get("confidence"),
                }
            )

    return full_transcript, segments, normalized_words


def _extract_duration_seconds(gladia_payload: dict[str, Any]) -> float | None:
    file_data = gladia_payload.get("file")
    if isinstance(file_data, dict):
        for key in ("audio_duration", "duration"):
            value = file_data.get(key)
            if isinstance(value, (int, float)):
                return float(value)

    result = gladia_payload.get("result")
    if isinstance(result, dict):
        metadata = result.get("metadata")
        if isinstance(metadata, dict):
            for key in ("audio_duration", "duration"):
                value = metadata.get(key)
                if isinstance(value, (int, float)):
                    return float(value)

    return None


def _extract_language(gladia_payload: dict[str, Any]) -> str | None:
    result = gladia_payload.get("result")
    if not isinstance(result, dict):
        return None

    transcription = result.get("transcription")
    if isinstance(transcription, dict):
        languages = transcription.get("languages")
        if isinstance(languages, list) and languages:
            first_language = languages[0]
            if isinstance(first_language, str) and first_language.strip():
                return first_language.strip()

    metadata = result.get("metadata")
    if isinstance(metadata, dict):
        language = metadata.get("language")
        if isinstance(language, str) and language.strip():
            return language.strip()

    return None


def _shift_timecode(value: Any, offset_seconds: float) -> Any:
    if isinstance(value, (int, float)):
        return value + offset_seconds
    return value


def _shift_timed_dict(entry: dict[str, Any], offset_seconds: float) -> dict[str, Any]:
    shifted = dict(entry)
    for key in ("start", "end"):
        shifted[key] = _shift_timecode(shifted.get(key), offset_seconds)
    return shifted


def _speaker_id(value: Any) -> int | None:
    """A Gladia speaker number, or None for anything else (bools are not speakers)."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _shift_speaker(entry: dict[str, Any], speaker_base: int) -> None:
    speaker = _speaker_id(entry.get("speaker"))
    if speaker is not None:
        entry["speaker"] = speaker + speaker_base


def _shift_utterance(utterance: dict[str, Any], offset_seconds: float, speaker_base: int) -> dict[str, Any]:
    """A copy of a chunk's utterance (or sentence) on the whole recording's clock.

    Shifts its `start` and `end` and those of every word inside it by the
    chunk's offset, and moves its speaker number (and any word's) up by
    `speaker_base`, so speakers from different chunks never share a number.
    The input is not modified.
    """
    shifted = _shift_timed_dict(utterance, offset_seconds)
    _shift_speaker(shifted, speaker_base)
    words = utterance.get("words")
    if isinstance(words, list):
        shifted_words: list[Any] = []
        for word in words:
            if isinstance(word, dict):
                shifted_word = _shift_timed_dict(word, offset_seconds)
                _shift_speaker(shifted_word, speaker_base)
                shifted_words.append(shifted_word)
            else:
                shifted_words.append(word)
        shifted["words"] = shifted_words
    return shifted


def _dict_items(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _result_block_items(block: Any) -> list[dict[str, Any]] | None:
    """The items of a Gladia result block (`{success, results}` or a plain list), or None."""
    if isinstance(block, dict) and isinstance(block.get("results"), list):
        return _dict_items(block["results"])
    if isinstance(block, list):
        return _dict_items(block)
    return None


def _merged_result_block(first_block: Any, items: list[dict[str, Any]]) -> Any:
    """`items` in the shape of the first chunk's block."""
    if isinstance(first_block, list):
        return items
    merged = dict(first_block)
    merged["results"] = items
    if "is_empty" in merged:
        merged["is_empty"] = not items
    return merged


def _chunk_speaker_ids(*entry_lists: list[dict[str, Any]]) -> list[int]:
    speakers: list[int] = []
    for entries in entry_lists:
        for entry in entries:
            speaker = _speaker_id(entry.get("speaker"))
            if speaker is not None:
                speakers.append(speaker)
    return speakers


def _chunk_map_entry(chunk_result: dict[str, Any], *, index: int, offset_seconds: float, speaker_base: int) -> dict[str, Any]:
    started_job = chunk_result.get("started_job")
    gladia_result = chunk_result.get("gladia_result")
    gladia_result = gladia_result if isinstance(gladia_result, dict) else {}
    job_id = started_job.get("id") if isinstance(started_job, dict) else None
    duration_seconds = _extract_duration_seconds(gladia_result)
    return {
        "index": index,
        "offset_s": round(offset_seconds, 3),
        "duration_s": round(duration_seconds, 3) if duration_seconds is not None else None,
        "speaker_base": speaker_base,
        "gladia_job_id": job_id or gladia_result.get("id"),
    }


def _parse_subtitle_timestamp(value: str) -> float | None:
    match = re.fullmatch(r"(?:(\d{2}):)?(\d{2}):(\d{2})[,.](\d{3})", value.strip())
    if not match:
        return None

    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    milliseconds = int(match.group(4))
    return float(hours * 3600 + minutes * 60 + seconds + (milliseconds / 1000.0))


def _format_subtitle_timestamp(total_seconds: float, *, use_comma: bool) -> str:
    total_milliseconds = max(0, int(round(total_seconds * 1000)))
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    separator = "," if use_comma else "."
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{separator}{milliseconds:03d}"


def _parse_subtitle_cues(subtitle_text: str) -> list[dict[str, Any]]:
    normalized_text = str(subtitle_text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized_text:
        return []

    cues: list[dict[str, Any]] = []
    for raw_block in re.split(r"\n\s*\n", normalized_text):
        block = raw_block.strip()
        if not block or block == "WEBVTT" or block.startswith("NOTE"):
            continue

        lines = [line.rstrip() for line in block.split("\n")]
        if lines and re.fullmatch(r"\d+", lines[0].strip()):
            lines = lines[1:]
        if not lines:
            continue

        timing_line = lines[0].strip()
        if "-->" not in timing_line:
            continue

        start_raw, end_raw = [part.strip() for part in timing_line.split("-->", 1)]
        end_parts = end_raw.split(maxsplit=1)
        end_timestamp_raw = end_parts[0].strip() if end_parts else ""
        settings = end_parts[1].strip() if len(end_parts) > 1 else ""

        start_seconds = _parse_subtitle_timestamp(start_raw)
        end_seconds = _parse_subtitle_timestamp(end_timestamp_raw)
        if start_seconds is None or end_seconds is None:
            continue

        cues.append(
            {
                "start": start_seconds,
                "end": end_seconds,
                "settings": settings,
                "text_lines": lines[1:],
            }
        )

    return cues


def _render_subtitle_cues(cues: list[dict[str, Any]], subtitle_format: str) -> str:
    normalized_format = str(subtitle_format or "").strip().lower()
    use_comma = normalized_format == "srt"
    blocks: list[str] = []

    if normalized_format == "vtt":
        blocks.append("WEBVTT")

    for index, cue in enumerate(cues, start=1):
        start_value = _format_subtitle_timestamp(float(cue.get("start") or 0.0), use_comma=use_comma)
        end_value = _format_subtitle_timestamp(float(cue.get("end") or 0.0), use_comma=use_comma)
        settings = str(cue.get("settings") or "").strip()
        timing_line = f"{start_value} --> {end_value}"
        if settings:
            timing_line = f"{timing_line} {settings}"

        text_lines = [str(line) for line in cue.get("text_lines") or []]
        if normalized_format == "srt":
            block_lines = [str(index), timing_line, *text_lines]
        else:
            block_lines = [timing_line, *text_lines]
        blocks.append("\n".join(block_lines).rstrip())

    return "\n\n".join(blocks).strip() + ("\n" if blocks else "")


def _extract_subtitles(gladia_payload: dict[str, Any]) -> list[dict[str, str]]:
    result = gladia_payload.get("result")
    if not isinstance(result, dict):
        return []

    transcription = result.get("transcription")
    if not isinstance(transcription, dict):
        return []

    subtitles = transcription.get("subtitles")
    if not isinstance(subtitles, list):
        return []

    normalized_subtitles: list[dict[str, str]] = []
    for subtitle in subtitles:
        if not isinstance(subtitle, dict):
            continue
        subtitle_format = str(subtitle.get("format") or "").strip().lower()
        subtitle_text = str(subtitle.get("subtitles") or "").strip()
        if subtitle_format and subtitle_text:
            normalized_subtitles.append(
                {
                    "format": subtitle_format,
                    "subtitles": subtitle_text,
                }
            )
    return normalized_subtitles


def _merge_chunk_results(chunk_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Combine the Gladia results of a recording's chunks into one payload.

    A recording longer than GLADIA_MAX_AUDIO_CHUNK_SECONDS is transcribed in
    chunks, and each chunk's result starts at 0 s with speakers numbered from
    0. The merge puts everything on the whole recording's clock:

    - utterances and the words inside them, sentences (with their words),
      named entities and subtitle cues are shifted by the chunk's offset;
    - each chunk's speakers get their own numbers, above the highest number
      of the chunks before it (`_shift_utterance`);
    - `transcription.words` is written only when a chunk had one, so an empty
      list never hides the words inside the utterances;
    - `billing_time` and `transcription_time` are summed, and the languages
      of every chunk are kept.

    `result.stormiq_chunks` records each chunk's index, offset, duration,
    speaker base and Gladia job id, for one chunk as for several.
    """
    if not chunk_results:
        raise RuntimeError("No chunk transcriptions were produced.")

    if len(chunk_results) == 1:
        single_payload = dict(chunk_results[0]["gladia_result"])
        single_result = single_payload.get("result")
        if isinstance(single_result, dict):
            offset_seconds = float(chunk_results[0].get("offset_seconds") or 0.0)
            single_payload["result"] = {
                **single_result,
                "stormiq_chunks": [
                    _chunk_map_entry(chunk_results[0], index=1, offset_seconds=offset_seconds, speaker_base=0)
                ],
            }
        return single_payload

    merged_payload = json.loads(json.dumps(chunk_results[0]["gladia_result"]))
    merged_payload["status"] = "done"

    merged_text_parts: list[str] = []
    merged_utterances: list[dict[str, Any]] = []
    merged_words: list[dict[str, Any]] = []
    merged_transcription_sentences: list[dict[str, Any]] = []
    merged_sentence_results: list[dict[str, Any]] = []
    merged_entities: list[dict[str, Any]] = []
    first_sentence_block: Any = None
    first_entity_block: Any = None
    merged_languages: list[str] = []
    timing_totals: dict[str, float] = {}
    chunk_map: list[dict[str, Any]] = []
    merged_subtitle_cues_by_format: dict[str, list[dict[str, Any]]] = {}
    merged_duration_seconds = 0.0
    detected_language: str | None = None
    next_speaker_base = 0

    for chunk_index, chunk_result in enumerate(chunk_results, start=1):
        chunk_payload = chunk_result["gladia_result"]
        offset_seconds = float(chunk_result.get("offset_seconds") or 0.0)
        speaker_base = next_speaker_base
        duration_seconds = _extract_duration_seconds(chunk_payload) or 0.0
        merged_duration_seconds = max(merged_duration_seconds, offset_seconds + duration_seconds)
        if detected_language is None:
            detected_language = _extract_language(chunk_payload)
        chunk_map.append(
            _chunk_map_entry(chunk_result, index=chunk_index, offset_seconds=offset_seconds, speaker_base=speaker_base)
        )

        result = chunk_payload.get("result")
        result = result if isinstance(result, dict) else {}
        transcription = result.get("transcription")
        transcription = transcription if isinstance(transcription, dict) else {}

        utterances = _dict_items(transcription.get("utterances"))
        words = _dict_items(transcription.get("words"))
        transcription_sentences = _dict_items(transcription.get("sentences"))
        sentence_items = _result_block_items(result.get("sentences"))
        entity_items = _result_block_items(result.get("named_entity_recognition"))

        full_transcript = str(transcription.get("full_transcript") or "").strip()
        if full_transcript:
            merged_text_parts.append(full_transcript)
        merged_utterances.extend(_shift_utterance(item, offset_seconds, speaker_base) for item in utterances)
        merged_words.extend(_shift_utterance(item, offset_seconds, speaker_base) for item in words)
        merged_transcription_sentences.extend(
            _shift_utterance(item, offset_seconds, speaker_base) for item in transcription_sentences
        )
        if sentence_items is not None:
            if first_sentence_block is None:
                first_sentence_block = result.get("sentences")
            merged_sentence_results.extend(
                _shift_utterance(item, offset_seconds, speaker_base) for item in sentence_items
            )
        if entity_items is not None:
            if first_entity_block is None:
                first_entity_block = result.get("named_entity_recognition")
            merged_entities.extend(_shift_timed_dict(item, offset_seconds) for item in entity_items)

        chunk_speakers = _chunk_speaker_ids(utterances, words, transcription_sentences, sentence_items or [])
        if chunk_speakers:
            next_speaker_base = max(next_speaker_base, speaker_base + max(chunk_speakers) + 1)

        languages = transcription.get("languages")
        if isinstance(languages, list):
            for language in languages:
                if isinstance(language, str) and language.strip() and language.strip() not in merged_languages:
                    merged_languages.append(language.strip())

        metadata = result.get("metadata")
        if isinstance(metadata, dict):
            for key in ("billing_time", "transcription_time"):
                value = metadata.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    timing_totals[key] = timing_totals.get(key, 0.0) + float(value)

        subtitles = transcription.get("subtitles")
        if isinstance(subtitles, list):
            for subtitle in subtitles:
                if not isinstance(subtitle, dict):
                    continue
                subtitle_format = str(subtitle.get("format") or "").strip().lower()
                subtitle_text = str(subtitle.get("subtitles") or "").strip()
                if not subtitle_format or not subtitle_text:
                    continue
                cues = _parse_subtitle_cues(subtitle_text)
                if not cues:
                    continue
                shifted_cues = []
                for cue in cues:
                    shifted_cues.append(
                        {
                            **cue,
                            "start": float(cue.get("start") or 0.0) + offset_seconds,
                            "end": float(cue.get("end") or 0.0) + offset_seconds,
                        }
                    )
                merged_subtitle_cues_by_format.setdefault(subtitle_format, []).extend(shifted_cues)

    merged_result = merged_payload.setdefault("result", {})
    if not isinstance(merged_result, dict):
        merged_result = {}
        merged_payload["result"] = merged_result

    merged_transcription = merged_result.setdefault("transcription", {})
    if not isinstance(merged_transcription, dict):
        merged_transcription = {}
        merged_result["transcription"] = merged_transcription

    merged_transcription["full_transcript"] = "\n\n".join(merged_text_parts).strip()
    for utterance_index, utterance in enumerate(merged_utterances, start=1):
        utterance["id"] = utterance_index
    merged_transcription["utterances"] = merged_utterances
    if merged_words:
        merged_transcription["words"] = merged_words
    else:
        merged_transcription.pop("words", None)
    if merged_transcription_sentences:
        merged_transcription["sentences"] = merged_transcription_sentences
    if merged_subtitle_cues_by_format:
        merged_transcription["subtitles"] = [
            {
                "format": subtitle_format,
                "subtitles": _render_subtitle_cues(cues, subtitle_format),
            }
            for subtitle_format, cues in merged_subtitle_cues_by_format.items()
            if cues
        ]
    if first_sentence_block is not None:
        merged_result["sentences"] = _merged_result_block(first_sentence_block, merged_sentence_results)
    if first_entity_block is not None:
        merged_result["named_entity_recognition"] = _merged_result_block(first_entity_block, merged_entities)

    if merged_languages:
        merged_transcription["languages"] = merged_languages
    elif not merged_transcription.get("languages") and detected_language:
        merged_transcription["languages"] = [detected_language]

    metadata = merged_result.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
        merged_result["metadata"] = metadata
    metadata["audio_duration"] = merged_duration_seconds
    for key, total in timing_totals.items():
        metadata[key] = round(total, 3)
    if detected_language and not metadata.get("language"):
        metadata["language"] = detected_language

    merged_result["stormiq_chunks"] = chunk_map

    file_data = merged_payload.get("file")
    if isinstance(file_data, dict):
        file_data["audio_duration"] = merged_duration_seconds
        if file_data.get("duration") is not None:
            file_data["duration"] = merged_duration_seconds

    return merged_payload


def _normalize_transcription(
    gladia_payload: dict[str, Any],
    *,
    source_video: Path,
    extracted_audio: Path | None,
    requested_model: str,
    actual_model: str,
    session_id: str | None = None,
    recording: dict[str, Any] | None = None,
    upload_payload: dict[str, Any] | None = None,
    gladia_request: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    text, segments, words = _extract_text_segments(gladia_payload)
    normalized = dict(gladia_payload)
    normalized["provider"] = "gladia"
    normalized["source_video"] = str(source_video.resolve())
    normalized["extracted_audio"] = str(extracted_audio.resolve()) if extracted_audio else None
    normalized["model"] = actual_model
    normalized["requested_model"] = requested_model
    normalized["timestamped"] = True
    normalized["created_at"] = datetime.now(timezone.utc).isoformat()
    normalized["text"] = text
    normalized["language"] = _extract_language(gladia_payload)
    normalized["duration_seconds"] = _extract_duration_seconds(gladia_payload)
    normalized["segments"] = segments
    normalized["words"] = words
    normalized["subtitles"] = _extract_subtitles(gladia_payload)
    if session_id:
        normalized["session_id"] = session_id
    if recording:
        normalized["recording"] = {
            "id": recording.get("id"),
            "event_id": recording.get("attributes", {}).get("event_id"),
            "session_id": recording.get("attributes", {}).get("session_id"),
            "file_type": recording.get("attributes", {}).get("file_type"),
            "mime_type": recording.get("attributes", {}).get("mime_type"),
            "file_size": recording.get("attributes", {}).get("file_size"),
            "file_name": recording.get("attributes", {}).get("file_name"),
            "url_generated_at": recording.get("attributes", {}).get("url_generated_at"),
            "url_expires_in": recording.get("attributes", {}).get("url_expires_in"),
        }
    if upload_payload is not None:
        normalized["upload"] = upload_payload
    if gladia_request is not None:
        normalized["request_payload"] = gladia_request
    if provenance is not None:
        normalized["stormiq"] = provenance
    return normalized


def _provenance(chunk_results: list[dict[str, Any]], media: dict[str, Any] | None) -> dict[str, Any]:
    """The `stormiq` block of a stored transcript: how it was made.

    - `pipeline_version`: `PIPELINE_VERSION`;
    - `config`: the fingerprint of the settings sent (`gladia_config_fingerprint`),
      the same for every chunk;
    - `max_speakers`: the speaker cap sent;
    - `people_on_stage`: None here; the worker sets the count it built the
      cap from (`worker.run_transcription`);
    - `gladia_jobs`: per chunk, its number, its Gladia job id and the
      `custom_metadata` sent with it;
    - `media`: the recording file's duration and `creation_time` (`_probe_media_info`).
    """
    first_request = chunk_results[0].get("gladia_request") if chunk_results else None
    first_request = first_request if isinstance(first_request, dict) else {}
    diarization = first_request.get("diarization_config")
    max_speakers = diarization.get("max_speakers") if isinstance(diarization, dict) else None
    jobs = []
    for index, chunk_result in enumerate(chunk_results, start=1):
        started_job = chunk_result.get("started_job")
        request = chunk_result.get("gladia_request")
        custom_metadata = request.get("custom_metadata") if isinstance(request, dict) else None
        jobs.append(
            {
                "chunk": index,
                "id": started_job.get("id") if isinstance(started_job, dict) else None,
                "custom_metadata": dict(custom_metadata) if isinstance(custom_metadata, dict) else None,
            }
        )
    return {
        "pipeline_version": PIPELINE_VERSION,
        "config": gladia_config_fingerprint(first_request),
        "max_speakers": max_speakers,
        "people_on_stage": None,
        "gladia_jobs": jobs,
        "media": media,
    }


def _file_size(value: Any) -> int | None:
    """A recording's `file_size` as Livestorm sends it, as a whole number of bytes."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer() and value >= 0:
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _recording_metadata(session_id: str, recording: dict[str, Any]) -> dict[str, Any]:
    """The ids that tie a Gladia job to one recording of one session. Never
    the recording's URL: it is signed."""
    attributes = recording.get("attributes")
    attributes = attributes if isinstance(attributes, dict) else {}
    recording_id = recording.get("id")
    return {
        "stormiq_session_id": str(session_id),
        "recording_id": str(recording_id) if recording_id is not None else None,
        "file_size": _file_size(attributes.get("file_size")),
    }


def _fetch_livestorm_recordings(session_id: str, livestorm_api_key: str | None = None) -> dict[str, Any]:
    api_key = _resolve_livestorm_api_key(livestorm_api_key)
    request = urllib.request.Request(
        url=f"{LIVESTORM_API_BASE}/sessions/{session_id}/recordings",
        headers={
            "Authorization": api_key,
            "accept": "application/vnd.api+json",
        },
        method="GET",
    )
    for attempt in range(1, LIVESTORM_GET_MAX_ATTEMPTS + 1):
        try:
            return _urlopen_json(request, "Livestorm API request")
        except TransientHTTPError:
            if attempt == LIVESTORM_GET_MAX_ATTEMPTS:
                raise
            time.sleep(2 ** attempt)
    raise AssertionError("unreachable: the last attempt returns or raises")


def _select_recording(payload: dict[str, Any]) -> dict[str, Any]:
    recordings = payload.get("data", [])
    for recording in recordings:
        attributes = recording.get("attributes", {})
        if attributes.get("file_type") == "video" and attributes.get("mime_type") == "mp4":
            return recording
    raise RuntimeError("No MP4 video recording found in the Livestorm session response.")


def _download_recording(recording: dict[str, Any], destination: Path) -> None:
    url = recording.get("attributes", {}).get("url")
    if not url:
        raise RuntimeError("Livestorm recording is missing a download URL.")

    request = urllib.request.Request(url=url, method="GET")
    try:
        # timeout=60 is a per-read socket timeout, not a total-download limit,
        # so it detects stalled connections without cutting off legitimate large files.
        with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output_handle:
            shutil.copyfileobj(response, output_handle, length=1024 * 1024)
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Recording download failed with HTTP {exc.code}: {details}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Recording download failed: {exc.reason}") from exc


def transcribe_video(
    input_path: str | Path,
    output_path: str | Path | None = None,
    *,
    provider: str | None = None,
    keep_audio: bool = False,
    gladia_options: dict[str, Any] | None = None,
    gladia_api_key: str | None = None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    job_metadata: Mapping[str, Any] | None = None,
) -> Path:
    """Transcribe a local video with Gladia and write the normalized JSON.

    `job_metadata` names the session and recording (`_recording_metadata`).
    Each chunk's Gladia job carries it, with this process's `env` tag, the
    chunk's number, the chunk count and the chunk's offset
    (`_custom_metadata`). The result records how it was made in `stormiq`
    (`_provenance`).
    """
    source_video = Path(input_path).expanduser().resolve()
    if not source_video.exists():
        raise FileNotFoundError(f"Input file not found: {source_video}")

    if output_path is None:
        output_file = source_video.with_suffix(".transcript.json")
    else:
        output_file = Path(output_path).expanduser().resolve()

    output_file.parent.mkdir(parents=True, exist_ok=True)

    api_key = _resolve_api_key(gladia_api_key)
    requested_model = provider or DEFAULT_MODEL
    actual_model = DEFAULT_MODEL

    with tempfile.TemporaryDirectory(prefix="video-transcript-") as temp_dir:
        temp_dir_path = Path(temp_dir)
        temp_audio = temp_dir_path / f"{source_video.stem}.wav"
        if on_progress:
            on_progress({"step": "extracting", "message": "Extracting audio track…"})
        media = _probe_media_info(source_video)
        _extract_audio(source_video, temp_audio)
        chunk_specs = _split_audio_file(temp_audio, temp_dir_path / "audio-chunks")
        chunk_total = len(chunk_specs)
        base_metadata = {**(job_metadata or {}), "env": environment_tag(), "chunks": chunk_total}

        chunk_results: list[dict[str, Any]] = []
        for chunk_index, (chunk_path, offset_seconds) in enumerate(chunk_specs, start=1):
            chunk_result = _transcribe_audio_file(
                chunk_path,
                api_key=api_key,
                gladia_options=gladia_options,
                metadata={**base_metadata, "chunk": chunk_index, "offset_s": round(float(offset_seconds), 3)},
                on_progress=on_progress,
                chunk_index=chunk_index,
                chunk_total=chunk_total,
            )
            chunk_result["offset_seconds"] = offset_seconds
            chunk_result["audio_path"] = str(chunk_path)
            chunk_results.append(chunk_result)

        if chunk_total > 1 and on_progress:
            on_progress({"step": "merging", "message": f"Merging {chunk_total} transcript chunks…"})
        gladia_result = _merge_chunk_results(chunk_results)

        extracted_audio: Path | None = None
        if keep_audio:
            extracted_audio = output_file.with_suffix(".audio.wav")
            shutil.copy2(temp_audio, extracted_audio)

        normalized = _normalize_transcription(
            gladia_payload=gladia_result,
            source_video=source_video,
            extracted_audio=extracted_audio,
            requested_model=requested_model,
            actual_model=actual_model,
            upload_payload=chunk_results[0]["upload_payload"],
            gladia_request=chunk_results[0]["gladia_request"],
            provenance=_provenance(chunk_results, media),
        )
        normalized["job"] = chunk_results[0]["started_job"]
        output_file.write_text(json.dumps(normalized, indent=2, ensure_ascii=True) + "\n")

    return output_file


def transcribe_livestorm_session(
    session_id: str,
    output_path: str | Path | None = None,
    *,
    provider: str | None = None,
    keep_audio: bool = False,
    keep_video: bool = False,
    gladia_options: dict[str, Any] | None = None,
    gladia_api_key: str | None = None,
    livestorm_api_key: str | None = None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> Path:
    payload = _fetch_livestorm_recordings(session_id, livestorm_api_key=livestorm_api_key)
    recording = _select_recording(payload)
    file_name = recording.get("attributes", {}).get("file_name") or f"{session_id}.mp4"
    file_stem = Path(file_name).stem or session_id

    if output_path is None:
        output_file = Path.cwd() / f"{file_stem}.transcript.json"
    else:
        output_file = Path(output_path).expanduser().resolve()

    output_file.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="video-transcript-") as temp_dir:
        temp_dir_path = Path(temp_dir)
        downloaded_video = temp_dir_path / file_name
        if on_progress:
            on_progress({"step": "downloading", "message": "Downloading recording…"})
        _download_recording(recording, downloaded_video)

        final_video_path: Path | None = None
        if keep_video:
            final_video_path = output_file.with_suffix(".mp4")
            shutil.copy2(downloaded_video, final_video_path)

        transcript_path = transcribe_video(
            input_path=downloaded_video,
            output_path=output_file,
            provider=provider,
            keep_audio=keep_audio,
            gladia_options=gladia_options,
            gladia_api_key=gladia_api_key,
            on_progress=on_progress,
            job_metadata=_recording_metadata(session_id, recording),
        )

        transcript_payload = json.loads(transcript_path.read_text())
        transcript_payload["session_id"] = session_id
        transcript_payload["source_video"] = (
            str(final_video_path.resolve()) if final_video_path else transcript_payload["source_video"]
        )
        transcript_payload["recording"] = {
            "id": recording.get("id"),
            "event_id": recording.get("attributes", {}).get("event_id"),
            "session_id": recording.get("attributes", {}).get("session_id"),
            "file_type": recording.get("attributes", {}).get("file_type"),
            "mime_type": recording.get("attributes", {}).get("mime_type"),
            "file_size": recording.get("attributes", {}).get("file_size"),
            "file_name": recording.get("attributes", {}).get("file_name"),
            "url_generated_at": recording.get("attributes", {}).get("url_generated_at"),
            "url_expires_in": recording.get("attributes", {}).get("url_expires_in"),
        }
        transcript_path.write_text(json.dumps(transcript_payload, indent=2, ensure_ascii=True) + "\n")

    return output_file


def transcribe_livestorm_session_data(
    session_id: str,
    output_path: str | Path | None = None,
    *,
    provider: str | None = None,
    keep_audio: bool = False,
    keep_video: bool = False,
    gladia_options: dict[str, Any] | None = None,
    gladia_api_key: str | None = None,
    livestorm_api_key: str | None = None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    transcript_path = transcribe_livestorm_session(
        session_id=session_id,
        output_path=output_path,
        provider=provider,
        keep_audio=keep_audio,
        keep_video=keep_video,
        gladia_options=gladia_options,
        gladia_api_key=gladia_api_key,
        livestorm_api_key=livestorm_api_key,
        on_progress=on_progress,
    )
    transcript_payload = json.loads(transcript_path.read_text())
    transcript_payload["output_path"] = str(transcript_path.resolve())
    return transcript_payload
