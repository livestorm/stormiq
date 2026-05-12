"""Redis + arq queue setup.

The worker process consumes jobs from these queues. The web process enqueues
jobs into them. Both processes share the same Redis instance.

CLAUDE.md §5 (processing flow) + §14 (deployment) describe how this fits
into the rest of the app. Phase 1 ships the infrastructure only — no flow
has been migrated to the queue yet, so the `functions` registered in the
worker are currently scaffolding (one no-op + the stuck-job sweeper).
Phase 2 wires real jobs into this same machinery.

Hard rules:
- The worker is a separate OS process. Never call worker functions
  directly from FastAPI handlers — enqueue them via `enqueue_job`.
- Redis is a hard dependency once Phase 1 ships. Health-check at startup
  so misconfiguration fails loudly instead of silently dropping jobs.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional, TypedDict

import redis as redis_sync

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings


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
    """Singleton arq pool for enqueuing jobs from FastAPI handlers.

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
#
# Phase 2 will add concrete TypedDicts for each migrated flow (transcription,
# overall analysis, deep analysis, smart recap, content repurposing). Keeping
# the shape here so handlers always pass a structured dict rather than
# positional args — easier to evolve, easier to log.


class JobMeta(TypedDict, total=False):
    """Fields every job carries for observability + accounting.

    `session_id`  — the session the job operates on (always present once
                    flows migrate; nullable here only because cron jobs
                    like the sweeper don't have one).
    `enqueued_by` — `"web"` for handler-enqueued, `"cron"` for scheduled.
    """

    session_id: str
    enqueued_by: str


# ── Enqueue helper ──────────────────────────────────────────────────────────


async def enqueue_job(function_name: str, *args: Any, **kwargs: Any) -> Optional[str]:
    """Enqueue a job by name. Async — use from worker / async handlers.

    Returns the job id, or None on failure. Thin wrapper around
    `pool.enqueue_job` that swallows connection errors so a Redis hiccup
    never bubbles up into a 500.
    """
    try:
        pool = await get_arq_pool()
        job = await pool.enqueue_job(function_name, *args, **kwargs)
        return job.job_id if job is not None else None
    except Exception:
        logger.exception("Failed to enqueue arq job %s", function_name)
        return None


def enqueue_job_sync(function_name: str, *args: Any, **kwargs: Any) -> Optional[str]:
    """Enqueue a job from sync code (e.g. a sync FastAPI handler).

    FastAPI runs `def` handlers in a threadpool — there's no event loop
    in those threads, so we can't `await` from them. This helper spins up
    a short-lived event loop just to enqueue, then tears it down. The
    overhead is a few ms per call; fine for our enqueue rate.

    Returns the job id, or None on failure.

    Do NOT call this from inside an `async def` handler — use `enqueue_job`
    there, since asyncio.run() inside a running loop raises.
    """
    try:
        return asyncio.run(_enqueue_one_shot(function_name, *args, **kwargs))
    except Exception:
        logger.exception("Failed to enqueue arq job %s (sync)", function_name)
        return None


async def _enqueue_one_shot(function_name: str, *args: Any, **kwargs: Any) -> Optional[str]:
    # Create a fresh pool inside this event loop. The module-level cached
    # pool (if any) belongs to a different loop and can't be reused; per-call
    # connection setup is the price of a one-shot enqueue.
    pool = await create_pool(get_redis_settings())
    try:
        job = await pool.enqueue_job(function_name, *args, **kwargs)
        return job.job_id if job is not None else None
    finally:
        await pool.close()


# ── Job status lookup (sync) ───────────────────────────────────────────────
#
# Sync wrapper around arq's `Job.status()`. Used by the AI-flow polling
# endpoints. The status enum is normalised to the same vocabulary the
# transcription flow already uses, so the frontend handles all four AI
# polls + the transcript poll with one shape:
#
#     'pending'    — enqueued but not yet picked up (arq: deferred|queued)
#     'running'    — worker has picked it up (arq: in_progress)
#     'completed'  — worker finished successfully (arq: complete, no error)
#     'error'      — worker raised; `error` field carries the message
#     'not_found'  — job_id unknown or expired (arq keeps results for 1h
#                    by default; after that, status falls back to not_found
#                    and the polling endpoint should consult session_cache
#                    for the durable result instead)


def read_job_status_sync(job_id: str) -> Dict[str, Any]:
    """Read an arq job's status from sync code. Never raises.

    Returns: `{ jobStatus: str, error?: str }`. See module docstring for
    the normalised status vocabulary.
    """
    if not str(job_id or "").strip():
        return {"jobStatus": "not_found"}
    try:
        return asyncio.run(_read_job_status_async(str(job_id).strip()))
    except Exception:
        logger.warning("read_job_status_sync failed for %s", job_id, exc_info=True)
        return {"jobStatus": "unknown"}


async def _read_job_status_async(job_id: str) -> Dict[str, Any]:
    from arq.jobs import Job, JobStatus

    pool = await create_pool(get_redis_settings())
    try:
        job = Job(job_id, pool)
        status = await job.status()
        if status == JobStatus.not_found:
            return {"jobStatus": "not_found"}
        if status == JobStatus.complete:
            try:
                # timeout=0 returns immediately. Raises ResultNotFound if the
                # job is somehow complete-without-result; raises whatever the
                # worker raised if the job errored.
                await job.result(timeout=0)
                return {"jobStatus": "completed"}
            except Exception as exc:
                return {"jobStatus": "error", "error": str(exc)}
        if status == JobStatus.in_progress:
            return {"jobStatus": "running"}
        # deferred or queued — both surface as "pending" to the frontend.
        return {"jobStatus": "pending"}
    finally:
        await pool.close()


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


# ── In-flight job dedupe ───────────────────────────────────────────────────
#
# Server-side guard against rapid double-clicks on the Generate button.
# Without this, each click enqueues a fresh job (worker logs after the
# Phase 2 deploy showed 3 identical run_overall_analysis_job invocations
# from a single page session — the frontend disable flag races the user).
#
# Key shape: stormiq:ai-job:in-flight:{kind}:{session_id}:{dimension}
# Value:     the arq job_id
# TTL:       1 hour — covers the longest plausible job runtime; arq's
#            default keep_result is also 1 hour, so the two lifetimes are
#            naturally aligned.
#
# Used by _start_ai_job in api_logic.py: before enqueuing, look up an
# existing job id for the same (kind, session, dimension). If found AND
# the arq job is still pending/running, return that job_id instead of
# enqueuing a duplicate. The worker doesn't have to clean the key up
# explicitly — the lookup path verifies arq state and self-heals on
# stale keys.

IN_FLIGHT_TTL_SECONDS = 60 * 60


def _in_flight_key(kind: str, session_id: str, dimension: str) -> str:
    safe_kind = str(kind or "").strip()
    safe_session = str(session_id or "").strip()
    safe_dim = str(dimension or "").strip() or "_"
    return f"stormiq:ai-job:in-flight:{safe_kind}:{safe_session}:{safe_dim}"


_sync_redis_client: Optional[redis_sync.Redis] = None


def _get_sync_redis() -> redis_sync.Redis:
    global _sync_redis_client
    if _sync_redis_client is None:
        _sync_redis_client = redis_sync.Redis.from_url(get_redis_url(), decode_responses=True)
    return _sync_redis_client


def get_in_flight_ai_job_id(kind: str, session_id: str, dimension: str) -> Optional[str]:
    """Look up the in-flight arq job id for this (kind, session, dimension).

    Returns None when there's no marker, the marker has expired, or Redis
    is unreachable. Callers should treat any non-None return as "maybe
    in flight" and verify via `read_job_status_sync` before reusing.
    """
    try:
        value = _get_sync_redis().get(_in_flight_key(kind, session_id, dimension))
        return str(value).strip() if value else None
    except Exception:
        logger.warning(
            "get_in_flight_ai_job_id failed for %s/%s/%s", kind, session_id, dimension, exc_info=True,
        )
        return None


def set_in_flight_ai_job_id(kind: str, session_id: str, dimension: str, job_id: str) -> None:
    """Record an in-flight arq job. Overwrites any previous marker for the
    same key (the new job has effectively superseded it)."""
    try:
        _get_sync_redis().set(
            _in_flight_key(kind, session_id, dimension),
            str(job_id).strip(),
            ex=IN_FLIGHT_TTL_SECONDS,
        )
    except Exception:
        logger.warning(
            "set_in_flight_ai_job_id failed for %s/%s/%s", kind, session_id, dimension, exc_info=True,
        )


def clear_in_flight_ai_job_id(kind: str, session_id: str, dimension: str) -> None:
    """Drop the in-flight marker. Called by callers that detect a stale
    marker (arq says completed/error/not_found). Safe to call when the
    key doesn't exist."""
    try:
        _get_sync_redis().delete(_in_flight_key(kind, session_id, dimension))
    except Exception:
        logger.warning(
            "clear_in_flight_ai_job_id failed for %s/%s/%s", kind, session_id, dimension, exc_info=True,
        )
