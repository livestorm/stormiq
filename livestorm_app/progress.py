"""Redis-backed progress reporting with stage floors.

The worker calls `publish_progress_sync(kind, key, stage)` at each stage;
the web process polls `read_progress(kind, key)` from a FastAPI handler and
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
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Mapping, Optional

import redis as redis_sync

from livestorm_app.queue import get_redis_url


logger = logging.getLogger(__name__)


# ── Stage definitions ──────────────────────────────────────────────────────
#
# Each map: ordered dict { stage_name: floor_percent }. The order matters
# only for documentation — clients read whatever floor is published.
#
# Floor percents are monotonic and end at 100 ("done").


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
# → call the LLM → persist. The card registry (docs/roadmap.md) may add
# intermediate stages later; floors are reserved with gaps so new stages
# can slot in without renumbering.
ANALYSIS_STAGE_FLOORS: Dict[str, int] = {
    "queued": 0,
    "loading_sources": 15,
    "building_prompt": 25,
    "analyzing": 40,              # LLM call — by far the longest stage
    "persisting": 90,
    "done": 100,
}


# The people sync (`worker.run_people_sync`, keyed by session): it may wait
# for another sync of the same org, then lists the people page by page (the
# longest stage on a large session), then the workspace's field labels, then
# saves the roster.
ROSTER_STAGE_FLOORS: Dict[str, int] = {
    "queued": 0,
    "waiting": 5,
    "people": 15,
    "labels": 80,
    "saving": 90,
    "done": 100,
}


# The v1 session analysis (`worker.run_session_analysis_job`, keyed by
# session): label the chat (about a minute), then read the transcript (one
# long model call, a few minutes on a long session), then save. A step with
# nothing new to do is skipped, so a stage can be passed over.
SESSION_ANALYSIS_STAGE_FLOORS: Dict[str, int] = {
    "queued": 0,
    "labelling": 5,
    "reading": 25,
    "saving": 95,
    "done": 100,
}


# Surprise me, read aloud (`worker.run_surprise_voice_job`, keyed by
# session): read the take, plan its scenes (the storyboard, Claude), then
# one OpenAI clip per beat. While it speaks,
# the payload also carries `done` and `total` (clips) and a `percent` that
# moves with them.
SURPRISE_VOICE_STAGE_FLOORS: Dict[str, int] = {
    "queued": 0,
    "reading": 5,
    "storyboard": 7,
    "speaking": 10,
    "done": 100,
}


STAGE_TABLE: Dict[str, Dict[str, int]] = {
    "transcript": TRANSCRIPT_STAGE_FLOORS,
    "surprise_voice": SURPRISE_VOICE_STAGE_FLOORS,
    "roster": ROSTER_STAGE_FLOORS,
    "session_analysis": SESSION_ANALYSIS_STAGE_FLOORS,
    "overall_analysis": ANALYSIS_STAGE_FLOORS,
    "faq": ANALYSIS_STAGE_FLOORS,
    "decisions": ANALYSIS_STAGE_FLOORS,
    "team_digest": ANALYSIS_STAGE_FLOORS,
    "attendee_followup": ANALYSIS_STAGE_FLOORS,
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


# ── Write path (worker side) ───────────────────────────────────────────────
#
# Job handlers run blocking code (Gladia, requests, reportlab) inside
# `asyncio.to_thread`, and that code reports progress from its thread,
# which has no running event loop. So the writers are synchronous. A
# handler's own stage updates on the event loop are single, sub-millisecond
# Redis SETs, which is acceptable there too.
#
# Best-effort: a Redis blip never fails the underlying job. The job's status
# in Postgres is the system of record; this is the UX layer on top.


def publish_progress_sync(
    kind: str,
    key: str,
    stage: str,
    label: Optional[str] = None,
    extra: Optional[Mapping[str, Any]] = None,
) -> None:
    payload = _payload(kind, stage, label, extra)
    try:
        _get_sync_client().set(_key(kind, key), json.dumps(payload), ex=TTL_SECONDS)
    except Exception:
        logger.warning("publish_progress_sync failed for %s:%s/%s", kind, key, stage, exc_info=True)


def clear_progress_sync(kind: str, key: str) -> None:
    try:
        _get_sync_client().delete(_key(kind, key))
    except Exception:
        logger.warning("clear_progress_sync failed for %s:%s", kind, key, exc_info=True)


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
