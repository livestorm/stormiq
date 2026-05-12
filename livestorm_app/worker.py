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
from livestorm_app.db import database_enabled, ensure_database_schema, get_db_connection
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
# Replaced in Phase 2. Exists today so the worker has at least one
# registered function and can be smoke-tested end-to-end:
#
#     >>> pool = await create_pool(get_redis_settings())
#     >>> job = await pool.enqueue_job('ping')
#     >>> await job.result()
#     'pong'


async def ping(ctx: Dict[str, Any]) -> str:
    job_id = ctx.get("job_id", "n/a")
    logger.info("[ping] job_id=%s", job_id)
    return "pong"


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

    functions = [ping]

    cron_jobs = [
        cron(sweep_stuck_jobs, minute={0, 10, 20, 30, 40, 50}, run_at_startup=False),
    ]

    on_startup = on_startup
    on_shutdown = on_shutdown
