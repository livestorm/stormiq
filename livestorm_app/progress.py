"""Redis-backed progress reporting with stage floors.

The worker calls `publish_progress(kind, key, stage)` at each stage; the
web process polls `read_progress(kind, key)` from a FastAPI handler and
forwards the value to the client. Written to Redis (not Postgres) because
progress updates are high-frequency and disposable — we don't want a DB
write on every stage transition, and we don't need to retain progress
after the job finishes.

Pattern lifted from Crowdlens (`lib/progress.ts`): each job kind defines
its own ordered stages with monotonic floor percentages. The client
smoothly animates toward the next stage's floor over the expected stage
duration. Stages only move forward — never backward — even if a later
stage turns out to be fast.

Key shape: `stormiq:progress:{kind}:{key}` → JSON-encoded payload
TTL:       30 minutes — bounded so a dead worker doesn't leave a stale
           row in Redis forever.

Phase 1 ships the module + stage tables for the flows we plan to migrate
in Phase 2 (transcript, overall analysis, deep analysis, smart recap,
content repurposing). No flow writes to it yet.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Mapping, Optional

import redis as redis_sync
import redis.asyncio as redis_async

from livestorm_app.queue import get_redis_url


logger = logging.getLogger(__name__)


# ── Stage definitions ──────────────────────────────────────────────────────
#
# Each map: ordered dict { stage_name: floor_percent }. The order matters
# only for documentation — clients read whatever floor is published.
#
# Floor percents are monotonic and end at 100 ("done"). Phase 2 will add
# the matching `publish_progress` calls inside the worker job functions.


TRANSCRIPT_STAGE_FLOORS: Dict[str, int] = {
    "queued": 0,
    "fetching_recording": 15,
    "uploading_to_gladia": 30,
    "transcribing": 45,           # the longest stage in practice
    "post_processing": 85,        # diarization labels, sentence merging
    "persisting": 95,
    "done": 100,
}


# Used by overall, deep, smart-recap, and content-repurposing flows. They
# all have the same coarse shape: load cached sources → assemble prompt
# → call OpenAI → persist. Cards-style refactor (Phase 2 roadmap) may
# add intermediate stages later; floors are reserved with gaps so new
# stages can slot in without renumbering.
ANALYSIS_STAGE_FLOORS: Dict[str, int] = {
    "queued": 0,
    "loading_sources": 15,
    "building_prompt": 25,
    "analyzing": 40,              # OpenAI call — by far the longest stage
    "persisting": 90,
    "done": 100,
}


STAGE_TABLE: Dict[str, Dict[str, int]] = {
    "transcript": TRANSCRIPT_STAGE_FLOORS,
    "overall_analysis": ANALYSIS_STAGE_FLOORS,
    "deep_analysis": ANALYSIS_STAGE_FLOORS,
    "smart_recap": ANALYSIS_STAGE_FLOORS,
    "content_repurposing": ANALYSIS_STAGE_FLOORS,
}


TTL_SECONDS = 30 * 60


def _key(kind: str, key: str) -> str:
    return f"stormiq:progress:{kind}:{key}"


def _resolve_floor(kind: str, stage: str) -> int:
    table = STAGE_TABLE.get(kind)
    if not table:
        # Unknown kind — return 0 so we don't crash, but log loudly.
        # Worker is expected to use a registered kind; this guard exists
        # so a typo doesn't take down a job.
        logger.warning("Unknown progress kind %r; defaulting floor to 0", kind)
        return 0
    return int(table.get(stage, 0))


def _payload(kind: str, stage: str, label: Optional[str] = None, extra: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "kind": kind,
        "stage": stage,
        "percent": _resolve_floor(kind, stage),
        "updated_at_ms": int(time.time() * 1000),
    }
    if label:
        body["label"] = label
    if extra:
        body.update({str(k): v for k, v in extra.items()})
    return body


# ── Async write path (worker side) ─────────────────────────────────────────


_async_client: Optional[redis_async.Redis] = None


def _get_async_client() -> redis_async.Redis:
    global _async_client
    if _async_client is None:
        _async_client = redis_async.from_url(get_redis_url(), decode_responses=True)
    return _async_client


async def publish_progress(
    kind: str,
    key: str,
    stage: str,
    label: Optional[str] = None,
    extra: Optional[Mapping[str, Any]] = None,
) -> None:
    """Write a progress snapshot. Worker-side.

    Best-effort: a Redis blip never fails the underlying job. The job's
    eventual status row in Postgres is the system of record; this is the
    UX layer on top.
    """
    payload = _payload(kind, stage, label, extra)
    try:
        client = _get_async_client()
        await client.set(_key(kind, key), json.dumps(payload), ex=TTL_SECONDS)
    except Exception:
        logger.warning("publish_progress failed for %s:%s/%s", kind, key, stage, exc_info=True)


async def clear_progress(kind: str, key: str) -> None:
    """Drop the progress row. Called when the job terminates (success or error)."""
    try:
        client = _get_async_client()
        await client.delete(_key(kind, key))
    except Exception:
        logger.warning("clear_progress failed for %s:%s", kind, key, exc_info=True)


# ── Sync read path (web side) ──────────────────────────────────────────────


_sync_client: Optional[redis_sync.Redis] = None


def _get_sync_client() -> redis_sync.Redis:
    global _sync_client
    if _sync_client is None:
        _sync_client = redis_sync.Redis.from_url(get_redis_url(), decode_responses=True)
    return _sync_client


def read_progress(kind: str, key: str) -> Optional[Dict[str, Any]]:
    """Read the current progress snapshot. Web-side.

    Returns None when no progress row exists (job hasn't started, or
    finished + cleared). Callers should treat None as "not running."
    """
    try:
        raw = _get_sync_client().get(_key(kind, key))
        if not raw:
            return None
        return json.loads(raw)
    except Exception:
        logger.warning("read_progress failed for %s:%s", kind, key, exc_info=True)
        return None
