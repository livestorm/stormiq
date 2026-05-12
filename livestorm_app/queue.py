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

import logging
import os
from typing import Any, Optional, TypedDict

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
    """Enqueue a job by name. Returns the job id, or None on failure.

    Thin wrapper around `pool.enqueue_job` that swallows connection errors
    so a Redis hiccup never bubbles up into a 500. Phase 2 will wrap this
    in per-flow helpers (e.g. `enqueue_overall_analysis(session_id, lang)`)
    so handlers don't pass raw function names around.
    """
    try:
        pool = await get_arq_pool()
        job = await pool.enqueue_job(function_name, *args, **kwargs)
        return job.job_id if job is not None else None
    except Exception:
        logger.exception("Failed to enqueue arq job %s", function_name)
        return None
