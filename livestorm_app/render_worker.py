"""The render worker: Surprise me's MP4, on its own queue and its own Render
service (`docker/Dockerfile.render`: Python, Node, the pinned `hyperframes`
CLI and its headless Chrome, ffmpeg, the fonts).

It takes jobs from `queue.RENDER_QUEUE` only, one at a time (`max_jobs=1`):
`render_surprise_video(session_id)`, queued by the show job
(`api_logic.run_surprise_show`) with the session id as its only argument
under the id `surprise_video:{session_id}`. It reads what the show job saved
(the timeline, the final shot list, the voice clips and the pictures),
renders the video (`video/render.py`) and saves it whole
(`db.save_surprise_video`). It calls no paid provider and needs no key:
only `DATABASE_URL` and `REDIS_URL`. The web process and the main worker
never import it, nor the renderer.

Run: `arq livestorm_app.render_worker.WorkerSettings`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from arq import func

from livestorm_app.config import load_env_file
from livestorm_app.db import (
    ALL_ORGS,
    ensure_database_schema,
    fetch_session_fields,
    fetch_surprise_enabled,
    fetch_surprise_images,
    fetch_surprise_shotlist,
    fetch_surprise_timeline,
    fetch_surprise_video_meta,
    fetch_surprise_voice_clip,
    save_surprise_video,
)
from livestorm_app.progress import clear_progress_sync, publish_progress_sync
from livestorm_app.queue import RENDER_QUEUE, get_redis_settings, job_result_serializer
from livestorm_app.video.keys import video_key

logger = logging.getLogger(__name__)

VIDEO_KIND = "surprise_video"
# A two-minute show takes a few minutes on one CPU; the picture step alone
# stops at `render.PICTURE_TIMEOUT_S`.
RENDER_JOB_TIMEOUT_SECONDS = 20 * 60


class VideoFailed(RuntimeError):
    """The video could not be made; the message is what the page shows."""


def _load(session_id: str) -> Dict[str, Any]:
    """Everything the render needs, as the show job saved it, and the video key."""
    row = fetch_session_fields(session_id, ["organization_id"], organization_id=ALL_ORGS)
    if not isinstance(row, dict):
        raise VideoFailed("This session is no longer here.")
    if not fetch_surprise_enabled(str(row.get("organization_id") or "")):
        raise VideoFailed("Surprise me is turned off for your organization. A StormIQ admin can turn it back on.")
    timeline = fetch_surprise_timeline(session_id, None, organization_id=ALL_ORGS)
    shotlist = fetch_surprise_shotlist(session_id, None, organization_id=ALL_ORGS)
    if timeline is None or shotlist is None or shotlist["body"].get("stage") != "final" \
            or shotlist["body"].get("timeline_key") != timeline["key"]:
        raise VideoFailed("The show isn't ready for its video yet. Make the show again.")
    shots = list(shotlist["body"].get("shots") or [])
    image_by_shot = {int(shot["id"]): str(shot["image"]) for shot in shots if shot.get("image")}
    images = fetch_surprise_images(session_id, list(image_by_shot.values()), organization_id=ALL_ORGS)
    clips: Dict[str, bytes] = {}
    for placed in timeline["body"].get("voice") or []:
        key = str(placed["clip"])
        if key not in clips:
            clip = fetch_surprise_voice_clip(session_id, key, organization_id=ALL_ORGS)
            if not clip:
                raise VideoFailed("A line of the voice is missing. Make the show again.")
            clips[key] = clip["bytes"]
    return {
        "timeline": timeline["body"],
        "shots": shots,
        "pulse": list(shotlist["body"].get("pulse") or []),
        "clips": clips,
        "images": {shot_id: images[key] for shot_id, key in image_by_shot.items() if key in images},
        "video_key": video_key(timeline["key"], shotlist["key"], list(image_by_shot.values())),
    }


def render_and_save(session_id: str, publish=lambda stage: None) -> Dict[str, Any]:
    """Render the session's video and save it, unless the saved one was made
    from the same show (`video_key`). Raises `VideoFailed` or the renderer's
    `RenderFailed`, whose messages the page shows."""
    from livestorm_app.video.render import render_video

    parts = _load(session_id)
    meta = fetch_surprise_video_meta(session_id, organization_id=ALL_ORGS)
    if meta and meta["video_key"] == parts["video_key"]:
        return {"status": "kept", "size": meta["size_bytes"], "duration_s": meta["duration_s"]}
    publish("rendering")
    made = render_video(
        parts["timeline"], parts["shots"], pulse=parts["pulse"], clips=parts["clips"], images=parts["images"],
        on_stage=publish,
    )
    save_surprise_video(session_id, parts["video_key"], mp4=made["mp4"], duration_s=made["duration_s"])
    return {"status": "made", "size": made["size"], "duration_s": made["duration_s"]}


async def render_surprise_video(ctx: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    logger.info("[render_surprise_video] session_id=%s start", session_id)
    try:
        publish_progress_sync(VIDEO_KIND, session_id, "rendering")

        def publish(stage: str) -> None:
            publish_progress_sync(VIDEO_KIND, session_id, stage)

        result = await asyncio.to_thread(render_and_save, session_id, publish)
        logger.info(
            "[render_surprise_video] session_id=%s %s size=%s duration_s=%s",
            session_id, result["status"], result["size"], result["duration_s"],
        )
        return result
    except Exception:
        logger.exception("[render_surprise_video] session_id=%s failed", session_id)
        raise
    finally:
        clear_progress_sync(VIDEO_KIND, session_id)


async def on_startup(ctx: Dict[str, Any]) -> None:
    load_env_file()
    try:
        ensure_database_schema()
    except Exception:
        logger.exception("[render worker] ensure_database_schema failed on startup")
    logger.info("[render worker] startup complete (queue %s)", RENDER_QUEUE)


async def on_shutdown(ctx: Dict[str, Any]) -> None:
    logger.info("[render worker] shutdown")


class WorkerSettings:
    """arq settings for the render worker: its own queue, one render at a
    time (Chrome and ffmpeg take the machine), no retries (a user retries
    from the page), and a failed job stored as its message only."""

    redis_settings = get_redis_settings()
    queue_name = RENDER_QUEUE
    job_serializer = job_result_serializer
    functions = [func(render_surprise_video, timeout=RENDER_JOB_TIMEOUT_SECONDS)]
    max_jobs = 1
    max_tries = 1
    on_startup = on_startup
    on_shutdown = on_shutdown
