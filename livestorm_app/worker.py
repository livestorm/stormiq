"""arq worker entry point.

Run with:
    arq livestorm_app.worker.WorkerSettings

The worker is a separate OS process. It consumes jobs enqueued by the
FastAPI app (via `livestorm_app.queue.enqueue_unique_job_sync`):

    - run_transcription(job_id, session_id)
    - run_overall_analysis_job(session_id, output_language)
    - run_deep_analysis_job(session_id, output_language)
    - run_smart_recap_job(session_id, tone)
    - run_content_repurposing_job(session_id, output_language)
    - run_cover_image_job(session_id)   (chained after a Professional recap)
    - run_people_sync(sync_id, session_id)

plus a no-op `ping` and the stuck-job sweeper cron.

Hard rules:
- Job handlers receive `ctx` (arq's per-job context) as their first arg
  and a minimal payload after. Job args are stored in Redis and logged by
  arq: never pass a secret, or a connection id (it is also the user's
  session cookie). API keys come from the worker's own env; Livestorm
  credentials are resolved from the connection id stored on the job's
  `transcript_jobs` or `livestorm_sync_jobs` row (`db.claim_transcript_job`
  or `db.claim_livestorm_sync`, then `auth.livestorm_auth_for_job`).
- Every handler must publish progress at each meaningful stage via
  `livestorm_app.progress.publish_progress_sync`. The UI depends on it.
- On any error, the handler is responsible for writing the failure state
  to Postgres before re-raising. Nothing is retried (`max_tries=1`).
- Each job kind has its own timeout (`WorkerSettings.functions`).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Dict, Optional, Tuple

from arq import cron
from arq.worker import func

from livestorm_app.config import get_runtime_secret, load_env_file
from livestorm_app.db import (
    ALL_ORGS,
    claim_livestorm_sync,
    claim_transcript_job,
    database_enabled,
    ensure_database_schema,
    fetch_cached_session,
    fetch_session_fields,
    fetch_session_roster,
    get_db_connection,
    sweep_stuck_livestorm_syncs,
    touch_livestorm_sync,
    update_transcript_job_progress,
    update_transcript_job_status,
    upsert_cached_session,
)
from livestorm_app.progress import (
    clear_progress_sync,
    publish_progress_sync,
)
from livestorm_app.queue import cover_image_job_id, get_redis_settings, job_result_serializer


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
# 30–45 minutes on a 2-hour recording. The transcriber reports progress on
# every Gladia status change and at least once a minute in between
# (PROGRESS_HEARTBEAT_SECONDS in gladia/transcriber.py); each report
# refreshes `updated_at`. A 'running' row silent for an hour is stuck.
# 'pending' rows are swept too: if Redis lost the queued job (e.g. a
# restart of a non-persistent instance), nothing would ever pick it up.
# A job that finishes after being swept still writes 'completed'.

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
                        connection_id = NULL,
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


def _sweep_stuck_people_syncs_sync() -> int:
    """Close `livestorm_sync_jobs` rows stuck in 'pending' or 'running' for
    STALE_AFTER: 'error', connection id cleared (`db.sweep_stuck_livestorm_syncs`).
    A people sync times out after 15 minutes, its wait for the org's lock
    included, so a row silent for an hour was lost (a Redis restart, a killed
    worker). Returns the number of rows closed."""
    if not database_enabled():
        return 0
    try:
        return sweep_stuck_livestorm_syncs(datetime.now(timezone.utc) - STALE_AFTER)
    except Exception:
        logger.exception("Stuck people-sync sweep failed")
        return 0


async def sweep_stuck_jobs(ctx: Dict[str, Any]) -> None:
    """Cron handler: close stalled transcript_jobs and livestorm_sync_jobs rows every 10 minutes."""
    affected = await asyncio.to_thread(_sweep_stuck_transcript_jobs_sync)
    if affected:
        logger.warning("[sweeper] Marked %d stuck transcript job(s) as error", affected)
    syncs = await asyncio.to_thread(_sweep_stuck_people_syncs_sync)
    if syncs:
        logger.warning("[sweeper] Marked %d stuck people sync(s) as error", syncs)


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
# The web handler enqueues this job and returns immediately with a job_id;
# the worker downloads the recording, uploads to Gladia, polls, persists,
# and writes status back to `transcript_jobs`.
#
# Progress is written to two places:
#   - `transcript_jobs.progress` (Postgres TEXT, JSON-encoded): the raw
#     Gladia step. Its writes also keep `updated_at` fresh for the sweeper.
#   - Redis via `publish_progress_sync('transcript', session_id, ...)`: the
#     stage-floor progress the frontend renders.
#
# The Gladia transcriber is sync (urllib under the hood) and may take
# 30-45 minutes on a 2-hour recording. We push it into a thread via
# `asyncio.to_thread` so the worker's event loop stays responsive for
# other jobs. The callback runs INSIDE that thread, so it uses the sync
# Redis writer — async would need a running loop the thread doesn't have.
#
# Timeouts are per job kind (see WorkerSettings.functions). A timeout
# cancels the coroutine, but a thread started with `asyncio.to_thread`
# runs to completion regardless; the HTTP timeouts inside the clients
# are what bound it. Cancellation is handled so the DB row never stays
# 'running' after a timeout or a worker shutdown.

TRANSCRIPTION_JOB_TIMEOUT_SECONDS = 60 * 120  # download + ffmpeg + upload + Gladia on a long recording
LLM_JOB_TIMEOUT_SECONDS = 60 * 15  # up to two LLM calls (truncation or completeness retry), 5-min read timeout each
# The session analysis: the chat labels' batches, then the understanding
# call, streamed with its own 10-minute deadline (`JobSpec.call_deadline_s`).
SESSION_ANALYSIS_TIMEOUT_SECONDS = 60 * 25
COVER_IMAGE_JOB_TIMEOUT_SECONDS = 60 * 5  # one Images call, 3-min HTTP timeout

# Transcriptions download the recording and run ffmpeg locally; more than a
# couple at once can exhaust the starter instance's memory. Extra jobs wait
# for a slot (the wait counts toward their timeout).
MAX_CONCURRENT_TRANSCRIPTIONS = 2
TRANSCRIPTION_SLOT_HEARTBEAT_SECONDS = 60


@contextlib.asynccontextmanager
async def _transcription_slot(ctx: Dict[str, Any], job_id: str) -> AsyncIterator[None]:
    """Hold one of the worker's transcription slots for the duration of a job.

    While waiting, the job's progress is refreshed every minute: that keeps
    `transcript_jobs.updated_at` fresh (so the stuck-job sweeper leaves it
    alone) and tells the user why nothing is happening yet.
    """
    slots = ctx.get("transcription_slots") if isinstance(ctx, dict) else None
    if slots is None:
        yield
        return
    while True:
        try:
            await asyncio.wait_for(slots.acquire(), timeout=TRANSCRIPTION_SLOT_HEARTBEAT_SECONDS)
            break
        except asyncio.TimeoutError:
            update_transcript_job_progress(job_id, {
                "step": "waiting",
                "message": "Waiting for another transcription to finish…",
            })
    try:
        yield
    finally:
        slots.release()


def _read_gladia_key_or_raise() -> str:
    key = str(get_runtime_secret("GLADIA_KEY", "") or "").strip()
    if not key:
        raise RuntimeError("GLADIA_KEY is not configured on the worker. Transcription is unavailable.")
    return key


def _transcription_options(session_id: str) -> Tuple[Dict[str, Any], Optional[int]]:
    """The Gladia options for a new transcript of `session_id`, and the count
    of people on stage they were built from.

    The speaker cap follows the people on stage of the cached session
    (`record.inputs.count_people_on_stage`, which picks the people source as
    the v1 record does; docs/product.md §13, "Speaker cap"): the session's
    stored roster when a people sync saved one (`db.fetch_session_roster`),
    the session payload's people otherwise. When that count is unknown (no
    cached people, nobody marked on stage, or any failure here), the cap
    stays at Gladia's default ceiling: options never fail a transcription.
    Light reads with `ALL_ORGS`: the worker has no caller, and the job was
    started by one who could read the session.
    """
    from livestorm_app.gladia.transcriber import speaker_options

    people_on_stage: Optional[int] = None
    try:
        # Imported here, the only place the worker runs v1 code, so an error
        # in `record/` falls back to the default cap (tests/test_roster.py).
        from livestorm_app.record.inputs import count_people_on_stage
        from livestorm_app.record.roster import stored_roster_rows

        fields = fetch_session_fields(session_id, ["session_payload"], organization_id=ALL_ORGS)
        stored = fetch_session_roster(session_id, ALL_ORGS)
        people_on_stage = count_people_on_stage(
            fields or {}, roster_rows=stored_roster_rows(stored) if stored is not None else None,
        )
    except Exception:
        logger.exception("[run_transcription] session_id=%s people on stage unknown; default speaker cap", session_id)
        people_on_stage = None
    return speaker_options(people_on_stage), people_on_stage


def _record_people_on_stage(transcript_payload: Any, people_on_stage: Optional[int]) -> None:
    """Note in the transcript's provenance (`stormiq`, written by the
    transcriber) the count of people on stage its speaker cap came from."""
    if not isinstance(transcript_payload, dict):
        return
    provenance = transcript_payload.get("stormiq")
    if isinstance(provenance, dict):
        provenance["people_on_stage"] = people_on_stage


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
    session_id: str,
) -> Dict[str, Any]:
    """Fetch + transcribe a Livestorm recording, persist to session_cache.

    Args:
        job_id:      transcript_jobs.job_id created at enqueue time
        session_id:  target Livestorm session

    The job args hold no credentials (arq stores them in Redis and logs
    them). The Livestorm credential reference is read from the job's row
    when the job is claimed; the Gladia key comes from the worker's env.
    The speaker cap comes from the cached session's people
    (`_transcription_options`), and the count it used is noted in the
    transcript's `stormiq` block.

    Returns a small status payload (for arq's result cache); the real
    result lives in `session_cache.transcript_payload`. A job whose row is
    no longer 'pending' (swept, closed as lost) is skipped without paid work.

    On error, timeout or cancellation: marks transcript_jobs as 'error',
    clears Redis progress, re-raises so arq records the failure. arq's
    retry is intentionally NOT configured for this job — see
    WorkerSettings note below.
    """
    # Import the sync Gladia client lazily so the worker can boot even
    # when imageio_ffmpeg or other transcoding deps are missing on a
    # given host (the import would otherwise fire at module load).
    from livestorm_app.auth import livestorm_auth_for_job
    from livestorm_app.transcript_client import fetch_session_transcript

    logger.info("[run_transcription] job_id=%s session_id=%s start", job_id, session_id)
    claim = claim_transcript_job(job_id)
    if claim is None:
        logger.warning("[run_transcription] job_id=%s is no longer pending; skipping", job_id)
        return {"jobId": job_id, "status": "skipped"}
    connection_id = claim["connection_id"]
    publish_progress_sync("transcript", session_id, "queued")

    # Set when the job is cancelled (timeout, shutdown). The transcription
    # thread can't be killed, but it stops at its next progress report
    # instead of running on (and holding resources) after the job failed.
    cancelled = threading.Event()

    def on_gladia_progress(progress: Dict[str, Any]) -> None:
        if cancelled.is_set():
            raise RuntimeError("Transcription job was cancelled; stopping.")
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
        gladia_api_key = _read_gladia_key_or_raise()
        gladia_options, people_on_stage = await asyncio.to_thread(_transcription_options, session_id)
        logger.info(
            "[run_transcription] job_id=%s people_on_stage=%s max_speakers=%s",
            job_id,
            people_on_stage,
            gladia_options["diarization_config"]["max_speakers"],
        )
        async with _transcription_slot(ctx, job_id):
            # Resolved after the wait for a slot, so the token is fresh.
            api_key = await asyncio.to_thread(livestorm_auth_for_job, connection_id)
            publish_progress_sync("transcript", session_id, "fetching_recording")
            transcript_payload = await asyncio.to_thread(
                fetch_session_transcript,
                gladia_api_key,
                session_id,
                livestorm_api_key=api_key,
                gladia_options=gladia_options,
                on_progress=on_gladia_progress,
            )
        _record_people_on_stage(transcript_payload, people_on_stage)
        publish_progress_sync("transcript", session_id, "persisting")
        upsert_cached_session(api_key, session_id, transcript_payload=transcript_payload)
        update_transcript_job_status(job_id, "completed")
        # Pollers read the durable cache first, so progress can go at once.
        clear_progress_sync("transcript", session_id)
        logger.info("[run_transcription] job_id=%s session_id=%s done", job_id, session_id)
        return {"jobId": job_id, "status": "completed"}
    except asyncio.CancelledError:
        # Timeout or worker shutdown. CancelledError is not an Exception,
        # so without this the row would stay 'running' until the sweeper.
        cancelled.set()
        logger.warning("[run_transcription] job_id=%s session_id=%s cancelled", job_id, session_id)
        update_transcript_job_status(
            job_id,
            "error",
            error="The transcription was interrupted (time limit reached or worker restarted). Please try again.",
        )
        clear_progress_sync("transcript", session_id)
        raise
    except Exception as exc:
        logger.exception("[run_transcription] job_id=%s session_id=%s failed", job_id, session_id)
        update_transcript_job_status(job_id, "error", error=str(exc))
        clear_progress_sync("transcript", session_id)
        raise


# ── AI flow jobs (overall / deep / smart recap / content repurposing) ──────
#
# Four flows, same shape: load cached payloads → build prompt → LLM →
# persist to session_cache. Each is a thin wrapper around the existing
# sync function in api_logic.py, with stage-floor progress around it.
#
# The LLM API key is read from the worker's own env rather than passed
# through the queue. Two reasons: (1) avoids putting secrets into Redis,
# (2) one source of truth for the key on the worker host.
# Active provider is selected via LLM_PROVIDER (defaults to "anthropic").
#
# Progress mapping is coarse: we publish 'loading_sources' → 'building_prompt'
# → 'analyzing' before the (single, blocking) LLM call, then 'persisting'
# → 'done' after it. The bar will sit at "analyzing" (floor 40) for the
# bulk of the wait — same UX as the transcription job. The card registry
# (docs/roadmap.md) would give natural inflection points for more stages.


def _read_llm_key_or_raise() -> str:
    from livestorm_app.llm_client import get_llm_key

    return get_llm_key()


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
    The runner persists its own result before returning.

    Always clears Redis progress on completion, failure, timeout or
    cancellation. Pollers check the durable cache before progress, so
    no 'done' marker needs to linger (and no worker slot is held for it).
    """
    logger.info("[%s] session_id=%s start", kind, session_id)
    publish_progress_sync(kind, session_id, "queued")
    try:
        publish_progress_sync(kind, session_id, "loading_sources")
        publish_progress_sync(kind, session_id, "building_prompt")
        publish_progress_sync(kind, session_id, "analyzing")
        result = await asyncio.to_thread(sync_runner, *runner_args)
        logger.info("[%s] session_id=%s done", kind, session_id)
        return result if isinstance(result, dict) else {"status": "ok"}
    except Exception:
        logger.exception("[%s] session_id=%s failed", kind, session_id)
        raise
    finally:
        clear_progress_sync(kind, session_id)


SESSION_ANALYSIS_PROGRESS = "session_analysis"  # progress.SESSION_ANALYSIS_STAGE_FLOORS


async def run_session_analysis_job(ctx: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    """The v1 session analysis the user starts: the chat labels, then the
    understanding job when there is a transcript
    (`api_logic.run_session_analysis`). Gets the session id only; the model
    key comes from the worker's environment."""
    from livestorm_app.api_logic import run_session_analysis

    api_key = _read_llm_key_or_raise()
    logger.info("[session_analysis] session_id=%s start", session_id)
    started_at_ms = int(time.time() * 1000)

    def report(stage: str) -> None:
        # The page's progress panel: the stage, and when this run started.
        publish_progress_sync(SESSION_ANALYSIS_PROGRESS, session_id, stage, extra={"started_at_ms": started_at_ms})

    # First thing, so a poll never reads an earlier run's last stage as this one's.
    report("labelling")
    try:
        result = await asyncio.to_thread(run_session_analysis, api_key, session_id, report)
    except Exception as exc:
        logger.error("[session_analysis] session_id=%s failed (%s)", session_id, type(exc).__name__)
        raise
    logger.info(
        "[session_analysis] session_id=%s done (labels=%s understanding=%s)",
        session_id, result.get("chatLabels"), result.get("understanding"),
    )
    return result


async def run_overall_analysis_job(
    ctx: Dict[str, Any],
    session_id: str,
    output_language: str,
) -> Dict[str, Any]:
    from livestorm_app.api_logic import run_overall_analysis

    openai_key = _read_llm_key_or_raise()
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

    openai_key = _read_llm_key_or_raise()
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

    openai_key = _read_llm_key_or_raise()
    result = await _run_ai_job(
        "smart_recap",
        session_id,
        run_smart_recap,
        openai_key, session_id, tone,
    )
    # After the Professional recap lands, kick off the cover image
    # generator so workspace cards can render the AI cover next time
    # /api/workspace-sessions is loaded. Skipped for other tones
    # (Hype / Surprise) — the Professional recap is the canonical
    # input for the cover prompt.
    if str(tone or "").strip().lower() == "professional":
        await _enqueue_default_cover_image(ctx, session_id)
    return result


async def run_content_repurposing_job(
    ctx: Dict[str, Any],
    session_id: str,
    output_language: str,
) -> Dict[str, Any]:
    from livestorm_app.api_logic import run_content_repurposing

    openai_key = _read_llm_key_or_raise()
    return await _run_ai_job(
        "content_repurposing",
        session_id,
        run_content_repurposing,
        openai_key, session_id, output_language,
    )


# ── Cover image job ─────────────────────────────────────────────────────────
#
# Renders the workspace card cover from the Professional Smart Recap.
# Auto-enqueued after run_smart_recap_job(professional) completes —
# stays out of the AI flow registry (run_*_job names) because it has
# no UI-triggered entry point. Users never call this directly.
#
# OpenAI Images calls are slower (~15-45s) than chat completions, so
# the job runs at lower concurrency in practice via arq's natural
# queuing. Failures are caught at the auto-enqueue site so a broken
# image generator never blocks recap or transcription completion.


def _read_openai_images_key_or_raise() -> str:
    """Cover image generation always uses the OpenAI Images API regardless of LLM_PROVIDER."""
    from livestorm_app.config import get_runtime_secret

    key = str(get_runtime_secret("OPENAI_API_KEY", "") or "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY not configured on worker process (required for cover image generation).")
    return key


async def run_cover_image_job(
    ctx: Dict[str, Any],
    session_id: str,
    force_regenerate: bool = False,
) -> Dict[str, Any]:
    from livestorm_app.api_logic import run_cover_image_generation

    openai_key = _read_openai_images_key_or_raise()
    logger.info("[run_cover_image_job] session_id=%s start", session_id)
    try:
        result = await asyncio.to_thread(
            run_cover_image_generation,
            openai_key,
            session_id,
            force_regenerate=force_regenerate,
        )
        logger.info(
            "[run_cover_image_job] session_id=%s done status=%s",
            session_id,
            (result or {}).get("status"),
        )
        return result if isinstance(result, dict) else {"status": "ok"}
    except Exception:
        logger.exception("[run_cover_image_job] session_id=%s failed", session_id)
        raise


async def _enqueue_default_cover_image(ctx: Dict[str, Any], session_id: str) -> None:
    """Fire-and-forget enqueue of the cover image after a Professional recap.

    This is the only automatic cover trigger (page reads never start one).
    Skips when a cover already exists. The fixed job id means a cover that
    is already queued, running, or failed within the last hour (arq's
    retained result) is not started again. Failures are logged and
    swallowed so a broken image generator never bubbles into the recap.
    """
    try:
        cached = await asyncio.to_thread(fetch_cached_session, "", session_id, organization_id=ALL_ORGS)
        if (cached or {}).get("has_cover_image"):
            logger.info(
                "[run_smart_recap_job] session_id=%s already has cover image; skipping",
                session_id,
            )
            return
        redis_pool = ctx.get("redis") if isinstance(ctx, dict) else None
        if redis_pool is None:
            logger.warning(
                "[run_smart_recap_job] session_id=%s ctx missing redis pool; cannot auto-enqueue cover",
                session_id,
            )
            return
        job = await redis_pool.enqueue_job(
            "run_cover_image_job", session_id, _job_id=cover_image_job_id(session_id),
        )
        if job is None:
            logger.info(
                "[run_smart_recap_job] session_id=%s cover job already queued or ran within the last hour; skipping",
                session_id,
            )
            return
        logger.info(
            "[run_smart_recap_job] session_id=%s auto-enqueued run_cover_image_job",
            session_id,
        )
    except Exception:
        logger.exception(
            "[run_smart_recap_job] session_id=%s failed to auto-enqueue cover image",
            session_id,
        )


# ── People sync job ────────────────────────────────────────────────────────
#
# Reads a session's complete people list from Livestorm (about one call per
# 50 registrants) and saves it as the session's roster. The web process
# creates the `livestorm_sync_jobs` row and enqueues this job with ids only
# (`people_sync.start_people_sync`); the orchestration lives in
# `people_sync.py`, run here in a thread.
#
# At most one people sync per org runs at a time: a Redis lock,
# `stormiq:livestorm-sync:{org}`, keeps one org's syncs from adding up
# against its Livestorm rate limit. A job that finds it held waits, with a
# heartbeat (as `_transcription_slot` does), and the wait counts toward its
# timeout. A waiting job holds one of the worker's `max_jobs` slots, so only
# one sync per org may wait (`stormiq:livestorm-sync-wait:{org}`); a third
# fails at once with a clear message, and one org can never fill the slots
# every org's jobs share. Both keys outlive the timeout
# (`PEOPLE_SYNC_LOCK_TTL_SECONDS`), so a killed worker's keys free
# themselves, and only their owner deletes them (the token check in
# `_RELEASE_LOCK_SCRIPT`).

PEOPLE_SYNC_JOB_TIMEOUT_SECONDS = 60 * 15  # about 100 calls for 5,000 registrants, plus the wait for the org's lock
PEOPLE_SYNC_LOCK_TTL_SECONDS = PEOPLE_SYNC_JOB_TIMEOUT_SECONDS + 5 * 60
PEOPLE_SYNC_LOCK_POLL_SECONDS = 2.0
PEOPLE_SYNC_LOCK_HEARTBEAT_SECONDS = 60
# The pager stops itself this long before the job's time limit, so the job
# fails with a clear message rather than being cancelled mid-page.
PEOPLE_SYNC_DEADLINE_MARGIN_SECONDS = 30

# Deletes the lock only if it still holds this job's token: a job whose lock
# expired must never release the next owner's.
_RELEASE_LOCK_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


def people_sync_lock_key(organization_id: str) -> str:
    """The per-org lock. A session without an org (local dev only) shares one."""
    return f"stormiq:livestorm-sync:{str(organization_id or '').strip() or '-'}"


def people_sync_wait_key(organization_id: str) -> str:
    """The per-org waiting place: held by the one sync that waits for the lock."""
    return f"stormiq:livestorm-sync-wait:{str(organization_id or '').strip() or '-'}"


async def _release_people_sync_key(redis: Any, key: str, token: str, sync_id: str) -> None:
    try:
        await redis.eval(_RELEASE_LOCK_SCRIPT, 1, key, token)
    except Exception:
        logger.warning("[run_people_sync] sync_id=%s could not release a people-sync key; it expires on its own", sync_id, exc_info=True)


@contextlib.asynccontextmanager
async def _people_sync_org_lock(
    ctx: Dict[str, Any], organization_id: str, sync_id: str, session_id: str,
) -> AsyncIterator[None]:
    """Hold the org's people-sync lock for the duration of a sync.

    While another sync of the org holds it, the job takes the org's one
    waiting place and waits: every PEOPLE_SYNC_LOCK_HEARTBEAT_SECONDS it
    refreshes its row's `updated_at` (so the sweeper leaves it alone) and its
    progress (so the user sees why). A row closed meanwhile (swept) stops the
    wait. When another sync already waits, it raises `PeopleSyncBusy` at
    once. Both keys are released in `finally`, only by their owner.
    """
    from livestorm_app import people_sync

    redis = ctx.get("redis") if isinstance(ctx, dict) else None
    if redis is None:
        logger.warning("[run_people_sync] sync_id=%s no Redis in the job context; running without the org lock", sync_id)
        yield
        return
    key = people_sync_lock_key(organization_id)
    token = uuid.uuid4().hex
    if not await redis.set(key, token, nx=True, ex=PEOPLE_SYNC_LOCK_TTL_SECONDS):
        wait_key = people_sync_wait_key(organization_id)
        if not await redis.set(wait_key, token, nx=True, ex=PEOPLE_SYNC_LOCK_TTL_SECONDS):
            raise people_sync.PeopleSyncBusy()
        try:
            loop = asyncio.get_running_loop()
            next_heartbeat = loop.time()
            while not await redis.set(key, token, nx=True, ex=PEOPLE_SYNC_LOCK_TTL_SECONDS):
                if loop.time() >= next_heartbeat:
                    if not await asyncio.to_thread(touch_livestorm_sync, sync_id):
                        raise RuntimeError("The people sync was closed while it waited for another sync of this organization.")
                    publish_progress_sync(
                        "roster", session_id, "waiting", label="Waiting for another people sync of this organization to finish…",
                    )
                    next_heartbeat = loop.time() + PEOPLE_SYNC_LOCK_HEARTBEAT_SECONDS
                await asyncio.sleep(PEOPLE_SYNC_LOCK_POLL_SECONDS)
        finally:
            await _release_people_sync_key(redis, wait_key, token, sync_id)
    try:
        yield
    finally:
        await _release_people_sync_key(redis, key, token, sync_id)


async def run_people_sync(
    ctx: Dict[str, Any],
    sync_id: str,
    session_id: str,
) -> Dict[str, Any]:
    """Read a session's people from Livestorm and save its roster.

    Args:
        sync_id:     livestorm_sync_jobs.sync_id created at enqueue time
        session_id:  the session it belongs to

    The arguments hold no credentials. The job claims its row
    (`db.claim_livestorm_sync`, which hands the stored connection id over
    once and clears it); a row that is no longer 'pending' (swept, closed as
    lost) is skipped with no Livestorm call. It then takes the org's lock
    (`_people_sync_org_lock`), checks the daily cap again
    (`people_sync.check_daily_cap`), checks that the connection belongs to
    the session's org and resolves its token
    (`people_sync._resolve_sync_credentials`), and runs
    `people_sync.run_people_sync` in a thread. Progress goes to Redis under
    "roster" and is cleared in `finally`.

    Returns counts only (arq keeps results in Redis). On error, timeout or
    cancellation the row is marked 'error' (connection id cleared) and the
    job fails; nothing is retried. A failure reaches arq only as
    `people_sync.PeopleSyncFailed` with the row's message, never the
    original exception: arq pickles it into Redis, and a `requests`
    exception carries the user's token and the upstream body.
    """
    from livestorm_app import people_sync

    logger.info("[run_people_sync] sync_id=%s session_id=%s start", sync_id, session_id)
    started = time.monotonic()
    claim = await asyncio.to_thread(claim_livestorm_sync, sync_id)
    if claim is None:
        logger.warning("[run_people_sync] sync_id=%s is no longer pending; skipping", sync_id)
        return {"status": "skipped"}
    publish_progress_sync("roster", session_id, "queued")

    # Set when the job is cancelled (timeout, shutdown). The sync's thread
    # can't be killed; it stops at its next page or before it writes.
    cancelled = threading.Event()
    try:
        if claim["session_id"] != str(session_id or "").strip():
            raise RuntimeError("The people sync's row belongs to another session.")
        organization_id = await asyncio.to_thread(people_sync.session_organization, session_id)
        async with _people_sync_org_lock(ctx, organization_id, sync_id, session_id):
            # Under the lock every earlier sync of the org has recorded its
            # calls, so the cap check is exact here.
            await asyncio.to_thread(people_sync.check_daily_cap, sync_id, organization_id)
            # Resolved after the wait for the lock, so the token is fresh.
            api_key = await asyncio.to_thread(
                people_sync._resolve_sync_credentials, claim, session_org_id=organization_id,
            )
            deadline = started + PEOPLE_SYNC_JOB_TIMEOUT_SECONDS - PEOPLE_SYNC_DEADLINE_MARGIN_SECONDS
            counts = await asyncio.to_thread(
                people_sync.run_people_sync,
                api_key,
                session_id,
                {"sync_id": sync_id, "reason": claim["reason"], "organization_id": organization_id},
                deadline=deadline,
                should_stop=cancelled.is_set,
            )
        logger.info(
            "[run_people_sync] sync_id=%s session_id=%s done calls=%s people=%s",
            sync_id, session_id, counts.get("calls"), counts.get("people"),
        )
        return counts
    except asyncio.CancelledError:
        # Timeout or worker shutdown. CancelledError is not an Exception.
        cancelled.set()
        logger.warning("[run_people_sync] sync_id=%s session_id=%s cancelled", sync_id, session_id)
        people_sync.mark_sync_failed(sync_id, people_sync.PeopleSyncStopped("cancelled"))
        raise
    except Exception as exc:
        if isinstance(exc, people_sync.EXPECTED_REFUSALS):
            logger.warning("[run_people_sync] sync_id=%s session_id=%s not run (%s)", sync_id, session_id, type(exc).__name__)
        else:
            # The traceback stays server-side; it holds no token.
            logger.exception("[run_people_sync] sync_id=%s session_id=%s failed", sync_id, session_id)
        people_sync.mark_sync_failed(sync_id, exc)
        # Our own message only, with no cause or context attached: arq
        # pickles this into Redis (see the docstring).
        raise people_sync.PeopleSyncFailed(people_sync.failure_message(exc)) from None
    finally:
        clear_progress_sync("roster", session_id)


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
    # Created here so it binds to the worker's event loop; arq copies ctx
    # into every job, so all jobs share this one semaphore.
    ctx["transcription_slots"] = asyncio.Semaphore(MAX_CONCURRENT_TRANSCRIPTIONS)
    if not str(get_runtime_secret("GLADIA_KEY", "") or "").strip():
        logger.warning("[worker] GLADIA_KEY is not set: transcription jobs will fail.")
    logger.info("[worker] startup complete")


async def on_shutdown(ctx: Dict[str, Any]) -> None:
    logger.info("[worker] shutdown")


class WorkerSettings:
    """arq WorkerSettings.

    `functions` lists every job handler, each with its own timeout.

    `cron_jobs` registers scheduled tasks. `run_at_startup=False` keeps
    the sweeper from running on every deploy (it would race the first
    real job).

    Defaults intentionally NOT customised:
    - `max_jobs` (default 10) — sized for one host with light parallelism.
      Transcriptions are further capped by MAX_CONCURRENT_TRANSCRIPTIONS.
    - `keep_result` (default 3600s) — results stay in Redis 1h for polling.
      With deterministic job ids this also blocks an identical job for an
      hour; `queue.enqueue_unique_job_sync` clears it when a user retries.
    - `job_timeout` (default 300s) — applies to `ping` only; every real
      job sets its own timeout below.

    `job_serializer` stores a failed job's error as its message only
    (`queue.job_result_serializer`); everything else is pickled as arq's
    default does.
    """

    redis_settings = get_redis_settings()
    job_serializer = job_result_serializer

    functions = [
        ping,
        func(run_transcription, timeout=TRANSCRIPTION_JOB_TIMEOUT_SECONDS),
        func(run_session_analysis_job, timeout=SESSION_ANALYSIS_TIMEOUT_SECONDS),
        func(run_overall_analysis_job, timeout=LLM_JOB_TIMEOUT_SECONDS),
        func(run_deep_analysis_job, timeout=LLM_JOB_TIMEOUT_SECONDS),
        func(run_smart_recap_job, timeout=LLM_JOB_TIMEOUT_SECONDS),
        func(run_content_repurposing_job, timeout=LLM_JOB_TIMEOUT_SECONDS),
        func(run_cover_image_job, timeout=COVER_IMAGE_JOB_TIMEOUT_SECONDS),
        func(run_people_sync, timeout=PEOPLE_SYNC_JOB_TIMEOUT_SECONDS),
    ]

    cron_jobs = [
        cron(sweep_stuck_jobs, minute={0, 10, 20, 30, 40, 50}, run_at_startup=False),
    ]

    # Transcription is NOT retried automatically:
    # - It costs money on Gladia (audio uploaded again)
    # - Most failures are deterministic (missing MP4 recording, expired
    #   Livestorm token, audio too long) — retrying makes them worse
    # The handler writes status='error' on failure and the user can
    # re-trigger from the UI. The same holds for every job: a people sync
    # spends Livestorm calls, and its user retries with POST /roster-sync.
    max_tries = 1

    on_startup = on_startup
    on_shutdown = on_shutdown
