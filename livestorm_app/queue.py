"""Redis + arq queue setup.

The worker process consumes jobs from these queues. The web process enqueues
jobs into them. Both processes share the same Redis instance.

docs/jobs.md (processing flows) and docs/operations.md (deployment)
describe how this fits into the rest of the app.

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
import concurrent.futures
import dataclasses
import logging
import os
import pickle
import threading
import time
from typing import Any, Awaitable, Callable, Dict, Optional, TypeVar

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from arq.constants import result_key_prefix


logger = logging.getLogger(__name__)

T = TypeVar("T")


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


# ── Job ids ─────────────────────────────────────────────────────────────────
#
# arq refuses to enqueue a job whose id already has a queued job or a
# retained result (`keep_result`, 1 hour by default). Deterministic ids
# turn that into dedupe: two clicks on Generate, or two teammates opening
# the same session, share one job instead of paying for two.


def ai_job_id(kind: str, session_id: str, dimension: str) -> str:
    """One job per (AI flow, session, language or tone)."""
    return f"{kind}:{str(session_id).strip()}:{str(dimension or '').strip() or '_'}"


def surprise_show_job_id(session_id: str) -> str:
    """One show job per session (Surprise me: the take, the script, the
    storyboard and the voice): two clicks on "Make the show" share it."""
    return f"surprise_show:{str(session_id).strip()}"


def transcription_job_id(transcript_job_id: str) -> str:
    """arq id for a `transcript_jobs` row. The row's partial unique index
    (one active job per session) is the dedupe; this id lets the web
    process ask arq whether the job behind a row is still alive."""
    return f"transcription:{str(transcript_job_id).strip()}"


def livestorm_sync_job_id(sync_id: str) -> str:
    """arq id for a `livestorm_sync_jobs` row (the people sync). As for
    transcriptions, the row's partial unique index (one active sync per
    session) is the dedupe; this id lets the web process ask arq whether the
    job behind a row is still alive."""
    return f"livestorm_sync:{str(sync_id).strip()}"


# ── Sync bridge ─────────────────────────────────────────────────────────────
#
# FastAPI runs `def` handlers in a threadpool with no event loop, while arq
# is async. Instead of building a new event loop and a new Redis connection
# for every enqueue or status read, each process keeps one background
# event-loop thread holding one arq pool, and sync callers submit
# coroutines to it. The pool follows REDIS_URL (tests switch databases).

SYNC_CALL_TIMEOUT_SECONDS = 20.0
# When Redis is unreachable, calls fail at once for this long instead of each
# waiting for its own connection attempt: polls must never tie up the web
# server's threads during a Redis outage.
REDIS_DOWN_COOLDOWN_SECONDS = 5.0


class _QueueBridge:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._pool: Optional[ArqRedis] = None
        self._pool_url = ""
        self._pool_lock: Optional[asyncio.Lock] = None
        self._down_url = ""
        self._down_until = 0.0

    def _event_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is None or self._loop.is_closed():
                loop = asyncio.new_event_loop()
                threading.Thread(target=loop.run_forever, name="stormiq-queue", daemon=True).start()
                self._loop = loop
            return self._loop

    async def _current_pool(self) -> ArqRedis:
        if self._pool_lock is None:
            self._pool_lock = asyncio.Lock()
        async with self._pool_lock:
            url = get_redis_url()
            if self._pool is not None and self._pool_url == url:
                return self._pool
            if url == self._down_url and time.monotonic() < self._down_until:
                raise ConnectionError("Redis was unreachable a moment ago; not retrying yet.")
            stale, self._pool = self._pool, None
            if stale is not None:
                await stale.aclose()
            try:
                # One attempt (about 1 s), not arq's default 5 retries: callers
                # wait behind this lock, and the cooldown covers the retries.
                self._pool = await create_pool(dataclasses.replace(get_redis_settings(), conn_retries=0))
            except Exception:
                self._down_url, self._down_until = url, time.monotonic() + REDIS_DOWN_COOLDOWN_SECONDS
                raise
            self._pool_url = url
            return self._pool

    def run(self, operation: Callable[[ArqRedis], Awaitable[T]]) -> T:
        """Run `operation(pool)` on the bridge loop and wait for its result."""

        async def with_pool() -> T:
            return await operation(await self._current_pool())

        future = asyncio.run_coroutine_threadsafe(with_pool(), self._event_loop())
        try:
            return future.result(timeout=SYNC_CALL_TIMEOUT_SECONDS)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise

    def close(self) -> None:
        with self._lock:
            loop, self._loop = self._loop, None
        if loop is None:
            return

        async def shutdown() -> None:
            if self._pool is not None:
                await self._pool.aclose()
            self._pool, self._pool_url = None, ""
            self._pool_lock = None  # bound to this loop; a new loop needs a new lock

        try:
            asyncio.run_coroutine_threadsafe(shutdown(), loop).result(timeout=5)
        except Exception:
            logger.warning("Closing the queue connection failed", exc_info=True)
        loop.call_soon_threadsafe(loop.stop)


_bridge = _QueueBridge()


def close_queue_bridge() -> None:
    """Release the web process's Redis connection (app shutdown)."""
    _bridge.close()


async def enqueue_unique_job(pool: ArqRedis, function_name: str, job_id: str, *args: Any, **kwargs: Any) -> Dict[str, Any]:
    """`enqueue_unique_job_sync`'s work, for async code that has a pool (the
    worker chaining one job after another): `{jobId, jobStatus, enqueued}`."""
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
    async def enqueue(pool: ArqRedis) -> Dict[str, Any]:
        return await enqueue_unique_job(pool, function_name, job_id, *args, **kwargs)

    try:
        return _bridge.run(enqueue)
    except Exception:
        logger.exception("Failed to enqueue arq job %s (%s)", function_name, job_id)
        return None


# ── Stored job results ──────────────────────────────────────────────────────
#
# arq keeps each job's result in Redis for an hour (`keep_result`), and the
# poll reads a failed job's error back from it (`_job_status`). The poll uses
# only the error's message, so that is all the worker stores: a failed job's
# exception becomes a `JobFailed` carrying its message and nothing else.


class JobFailed(RuntimeError):
    """A failed job's error as the worker stores it: its message only."""


def storable_job_error(exc: BaseException) -> JobFailed:
    """The text the poll shows for `exc` (its message, or its class name
    when it has none), as an exception that holds nothing else."""
    try:
        message = str(exc)
    except Exception:
        message = ""
    return JobFailed(message or type(exc).__name__)


def job_result_serializer(data: Any) -> bytes:
    """The worker's arq serializer (`WorkerSettings.job_serializer`).

    It pickles what arq gives it, as arq's default does, except a failed
    job's result: arq hands over the exception object itself (key `r` of
    the result dict), which is replaced with `storable_job_error(...)`.
    Job arguments and successful results are pickled unchanged, and the
    web process reads both with arq's default `pickle.loads`.
    """
    if isinstance(data, dict) and isinstance(data.get("r"), BaseException):
        data = {**data, "r": storable_job_error(data["r"])}
    return pickle.dumps(data)


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
    cleaned = str(job_id or "").strip()
    if not cleaned:
        return {"jobStatus": "not_found"}
    try:
        return _bridge.run(lambda pool: _job_status(pool, cleaned))
    except Exception:
        logger.warning("read_job_status_sync failed for %s", job_id, exc_info=True)
        return {"jobStatus": "unknown"}


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
    "faq",
    "decisions",
    "team_digest",
    "attendee_followup",
    "no_show_followup",
    "deep_analysis",
    "smart_recap",
    "content_repurposing",
})
