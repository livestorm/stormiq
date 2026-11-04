"""Redis + arq queue setup.

The worker process consumes jobs from these queues. The web process enqueues
jobs into them. Both processes share the same Redis instance.

CLAUDE.md §5 (processing flow) + §14 (deployment) describe how this fits
into the rest of the app.

Hard rules:
- The worker is a separate OS process. Never call worker functions
  directly from FastAPI handlers — enqueue them via `enqueue_unique_job_sync`.
- Every job has a deterministic id (see "Job ids" below), so the same
  work can never be queued twice at once.
- Job arguments are stored in Redis. Never put a secret in them (API keys,
  OAuth tokens): pass a reference such as a connection id and let the
  worker resolve it.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, Optional, TypedDict

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from arq.constants import result_key_prefix


logger = logging.getLogger(__name__)


# ── Connection ──────────────────────────────────────────────────────────────


def get_redis_url() -> str:
    """Resolve the Redis URL.

    Order: REDIS_URL → default (localhost:6379, db 0).
    The default is for local dev only — production must set REDIS_URL.
    """
    raw = str(os.getenv("REDIS_URL", "") or "").strip()
    return raw or "redis://localhost:6379/0"


def get_redis_settings() -> RedisSettings:
    """Convert REDIS_URL into the RedisSettings shape arq expects."""
    return RedisSettings.from_dsn(get_redis_url())


_pool: Optional[ArqRedis] = None


async def get_arq_pool() -> ArqRedis:
    """Singleton arq pool for enqueuing jobs from async code.

    arq pools are long-lived. Reuse one per process. Created lazily so
    importing this module doesn't open a connection.
    """
    global _pool
    if _pool is None:
        _pool = await create_pool(get_redis_settings())
    return _pool


async def close_arq_pool() -> None:
    """Tear down the pool on app shutdown. Safe to call when uninitialised."""
    global _pool
    if _pool is not None:
        try:
            await _pool.close()
        except Exception:
            logger.exception("Failed to close arq pool")
        _pool = None


# ── Job type hints ──────────────────────────────────────────────────────────


class JobMeta(TypedDict, total=False):
    """Fields every job carries for observability + accounting.

    `session_id`  — the session the job operates on (nullable only for
                    cron jobs like the sweeper).
    `enqueued_by` — `"web"` for handler-enqueued, `"cron"` for scheduled.
    """

    session_id: str
    enqueued_by: str


# ── Job ids ─────────────────────────────────────────────────────────────────
#
# arq refuses to enqueue a job whose id already has a queued job or a
# retained result (`keep_result`, 1 hour by default). Deterministic ids
# turn that into dedupe: two clicks on Generate, or two teammates opening
# the same session, share one job instead of paying for two.


def ai_job_id(kind: str, session_id: str, dimension: str) -> str:
    """One job per (AI flow, session, language or tone)."""
    return f"{kind}:{str(session_id).strip()}:{str(dimension or '').strip() or '_'}"


def cover_image_job_id(session_id: str) -> str:
    """One cover job per session. The retained result doubles as a cooldown:
    after a failure, the cover is not retried automatically for an hour."""
    return f"cover_image:{str(session_id).strip()}"


def transcription_job_id(transcript_job_id: str) -> str:
    """arq id for a `transcript_jobs` row. The row's partial unique index
    (one active job per session) is the dedupe; this id lets the web
    process ask arq whether the job behind a row is still alive."""
    return f"transcription:{str(transcript_job_id).strip()}"


# ── Enqueue ─────────────────────────────────────────────────────────────────


async def enqueue_job(function_name: str, *args: Any, **kwargs: Any) -> Optional[str]:
    """Enqueue a job by name from async code.

    Returns the job id, or None on failure or when `_job_id` is already
    taken. Swallows connection errors so a Redis hiccup never bubbles up
    into a 500.
    """
    try:
        pool = await get_arq_pool()
        job = await pool.enqueue_job(function_name, *args, **kwargs)
        return job.job_id if job is not None else None
    except Exception:
        logger.exception("Failed to enqueue arq job %s", function_name)
        return None


def enqueue_unique_job_sync(
    function_name: str,
    job_id: str,
    *args: Any,
    **kwargs: Any,
) -> Optional[Dict[str, Any]]:
    """Enqueue `function_name` under a fixed `job_id`, or join the live job.

    For sync code (FastAPI runs `def` handlers in a threadpool with no event
    loop). Returns `{jobId, jobStatus, enqueued}`:

        enqueued=True,  jobStatus='pending'            a new job was queued
        enqueued=False, jobStatus='pending'|'running'  the same job is
                                                       already in flight

    A finished job (completed or failed) only blocks re-enqueueing through
    its retained result. The caller asked to run the work again, so that
    result is cleared and the job queued afresh: this is what lets a user
    retry after an error. Returns None when Redis is unreachable.
    """
    try:
        return asyncio.run(_enqueue_unique(function_name, job_id, *args, **kwargs))
    except Exception:
        logger.exception("Failed to enqueue arq job %s (%s)", function_name, job_id)
        return None


async def _enqueue_unique(function_name: str, job_id: str, *args: Any, **kwargs: Any) -> Dict[str, Any]:
    # A fresh pool per call: the module-level pool belongs to another event
    # loop and can't be reused from asyncio.run().
    pool = await create_pool(get_redis_settings())
    try:
        for _attempt in range(2):
            job = await pool.enqueue_job(function_name, *args, _job_id=job_id, **kwargs)
            if job is not None:
                return {"jobId": job_id, "jobStatus": "pending", "enqueued": True}
            status = await _job_status(pool, job_id)
            if status["jobStatus"] in ("pending", "running"):
                return {"jobId": job_id, "jobStatus": status["jobStatus"], "enqueued": False}
            # Finished (or gone between the two calls): drop the retained
            # result so the id is free, then enqueue again.
            await pool.delete(result_key_prefix + job_id)
        status = await _job_status(pool, job_id)
        return {"jobId": job_id, "jobStatus": status["jobStatus"], "enqueued": False}
    finally:
        await pool.close()


# ── Job status lookup (sync) ───────────────────────────────────────────────
#
# Sync wrapper around arq's `Job.status()`. Used by the polling endpoints.
# The status enum is normalised to the same vocabulary the transcription
# flow already uses, so the frontend handles every poll with one shape:
#
#     'pending'    — enqueued but not yet picked up (arq: deferred|queued)
#     'running'    — worker has picked it up (arq: in_progress)
#     'completed'  — worker finished successfully (arq: complete, no error)
#     'error'      — worker raised; `error` field carries the message
#     'not_found'  — job_id unknown or expired (arq keeps results for 1h;
#                    after that the polling endpoint relies on
#                    session_cache for the durable result instead)
#     'unknown'    — Redis could not be reached


def read_job_status_sync(job_id: str) -> Dict[str, Any]:
    """Read an arq job's status from sync code. Never raises.

    Returns: `{ jobStatus: str, error?: str }`. See the vocabulary above.
    """
    if not str(job_id or "").strip():
        return {"jobStatus": "not_found"}
    try:
        return asyncio.run(_read_job_status_async(str(job_id).strip()))
    except Exception:
        logger.warning("read_job_status_sync failed for %s", job_id, exc_info=True)
        return {"jobStatus": "unknown"}


async def _read_job_status_async(job_id: str) -> Dict[str, Any]:
    pool = await create_pool(get_redis_settings())
    try:
        return await _job_status(pool, job_id)
    finally:
        await pool.close()


async def _job_status(pool: ArqRedis, job_id: str) -> Dict[str, Any]:
    from arq.jobs import Job, JobStatus

    job = Job(job_id, pool)
    status = await job.status()
    if status == JobStatus.not_found:
        return {"jobStatus": "not_found"}
    if status == JobStatus.complete:
        try:
            # timeout=0 returns immediately. Raises whatever the worker
            # raised if the job errored.
            await job.result(timeout=0)
            return {"jobStatus": "completed"}
        except Exception as exc:
            return {"jobStatus": "error", "error": str(exc) or type(exc).__name__}
    if status == JobStatus.in_progress:
        return {"jobStatus": "running"}
    # deferred or queued — both surface as "pending" to the frontend.
    return {"jobStatus": "pending"}


# ── Job kind registry ──────────────────────────────────────────────────────
#
# The four AI flows the worker exposes. Used by the polling endpoints to
# validate the `kind` parameter and look up the matching arq function /
# progress kind / session_cache field.

AI_JOB_KINDS = frozenset({
    "overall_analysis",
    "deep_analysis",
    "smart_recap",
    "content_repurposing",
})
