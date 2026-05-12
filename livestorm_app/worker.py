"""arq worker entry point.

Run with:
    arq livestorm_app.worker.WorkerSettings

The worker is a separate OS process. It consumes jobs enqueued by the
FastAPI app (via `livestorm_app.queue.enqueue_job`). Phase 1 ships the
worker scaffolding only — no production flow has been migrated yet, so
the registered functions are a no-op `ping` (to prove the worker is
healthy end-to-end) plus the stuck-job sweeper cron.

Phase 2 will replace `ping` with real job handlers for:
    - run_transcription(session_id, livestorm_api_key, gladia_api_key)
    - run_overall_analysis(session_id, output_language)
    - run_deep_analysis(session_id, output_language)
    - run_smart_recap(session_id, tone)
    - run_content_repurposing(session_id, output_language)

Hard rules:
- Job handlers receive `ctx` (arq's per-job context) as their first arg
  and a structured payload after. Never pass positional args through the
  queue beyond the bare minimum — push complex shapes into TypedDicts
  defined in `queue.py`.
- Every handler must publish progress at each meaningful stage via
  `livestorm_app.progress.publish_progress`. The UI depends on it.
- On any error, the handler is responsible for writing the failure state
  to Postgres before re-raising. arq's retry logic kicks in after that.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from arq import cron

from livestorm_app.config import load_env_file
from livestorm_app.db import (
    database_enabled,
    ensure_database_schema,
    fetch_cached_session,
    get_db_connection,
    update_transcript_job_progress,
    update_transcript_job_status,
    upsert_cached_session,
)
from livestorm_app.progress import (
    clear_progress_sync,
    publish_progress_sync,
)
from livestorm_app.queue import get_redis_settings


logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


# ── Stuck-job sweeper ──────────────────────────────────────────────────────
#
# Defense-in-depth. The happy path is: handler runs to completion or to a
# typed failure that writes status='error' to transcript_jobs. The unhappy
# path is: the worker process dies mid-job (OOM, SIGKILL, host reboot) —
# then the row stays in 'running' forever and the UI polls indefinitely.
#
# Sweep every 10 minutes. Mark any 'running' row whose updated_at is older
# than STALE_AFTER as 'error' with a clear sweeper signature. The polling
# UI will then surface the error to the user.
#
# Threshold rationale: Gladia transcription can legitimately run for
# 30–45 minutes on a 2-hour recording, but the transcriber publishes
# progress on every poll cycle (every 3s — see gladia/transcriber.py), so
# `updated_at` is kept fresh. A row that's been silent for an hour is
# genuinely stuck.

STALE_AFTER = timedelta(hours=1)


def _sweep_stuck_transcript_jobs_sync() -> int:
    """Mark transcript_jobs rows stuck in 'running' as 'error'.

    Returns the number of rows touched. Sync because db.py is sync;
    called via `asyncio.to_thread` from the async cron handler.
    """
    if not database_enabled():
        return 0

    threshold = (datetime.now(timezone.utc) - STALE_AFTER).isoformat()
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE transcript_jobs
                    SET status = 'error',
                        error = 'sweeper: job stalled with no progress updates',
                        updated_at = NOW()
                    WHERE status IN ('pending', 'running')
                      AND updated_at < %s
                    """,
                    (threshold,),
                )
                affected = cursor.rowcount or 0
            connection.commit()
        return int(affected)
    except Exception:
        logger.exception("Stuck-job sweep failed")
        return 0


async def sweep_stuck_jobs(ctx: Dict[str, Any]) -> None:
    """Cron handler: scan transcript_jobs for stalled rows every 10 minutes."""
    affected = await asyncio.to_thread(_sweep_stuck_transcript_jobs_sync)
    if affected:
        logger.warning("[sweeper] Marked %d stuck transcript job(s) as error", affected)


# ── Stub no-op job ─────────────────────────────────────────────────────────
#
# Kept as a smoke-test handle. Useful for proving the web → Redis → worker
# loop after deploys:
#
#     >>> pool = await create_pool(get_redis_settings())
#     >>> job = await pool.enqueue_job('ping')
#     >>> await job.result()
#     'pong'


async def ping(ctx: Dict[str, Any]) -> str:
    job_id = ctx.get("job_id", "n/a")
    logger.info("[ping] job_id=%s", job_id)
    return "pong"


# ── Transcription job ──────────────────────────────────────────────────────
#
# Replaces the legacy `threading.Thread` path in api_logic.py. The web
# handler enqueues this job and returns immediately with a job_id; the
# worker downloads the recording, uploads to Gladia, polls, persists, and
# writes status back to `transcript_jobs`.
#
# Progress is written to two places:
#   - `transcript_jobs.progress` (Postgres TEXT, JSON-encoded) — legacy path,
#     kept for backward compat with the existing polling endpoint.
#   - Redis via `publish_progress_sync('transcript', session_id, ...)` —
#     new stage-floor progress; future-proof for the AI flows in commit 2.
#
# The Gladia transcriber is sync (uses `requests` under the hood) and may
# take 30-45 minutes on a 2-hour recording. We push it into a thread via
# `asyncio.to_thread` so the worker's event loop stays responsive for
# other jobs. The callback runs INSIDE that thread, so it uses the sync
# Redis writer — async would need a running loop the thread doesn't have.
#
# arq's default job_timeout is 300s — way too short for a long Gladia
# call. The enqueue site passes `_job_try=1, _expires=...` etc.; this
# function relies on the WorkerSettings.job_timeout override below.

TRANSCRIPTION_JOB_TIMEOUT_SECONDS = 60 * 90  # 90 min — fits Gladia's 135-min chunk cap with margin


def _map_gladia_step_to_stage(step: str) -> str:
    """Translate Gladia's progress `step` to our stage-floor vocabulary.

    See `progress.TRANSCRIPT_STAGE_FLOORS`. Unknown steps default to
    'transcribing' (the broad middle stage) — the bar still moves forward,
    just less precisely.
    """
    normalized = str(step or "").strip().lower()
    if normalized in {"downloading", "extracting"}:
        return "fetching_recording"
    if normalized == "uploading":
        return "uploading_to_gladia"
    if normalized == "merging":
        return "post_processing"
    return "transcribing"


async def run_transcription(
    ctx: Dict[str, Any],
    job_id: str,
    api_key: str,
    session_id: str,
    gladia_api_key: str,
) -> Dict[str, Any]:
    """Fetch + transcribe a Livestorm recording, persist to session_cache.

    Args:
        job_id:          transcript_jobs.job_id created at enqueue time
        api_key:         resolved Livestorm auth (raw key or "Bearer <token>")
        session_id:      target Livestorm session
        gladia_api_key:  Gladia transcription API key

    Returns a small status payload (for arq's result cache); the real
    result lives in `session_cache.transcript_payload`.

    On error: marks transcript_jobs as 'error', clears Redis progress,
    re-raises so arq logs the failure. arq's retry is intentionally NOT
    configured for this job — see WorkerSettings note below.
    """
    # Import the sync Gladia client lazily so the worker can boot even
    # when imageio_ffmpeg or other transcoding deps are missing on a
    # given host (the import would otherwise fire at module load).
    from livestorm_app.transcript_client import fetch_session_transcript

    logger.info("[run_transcription] job_id=%s session_id=%s start", job_id, session_id)
    update_transcript_job_status(job_id, "running")
    publish_progress_sync("transcript", session_id, "queued")

    def on_gladia_progress(progress: Dict[str, Any]) -> None:
        # 1) Legacy DB write — keeps the existing /transcript-job endpoint
        #    working unchanged.
        update_transcript_job_progress(job_id, progress)
        # 2) New Redis write — stage-floor progress for the new UI.
        step = str(progress.get("step") or "").strip()
        message = str(progress.get("message") or "").strip() or None
        stage = _map_gladia_step_to_stage(step)
        publish_progress_sync(
            "transcript",
            session_id,
            stage,
            label=message,
            extra={"gladia_step": step} if step else None,
        )

    try:
        publish_progress_sync("transcript", session_id, "fetching_recording")
        transcript_payload = await asyncio.to_thread(
            fetch_session_transcript,
            gladia_api_key,
            session_id,
            livestorm_api_key=api_key,
            on_progress=on_gladia_progress,
        )
        publish_progress_sync("transcript", session_id, "persisting")
        upsert_cached_session(api_key, session_id, transcript_payload=transcript_payload)
        update_transcript_job_status(job_id, "completed")
        publish_progress_sync("transcript", session_id, "done")
        # Hold the 'done' marker briefly so a slow poller can see the
        # 100% before it disappears, then clear so the next run starts
        # from a clean slate.
        await asyncio.sleep(2)
        clear_progress_sync("transcript", session_id)
        logger.info("[run_transcription] job_id=%s session_id=%s done", job_id, session_id)
        # Auto-trigger the Professional Smart Recap so every fetched
        # session has one in the cache without the user having to click
        # Generate. The recap powers the workspace card cover-image
        # logic downstream. Skip when a Professional recap is already
        # cached (re-running would burn an OpenAI call for no benefit).
        await _enqueue_default_smart_recap(ctx, session_id)
        return {"jobId": job_id, "status": "completed"}
    except Exception as exc:
        logger.exception("[run_transcription] job_id=%s session_id=%s failed", job_id, session_id)
        update_transcript_job_status(job_id, "error", error=str(exc))
        clear_progress_sync("transcript", session_id)
        raise


async def _enqueue_default_smart_recap(ctx: Dict[str, Any], session_id: str) -> None:
    """Fire-and-forget enqueue for the default Professional recap.

    Safe to call from any successful transcription. Reads the cache
    on a worker thread (db.py is sync psycopg) and skips when the
    Professional tone is already present. Failures are logged and
    swallowed — we don't want a recap-enqueue hiccup to roll back a
    successful transcription.
    """
    try:
        cached = await asyncio.to_thread(fetch_cached_session, "", session_id)
        smart_bundle = (cached or {}).get("smart_recap_bundle") or {}
        if isinstance(smart_bundle, dict) and str(smart_bundle.get("professional") or "").strip():
            logger.info(
                "[run_transcription] session_id=%s already has Professional recap; skipping auto-enqueue",
                session_id,
            )
            return
        redis_pool = ctx.get("redis") if isinstance(ctx, dict) else None
        if redis_pool is None:
            logger.warning(
                "[run_transcription] session_id=%s ctx missing redis pool; cannot auto-enqueue recap",
                session_id,
            )
            return
        await redis_pool.enqueue_job("run_smart_recap_job", session_id, "professional")
        logger.info(
            "[run_transcription] session_id=%s auto-enqueued smart_recap professional",
            session_id,
        )
    except Exception:
        logger.exception(
            "[run_transcription] session_id=%s failed to auto-enqueue smart_recap",
            session_id,
        )


# ── AI flow jobs (overall / deep / smart recap / content repurposing) ──────
#
# Four flows, same shape: load cached payloads → build prompt → OpenAI →
# persist to session_cache. Each is a thin wrapper around the existing
# sync function in api_logic.py, with stage-floor progress around it.
#
# The OpenAI API key is read from the worker's own env (OPENAI_API_KEY)
# rather than passed through the queue. Two reasons: (1) avoids putting
# secrets into Redis, (2) one source of truth for the key on the worker
# host.
#
# Progress mapping is coarse: we publish 'loading_sources' → 'building_prompt'
# → 'analyzing' before the (single, blocking) OpenAI call, then 'persisting'
# → 'done' after it. The bar will sit at "analyzing" (floor 40) for the
# bulk of the wait — same UX as the transcription job. Phase 2 refactor
# (cards) will give us natural inflection points to publish more stages.


def _read_openai_key_or_raise() -> str:
    from livestorm_app.config import get_runtime_secret

    key = str(get_runtime_secret("OPENAI_API_KEY", "") or "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY not configured on worker process.")
    return key


async def _run_ai_job(
    kind: str,
    session_id: str,
    sync_runner,
    *runner_args,
) -> Dict[str, Any]:
    """Shared scaffolding for all four AI jobs.

    `sync_runner` is the existing sync function in api_logic.py
    (run_overall_analysis / run_deep_analysis / etc.). Called inside
    `asyncio.to_thread` so the worker's event loop stays responsive.

    Always clears Redis progress on completion or failure. On success,
    holds the 'done' marker for 2 seconds so slow pollers see the 100%
    state before it disappears.
    """
    logger.info("[%s] session_id=%s start", kind, session_id)
    publish_progress_sync(kind, session_id, "queued")
    try:
        publish_progress_sync(kind, session_id, "loading_sources")
        publish_progress_sync(kind, session_id, "building_prompt")
        publish_progress_sync(kind, session_id, "analyzing")
        result = await asyncio.to_thread(sync_runner, *runner_args)
        publish_progress_sync(kind, session_id, "persisting")
        publish_progress_sync(kind, session_id, "done")
        await asyncio.sleep(2)
        clear_progress_sync(kind, session_id)
        logger.info("[%s] session_id=%s done", kind, session_id)
        return result if isinstance(result, dict) else {"status": "ok"}
    except Exception:
        logger.exception("[%s] session_id=%s failed", kind, session_id)
        clear_progress_sync(kind, session_id)
        raise


async def run_overall_analysis_job(
    ctx: Dict[str, Any],
    session_id: str,
    output_language: str,
) -> Dict[str, Any]:
    from livestorm_app.api_logic import run_overall_analysis

    openai_key = _read_openai_key_or_raise()
    return await _run_ai_job(
        "overall_analysis",
        session_id,
        run_overall_analysis,
        openai_key, session_id, output_language,
    )


async def run_deep_analysis_job(
    ctx: Dict[str, Any],
    session_id: str,
    output_language: str,
) -> Dict[str, Any]:
    from livestorm_app.api_logic import run_deep_analysis

    openai_key = _read_openai_key_or_raise()
    return await _run_ai_job(
        "deep_analysis",
        session_id,
        run_deep_analysis,
        openai_key, session_id, output_language,
    )


async def run_smart_recap_job(
    ctx: Dict[str, Any],
    session_id: str,
    tone: str,
) -> Dict[str, Any]:
    from livestorm_app.api_logic import run_smart_recap

    openai_key = _read_openai_key_or_raise()
    return await _run_ai_job(
        "smart_recap",
        session_id,
        run_smart_recap,
        openai_key, session_id, tone,
    )


async def run_content_repurposing_job(
    ctx: Dict[str, Any],
    session_id: str,
    output_language: str,
) -> Dict[str, Any]:
    from livestorm_app.api_logic import run_content_repurposing

    openai_key = _read_openai_key_or_raise()
    return await _run_ai_job(
        "content_repurposing",
        session_id,
        run_content_repurposing,
        openai_key, session_id, output_language,
    )


# ── Worker lifecycle ───────────────────────────────────────────────────────


async def on_startup(ctx: Dict[str, Any]) -> None:
    """Run once when the worker process starts.

    Loads .env (the web process does this in app.py; the worker process
    needs its own load since it boots independently), then verifies the
    DB schema. Failing here surfaces config errors at process start rather
    than on the first job.
    """
    load_env_file()
    try:
        ensure_database_schema()
    except Exception:
        logger.exception("[worker] ensure_database_schema failed on startup")
    logger.info("[worker] startup complete")


async def on_shutdown(ctx: Dict[str, Any]) -> None:
    logger.info("[worker] shutdown")


class WorkerSettings:
    """arq WorkerSettings.

    `functions` lists every job handler. Phase 2 will extend this list as
    flows migrate; Phase 1 keeps it minimal so the worker boots clean.

    `cron_jobs` registers scheduled tasks. `run_at_startup=False` keeps
    the sweeper from running on every deploy (it would race the first
    real job).

    Defaults intentionally NOT customised:
    - `max_jobs` (default 10) — sized for one host with light parallelism.
    - `job_timeout` (default 300s) — overridden per-job in Phase 2 for
      long transcriptions.
    - `keep_result` (default 3600s) — results stay in Redis 1h for polling.
    """

    redis_settings = get_redis_settings()

    functions = [
        ping,
        run_transcription,
        run_overall_analysis_job,
        run_deep_analysis_job,
        run_smart_recap_job,
        run_content_repurposing_job,
    ]

    cron_jobs = [
        cron(sweep_stuck_jobs, minute={0, 10, 20, 30, 40, 50}, run_at_startup=False),
    ]

    # Global default. The transcription job's actual upper bound is set
    # at enqueue time via `_job_try` / `_expires` kwargs to enqueue_job
    # so other (faster) jobs don't inherit this ceiling. arq itself uses
    # max(job_timeout, _timeout-at-enqueue) — we set the global to the
    # transcription ceiling so the worker never SIGKILLs a long Gladia
    # call mid-poll. AI flow jobs (Phase 2 commit 2) are bounded much
    # tighter at their enqueue sites.
    job_timeout = TRANSCRIPTION_JOB_TIMEOUT_SECONDS

    # Transcription is NOT retried automatically:
    # - It costs money on Gladia (audio uploaded again)
    # - Most failures are deterministic (missing MP4 recording, expired
    #   Livestorm token, audio too long) — retrying makes them worse
    # The handler writes status='error' on failure and the user can
    # re-trigger from the UI.
    max_tries = 1

    on_startup = on_startup
    on_shutdown = on_shutdown
