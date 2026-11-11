from __future__ import annotations

import json
import logging
import math
import threading
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

import pandas as pd
import requests

from livestorm_app.config import LIVESTORM_MAX_PAGE_SIZE, OUTPUT_LANGUAGE_MAP, SMART_RECAP_TONES, START_PAGE_NUMBER
from livestorm_app.llm_client import get_llm_model
from livestorm_app.db import (
    ALL_ORGS,
    OrgScope,
    create_transcript_job,
    fetch_cached_session,
    fetch_chat_labels,
    fetch_chat_labels_stamp,
    fetch_figure_checks,
    mark_transcript_job_then_analyse,
    fetch_question_replies,
    fetch_roster_status,
    fetch_session_fields,
    fetch_session_roster,
    fetch_surprise_enabled,
    fetch_understanding,
    get_transcript_job_for_session,
    list_output_versions,
    list_workspace_sessions,
    merge_cached_bundle_entry,
    save_figure_check,
    save_located_decision,
    save_question_reply,
    update_transcript_job_status,
    upsert_cached_session,
)
from livestorm_app.people_sync import roster_live_state, start_sync_on_fetch
from livestorm_app.progress import read_progress
from livestorm_app.queue import (
    AI_JOB_KINDS,
    ai_job_id,
    enqueue_unique_job_sync,
    read_job_status_sync,
    transcription_job_id,
)
from livestorm_app.ai.cover_image import generate_cover_image_with_openai
from livestorm_app.ai.usage import CallContext, run_provenance
from livestorm_app.ai.generation import (
    _bundle_has_all_sections,
    analyze_with_openai,
    generate_content_repurpose_bundle_with_openai,
    is_placeholder_bundle,
    missing_bundle_sections,
    translate_content_repurpose_bundle_with_openai,
    translate_markdown_with_openai,
)
from livestorm_app.ai.llm_inputs import (
    build_compact_transcript_payload_for_llm,
    build_deep_analysis_chat_payload_for_llm,
    build_deep_analysis_questions_payload_for_llm,
    build_derived_stats,
)
from livestorm_app.ai.prompts import (
    build_deep_analysis_prompt,
    build_cover_image_prompt,
    build_smart_recap_prompt,
)
from livestorm_app.analytics.audience import (
    build_chat_df_from_payload,
    build_questions_df_from_payload,
)
from livestorm_app.analytics.cross_source import build_cross_source_insights
from livestorm_app.analytics.transcript import (
    build_transcript_display_text,
    build_transcript_insights,
    build_transcript_plain_text,
)
from livestorm_app.livestorm.client import (
    build_request_exception_debug_details,
    build_http_error_debug_details,
    fetch_chat_and_questions_bundle,
    fetch_event_details,
    fetch_event_sessions_page,
    fetch_workspace_events_page,
    fetch_session_details,
    format_generic_http_error,
    format_livestorm_http_error,
)
from livestorm_app.livestorm.payloads import (
    build_event_session_options,
    build_workspace_event_options,
    extract_record_count,
)
from livestorm_app.pdf import analysis_markdown_to_pdf_bytes
from livestorm_app.analytics.session_overview import (
    build_compact_session_payload_for_llm,
    build_session_overview_data,
    extract_session_attributes,
    session_is_internal,
)
logger = logging.getLogger(__name__)


class SessionNotFoundError(RuntimeError):
    """The session is not cached, or not visible in the caller's org scope.

    Both cases deliberately look the same to the caller (HTTP 404), so a
    session id from another org reveals nothing about whether it exists.
    """

    def __init__(self, message: str = "No cached session was found. Fetch the session data first.") -> None:
        super().__init__(message)


def _sanitize_json_value(value: Any) -> Any:
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, dict):
        return {key: _sanitize_json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_json_value(item) for item in value]
    return value


def _normalize_text_bundle(raw_bundle: Any, fallback_text: str = "", fallback_language: str = "English") -> Dict[str, str]:
    bundle: Dict[str, str] = {}
    if isinstance(raw_bundle, dict):
        for language, markdown in raw_bundle.items():
            language_key = str(language or "").strip()
            markdown_text = str(markdown or "").strip()
            if language_key and markdown_text:
                bundle[language_key] = markdown_text
    fallback_markdown = str(fallback_text or "").strip()
    if fallback_markdown and fallback_language not in bundle:
        bundle[fallback_language] = fallback_markdown
    return bundle


def _normalize_smart_recap_bundle(raw_bundle: Any) -> Dict[str, str]:
    if not isinstance(raw_bundle, dict):
        return {}
    allowed_tones = {"professional", "hype", "surprise"}
    return {
        str(tone): str(markdown).strip()
        for tone, markdown in raw_bundle.items()
        if str(tone) in allowed_tones and isinstance(markdown, str) and str(markdown).strip()
    }


def _get_alternate_language_text(bundle: Any, target_language: str) -> Tuple[str, str]:
    normalized_bundle = _normalize_text_bundle(bundle)
    for language, markdown in normalized_bundle.items():
        if str(language) != str(target_language) and markdown.strip():
            return str(language), markdown
    return "", ""


def _stored_content_bundles(cached: Dict[str, Any]) -> Dict[str, Any]:
    """The stored content-repurposing bundles by language, without the
    empty-transcript placeholder bundles earlier versions saved: those are
    treated as missing, so they can be generated again."""
    raw = cached.get("content_repurpose_bundle")
    if not isinstance(raw, dict):
        return {}
    return {language: bundle for language, bundle in raw.items() if not is_placeholder_bundle(bundle)}


def _get_alternate_language_bundle(bundle_by_language: Any, target_language: str) -> Tuple[str, Dict[str, str]]:
    if not isinstance(bundle_by_language, dict):
        return "", {}
    for language, bundle in bundle_by_language.items():
        if str(language) == str(target_language):
            continue
        normalized_bundle = {
            key: str(value or "").strip()
            for key, value in (bundle or {}).items()
            if key in {"summary", "blog", "email", "social_media"} and str(value or "").strip()
        }
        if normalized_bundle:
            return str(language), normalized_bundle
    return "", {}


def _df_records(df: Optional[pd.DataFrame], limit: Optional[int] = None) -> List[Dict[str, Any]]:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return []
    safe_df = df.copy()
    safe_df = safe_df.where(pd.notna(safe_df), None)
    if limit is not None:
        safe_df = safe_df.head(limit)
    return safe_df.to_dict("records")


# ── Derived-insights cache ──────────────────────────────────────────────────
#
# `build_transcript_insights` is the expensive part of a workspace read (it
# builds ~27 DataFrames). It depends only on the stored transcript, and
# every write to a session_cache row bumps `updated_at`, so
# (session_id, updated_at) identifies its input exactly. A few recent
# sessions are kept per process; the frames are never modified by readers
# (`_df_records` and the cross-source build copy them).

TRANSCRIPT_INSIGHTS_CACHE_SIZE = 6
_insights_cache: "OrderedDict[Tuple[str, Any], Dict[str, Any]]" = OrderedDict()
_insights_cache_lock = threading.Lock()


def _cached_transcript_insights(session_id: str, cached_session: Dict[str, Any]) -> Dict[str, Any]:
    transcript_payload = cached_session.get("transcript_payload")
    if not isinstance(transcript_payload, dict):
        return {}
    updated_at = cached_session.get("updated_at")
    key = (str(session_id), updated_at) if updated_at is not None else None
    if key is not None:
        with _insights_cache_lock:
            hit = _insights_cache.get(key)
            if hit is not None:
                _insights_cache.move_to_end(key)
                return hit
    insights = build_transcript_insights(transcript_payload)
    if key is not None:
        with _insights_cache_lock:
            for stale in [k for k in _insights_cache if k[0] == key[0] and k != key]:
                del _insights_cache[stale]  # an older version of this session
            _insights_cache[key] = insights
            while len(_insights_cache) > TRANSCRIPT_INSIGHTS_CACHE_SIZE:
                _insights_cache.popitem(last=False)
    return insights


def _transcript_summary(transcript_payload: Any) -> Optional[Dict[str, Any]]:
    """What the frontend needs from the raw transcript: its subtitle files.

    None when there is no transcript (the frontend checks presence). The
    full raw payload is served by `get_transcript_download` instead.
    """
    if not isinstance(transcript_payload, dict):
        return None
    subtitles = transcript_payload.get("subtitles")
    return {
        "subtitles": [item for item in subtitles if isinstance(item, dict)] if isinstance(subtitles, list) else [],
        "language": transcript_payload.get("language"),
        "durationSeconds": transcript_payload.get("duration_seconds"),
    }


def get_transcript_download(session_id: str, *, organization_id: OrgScope) -> Dict[str, Any]:
    """The full stored transcript payload, for the "Download JSON" button."""
    row = fetch_session_fields(session_id, ["transcript_payload"], organization_id=organization_id)
    if not isinstance(row, dict):
        raise SessionNotFoundError()
    if not isinstance(row.get("transcript_payload"), dict):
        raise SessionNotFoundError("This session has no transcript yet.")
    return row["transcript_payload"]


# The columns `_serialize_outputs` reads (all AI bundles, plus the legacy
# English-only markdown columns).
_OUTPUT_FIELDS = [
    "analysis_bundle", "analysis_md", "deep_analysis_bundle", "deep_analysis_md",
    "content_repurpose_bundle", "smart_recap_bundle", "faq_bundle", "decisions_bundle",
    "team_digest_bundle",
]


def _serialize_outputs(cached_session: Dict[str, Any]) -> Dict[str, Any]:
    """The `outputs` part of a workspace: every AI result of the session."""
    return {
        "analysisBundle": _normalize_text_bundle(cached_session.get("analysis_bundle"), str(cached_session.get("analysis_md") or "")),
        "deepAnalysisBundle": _normalize_text_bundle(cached_session.get("deep_analysis_bundle"), str(cached_session.get("deep_analysis_md") or "")),
        "contentRepurposeBundle": _stored_content_bundles(cached_session),
        "smartRecapBundle": _normalize_smart_recap_bundle(cached_session.get("smart_recap_bundle")),
        "faqBundle": _normalize_text_bundle(cached_session.get("faq_bundle"), ""),
        "decisionsBundle": _normalize_text_bundle(cached_session.get("decisions_bundle"), ""),
        "teamDigestBundle": _normalize_text_bundle(cached_session.get("team_digest_bundle"), ""),
    }


def _serialize_cached_session(session_id: str, cached_session: Dict[str, Any]) -> Dict[str, Any]:
    session_payload = cached_session.get("session_payload")
    chat_payload = cached_session.get("chat_payload")
    questions_payload = cached_session.get("questions_payload")
    transcript_payload = cached_session.get("transcript_payload")
    speaker_names = cached_session.get("transcript_speaker_names") if isinstance(cached_session.get("transcript_speaker_names"), dict) else {}

    chat_df = build_chat_df_from_payload(chat_payload) if isinstance(chat_payload, dict) else pd.DataFrame()
    questions_df = build_questions_df_from_payload(questions_payload) if isinstance(questions_payload, dict) else pd.DataFrame()
    transcript_text = build_transcript_display_text(transcript_payload) if isinstance(transcript_payload, dict) else ""
    transcript_insights = _cached_transcript_insights(session_id, cached_session)
    session_overview = (
        # Chat Messages counts the cached chat; only without one does it
        # fall back to the people's `messages_count`.
        build_session_overview_data(session_payload, chat_df=chat_df if isinstance(chat_payload, dict) else None)
        if isinstance(session_payload, dict)
        else {}
    )
    # Internal session (the team is most of the room): nothing is left out
    # of the audience. Decided once, from the people, for every chart.
    internal_session = bool(session_overview.get("internal_session", False))
    derived_stats = build_derived_stats(
        chat_df=chat_df if not chat_df.empty else None,
        questions_df=questions_df if not questions_df.empty else None,
        transcript_payload=transcript_payload if isinstance(transcript_payload, dict) else None,
        session_payload=session_payload if isinstance(session_payload, dict) else None,
        internal=internal_session,
    )
    cross_source = build_cross_source_insights(
        chat_df=chat_df if not chat_df.empty else None,
        questions_df=questions_df if not questions_df.empty else None,
        transcript_payload=transcript_payload if isinstance(transcript_payload, dict) else None,
        transcript_insights=transcript_insights,
        internal=internal_session,
    )

    # ── Hero metadata ──────────────────────────────────────────────────
    # Surface the same card-level info the workspace grid uses so the
    # session-detail header can render a hero matching the card the
    # user clicked. Cheap to compute (it's already in the row) and
    # avoids the frontend re-deriving event titles and durations from
    # raw session_payload attributes.
    session_attributes = (
        extract_session_attributes(cached_session.get("session_payload"))
        if isinstance(cached_session.get("session_payload"), dict)
        else {}
    )
    started_at_unix_meta = (
        session_attributes.get("started_at") or session_attributes.get("estimated_started_at")
    )
    duration_seconds_meta = session_attributes.get("duration")
    event_title_meta = _extract_event_title(
        cached_session.get("event_payload"),
        fallback=str(session_attributes.get("name") or "").strip() or "Untitled event",
    )
    created_at_meta = cached_session.get("created_at")
    cover_generated_at_meta = cached_session.get("cover_image_generated_at")
    meta = {
        "eventTitle": event_title_meta,
        "sessionName": str(session_attributes.get("name") or "").strip(),
        "startedAtUnix": started_at_unix_meta,
        "startedAtLabel": _format_started_at_label(started_at_unix_meta),
        "durationSeconds": duration_seconds_meta,
        "durationLabel": _format_duration_label(duration_seconds_meta),
        "attendeesCount": session_attributes.get("attendees_count"),
        "registrantsCount": session_attributes.get("registrants_count"),
        # The SPA counts everyone as audience when this is true
        # (`audience.is_internal_session`); it doesn't decide it again.
        "internalSession": internal_session,
        "generatedByUserId": str(cached_session.get("created_by_user_id") or "").strip(),
        "generatedByEmail": str(cached_session.get("created_by_email") or "").strip(),
        "generatedByName": str(cached_session.get("created_by_name") or "").strip(),
        "generatedAtIso": created_at_meta.isoformat() if created_at_meta else "",
        "generatedAtLabel": _format_iso_date_label(created_at_meta),
        # `has_cover_image` is the IS NOT NULL alias from
        # fetch_cached_session's SELECT — the actual BYTEA stays out
        # of every cache read because it's 1-2 MB and served by its
        # own /cover.png endpoint.
        "hasCoverImage": bool(cached_session.get("has_cover_image")),
        "coverImageMime": str(cached_session.get("cover_image_mime") or "image/png"),
        "coverImageGeneratedAt": (
            cover_generated_at_meta.isoformat() if cover_generated_at_meta else ""
        ),
    }

    serialized = {
        "sessionId": session_id,
        "updatedAt": cached_session.get("updated_at"),
        "speakerNames": speaker_names,
        "meta": meta,
        # Raw upstream payloads are not sent: the frontend renders the derived
        # tables. Only the transcript's subtitles are used (and its presence);
        # the full raw transcript is a separate download, transcript.json.
        "payloads": {
            "transcript": _transcript_summary(transcript_payload),
        },
        "tables": {
            "chat": _df_records(chat_df),
            "questions": _df_records(questions_df),
            "overview": _df_records(session_overview.get("overview_df")),
            "people": _df_records(session_overview.get("people_df")),
            "country": _df_records(session_overview.get("country_df")),
            "role": _df_records(session_overview.get("role_df")),
            "attendanceDistribution": _df_records(session_overview.get("attendance_distribution_df")),
            "engagementTop": _df_records(session_overview.get("engagement_top_df")),
            "transcriptSegments": _df_records(transcript_insights.get("segments_df")),
            "transcriptNumbers": _df_records(transcript_insights.get("numbers_df")),
            "transcriptSpeakers": _df_records(transcript_insights.get("speaker_df")),
            "transcriptSpeakerTurns": _df_records(transcript_insights.get("speaker_turns_df")),
            "transcriptSilence": _df_records(transcript_insights.get("silence_df")),
            "transcriptPauseTypes": _df_records(transcript_insights.get("pause_type_df")),
            "transcriptPauseDistribution": _df_records(transcript_insights.get("pause_distribution_df")),
            "transcriptUtteranceDistribution": _df_records(transcript_insights.get("utterance_duration_distribution_df")),
            "transcriptTimeline": _df_records(transcript_insights.get("timeline_df")),
            "transcriptBursts": _df_records(transcript_insights.get("burst_df")),
            "transcriptBurstDistribution": _df_records(transcript_insights.get("burst_distribution_df")),
            "chatQuestionsTimeline": _df_records(cross_source.get("combined_timeline_df")),
            "chatQuestionReactionMoments": _df_records(cross_source.get("reaction_moments_df")),
        },
        "stats": {
            "sessionOverview": session_overview.get("stats", {}),
            "transcriptSummary": transcript_insights.get("summary", {}),
            "chat": derived_stats.get("chat", {}),
            "questions": derived_stats.get("questions", {}),
        },
        "text": {
            "transcriptDisplay": transcript_text,
        },
        "outputs": _serialize_outputs(cached_session),
    }
    return _sanitize_json_value(serialized)


def get_cached_workspace(session_id: str, *, organization_id: OrgScope) -> Optional[Dict[str, Any]]:
    cached = fetch_cached_session("", session_id, organization_id=organization_id)
    if not isinstance(cached, dict):
        return None
    # Reads never start paid work. Cover images are generated only after a
    # Professional Smart Recap (worker) or by scripts/backfill_cover_images.py.
    return _serialize_cached_session(session_id, cached)


# ── The v1 record ───────────────────────────────────────────────────────────
#
# `GET /api/sessions/{id}/record` serves the v1 Report. `livestorm_app/record/`
# builds it; the classic serializer above never runs v1 code, so a bug there
# can't break a classic read.
#
# The computed block is cached per process, keyed by everything it reads:
# the row's `updated_at` (every write to the row bumps it, the transcript's
# and the speaker labels' included), the record's, the measures', the
# transcript health's and the alignment probe's versions (`RECORD_VERSION`,
# `METRICS_VERSION`, `HEALTH_VERSION`, `ALIGNMENT_VERSION`), and what
# changes without a new row version (`_record_input_versions`): the stored
# roster's version. Live state that changes without any of these (the
# people sync's status, whether the roster is due a refresh) is read outside
# the cache, on every request, and added to a copy of the block's roster
# header (`people_sync.roster_live_state`).
#
# The people come from the session's stored roster when a people sync saved
# one (`db.fetch_session_roster`, the header and the rows of one version),
# and from the session payload's `include=people` list until then.
#
# The transcript is the largest column: it is read only on a miss, in the
# same read as the base payloads (a session without one reads a NULL), and
# only its health and the alignment (ids and times) stay in the cached block.

RECORD_FIELDS = (
    "session_payload", "event_payload", "chat_payload", "questions_payload",
    "transcript_payload", "transcript_speaker_names", "updated_at",
)
RECORD_CACHE_SIZE = 32
_record_cache: "OrderedDict[Tuple[Any, ...], Dict[str, Any]]" = OrderedDict()
_record_cache_lock = threading.Lock()


def _record_input_versions(
    roster: Optional[Mapping[str, Any]], labels: Optional[Mapping[str, Any]] = None
) -> Tuple[Any, Any, Any]:
    """(roster version, labels stamp, metrics fingerprint): what the record
    reads that changes without bumping `session_cache.updated_at`.

    `roster` is the stored roster's header (`db.fetch_roster_status`), or
    None while the people come from the session payload: its
    `roster_version` grows with every people sync that saves a roster.
    `labels` is the stored chat labels' stamp (`db.fetch_chat_labels_stamp`):
    a new analysis run writes a new `created_at`. The fingerprint is None
    for now, because no measure is stored; stored measures add theirs here."""
    label_stamp = (
        (labels.get("label_version"), labels.get("chat_hash"), str(labels.get("created_at"))) if labels else None
    )
    return (roster.get("roster_version") if roster else None), label_stamp, None


def _record_cache_key(
    session_id: str, updated_at: Any, code_versions: Tuple[int, ...], input_versions: Tuple[Any, ...]
) -> Optional[Tuple[Any, ...]]:
    if updated_at is None:
        return None
    return (str(session_id), updated_at, *code_versions, *input_versions)


def _cached_record_block(key: Optional[Tuple[Any, ...]]) -> Optional[Dict[str, Any]]:
    if key is None:
        return None
    with _record_cache_lock:
        hit = _record_cache.get(key)
        if hit is not None:
            _record_cache.move_to_end(key)
        return hit


def _store_record_block(key: Optional[Tuple[Any, ...]], block: Dict[str, Any]) -> None:
    if key is None:
        return
    with _record_cache_lock:
        for stale in [k for k in _record_cache if k[0] == key[0] and k != key]:
            del _record_cache[stale]  # an older version of this session
        _record_cache[key] = block
        while len(_record_cache) > RECORD_CACHE_SIZE:
            _record_cache.popitem(last=False)


def get_session_record(session_id: str, *, organization_id: OrgScope) -> Dict[str, Any]:
    """The v1 Report's record of one session: `{sessionId, updatedAt, meta:
    {clock}, record}`.

    The scope check comes first: a row that is missing or outside the
    caller's scope raises `SessionNotFoundError` (404) before anything else
    is read. Every request then reads the stored roster's header and the
    latest people sync, with the same scope (`db.fetch_roster_status`, one
    small query): the header's version is part of the cache key, and the
    sync's state is added to the response's roster header, never cached
    (`people_sync.roster_live_state`). The stored chat labels' stamp is read
    too (`db.fetch_chat_labels_stamp`) and keys the block; a miss reads the
    labels whole. A cache hit costs those small
    reads.

    A miss reads the base payloads, the transcript and its speaker labels
    (never the AI outputs), and the stored roster when there is one
    (`db.fetch_session_roster`: its header and its rows, of one version),
    and builds the block, transcript health and measures included, with
    `record.assemble.assemble_record`. Without a stored roster the people
    come from the session payload. A roster that can't be read raises: the
    record is never built from a people list that only looks empty.

    The block is shared between requests: never modify it. The response's
    `record` and `record.roster` are copies.
    """
    stamp = fetch_session_fields(session_id, ["updated_at"], organization_id=organization_id)
    if not isinstance(stamp, dict):
        raise SessionNotFoundError()
    # The record package is imported here, in the only function that runs
    # it, so it loads with the first record request: an error in it can't
    # stop the app from starting or reach a classic read. Nothing else
    # outside `record/` imports it (tests/test_roster.py).
    from livestorm_app.record.alignment import ALIGNMENT_VERSION
    from livestorm_app.record.assemble import RECORD_VERSION, assemble_record
    from livestorm_app.record.inputs import load_record_inputs
    from livestorm_app.record.measures import METRICS_VERSION
    from livestorm_app.record.roster import stored_roster_rows
    from livestorm_app.record.transcript_health import HEALTH_VERSION

    roster_status = fetch_roster_status(session_id, organization_id) or {}
    roster_header = roster_status.get("roster")
    label_stamp = fetch_chat_labels_stamp(session_id, organization_id)
    code_versions = (RECORD_VERSION, METRICS_VERSION, HEALTH_VERSION, ALIGNMENT_VERSION)
    block = _cached_record_block(
        _record_cache_key(
            session_id, stamp.get("updated_at"), code_versions, _record_input_versions(roster_header, label_stamp)
        )
    )
    if block is None:
        row = fetch_session_fields(session_id, list(RECORD_FIELDS), organization_id=organization_id)
        if not isinstance(row, dict):
            raise SessionNotFoundError()
        stamp = row
        # Read again with its rows: a sync may have replaced the roster
        # since the header above, and the block is keyed by what it holds.
        stored = fetch_session_roster(session_id, organization_id) if roster_header is not None else None
        roster_header = stored["roster"] if stored is not None else None
        # Read the labels whole, and key the block by what was read.
        labels = fetch_chat_labels(session_id, organization_id) if label_stamp is not None else None
        label_stamp = labels
        inputs = load_record_inputs(
            {**row, "session_id": session_id},
            roster_rows=stored_roster_rows(stored) if stored is not None else None,
            stored_labels=labels,
        )
        block = _sanitize_json_value(assemble_record(inputs))
        _store_record_block(
            _record_cache_key(
                session_id, row.get("updated_at"), code_versions, _record_input_versions(roster_header, label_stamp)
            ),
            block,
        )
    live = roster_live_state(roster_header, roster_status.get("sync"))
    record = {**block["record"], "roster": {**block["record"]["roster"], **live}}
    return {"sessionId": session_id, "updatedAt": stamp.get("updated_at"), "meta": block["meta"], "record": record}


def _transcript_inputs(session_id: str, *, organization_id: OrgScope) -> Tuple[Any, Dict[str, Any]]:
    """The record inputs the Transcript tab and the chapters read, with the
    caller's scope: the session's payloads, its stored roster (one version)
    and its stored chat labels (the understanding's dossier lists the chat's
    questions). Raises `SessionNotFoundError` for a row that is missing or
    outside the scope."""
    row = fetch_session_fields(session_id, list(RECORD_FIELDS), organization_id=organization_id)
    if not isinstance(row, dict):
        raise SessionNotFoundError()
    from livestorm_app.record.inputs import load_record_inputs
    from livestorm_app.record.roster import stored_roster_rows

    roster_status = fetch_roster_status(session_id, organization_id) or {}
    stored = fetch_session_roster(session_id, organization_id) if roster_status.get("roster") is not None else None
    inputs = load_record_inputs(
        {**row, "session_id": session_id},
        roster_rows=stored_roster_rows(stored) if stored is not None else None,
        stored_labels=fetch_chat_labels(session_id, organization_id),
    )
    return inputs, row


def _chapter_list(session_id: str, inputs: Any, *, organization_id: OrgScope) -> Tuple[List[Dict[str, Any]], str]:
    """The stored understanding's chapters and their state
    (`record.understanding.chapter_list`: used only while read from this
    session as it is now)."""
    from livestorm_app.record.understanding import chapter_list

    return chapter_list(fetch_understanding(session_id, organization_id), inputs)


def get_session_chapters(session_id: str, *, organization_id: OrgScope) -> Dict[str, Any]:
    """The session's chapters and their show notes, for the Follow-up tab
    and the Report's cover: `{sessionId, updatedAt, state, placed, chapters,
    showNotes: {text, youtube}}` (`record.understanding.chapter_list`,
    `show_notes`). When `placed`, each chapter also carries its place on the
    session clock and what the audience did in it (`record.assemble.
    chapter_marks`: `t`, `tEnd`, `audienceChat`, `questions`,
    `chatPerMinute`). No model call: it reads what the session analysis
    stored. Read on demand with the caller's scope."""
    inputs, row = _transcript_inputs(session_id, organization_id=organization_id)
    from livestorm_app.record.assemble import chapter_marks
    from livestorm_app.record.understanding import show_notes

    chapters, state = _chapter_list(session_id, inputs, organization_id=organization_id)
    marks = chapter_marks(inputs, chapters)
    placed = [{**chapter, **mark} for chapter, mark in zip(chapters, marks["marks"], strict=True)] if marks["placed"] else chapters
    body = {"state": state, "placed": marks["placed"], "chapters": placed, "showNotes": show_notes(chapters)}
    return {"sessionId": session_id, "updatedAt": row.get("updated_at"), **_sanitize_json_value(body)}


def get_session_findings(session_id: str, *, organization_id: OrgScope) -> Dict[str, Any]:
    """The Report's findings: `{sessionId, updatedAt, state, version, found,
    nothing, notChecked}` (`record.findings.findings`), from the session's
    payloads, its chat labels, the question desk's statuses and the stored
    understanding (`state`: its
    reading's, `ready`, `stale` or `none`). No model call; read on demand
    with the caller's scope and not cached."""
    inputs, row = _transcript_inputs(session_id, organization_id=organization_id)
    body, _ = _findings_and_desk(session_id, inputs, organization_id=organization_id)
    return {"sessionId": session_id, "updatedAt": row.get("updated_at"), **_sanitize_json_value(body)}


def _findings_and_desk(session_id: str, inputs: Any, *, organization_id: OrgScope) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """The findings' body (`{state, version, found, nothing, notChecked}`)
    and the question desk's summary, from one set of record inputs. The
    desk's statuses feed the findings (#5 compares what was asked with what
    was answered) only while the reading is current."""
    from livestorm_app.record.findings import findings
    from livestorm_app.record.question_desk import question_desk
    from livestorm_app.record.understanding import chapter_list, current_reading, located_answers

    stored = fetch_understanding(session_id, organization_id)
    result, state, _ = current_reading(stored, inputs)
    chapters, _ = chapter_list(stored, inputs)
    located, located_state = located_answers(stored, inputs)
    desk = question_desk(inputs, fetch_question_replies(session_id, organization_id), located, located_state)
    statuses = {str(q["id"]): str(q["status"]) for q in desk["questions"]} if state == "ready" else None
    return {"state": state, **findings(inputs, chapters, result, statuses)}, desk["summary"]


def get_session_library_row(session_id: str, *, organization_id: OrgScope) -> Dict[str, Any]:
    """One session as a row of the Sessions library: `{sessionId, updatedAt,
    version, sparkline, peak, attendees, wrote, questions, stillOpen, lead,
    analysed}` (`record.library.library_row`), from the record (cached, as
    the Report reads it), the findings and the question desk. No model call;
    read on demand with the caller's scope, one row at a time as the list
    scrolls. A row that is missing or outside the scope raises
    `SessionNotFoundError`."""
    record = get_session_record(session_id, organization_id=organization_id)
    inputs, row = _transcript_inputs(session_id, organization_id=organization_id)
    from livestorm_app.record.library import library_row

    found, desk_summary = _findings_and_desk(session_id, inputs, organization_id=organization_id)
    body = library_row(record.get("record") or {}, found, desk_summary)
    return {"sessionId": session_id, "updatedAt": row.get("updated_at"), **_sanitize_json_value(body)}


def get_session_moments(session_id: str, *, organization_id: OrgScope) -> Dict[str, Any]:
    """The Pulse's key moments, told: `{sessionId, updatedAt, version, state,
    usualPerMinute, surges, speech, moments}` (`record.moments.room_moments`):
    when the room reacted, what was being said, what people wrote and the
    questions asked then, from the session's payloads, its chat labels, the
    stored understanding's prompts (only while read from the session as it
    is now) and the speakers' saved names. Authors as short names that fail
    closed. No model call; read on demand with the caller's scope and not
    cached. A row that is missing or outside the scope raises
    `SessionNotFoundError`."""
    inputs, row = _transcript_inputs(session_id, organization_id=organization_id)
    from livestorm_app.record.moments import room_moments
    from livestorm_app.record.understanding import current_reading

    result, _, _ = current_reading(fetch_understanding(session_id, organization_id), inputs)
    names = row.get("transcript_speaker_names") if isinstance(row.get("transcript_speaker_names"), dict) else {}
    body = _sanitize_json_value(room_moments(inputs, result or {}, names))
    return {"sessionId": session_id, "updatedAt": row.get("updated_at"), **body}


def get_session_voices(session_id: str, *, organization_id: OrgScope) -> Dict[str, Any]:
    """The Report's "Who spoke up": `{sessionId, updatedAt, version,
    internal, total, people, others}` (`record.voices.chat_voices`): the
    audience members who wrote, the most active first, with short names only
    through `roster.display_people` and one message each. Read on demand with
    the caller's scope; nothing is cached. A row that is missing or outside
    the scope raises `SessionNotFoundError`."""
    inputs, row = _transcript_inputs(session_id, organization_id=organization_id)
    from livestorm_app.record.voices import chat_voices

    body = _sanitize_json_value(chat_voices(inputs))
    return {"sessionId": session_id, "updatedAt": row.get("updated_at"), **body}


def get_session_transcript_view(session_id: str, *, organization_id: OrgScope) -> Dict[str, Any]:
    """The Transcript tab's room view: `{sessionId, updatedAt, version,
    clock, speakers, speech, room, chapters}` (`record.transcript_view.transcript_view`).
    Speech, the chat and the questions as text by id, the speech on the
    session clock only when the alignment allows it to the second, and
    authors' short names only through `roster.display_people`. Read on
    demand, once per open tab, with the caller's scope: the session's
    payloads and, when there is one, its stored roster (read with its rows,
    of one version), and the chapters the session analysis stored. Nothing
    is cached. A row that is missing or outside the scope raises
    `SessionNotFoundError`."""
    inputs, row = _transcript_inputs(session_id, organization_id=organization_id)
    from livestorm_app.record.transcript_view import transcript_view

    chapters, chapters_state = _chapter_list(session_id, inputs, organization_id=organization_id)
    names = row.get("transcript_speaker_names") if isinstance(row.get("transcript_speaker_names"), dict) else {}
    body = _sanitize_json_value(transcript_view(inputs, names, chapters, chapters_state))
    return {"sessionId": session_id, "updatedAt": row.get("updated_at"), **body}


class QuestionNotFoundError(RuntimeError):
    """The question desk has no row with this id (a team message, a chat
    message not labelled a content question, an unknown id)."""


class QuestionNotSavableError(RuntimeError):
    """The question has no Livestorm id, so what the host marks can't be
    kept across a fetch."""


class NothingLocatedError(RuntimeError):
    """The question shows no located answer to confirm or set aside (none
    was found, it is stale, or the host already decided)."""


def _question_desk_inputs(session_id: str, *, organization_id: OrgScope) -> Tuple[Any, Dict[str, Any]]:
    """The record inputs the question desk reads, with the caller's scope:
    the session's payloads, its stored roster (one version) and its stored
    chat labels. Raises `SessionNotFoundError` for a row that is missing or
    outside the scope."""
    row = fetch_session_fields(session_id, list(RECORD_FIELDS), organization_id=organization_id)
    if not isinstance(row, dict):
        raise SessionNotFoundError()
    from livestorm_app.record.inputs import load_record_inputs
    from livestorm_app.record.roster import stored_roster_rows

    roster_status = fetch_roster_status(session_id, organization_id) or {}
    stored = fetch_session_roster(session_id, organization_id) if roster_status.get("roster") is not None else None
    labels = fetch_chat_labels(session_id, organization_id)
    inputs = load_record_inputs(
        {**row, "session_id": session_id},
        roster_rows=stored_roster_rows(stored) if stored is not None else None,
        stored_labels=labels,
    )
    return inputs, row


def _located_answers(session_id: str, inputs: Any, *, organization_id: OrgScope) -> Tuple[Dict[str, Any], str]:
    """The stored understanding's located answers for the desk, by question
    id, and their state (`record.understanding.located_answers`: used only
    while read from this session as it is now)."""
    from livestorm_app.record.understanding import located_answers

    return located_answers(fetch_understanding(session_id, organization_id), inputs)


def get_session_question_desk(session_id: str, *, organization_id: OrgScope) -> Dict[str, Any]:
    """The Follow-up tab's question desk: `{sessionId, updatedAt, version,
    summary, questions}` (`record.question_desk.question_desk`): the
    audience's Q&A questions and the chat's content questions, each with its
    status, what the host saved and, for a question the understanding job
    found answered in the talk, that passage; authors as short names that
    fail closed. Read on demand with the caller's scope; nothing is cached."""
    inputs, row = _question_desk_inputs(session_id, organization_id=organization_id)
    from livestorm_app.record.question_desk import question_desk

    saved = fetch_question_replies(session_id, organization_id)
    located, located_state = _located_answers(session_id, inputs, organization_id=organization_id)
    body = _sanitize_json_value(question_desk(inputs, saved, located, located_state))
    return {"sessionId": session_id, "updatedAt": row.get("updated_at"), **body}


def decide_session_located_answer(
    session_id: str,
    question_id: str,
    *,
    confirm: bool,
    organization_id: OrgScope,
    updated_by: Optional[str],
) -> Dict[str, Any]:
    """The host's decision on a located answer, then the desk as
    `get_session_question_desk` returns it. Confirm (the question must be
    `covered`) saves it as answered here, noting it was located; "Not this"
    (the question must show a passage) keeps it as it was and never suggests
    it again. `QuestionNotFoundError`, `QuestionNotSavableError`,
    `NothingLocatedError` or `SessionNotFoundError` otherwise."""
    inputs, _ = _question_desk_inputs(session_id, organization_id=organization_id)
    from livestorm_app.record.question_desk import CONFIRMED, COVERED, REJECTED, find_question

    saved = fetch_question_replies(session_id, organization_id)
    located, _ = _located_answers(session_id, inputs, organization_id=organization_id)
    found = find_question(inputs, str(question_id or "").strip(), saved, located)
    if found is None:
        raise QuestionNotFoundError("No such question in this session's question desk.")
    if not found["livestormId"]:
        raise QuestionNotSavableError("This question has no Livestorm id, so it can't be marked.")
    if not found["located"] or (confirm and found["status"] != COVERED):
        raise NothingLocatedError("There is no answer from the talk to decide on for this question.")
    stored = save_located_decision(
        session_id,
        organization_id=organization_id,
        source=found["source"],
        livestorm_id=found["livestormId"],
        evidence_id=found["id"],
        decision=CONFIRMED if confirm else REJECTED,
        updated_by=updated_by,
    )
    if not stored:
        raise SessionNotFoundError()
    return get_session_question_desk(session_id, organization_id=organization_id)


def save_session_question_reply(
    session_id: str,
    question_id: str,
    *,
    answered: bool,
    reply: str,
    organization_id: OrgScope,
    updated_by: Optional[str],
) -> Dict[str, Any]:
    """Mark one desk question answered (or not) with an optional reply, then
    return the desk as `get_session_question_desk` does. The question must be
    a desk row now (`QuestionNotFoundError` otherwise) with a Livestorm id
    (`QuestionNotSavableError`); the session must be in the scope
    (`SessionNotFoundError`). `updated_by` is the caller's Livestorm user id."""
    inputs, _ = _question_desk_inputs(session_id, organization_id=organization_id)
    from livestorm_app.record.question_desk import find_question

    found = find_question(inputs, str(question_id or "").strip())
    if found is None:
        raise QuestionNotFoundError("No such question in this session's question desk.")
    if not found["livestormId"]:
        raise QuestionNotSavableError("This question has no Livestorm id, so it can't be marked.")
    saved = save_question_reply(
        session_id,
        organization_id=organization_id,
        source=found["source"],
        livestorm_id=found["livestormId"],
        evidence_id=found["id"],
        answered=bool(answered),
        reply=str(reply or "").strip() or None,
        updated_by=updated_by,
    )
    if not saved:
        raise SessionNotFoundError()
    return get_session_question_desk(session_id, organization_id=organization_id)


class FigureCheckNotFoundError(RuntimeError):
    """No check with this key for the session as it is now, or the answer
    isn't one of its options."""


def _figure_check_list(session_id: str, inputs: Any, *, organization_id: OrgScope) -> Tuple[List[Dict[str, Any]], str]:
    """Before you share: the figure checks of the stored understanding, while
    read from the session as it is now, with the host's saved answers
    (`record.figure_checks.figure_checks`), and the understanding's state."""
    from livestorm_app.record.figure_checks import figure_checks
    from livestorm_app.record.understanding import current_reading

    result, state, _ = current_reading(fetch_understanding(session_id, organization_id), inputs)
    return figure_checks(result, fetch_figure_checks(session_id, organization_id)), state


# "Earlier versions" on Follow-up: each output's bundle column and what its
# entries are keyed by. `deep` is the Deep analysis, off the list since
# November 2026 and kept readable under "Earlier outputs".
OUTPUT_VERSION_KINDS: Dict[str, Tuple[str, str]] = {
    "overall": ("analysis_bundle", "language"),
    "deep": ("deep_analysis_bundle", "language"),
    "smart_recap": ("smart_recap_bundle", "tone"),
    "content": ("content_repurpose_bundle", "language"),
    "faq": ("faq_bundle", "language"),
    "decisions": ("decisions_bundle", "language"),
    "team_digest": ("team_digest_bundle", "language"),
}


class UnknownOutputKindError(ValueError):
    """Not an output with saved versions."""


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def get_session_output_versions(
    session_id: str,
    kind: str,
    *,
    language: str = "English",
    tone: str = "professional",
    organization_id: OrgScope,
) -> Dict[str, Any]:
    """The saved versions of one output, for Follow-up's "Earlier versions":
    `{sessionId, kind, entry, versions}`, newest first, each `{id,
    createdAt, models, current, content}` (`db.list_output_versions`, at most
    `db.MAX_OUTPUT_VERSIONS`). `current` marks the newest version whose
    content is the one saved now. Outputs saved before versions were kept
    (November 2026) have none. Read-only, no model call; another org's
    session looks missing (`SessionNotFoundError`)."""
    if kind not in OUTPUT_VERSION_KINDS:
        raise UnknownOutputKindError(f"There are no saved versions for {kind!r}.")
    column, keyed_by = OUTPUT_VERSION_KINDS[kind]
    entry = str((tone if keyed_by == "tone" else language) or "").strip()
    row = fetch_session_fields(session_id, [column], organization_id=organization_id)
    if row is None:
        raise SessionNotFoundError("This session isn't available.")
    saved = row.get(column) if isinstance(row.get(column), dict) else {}
    now = saved.get(entry)
    versions = []
    current_found = False
    for version in list_output_versions(session_id, column, entry, organization_id=organization_id):
        current = not current_found and now is not None and version["content"] == now
        current_found = current_found or current
        versions.append(
            {
                "id": version["id"],
                "createdAt": _iso(version["created_at"]),
                "models": version["models"],
                "current": current,
                "content": version["content"],
            }
        )
    return {"sessionId": session_id, "kind": kind, "entry": entry, "versions": _sanitize_json_value(versions)}


def get_session_figure_checks(session_id: str, *, organization_id: OrgScope) -> Dict[str, Any]:
    """Before you share, for the Follow-up tab: `{sessionId, updatedAt,
    state, unchecked, checks}`. No model call: the understanding job flagged
    the figures; the host answers here."""
    inputs, row = _question_desk_inputs(session_id, organization_id=organization_id)
    from livestorm_app.record.figure_checks import unchecked_count

    checks, state = _figure_check_list(session_id, inputs, organization_id=organization_id)
    body = {"state": state, "unchecked": unchecked_count(checks), "checks": checks}
    return {"sessionId": session_id, "updatedAt": row.get("updated_at"), **_sanitize_json_value(body)}


def save_session_figure_check(
    session_id: str,
    check_key: str,
    choice: Optional[str],
    *,
    organization_id: OrgScope,
    updated_by: Optional[str],
) -> Dict[str, Any]:
    """Answer one figure check (or reopen it with `choice` None), then return
    the checks as `get_session_figure_checks` does. The check must exist for
    the session as it is now and the answer be one of its options
    (`FigureCheckNotFoundError`); the session must be in the scope
    (`SessionNotFoundError`)."""
    inputs, _ = _question_desk_inputs(session_id, organization_id=organization_id)
    from livestorm_app.record.figure_checks import find_check

    checks, _ = _figure_check_list(session_id, inputs, organization_id=organization_id)
    check = find_check(checks, str(check_key or ""))
    if check is None or (choice is not None and choice not in {option["value"] for option in check["options"]}):
        raise FigureCheckNotFoundError("No such check, or no such answer, for this session's figures.")
    if not save_figure_check(
        session_id, organization_id=organization_id, check_key=check["key"], choice=choice, updated_by=updated_by
    ):
        raise SessionNotFoundError()
    return get_session_figure_checks(session_id, organization_id=organization_id)


def get_session_open_questions_csv(session_id: str, *, organization_id: OrgScope, scope: str = "open") -> str:
    """The desk's questions as CSV, with who asked each one (name and
    company, on every session; never an email): `scope` `open`, `all` or
    `answers` (`record.question_desk.questions_csv`). Statuses and answers
    are the desk's, located answers included."""
    inputs, _ = _question_desk_inputs(session_id, organization_id=organization_id)
    from livestorm_app.record.question_desk import questions_csv

    located, _ = _located_answers(session_id, inputs, organization_id=organization_id)
    return questions_csv(inputs, fetch_question_replies(session_id, organization_id), located, scope)


def get_session_chat_messages(session_id: str, *, organization_id: OrgScope) -> Dict[str, Any]:
    """The chat's text by evidence id, for the v1 Report: `{sessionId,
    updatedAt, messages}` (`record.messages.chat_messages`). The record
    lists `C` ids and never text; the page reads this when someone opens a
    count. One scoped read of the session and chat payloads; a row that is
    missing or outside the caller's scope raises `SessionNotFoundError`.
    Nothing is cached: it is read on demand, once per open page."""
    row = fetch_session_fields(
        session_id, ["session_payload", "chat_payload", "updated_at"], organization_id=organization_id
    )
    if not isinstance(row, dict):
        raise SessionNotFoundError()
    from livestorm_app.record.messages import chat_messages

    body = _sanitize_json_value(chat_messages(row.get("session_payload"), row.get("chat_payload")))
    return {"sessionId": session_id, "updatedAt": row.get("updated_at"), **body}


# ── Workspace sessions list ────────────────────────────────────────────────
#
# Powers the Single Analysis card grid. Returns every cached session
# the requesting organization can see, in reverse-chronological order.
# Lightweight by design — we surface only what a card needs to render,
# never the full transcript/chat/analysis bundles. The user clicks a card
# and the existing /api/sessions/{id} flow loads the full workspace.


def _format_started_at_label(value: Any) -> str:
    """Render a Livestorm Unix timestamp as 'Jan 15, 2026 14:00 UTC'."""
    try:
        if value in (None, "", 0):
            return ""
        timestamp = pd.to_datetime(value, unit="s", utc=True, errors="coerce")
        if pd.isna(timestamp):
            return ""
        return timestamp.strftime("%b %d, %Y %H:%M UTC")
    except (TypeError, ValueError):
        return ""


def _format_iso_date_label(value: Any) -> str:
    """Render a TIMESTAMPTZ value as 'May 12, 2026'.

    Used for the 'Generated by X · <date>' line on the workspace cards.
    Times-of-day are intentionally dropped — the card is a coarse
    'when was this added to the workspace' marker, not a fine-grained
    audit trail.
    """
    if value is None:
        return ""
    try:
        timestamp = pd.to_datetime(value, utc=True, errors="coerce")
    except (TypeError, ValueError):
        return ""
    if pd.isna(timestamp):
        return ""
    return timestamp.strftime("%b %d, %Y")


def _format_duration_label(value: Any) -> str:
    """Render a duration in seconds as 'Hh Mm' or 'Mm Ss'.

    Livestorm sessions vary from a few minutes (test sessions) to multi-hour
    webinars. The format picks a sensible unit for each range:
        > 1 hour     -> 'Hh Mm'
        > 1 minute   -> 'Mm Ss'  (drops seconds when zero)
        < 1 minute   -> 'Ss'
        invalid      -> ''
    """
    try:
        if value in (None, "", 0):
            return ""
        total_seconds = int(float(value))
    except (TypeError, ValueError):
        return ""
    if total_seconds <= 0:
        return ""
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    if minutes:
        return f"{minutes}m {seconds}s" if seconds else f"{minutes}m"
    return f"{seconds}s"


def _extract_event_title(event_payload: Any, fallback: str = "Untitled event") -> str:
    """Pull `data.attributes.title` out of a Livestorm event payload."""
    if not isinstance(event_payload, dict):
        return fallback
    data = event_payload.get("data")
    if not isinstance(data, dict):
        return fallback
    attributes = data.get("attributes")
    if not isinstance(attributes, dict):
        return fallback
    title = str(attributes.get("title") or "").strip()
    return title or fallback


def list_workspace_sessions_data(organization_id: str) -> Dict[str, Any]:
    """Return the card list for the Single Analysis page.

    The shape is intentionally flat — frontend renders one card per entry
    without further normalisation. `hasTranscript` / `hasOverallAnalysis`
    etc. let the card show small status pills without loading bundles.
    """
    rows = list_workspace_sessions(organization_id)
    sessions: List[Dict[str, Any]] = []
    for row in rows:
        # The query returns only the session's attributes object, the event
        # title and language, and has-output flags (see db.list_workspace_sessions).
        attributes = row.get("session_attributes") if isinstance(row.get("session_attributes"), dict) else {}
        started_at_unix = attributes.get("started_at") or attributes.get("estimated_started_at")
        duration_seconds = attributes.get("duration")
        # Falls back to session name (if any), then to "Untitled event"
        # when the event payload hasn't been backfilled yet.
        event_title = (
            str(row.get("event_title") or "").strip()
            or str(attributes.get("name") or "").strip()
            or "Untitled event"
        )
        created_at_value = row.get("created_at")
        sessions.append(
            {
                "sessionId": row.get("session_id"),
                "organizationId": row.get("organization_id"),
                "sessionName": str(attributes.get("name") or "").strip() or "Untitled session",
                "eventId": attributes.get("event_id"),
                "eventTitle": event_title,
                "startedAtUnix": started_at_unix,
                "startedAtLabel": _format_started_at_label(started_at_unix),
                "durationSeconds": duration_seconds,
                "durationLabel": _format_duration_label(duration_seconds),
                "attendeesCount": attributes.get("attendees_count"),
                "registrantsCount": attributes.get("registrants_count"),
                "schedulingStatus": attributes.get("status") or attributes.get("scheduling_status"),
                # Language belongs to the event; sessions have none.
                "language": row.get("event_language") or None,
                "hasTranscript": bool(row.get("has_transcript")),
                "hasOverallAnalysis": bool(row.get("has_overall_analysis")),
                "hasDeepAnalysis": bool(row.get("has_deep_analysis")),
                "hasSmartRecap": bool(row.get("has_smart_recap")),
                "hasContentRepurposing": bool(row.get("has_content_repurposing")),
                # The cover image is rendered by an OpenAI image
                # call from the Professional Smart Recap. The list view
                # uses hasCoverImage to decide between the real cover
                # (GET /api/sessions/{id}/cover.png) and the placeholder
                # gradient. `coverImageGeneratedAt` doubles as a cache
                # buster on the frontend — the URL gets ?v=<iso> so a
                # regenerated cover bypasses the 24h browser cache on
                # the cover endpoint.
                "hasCoverImage": bool(row.get("has_cover_image")),
                "coverImageMime": str(row.get("cover_image_mime") or "image/png"),
                "coverImageGeneratedAt": (
                    row.get("cover_image_generated_at").isoformat()
                    if row.get("cover_image_generated_at")
                    else ""
                ),
                # "Generated by" attribution — stamped on first insert,
                # preserved across re-fetches by the COALESCE clause in
                # upsert_cached_session. NULL/empty when the original
                # fetch predates attribution or used LS_API_KEY mode.
                "generatedByUserId": str(row.get("created_by_user_id") or "").strip(),
                "generatedByEmail": str(row.get("created_by_email") or "").strip(),
                "generatedByName": str(row.get("created_by_name") or "").strip(),
                "generatedAtIso": created_at_value.isoformat() if created_at_value else "",
                "generatedAtLabel": _format_iso_date_label(created_at_value),
                "updatedAt": row.get("updated_at").isoformat() if row.get("updated_at") else "",
                "createdAt": created_at_value.isoformat() if created_at_value else "",
            }
        )
    return {"sessions": sessions}


def _newest_start(options: List[Dict[str, Any]]) -> int:
    return max((option.get("startedAt") or 0 for option in options), default=0)


def fetch_event_sessions(api_key: str, event_id: str, cursor: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """An event's finished sessions, one Livestorm page (50) at a time,
    newest first, for the Add session page: `{eventId, options, total, next}`.

    Livestorm can't sort an event's sessions. So the first call reads the
    first page and, when there are more, the last one, and starts from the
    end that holds the newest sessions; `next` is the cursor of the following
    page (`{page, step, pages}`), or None when every page was read. The page
    asks for it with "Show more". `total` is how many finished sessions the
    event has (`meta.record_count`), or None when Livestorm doesn't say."""
    event_id = event_id.strip()
    first = START_PAGE_NUMBER
    if cursor:
        page, step, pages = int(cursor["page"]), int(cursor["step"]), int(cursor["pages"])
        if step not in (1, -1) or pages < 1 or not first <= page < first + pages:
            raise ValueError("Invalid page cursor.")
        payload = fetch_event_sessions_page(api_key, event_id, page)
        total = extract_record_count(payload)
        options = build_event_session_options(payload)
    else:
        payload = fetch_event_sessions_page(api_key, event_id, first)
        total = extract_record_count(payload)
        options = build_event_session_options(payload)
        pages = max(1, math.ceil(total / LIVESTORM_MAX_PAGE_SIZE)) if total is not None else 1
        page, step = first, 1
        if pages > 1:
            last = first + pages - 1
            last_options = build_event_session_options(fetch_event_sessions_page(api_key, event_id, last))
            if _newest_start(last_options) > _newest_start(options):
                options, page, step = last_options, last, -1
    following = page + step
    next_cursor = {"page": following, "step": step, "pages": pages} if first <= following < first + pages else None
    return {"eventId": event_id, "options": options, "total": total, "next": next_cursor}


def fetch_available_events(
    api_key: str,
    page_number: int = 0,
    page_size: int = 20,
    title: str = "",
    scheduling_status: str = "",
) -> Dict[str, Any]:
    payload = fetch_workspace_events_page(
        api_key,
        page_number=page_number,
        page_size=page_size,
        title=title,
        scheduling_status=scheduling_status,
    )
    return {
        "pagesFetched": payload.get("pages_fetched", 1),
        "currentPage": payload.get("requested_page_number", page_number),
        "nextPage": payload.get("next_page"),
        "title": str(title or "").strip(),
        "schedulingStatus": str(scheduling_status or "").strip(),
        "options": build_workspace_event_options(payload),
        "payload": payload,
    }


def _org_kwarg(organization_id: str) -> Dict[str, Any]:
    """Return `{'organization_id': org}` when org is non-empty, else `{}`.

    Lets us spread into `upsert_cached_session(..., **_org_kwarg(org))`
    without overwriting an existing org_id with an empty string for callers
    that don't have one (e.g. local API-key fallback mode).
    """
    cleaned = str(organization_id or "").strip()
    return {"organization_id": cleaned} if cleaned else {}


def _generator_kwargs(
    created_by_user_id: str = "",
    created_by_email: str = "",
    created_by_name: str = "",
) -> Dict[str, Any]:
    """Return `{created_by_*: ...}` kwargs to pass through to upsert.

    Only fields with non-empty values are included so we never write
    blanks to the cache. The upsert preserves existing values on
    update via the `preserve_on_update` set in db.py — these kwargs
    only "stick" the first time a session is inserted.
    """
    out: Dict[str, Any] = {}
    if str(created_by_user_id or "").strip():
        out["created_by_user_id"] = str(created_by_user_id).strip()
    if str(created_by_email or "").strip():
        out["created_by_email"] = str(created_by_email).strip()
    if str(created_by_name or "").strip():
        out["created_by_name"] = str(created_by_name).strip()
    return out


def _extract_event_id_from_session(session_payload: Dict[str, Any]) -> str:
    """Pull `event_id` out of a Livestorm session payload. Empty string when
    the payload is malformed or the field is missing."""
    if not isinstance(session_payload, dict):
        return ""
    attributes = extract_session_attributes(session_payload)
    return str(attributes.get("event_id") or "").strip()


def _fetch_event_payload_safe(api_key: str, event_id: str) -> Optional[Dict[str, Any]]:
    """Wrap `fetch_event_details` so a missing/forbidden event doesn't kill
    the parent session fetch.

    Returns the event payload on success, or None when the request fails
    (network error, 403, 404, etc.). The caller proceeds with the session
    fetch and the card will fall back to its un-titled display.
    """
    cleaned = str(event_id or "").strip()
    if not cleaned or not str(api_key or "").strip():
        return None
    try:
        return fetch_event_details(api_key, cleaned)
    except Exception:
        logger.warning("fetch_event_details failed for event_id=%s", cleaned, exc_info=True)
        return None


def fetch_all_session_data(
    api_key: str,
    session_id: str,
    force_refresh: bool = False,
    *,
    job_connection_id: str,
    read_scope: OrgScope,
    organization_id: str = "",
    local_dev: bool = False,
    created_by_user_id: str = "",
    created_by_email: str = "",
    created_by_name: str = "",
) -> Dict[str, Any]:
    """Fetch session + chat + questions, then start the transcript job.

    Returns the full workspace when the transcript is already cached;
    otherwise the base workspace plus `transcriptJob: {jobId, jobStatus}`
    to poll. Transcription always runs in the worker, never inside a web
    request. Either way the response carries the base fetch's `rosterSync`.
    Scope args as in `fetch_session_base_data`.
    """
    identity = {
        "read_scope": read_scope,
        "organization_id": organization_id,
        "created_by_user_id": created_by_user_id,
        "created_by_email": created_by_email,
        "created_by_name": created_by_name,
    }
    base = fetch_session_base_data(
        api_key, session_id, force_refresh, job_connection_id=job_connection_id, local_dev=local_dev, **identity,
    )
    transcript = fetch_session_transcript_data(
        api_key, session_id, force_refresh, job_connection_id=job_connection_id, **identity,
    )
    if "sessionId" in transcript:
        return {**transcript, "rosterSync": base.get("rosterSync")}
    return {**base, "transcriptJob": transcript}


ROSTER_SYNC_UNAVAILABLE = {"status": "unavailable", "started": False, "skipped": None}


def _roster_sync_on_fetch(
    session_id: str,
    cached: Mapping[str, Any],
    *,
    fresh: bool,
    read_scope: OrgScope,
    caller_org_id: str,
    job_connection_id: str,
    local_dev: bool,
) -> Dict[str, Any]:
    """The response's `rosterSync`: start the session's people sync when it
    needs one (`people_sync.start_sync_on_fetch`), and say what happened.

    Best-effort: the session's base data is already saved and about to be
    returned, so a database or Redis failure here never fails the fetch. It
    is logged with the session id and the error's class only (a database
    error's message can echo values) and reads `status: "unavailable"`.
    `cached` is the row just read with the caller's scope; its stamped org
    decides whether this caller may start a sync.
    """
    try:
        return start_sync_on_fetch(
            session_id,
            fresh=fresh,
            read_scope=read_scope,
            caller_org_id=caller_org_id,
            session_org_id=str(cached.get("organization_id") or ""),
            job_connection_id=job_connection_id,
            local_dev=local_dev,
        )
    except Exception as exc:
        logger.warning("The people sync was not started for session_id=%s (%s)", session_id, type(exc).__name__)
        return dict(ROSTER_SYNC_UNAVAILABLE)


class SessionNotFinishedError(RuntimeError):
    """A pasted session that hasn't happened: nothing to analyse yet."""


# Livestorm's session statuses that mean nothing happened yet, or never
# will (`filter[status]` in the API: upcoming, live, on_demand, past,
# past_not_started, canceled, draft). The event list shows `past` only;
# a pasted ID is refused for these, before anything is fetched or saved.
NOT_FINISHED_STATUSES: Dict[str, str] = {
    "upcoming": "hasn't taken place yet",
    "live": "is still live",
    "past_not_started": "was scheduled but never started",
    "canceled": "was cancelled",
    "draft": "is still a draft",
}


def _require_finished(session_payload: Any) -> None:
    """Raises `SessionNotFinishedError` when Livestorm says the session
    hasn't happened (`NOT_FINISHED_STATUSES`). A session with another
    status, or none, goes on."""
    data = session_payload.get("data") if isinstance(session_payload, Mapping) else None
    attributes = data.get("attributes") if isinstance(data, Mapping) else None
    status = str((attributes or {}).get("status") or "").strip().lower()
    if status in NOT_FINISHED_STATUSES:
        raise SessionNotFinishedError(
            f"This session {NOT_FINISHED_STATUSES[status]}, so there is nothing to analyse. "
            "Add it once it has ended: finished sessions are listed under its event."
        )


def fetch_session_base_data(
    api_key: str,
    session_id: str,
    force_refresh: bool = False,
    *,
    job_connection_id: str,
    read_scope: OrgScope,
    organization_id: str = "",
    local_dev: bool = False,
    created_by_user_id: str = "",
    created_by_email: str = "",
    created_by_name: str = "",
) -> Dict[str, Any]:
    """Fetch session + chat + questions, cache-first.

    `read_scope` governs cache lookups (what the caller may read).
    `organization_id` is the caller's own org, stamped on newly created rows.

    The response is the classic workspace plus `rosterSync: {status, started,
    skipped}`: the session's people sync, started here in the worker when
    the session has no stored roster yet, or was just fetched again
    (`_roster_sync_on_fetch`). `job_connection_id` is the caller's
    `Caller.job_connection_id()`, kept on the sync's row until the worker
    claims it, never in the job's arguments; `local_dev` is `Caller.local_dev`.
    The classic app ignores `rosterSync`.
    """
    session_id = str(session_id or "").strip()
    sync_args = {
        "read_scope": read_scope,
        "caller_org_id": organization_id,
        "job_connection_id": job_connection_id,
        "local_dev": local_dev,
    }
    cached = (
        None if force_refresh
        else fetch_cached_session(api_key, session_id, organization_id=read_scope)
    )
    if isinstance(cached, dict):
        has_base_payloads = all(isinstance(cached.get(key), dict) for key in ("session_payload", "chat_payload", "questions_payload"))
        if has_base_payloads:
            workspace = _serialize_cached_session(session_id, cached)
            return {**workspace, "rosterSync": _roster_sync_on_fetch(session_id, cached, fresh=False, **sync_args)}

    session_payload = fetch_session_details(api_key, session_id)
    # Before the chat is fetched or anything saved: a session that hasn't
    # happened would be stored with nobody in it.
    _require_finished(session_payload)
    chat_bundle = fetch_chat_and_questions_bundle(api_key, session_id)
    event_payload = _fetch_event_payload_safe(
        api_key, _extract_event_id_from_session(session_payload)
    )
    upsert_kwargs = {
        "session_payload": session_payload,
        "chat_payload": chat_bundle.get("chat_payload"),
        "questions_payload": chat_bundle.get("questions_payload"),
    }
    if event_payload is not None:
        upsert_kwargs["event_payload"] = event_payload
    upsert_cached_session(
        api_key,
        session_id,
        **upsert_kwargs,
        **_org_kwarg(organization_id),
        **_generator_kwargs(created_by_user_id, created_by_email, created_by_name),
    )
    cached = fetch_cached_session(api_key, session_id, organization_id=read_scope)
    if not isinstance(cached, dict):
        raise RuntimeError("Fetched session overview/chat data but failed to reload it from the cache.")
    workspace = _serialize_cached_session(session_id, cached)
    return {**workspace, "rosterSync": _roster_sync_on_fetch(session_id, cached, fresh=True, **sync_args)}


ACTIVE_TRANSCRIPT_JOB_STATUSES = ("pending", "running")

# A `transcript_jobs` row whose arq job has vanished (Redis restarted, or the
# job was lost during a deploy) is closed after this grace period. The grace
# covers the moment between inserting the row and enqueueing its job.
TRANSCRIPT_JOB_LOST_AFTER_SECONDS = 120


def _seconds_since(value: Any) -> Optional[float]:
    if not isinstance(value, datetime):
        return None
    moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - moment).total_seconds()


def _reconcile_transcript_job(job: Dict[str, Any]) -> Dict[str, Any]:
    """Close an active `transcript_jobs` row whose arq job is gone.

    Only one transcription per session may be active (unique index), so a
    dead row would otherwise block the session until the stuck-job sweeper
    runs an hour later. When Redis can't be reached the row is left alone.
    """
    if job.get("status") not in ACTIVE_TRANSCRIPT_JOB_STATUSES:
        return job
    arq_status = read_job_status_sync(transcription_job_id(job["job_id"]))
    state = arq_status.get("jobStatus")
    if state == "error":
        logger.warning("Transcription job %s failed in the queue: %s", job["job_id"], arq_status.get("error"))
        message = "The transcription job stopped before finishing. Please try again."
    elif state == "not_found" and (_seconds_since(job.get("updated_at")) or 0) > TRANSCRIPT_JOB_LOST_AFTER_SECONDS:
        message = "The transcription job was lost by the queue (the worker probably restarted). Please try again."
    else:
        return job
    update_transcript_job_status(job["job_id"], "error", error=message)
    return {**job, "status": "error", "error": message}


def fetch_session_transcript_data(
    api_key: str,
    session_id: str,
    force_refresh: bool = False,
    *,
    job_connection_id: str,
    read_scope: OrgScope,
    organization_id: str = "",
    created_by_user_id: str = "",
    created_by_email: str = "",
    created_by_name: str = "",
    analyse: bool = False,
) -> Dict[str, Any]:
    """Start a transcription job (or attach to the active one).

    `analyse` (the Add session page's "Add") chains the session analysis:
    queued at once when the transcript is already there, else marked on the
    transcript job, which the worker queues when the job ends
    (`worker.run_transcription`), with or without a transcript.

    Scope args as in `fetch_session_base_data`. `job_connection_id` is the
    caller's `Caller.job_connection_id()`. It is stored on the job's row
    (never in the queue); the worker resolves Livestorm credentials from it.

    Returns the cached workspace immediately if the transcript already
    exists (unless `force_refresh`). Otherwise returns `{ jobId, jobStatus }`
    for `get_transcript_job_status_data` polling. A session has at most one
    active transcription: concurrent and repeated requests, `force_refresh`
    included, attach to it instead of paying for a second one.

    The session_cache row is stamped with `organization_id` before the job
    is queued, so the worker's later `transcript_payload` upsert lands on a
    row that is already org-scoped (visible in the Single Analysis list).
    """
    session_id = str(session_id or "").strip()

    cached = fetch_cached_session(api_key, session_id, organization_id=read_scope)

    # Return immediately when transcript is already cached (skip job machinery).
    if not force_refresh and isinstance(cached, dict) and isinstance(cached.get("transcript_payload"), dict):
        if analyse:
            _chain_session_analysis(session_id)
        return _serialize_cached_session(session_id, cached)

    if not isinstance(cached, dict):
        # The caller cannot see a cached row for this session: it is new,
        # legacy (NULL org), or owned by another org. Before re-attaching to
        # a job, stamping the row, or enqueueing paid work, prove the
        # caller's own Livestorm credentials can access the session.
        # Livestorm answers 404/403 for sessions outside the caller's org.
        fetch_session_details(api_key, session_id)

    # Attach to the active job, unless its queue job is gone.
    existing_job = get_transcript_job_for_session(session_id)
    if isinstance(existing_job, dict):
        existing_job = _reconcile_transcript_job(existing_job)
        if existing_job.get("status") in ACTIVE_TRANSCRIPT_JOB_STATUSES:
            if analyse:
                mark_transcript_job_then_analyse(existing_job["job_id"])
            return {"jobId": existing_job["job_id"], "jobStatus": existing_job["status"]}

    # Pre-stamp organization_id (and the generator identity if we have it)
    # on the cache row. Safe if the row doesn't exist yet — the upsert
    # creates it with these fields; the worker fills in transcript_payload
    # later. Generator fields are preserve-on-update, so a refetch by a
    # different user doesn't rewrite the original attribution.
    prestamp_kwargs = {
        **_org_kwarg(organization_id),
        **_generator_kwargs(created_by_user_id, created_by_email, created_by_name),
    }
    if prestamp_kwargs:
        upsert_cached_session(api_key, session_id, **prestamp_kwargs)

    # Create the DB row first so the polling endpoint can find the job
    # even if enqueue races the first poll. A concurrent request may have
    # created one in the meantime: attach to it.
    # The connection id is stored on the row, not in the job args: it is
    # also the user's session cookie, and arq keeps args in Redis and logs.
    job = create_transcript_job(session_id, connection_id=job_connection_id)
    if analyse:
        mark_transcript_job_then_analyse(job["job_id"])
    if not job["created"]:
        return {"jobId": job["job_id"], "jobStatus": job["status"]}
    job_id = job["job_id"]
    enqueued = enqueue_unique_job_sync(
        "run_transcription",
        transcription_job_id(job_id),
        job_id,
        session_id,
    )
    if not enqueued:
        # Redis or arq was unreachable. Mark the DB row so the user
        # sees a clean error instead of a job that polls forever.
        update_transcript_job_status(
            job_id,
            "error",
            error="Failed to enqueue transcription job (queue unavailable).",
        )
        raise RuntimeError(
            "Transcription queue is unavailable. Check the worker service and Redis connection."
        )
    return {"jobId": job_id, "jobStatus": "pending"}


def get_transcript_job_status_data(
    api_key: str,
    session_id: str,
    *,
    organization_id: OrgScope,
) -> Dict[str, Any]:
    """Polling endpoint payload: returns full workspace data once done, else job status.

    Progress comes from two sources, merged:
    - Postgres `transcript_jobs.progress` (legacy DB-backed progress —
      raw Gladia step payloads). Kept for backward compat.
    - Redis `stormiq:progress:transcript:{session_id}` (new stage-floor
      progress with percent). Preferred when present.

    `organization_id` is the caller's read scope. Org-scoped callers only
    see job status for sessions whose cache row is in their org (the fetch
    route pre-stamps the row before enqueuing, so it exists while the job
    runs).
    """
    session_id = str(session_id or "").strip()

    # Transcript may have finished between polls — return full data if so.
    # Polls only read a flag; the full row is loaded once, at completion.
    state = fetch_session_fields(session_id, ["has_transcript"], organization_id=organization_id)
    if isinstance(state, dict) and state.get("has_transcript"):
        cached = fetch_cached_session(api_key, session_id, organization_id=organization_id)
        if isinstance(cached, dict) and isinstance(cached.get("transcript_payload"), dict):
            return _serialize_cached_session(session_id, cached)
    if state is None and organization_id is not ALL_ORGS:
        raise SessionNotFoundError()

    job = get_transcript_job_for_session(session_id)
    if not isinstance(job, dict):
        return {"jobStatus": "not_found"}
    # Surface a job that died in the queue now, not after the sweeper's hour.
    job = _reconcile_transcript_job(job)

    # Legacy DB progress (raw Gladia payload).
    db_progress: Optional[Dict[str, Any]] = None
    raw_progress = job.get("progress")
    if raw_progress:
        try:
            db_progress = json.loads(raw_progress)
        except Exception:
            pass

    # New Redis stage-floor progress. Worker writes this in parallel; we
    # surface it when available so the client can show a real percent
    # bar instead of just a step label.
    redis_progress = read_progress("transcript", session_id)

    # `progress` is the DB payload (the raw Gladia step); `progressRedis`
    # is the stage-floor payload the frontend renders.
    return {
        "jobId": job["job_id"],
        "jobStatus": job["status"],
        "error": job.get("error"),
        "progress": db_progress,
        "progressRedis": redis_progress,
    }


def save_speaker_labels(
    api_key: str,
    session_id: str,
    speaker_names: Dict[str, str],
    *,
    organization_id: OrgScope,
) -> Dict[str, Any]:
    """Save edited speaker labels on a session the caller can read.

    `organization_id` is the caller's read scope. The row must already be
    visible in that scope; this never creates rows or touches another org's.
    """
    if not isinstance(fetch_cached_session(api_key, session_id, organization_id=organization_id), dict):
        raise SessionNotFoundError()
    normalized = {
        str(speaker): str(label).strip()
        for speaker, label in (speaker_names or {}).items()
        if str(label).strip()
    }
    upsert_cached_session(
        api_key,
        session_id,
        transcript_speaker_names=normalized,
    )
    cached = fetch_cached_session(api_key, session_id, organization_id=organization_id)
    if not isinstance(cached, dict):
        raise RuntimeError("Speaker labels were saved, but the updated session could not be reloaded.")
    return _serialize_cached_session(session_id, cached)


def _require_cached_payloads(session_id: str, *, organization_id: OrgScope) -> Dict[str, Any]:
    """Load a cached row or raise SessionNotFoundError.

    Worker runners pass ALL_ORGS (trusted internal code); PDF routes pass
    the caller's read scope.
    """
    cached = fetch_cached_session("", session_id, organization_id=organization_id)
    if not isinstance(cached, dict):
        raise SessionNotFoundError()
    return cached


def _llm_call_context(job_kind: str, flow: str, session_id: str, dimension: str, cached: Dict[str, Any]) -> CallContext:
    """Who a classic runner's model calls are for, for their `llm_usage` rows:
    the model's job kind ("translation" for a French version), the session,
    its org from the cached row, and the arq id of the job running them."""
    return CallContext(
        job_kind=job_kind,
        session_id=str(session_id or "").strip(),
        organization_id=str(cached.get("organization_id") or "").strip(),
        job_id=ai_job_id(flow, session_id, dimension),
    )


def _require_generated_text(text: Any, label: str) -> str:
    """Return stripped model output, or raise so the job fails visibly.

    An empty answer must not be reported as a completed job: the UI would
    show "done" with nothing to display.
    """
    cleaned = str(text or "").strip()
    if not cleaned:
        raise RuntimeError(f"The model returned an empty {label}. Nothing was saved; please try again.")
    return cleaned


def run_overall_analysis(api_analysis_key: str, session_id: str, output_language: str) -> Dict[str, Any]:
    """The Follow-up's "Session analysis" in one language. English is written
    from the session's brief (`_write_session_analysis`); another language
    is a translation of the English one, which must exist first. Saved in
    `analysis_bundle` under the language."""
    cached = _require_cached_payloads(session_id, organization_id=ALL_ORGS)
    if not isinstance(cached.get("transcript_payload"), dict):
        raise RuntimeError("The session analysis needs the transcript. Make it on the Transcript tab first.")

    analysis_bundle = _normalize_text_bundle(cached.get("analysis_bundle"), str(cached.get("analysis_md") or ""))
    if str(output_language or "").strip() not in ("", "English") and not analysis_bundle.get("English", "").strip():
        raise RuntimeError("Generate the English analysis first before translating.")
    source_language, source_markdown = _get_alternate_language_text(analysis_bundle, output_language)
    if source_markdown:
        analysis_md = translate_markdown_with_openai(
            api_key=api_analysis_key,
            model=get_llm_model("translation"),
            source_markdown=source_markdown,
            source_language=OUTPUT_LANGUAGE_MAP.get(source_language, source_language),
            target_language=OUTPUT_LANGUAGE_MAP.get(output_language, output_language),
            ctx=_llm_call_context("translation", "overall_analysis", session_id, output_language, cached),
        )
    else:
        analysis_md = _write_session_analysis(api_analysis_key, session_id, cached, output_language)

    analysis_md = _require_generated_text(analysis_md, "session analysis")
    merge_cached_bundle_entry(
        session_id, "analysis_bundle", output_language, analysis_md, provenance=run_provenance(),
        **({"analysis_md": analysis_md} if output_language == "English" else {}),
    )
    analysis_bundle[output_language] = analysis_md
    return {"markdown": analysis_md, "bundle": analysis_bundle}


class SessionNotAnalysedError(RuntimeError):
    """The session analysis reads what "Analyse session" stored, and there is
    none for the session as it is now."""


def _write_session_analysis(api_key: str, session_id: str, cached: Mapping[str, Any], output_language: str) -> str:
    """The session analysis written from the session's brief
    (`record.session_brief.build_brief`): the record's numbers and pulse,
    the stored understanding (only while read from the session as it is
    now), the question desk's rows without their authors, and the
    transcript by speaker key. Raises `SessionNotAnalysedError` when the
    session hasn't been analysed, or changed since."""
    from livestorm_app.ai.session_analysis import write_analysis
    from livestorm_app.db import fetch_understanding
    from livestorm_app.record.assemble import assemble_record, clock_meta, session_meta
    from livestorm_app.record.figure_checks import figure_notes
    from livestorm_app.record.inputs import load_record_inputs
    from livestorm_app.record.question_desk import question_desk
    from livestorm_app.record.roster import stored_roster_rows
    from livestorm_app.record.session_brief import build_brief
    from livestorm_app.record.understanding import READY, STALE, current_reading, located_answers

    roster_status = fetch_roster_status(session_id, ALL_ORGS) or {}
    stored_roster = fetch_session_roster(session_id, ALL_ORGS) if roster_status.get("roster") is not None else None
    inputs = load_record_inputs(
        dict(cached),
        roster_rows=stored_roster_rows(stored_roster) if stored_roster is not None else None,
        stored_labels=fetch_chat_labels(session_id, ALL_ORGS),
    )
    stored = fetch_understanding(session_id, ALL_ORGS)
    result, state, utterances = current_reading(stored, inputs)
    if state == STALE:
        raise SessionNotAnalysedError('The session changed since it was analysed. Run "Analyse session" again on the Report first.')
    if state != READY:
        raise SessionNotAnalysedError('Run "Analyse session" on the Report first: the session analysis builds on it.')
    located, located_state = located_answers(stored, inputs)
    desk = question_desk(inputs, fetch_question_replies(session_id, ALL_ORGS), located, located_state)
    transcript = inputs.transcript
    languages = list(getattr(transcript, "languages", None) or [])
    brief = build_brief(
        title=session_meta(inputs)["title"],
        length_s=clock_meta(inputs).get("endS"),
        language=languages[0] if languages else "unknown",
        internal=bool(inputs.roster.meta.internal),
        record=assemble_record(inputs)["record"],
        understanding=result,
        utterances=utterances,
        # What the host answered in "Before you share", per flagged figure.
        figure_notes=figure_notes(result, fetch_figure_checks(session_id, ALL_ORGS)),
        # The desk's rows without their authors: names never reach a model.
        questions=[
            {"t": q["t"], "source": q["source"], "text": q["text"], "answered_as": q["status"], "located": q["located"]}
            for q in desk["questions"]
        ],
    )
    return write_analysis(
        brief,
        output_language=OUTPUT_LANGUAGE_MAP.get(output_language, output_language),
        api_key=api_key,
        ctx=_llm_call_context("overall_analysis", "overall_analysis", session_id, output_language, cached),
    )


def run_faq(api_analysis_key: str, session_id: str, output_language: str) -> Dict[str, Any]:
    """The Follow-up's "FAQ" in one language. English is written from the
    question desk (`_write_faq`); another language is a translation of the
    English one, which must exist first. Saved in `faq_bundle` under the
    language."""
    cached = _require_cached_payloads(session_id, organization_id=ALL_ORGS)
    faq_bundle = _normalize_text_bundle(cached.get("faq_bundle"), "")
    if str(output_language or "").strip() not in ("", "English") and not faq_bundle.get("English", "").strip():
        raise RuntimeError("Generate the English FAQ first before translating.")
    source_language, source_markdown = _get_alternate_language_text(faq_bundle, output_language)
    if source_markdown:
        faq_md = translate_markdown_with_openai(
            api_key=api_analysis_key,
            model=get_llm_model("translation"),
            source_markdown=source_markdown,
            source_language=OUTPUT_LANGUAGE_MAP.get(source_language, source_language),
            target_language=OUTPUT_LANGUAGE_MAP.get(output_language, output_language),
            ctx=_llm_call_context("translation", "faq", session_id, output_language, cached),
        )
    else:
        faq_md = _write_faq(api_analysis_key, session_id, cached, output_language)
    faq_md = _require_generated_text(faq_md, "FAQ")
    merge_cached_bundle_entry(session_id, "faq_bundle", output_language, faq_md, provenance=run_provenance())
    faq_bundle[output_language] = faq_md
    return {"markdown": faq_md, "bundle": faq_bundle}


def run_decisions(api_analysis_key: str, session_id: str, output_language: str) -> Dict[str, Any]:
    """The Follow-up's "Decisions and commitments" in one language. English
    is read from the session's dossier (`_write_decisions`); another
    language is a translation of the English one, which must exist first.
    Saved in `decisions_bundle` under the language."""
    cached = _require_cached_payloads(session_id, organization_id=ALL_ORGS)
    bundle = _normalize_text_bundle(cached.get("decisions_bundle"), "")
    if str(output_language or "").strip() not in ("", "English") and not bundle.get("English", "").strip():
        raise RuntimeError("Generate the English decisions first before translating.")
    source_language, source_markdown = _get_alternate_language_text(bundle, output_language)
    if source_markdown:
        markdown = translate_markdown_with_openai(
            api_key=api_analysis_key,
            model=get_llm_model("translation"),
            source_markdown=source_markdown,
            source_language=OUTPUT_LANGUAGE_MAP.get(source_language, source_language),
            target_language=OUTPUT_LANGUAGE_MAP.get(output_language, output_language),
            ctx=_llm_call_context("translation", "decisions", session_id, output_language, cached),
        )
    else:
        markdown = _write_decisions(api_analysis_key, session_id, cached, output_language)
    markdown = _require_generated_text(markdown, "list of decisions")
    merge_cached_bundle_entry(session_id, "decisions_bundle", output_language, markdown, provenance=run_provenance())
    bundle[output_language] = markdown
    return {"markdown": markdown, "bundle": bundle}


def _write_decisions(api_key: str, session_id: str, cached: Mapping[str, Any], output_language: str) -> str:
    """What was decided and promised on stage, read from the session's
    dossier (`record.understanding.build_dossier`: utterances by id and
    speaker key, never a name) by one model call (`ai.decisions`), checked
    against it and worded by code (`record.decisions`) with the speakers'
    saved names. Needs a transcript; it doesn't need "Analyse session"."""
    from livestorm_app.ai.decisions import read_decisions
    from livestorm_app.record.decisions import check_decisions, decisions_markdown
    from livestorm_app.record.inputs import load_record_inputs
    from livestorm_app.record.roster import stored_roster_rows
    from livestorm_app.record.understanding import build_dossier

    roster_status = fetch_roster_status(session_id, ALL_ORGS) or {}
    stored_roster = fetch_session_roster(session_id, ALL_ORGS) if roster_status.get("roster") is not None else None
    inputs = load_record_inputs(
        dict(cached),
        roster_rows=stored_roster_rows(stored_roster) if stored_roster is not None else None,
        stored_labels=fetch_chat_labels(session_id, ALL_ORGS),
    )
    dossier, utterances, _questions = build_dossier(inputs)
    if not utterances:
        raise RuntimeError("Decisions and commitments are read from the transcript: this session has none yet.")
    answer = read_decisions(
        dossier,
        api_key=api_key,
        ctx=_llm_call_context("decisions", "decisions", session_id, output_language, cached),
    )
    items = check_decisions([item.model_dump() for item in answer.items], utterances)
    names = cached.get("transcript_speaker_names") if isinstance(cached.get("transcript_speaker_names"), dict) else {}
    return decisions_markdown(items, utterances, names, on_clock=any(u.get("t") is not None for u in utterances))


def run_team_digest(api_analysis_key: str, session_id: str, output_language: str) -> Dict[str, Any]:
    """The Follow-up's "Team digest" in one language. English is written
    from the digest's brief and checked by code (`_write_team_digest`);
    another language is a translation of the English one, which must exist
    first. Saved in `team_digest_bundle` under the language."""
    cached = _require_cached_payloads(session_id, organization_id=ALL_ORGS)
    bundle = _normalize_text_bundle(cached.get("team_digest_bundle"), "")
    if str(output_language or "").strip() not in ("", "English") and not bundle.get("English", "").strip():
        raise RuntimeError("Generate the English team digest first before translating.")
    source_language, source_markdown = _get_alternate_language_text(bundle, output_language)
    if source_markdown:
        markdown = translate_markdown_with_openai(
            api_key=api_analysis_key,
            model=get_llm_model("translation"),
            source_markdown=source_markdown,
            source_language=OUTPUT_LANGUAGE_MAP.get(source_language, source_language),
            target_language=OUTPUT_LANGUAGE_MAP.get(output_language, output_language),
            ctx=_llm_call_context("translation", "team_digest", session_id, output_language, cached),
        )
    else:
        markdown = _write_team_digest(api_analysis_key, session_id, cached, output_language)
    markdown = _require_generated_text(markdown, "team digest")
    merge_cached_bundle_entry(session_id, "team_digest_bundle", output_language, markdown, provenance=run_provenance())
    bundle[output_language] = markdown
    return {"markdown": markdown, "bundle": bundle}


def _write_team_digest(api_key: str, session_id: str, cached: Mapping[str, Any], output_language: str) -> str:
    """About 100 words for the team's channel, written by one model call
    (`ai.team_digest`) from the digest's brief (`record.digest_brief`): the
    record's vitals, key moments, placed chapters, open questions and the
    listed decisions, never a name. The answer is checked by code before it
    is returned (`check_digest`: marked internal, every number and time in
    the brief, short); one that fails raises `LLMInvalidOutput`, so nothing
    is saved. Doesn't need "Analyse session": without it there are fewer
    parts to the brief."""
    from livestorm_app.ai.team_digest import write_team_digest
    from livestorm_app.llm_client import LLMInvalidOutput
    from livestorm_app.record.assemble import assemble_record, chapter_marks, clock_meta, session_meta
    from livestorm_app.record.decisions import decision_lines
    from livestorm_app.record.digest_brief import DigestRejected, build_digest_brief, check_digest
    from livestorm_app.record.inputs import load_record_inputs
    from livestorm_app.record.moments import room_moments
    from livestorm_app.record.question_desk import question_desk
    from livestorm_app.record.roster import stored_roster_rows
    from livestorm_app.record.understanding import chapter_list, current_reading, located_answers

    roster_status = fetch_roster_status(session_id, ALL_ORGS) or {}
    stored_roster = fetch_session_roster(session_id, ALL_ORGS) if roster_status.get("roster") is not None else None
    inputs = load_record_inputs(
        dict(cached),
        roster_rows=stored_roster_rows(stored_roster) if stored_roster is not None else None,
        stored_labels=fetch_chat_labels(session_id, ALL_ORGS),
    )
    stored = fetch_understanding(session_id, ALL_ORGS)
    result, _state, _utterances = current_reading(stored, inputs)
    names = cached.get("transcript_speaker_names") if isinstance(cached.get("transcript_speaker_names"), dict) else {}
    chapters, _chapters_state = chapter_list(stored, inputs)
    marks = chapter_marks(inputs, chapters)
    placed = (
        [{"title": chapter["title"], "t": mark["t"]} for chapter, mark in zip(chapters, marks["marks"], strict=True)]
        if marks["placed"] else []
    )
    located, located_state = located_answers(stored, inputs)
    desk = question_desk(inputs, fetch_question_replies(session_id, ALL_ORGS), located, located_state)
    listed = _normalize_text_bundle(cached.get("decisions_bundle"), "").get("English", "").strip()
    brief = build_digest_brief(
        title=session_meta(inputs)["title"],
        length_s=clock_meta(inputs).get("endS"),
        internal=bool(inputs.roster.meta.internal),
        measures=assemble_record(inputs)["record"].get("measures") or {},
        # Still open for the host, as the Follow-up page counts them: open,
        # and covered in the talk but not confirmed (`desk.js` `filterCounts`).
        questions_open=int(desk["summary"].get("open") or 0) + int(desk["summary"].get("covered") or 0),
        moments=room_moments(inputs, result or {}, names),
        chapters=placed,
        # The open questions' words only: never who asked, never the desk's ids.
        open_questions=[q["text"] for q in desk["questions"] if q["status"] == "open"],
        decisions=decision_lines(listed) if listed else None,
        # The speakers' names the host saved never reach the model.
        hidden_names=[str(name) for name in names.values() if name],
    )
    text = write_team_digest(
        brief,
        api_key=api_key,
        ctx=_llm_call_context("team_digest", "team_digest", session_id, output_language, cached),
    )
    try:
        return check_digest(text, brief.text)
    except DigestRejected as exc:
        # The check's own words, never the model's.
        raise LLMInvalidOutput(f"The team digest {exc}. Nothing was saved.") from None


class PublicOutputLeak(RuntimeError):
    """A public output held a personal detail after redaction; nothing saved."""


def _public_redactor(session_id: str, cached: Mapping[str, Any], roster: Any = None) -> Any:
    """The session's `record.redaction.Redactor` for a public format: the
    audience's names (everyone's on an internal session), never a speaker's
    saved name, plus emails, phone numbers and links. `roster` when the
    caller already loaded the record's inputs; else read here, from the same
    people source as the record (the stored roster when there is one)."""
    from livestorm_app.record.redaction import public_redactor, saved_speaker_names

    if roster is None:
        from livestorm_app.record.inputs import load_record_inputs
        from livestorm_app.record.roster import stored_roster_rows

        roster_status = fetch_roster_status(session_id, ALL_ORGS) or {}
        stored_roster = fetch_session_roster(session_id, ALL_ORGS) if roster_status.get("roster") is not None else None
        roster = load_record_inputs(
            dict(cached),
            roster_rows=stored_roster_rows(stored_roster) if stored_roster is not None else None,
        ).roster
    return public_redactor(roster, saved_speaker_names(cached))


def _require_no_leak(redactor: Any, texts: Iterable[Any], what: str) -> None:
    """Raises `PublicOutputLeak`, naming only the kinds found, when a public
    output still holds an email, a phone number, an attendee's full name or
    a placeholder."""
    found: List[str] = []
    for text in texts:
        found += [kind for kind in redactor.leaks(text) if kind not in found]
    if found:
        raise PublicOutputLeak(f"The {what} contained {' and '.join(found)}. Nothing was saved; please try again.")


class SurpriseOffError(RuntimeError):
    """Surprise Me! is turned off for the session's organization."""


SURPRISE_OFF_MESSAGE = "Surprise me is turned off for your organization. A StormIQ admin can turn it back on."


def _require_surprise_on(organization_id: Any) -> None:
    """Raises `SurpriseOffError` when a StormIQ admin turned Surprise Me!
    off for this organization (`db.fetch_surprise_enabled`, which raises on a
    failed read: no take is written then either)."""
    if not fetch_surprise_enabled(str(organization_id or "")):
        raise SurpriseOffError(SURPRISE_OFF_MESSAGE)


class SurpriseCheckError(RuntimeError):
    """A surprise take gave a figure the session doesn't have; nothing saved."""


def _surprise_facts(session_id: str, cached: Mapping[str, Any]) -> Tuple[str, Any]:
    """What StormIQ measured, for Surprise Me! (`record.surprise_brief`):
    the vitals, the key moments with what was being said, and the placed
    chapters, as text; and the session's roster, for the redaction. Read
    from the same people source as the record. No model call."""
    from livestorm_app.record.assemble import assemble_record, chapter_marks, clock_meta, session_meta
    from livestorm_app.record.inputs import load_record_inputs
    from livestorm_app.record.moments import room_moments
    from livestorm_app.record.roster import stored_roster_rows
    from livestorm_app.record.surprise_brief import build_surprise_brief
    from livestorm_app.record.understanding import chapter_list, current_reading

    roster_status = fetch_roster_status(session_id, ALL_ORGS) or {}
    stored_roster = fetch_session_roster(session_id, ALL_ORGS) if roster_status.get("roster") is not None else None
    inputs = load_record_inputs(
        dict(cached),
        roster_rows=stored_roster_rows(stored_roster) if stored_roster is not None else None,
        stored_labels=fetch_chat_labels(session_id, ALL_ORGS),
    )
    stored = fetch_understanding(session_id, ALL_ORGS)
    result, _state, _ = current_reading(stored, inputs)
    names = cached.get("transcript_speaker_names") if isinstance(cached.get("transcript_speaker_names"), dict) else {}
    chapters, _ = chapter_list(stored, inputs)
    marks = chapter_marks(inputs, chapters)
    placed = (
        [{"title": chapter["title"], "t": mark["t"]} for chapter, mark in zip(chapters, marks["marks"], strict=True)]
        if marks["placed"] else []
    )
    brief = build_surprise_brief(
        title=session_meta(inputs)["title"],
        length_s=clock_meta(inputs).get("endS"),
        measures=assemble_record(inputs)["record"].get("measures") or {},
        moments=room_moments(inputs, result or {}, names),
        chapters=placed,
    )
    return brief, inputs.roster


def _check_surprise(markdown: str, sent: str) -> None:
    """Raises `SurpriseCheckError` when the take gives a time or a number
    that isn't in what it was sent (`record.surprise_brief.check_surprise`),
    naming only the kind of figure."""
    from livestorm_app.record.surprise_brief import SurpriseRejected, check_surprise

    try:
        check_surprise(markdown, sent)
    except SurpriseRejected as exc:
        raise SurpriseCheckError(f"The surprise take {exc}. Nothing was saved; please try again.") from None


class NoQuestionsError(RuntimeError):
    """The session has no audience question to make a FAQ from."""


def _write_faq(api_key: str, session_id: str, cached: Mapping[str, Any], output_language: str) -> str:
    """The FAQ written from the session's question desk
    (`record.faq_brief.build_faq_brief`): its questions, statuses and
    answers, without their authors. The desk lists the chat's questions and
    the talk's answers only once the session is analysed, so this raises
    `SessionNotAnalysedError` until the chat is labelled and, with a
    transcript, the session read as it is now; `NoQuestionsError` when
    there is no question."""
    from livestorm_app.ai.faq import write_faq
    from livestorm_app.db import fetch_understanding
    from livestorm_app.record.assemble import session_meta
    from livestorm_app.record.faq_brief import build_faq_brief
    from livestorm_app.record.inputs import load_record_inputs
    from livestorm_app.record.question_desk import question_desk
    from livestorm_app.record.roster import stored_roster_rows
    from livestorm_app.record.understanding import READY, STALE, located_answers

    roster_status = fetch_roster_status(session_id, ALL_ORGS) or {}
    stored_roster = fetch_session_roster(session_id, ALL_ORGS) if roster_status.get("roster") is not None else None
    inputs = load_record_inputs(
        dict(cached),
        roster_rows=stored_roster_rows(stored_roster) if stored_roster is not None else None,
        stored_labels=fetch_chat_labels(session_id, ALL_ORGS),
    )
    if (inputs.chat_labels_info or {}).get("state") != "ready":
        raise SessionNotAnalysedError('Run "Analyse session" on the Report first: the FAQ lists the chat\'s questions it finds.')
    located, located_state = located_answers(fetch_understanding(session_id, ALL_ORGS), inputs)
    if inputs.transcript is not None and located_state == STALE:
        raise SessionNotAnalysedError('The session changed since it was analysed. Run "Analyse session" again on the Report first.')
    if inputs.transcript is not None and located_state != READY:
        raise SessionNotAnalysedError('Run "Analyse session" on the Report first: the FAQ uses the answers it finds in the talk.')
    desk = question_desk(inputs, fetch_question_replies(session_id, ALL_ORGS), located, located_state)
    # The desk's rows without their authors or ids: names never reach a model.
    # What the audience wrote, the answers and the talk's passages are
    # redacted first (`record.redaction`): the FAQ is a public format.
    redactor = _public_redactor(session_id, cached, inputs.roster)
    questions = [
        {"source": q["source"], "t": q["t"], "answered_as": q["status"], "text": redactor.redact(q["text"]),
         "answer": redactor.redact(q["answer"]) or q["answer"], "reply": redactor.redact(q["reply"]) or q["reply"],
         "located": {**q["located"], "quote": redactor.redact(q["located"].get("quote"))} if q["located"] else q["located"]}
        for q in desk["questions"]
    ]
    if not questions:
        raise NoQuestionsError("This session has no audience question to make a FAQ from.")
    brief = build_faq_brief(title=session_meta(inputs)["title"], internal=bool(inputs.roster.meta.internal), questions=questions)
    markdown = write_faq(
        brief,
        output_language=OUTPUT_LANGUAGE_MAP.get(output_language, output_language),
        api_key=api_key,
        ctx=_llm_call_context("faq", "faq", session_id, output_language, cached),
    )
    _require_no_leak(redactor, [markdown], "FAQ")
    return markdown


def run_deep_analysis(api_analysis_key: str, session_id: str, output_language: str) -> Dict[str, Any]:
    cached = _require_cached_payloads(session_id, organization_id=ALL_ORGS)
    session_payload = cached.get("session_payload")
    chat_payload = cached.get("chat_payload")
    questions_payload = cached.get("questions_payload")
    transcript_payload = cached.get("transcript_payload")
    if not all(isinstance(item, dict) for item in (chat_payload, questions_payload, transcript_payload)):
        raise RuntimeError("Deep analysis requires transcript, chat, and questions.")

    chat_df = build_chat_df_from_payload(chat_payload)
    questions_df = build_questions_df_from_payload(questions_payload)
    internal_session = session_is_internal(session_payload) if isinstance(session_payload, dict) else False
    deep_bundle = _normalize_text_bundle(cached.get("deep_analysis_bundle"), str(cached.get("deep_analysis_md") or ""))
    if str(output_language or "").strip() not in ("", "English") and not deep_bundle.get("English", "").strip():
        raise RuntimeError("Generate the English deep analysis first before translating.")
    source_language, source_markdown = _get_alternate_language_text(deep_bundle, output_language)
    if source_markdown:
        deep_analysis_md = translate_markdown_with_openai(
            api_key=api_analysis_key,
            model=get_llm_model("translation"),
            source_markdown=source_markdown,
            source_language=OUTPUT_LANGUAGE_MAP.get(source_language, source_language),
            target_language=OUTPUT_LANGUAGE_MAP.get(output_language, output_language),
            max_tokens=8000,
            ctx=_llm_call_context("translation", "deep_analysis", session_id, output_language, cached),
        )
    else:
        deep_analysis_md = analyze_with_openai(
            api_key=api_analysis_key,
            model=get_llm_model("deep_analysis"),
            system_prompt=build_deep_analysis_prompt(),
            output_language=OUTPUT_LANGUAGE_MAP.get(output_language, output_language),
            selected_sources=["session", "chat", "questions", "transcript"],
            derived_stats=build_derived_stats(
                chat_df=chat_df,
                questions_df=questions_df,
                transcript_payload=transcript_payload,
                session_payload=session_payload if isinstance(session_payload, dict) else None,
                internal=internal_session,
            ),
            raw_payload=build_deep_analysis_chat_payload_for_llm(chat_payload, max_rows=0),
            questions_payload=build_deep_analysis_questions_payload_for_llm(questions_payload, max_rows=0),
            transcript_payload=build_compact_transcript_payload_for_llm(transcript_payload, max_segments=800),
            session_payload=(
                build_compact_session_payload_for_llm(session_payload, internal=internal_session)
                if isinstance(session_payload, dict)
                else None
            ),
            max_tokens=5000,
            ctx=_llm_call_context("deep_analysis", "deep_analysis", session_id, output_language, cached),
        )
    deep_analysis_md = _require_generated_text(deep_analysis_md, "deep analysis")
    merge_cached_bundle_entry(
        session_id, "deep_analysis_bundle", output_language, deep_analysis_md, provenance=run_provenance(),
        **({"deep_analysis_md": deep_analysis_md} if output_language == "English" else {}),
    )
    deep_bundle[output_language] = deep_analysis_md
    return {"markdown": deep_analysis_md, "bundle": deep_bundle}


def build_analysis_pdf(session_id: str, analysis_kind: str, language: str, *, organization_id: OrgScope) -> Dict[str, Any]:
    cached = _require_cached_payloads(session_id, organization_id=organization_id)
    language = str(language or "English").strip() or "English"
    kind = str(analysis_kind or "").strip().lower()
    is_french = language.lower() == "french"

    if kind == "overall":
        bundle = _normalize_text_bundle(cached.get("analysis_bundle"), str(cached.get("analysis_md") or ""))
        title = "Analyse de la session" if is_french else "Session analysis"
        filename = f"stormiq-overall-analysis-{language.lower()}-{session_id}.pdf"
    elif kind == "deep":
        bundle = _normalize_text_bundle(cached.get("deep_analysis_bundle"), str(cached.get("deep_analysis_md") or ""))
        title = "Analyse approfondie" if is_french else "Deep Analysis"
        filename = f"stormiq-deep-analysis-{language.lower()}-{session_id}.pdf"
    else:
        raise RuntimeError("Unknown analysis kind. Expected `overall` or `deep`.")

    markdown = str(bundle.get(language) or "").strip()
    if not markdown:
        raise RuntimeError(f"No {kind} analysis is available yet for {language}.")

    pdf_bytes = analysis_markdown_to_pdf_bytes(markdown, title=title, language=language)
    return {"filename": filename, "content": pdf_bytes}


def run_cover_image_generation(
    api_analysis_key: str,
    session_id: str,
    *,
    force_regenerate: bool = False,
) -> Dict[str, Any]:
    """Generate (or refresh) the cover image for a session.

    Input: the Professional Smart Recap markdown — must already exist
    in the cache. Output: PNG bytes persisted on session_cache
    (cover_image_bytes / cover_image_mime / cover_image_generated_at).

    Designed to be called from the arq worker as a follow-up after
    run_smart_recap completes for the Professional tone. The auto-run
    skips when a cover image is already cached; pass
    `force_regenerate=True` to override (e.g. when the user explicitly
    asks for a new cover).

    Raises RuntimeError when the recap is missing or the OpenAI call
    can't produce image bytes. The caller decides whether to surface
    or swallow; the worker logs+swallows so a failed cover doesn't
    cascade into a failed recap.
    """
    cached = _require_cached_payloads(session_id, organization_id=ALL_ORGS)
    smart_bundle = _normalize_smart_recap_bundle(cached.get("smart_recap_bundle"))
    recap_markdown = str(smart_bundle.get("professional") or "").strip()
    if not recap_markdown:
        raise RuntimeError(
            "Cover image requires a Professional Smart Recap. Run the recap first."
        )

    # Idempotency: skip when a cover already exists, unless forced.
    # fetch_cached_session keeps the BYTEA out of its SELECT (heavy);
    # has_cover_image is the IS NOT NULL projection we read instead.
    if not force_regenerate and cached.get("has_cover_image"):
        return {"status": "exists", "mime": cached.get("cover_image_mime")}

    prompt = build_cover_image_prompt(recap_markdown)
    image = generate_cover_image_with_openai(api_analysis_key, prompt=prompt)

    from datetime import datetime, timezone  # local import keeps top-of-file tidy

    upsert_cached_session(
        api_analysis_key,
        session_id,
        cover_image_bytes=image["bytes"],
        cover_image_mime=image["mime"],
        cover_image_generated_at=datetime.now(timezone.utc),
    )
    return {
        "status": "generated",
        "mime": image["mime"],
        "byteSize": len(image["bytes"]),
    }


def run_smart_recap(api_analysis_key: str, session_id: str, tone: str) -> Dict[str, Any]:
    cached = _require_cached_payloads(session_id, organization_id=ALL_ORGS)
    transcript_payload = cached.get("transcript_payload")
    if not isinstance(transcript_payload, dict):
        raise RuntimeError("Smart recap requires a transcript payload.")
    smart_bundle = _normalize_smart_recap_bundle(cached.get("smart_recap_bundle"))
    # A public format: the audience's names and contact details said on
    # stage are redacted before the call, and the recap checked after.
    # Surprise Me! also reads what StormIQ measured (proposal §10), and its
    # take may give only the times and numbers it was sent.
    speech = build_transcript_plain_text(transcript_payload)
    if tone == "surprise":
        # Checked again in the worker: the switch may have changed since the click.
        _require_surprise_on(cached.get("organization_id"))
        facts, roster = _surprise_facts(session_id, cached)
        redactor = _public_redactor(session_id, cached, roster)
        sent = redactor.redact(f"{facts}\n\n# Transcript\n\n{speech}")
    else:
        redactor = _public_redactor(session_id, cached)
        sent = redactor.redact(speech)
    markdown = analyze_with_openai(
        api_key=api_analysis_key,
        model=get_llm_model("smart_recap"),
        system_prompt=build_smart_recap_prompt(tone),
        output_language="English",
        selected_sources=["transcript"],
        derived_stats={},
        transcript_text=sent,
        max_tokens=900,
        ctx=_llm_call_context("smart_recap", "smart_recap", session_id, tone, cached),
    )
    markdown = _require_generated_text(markdown, "smart recap")
    _require_no_leak(redactor, [markdown], "smart recap")
    if tone == "surprise":
        _check_surprise(markdown, sent)
    merge_cached_bundle_entry(session_id, "smart_recap_bundle", tone, markdown, provenance=run_provenance())
    smart_bundle[tone] = markdown
    return {"markdown": markdown, "bundle": smart_bundle}


def build_smart_recap_pdf(session_id: str, tone: str, *, organization_id: OrgScope) -> Dict[str, Any]:
    cached = _require_cached_payloads(session_id, organization_id=organization_id)
    tone_key = str(tone or "").strip().lower()
    smart_bundle = _normalize_smart_recap_bundle(cached.get("smart_recap_bundle"))
    markdown = str(smart_bundle.get(tone_key) or "").strip()
    if not markdown:
        raise RuntimeError(f"No smart recap is available yet for tone `{tone_key}`.")

    title_map = {
        "professional": "Smart Recap - Professional",
        "hype": "Smart Recap - Hype",
        "surprise": "Smart Recap - Surprise",
    }
    title = title_map.get(tone_key, "Smart Recap")
    filename = f"stormiq-smart-recap-{tone_key}-{session_id}.pdf"
    pdf_bytes = analysis_markdown_to_pdf_bytes(markdown, title=title)
    return {"filename": filename, "content": pdf_bytes}


def build_content_repurposing_pdf(session_id: str, language: str, item: str, *, organization_id: OrgScope) -> Dict[str, Any]:
    cached = _require_cached_payloads(session_id, organization_id=organization_id)
    language_key = str(language or "English").strip() or "English"
    is_french = language_key.lower() == "french"
    item_key = str(item or "").strip().lower()
    all_bundles = _stored_content_bundles(cached)
    language_bundle = all_bundles.get(language_key) if isinstance(all_bundles.get(language_key), dict) else {}
    markdown = str(language_bundle.get(item_key) or "").strip()
    if not markdown:
        raise RuntimeError(f"No content repurposing output is available yet for {language_key} / {item_key}.")

    label_map = {
        "summary": "Résumé" if is_french else "Summary",
        "blog": "Article de blog" if is_french else "Blog",
        "email": "Email de suivi" if is_french else "Email",
        "social_media": "Posts réseaux sociaux" if is_french else "Social Media",
    }
    item_label = label_map.get(item_key, item_key.title())
    title = item_label
    filename = f"stormiq-content-{language_key.lower()}-{item_key}-{session_id}.pdf"
    pdf_bytes = analysis_markdown_to_pdf_bytes(markdown, title=title, language=language_key)
    return {"filename": filename, "content": pdf_bytes}


def run_content_repurposing(api_analysis_key: str, session_id: str, output_language: str) -> Dict[str, Any]:
    cached = _require_cached_payloads(session_id, organization_id=ALL_ORGS)
    transcript_payload = cached.get("transcript_payload")
    if not isinstance(transcript_payload, dict):
        raise RuntimeError("Content repurposing requires a transcript payload.")

    all_bundles = _stored_content_bundles(cached)
    if str(output_language or "").strip() not in ("", "English"):
        english_bundle = all_bundles.get("English", {}) if isinstance(all_bundles, dict) else {}
        if not any(str(v or "").strip() for v in (english_bundle or {}).values()):
            raise RuntimeError("Generate the English content first before translating.")
    source_language, source_bundle = _get_alternate_language_bundle(all_bundles, output_language)
    if source_bundle:
        bundle = translate_content_repurpose_bundle_with_openai(
            api_key=api_analysis_key,
            model=get_llm_model("translation"),
            source_bundle=source_bundle,
            source_language=OUTPUT_LANGUAGE_MAP.get(source_language, source_language),
            target_language=OUTPUT_LANGUAGE_MAP.get(output_language, output_language),
            ctx=_llm_call_context("translation", "content_repurposing", session_id, output_language, cached),
        )
    else:
        # Public formats: the audience's names and contact details said on
        # stage are redacted before the call, and every part checked after.
        redactor = _public_redactor(session_id, cached)
        bundle = generate_content_repurpose_bundle_with_openai(
            api_key=api_analysis_key,
            model=get_llm_model("content_repurposing"),
            output_language=OUTPUT_LANGUAGE_MAP.get(output_language, output_language),
            transcript_text=redactor.redact(build_transcript_plain_text(transcript_payload)),
            ctx=_llm_call_context("content_repurposing", "content_repurposing", session_id, output_language, cached),
        )
        if isinstance(bundle, dict):
            _require_no_leak(redactor, bundle.values(), "content")
    if not isinstance(bundle, dict) or not _bundle_has_all_sections(bundle):
        missing = missing_bundle_sections(bundle)
        raise RuntimeError(
            f"Content repurposing returned an incomplete result (missing: {', '.join(missing)}). Nothing was saved; please try again."
        )
    merge_cached_bundle_entry(session_id, "content_repurpose_bundle", output_language, bundle, provenance=run_provenance())
    all_bundles[output_language] = bundle
    return {"bundle": all_bundles, "currentLanguage": output_language, "current": bundle}


# ── AI flow polling ────────────────────────────────────────────────────────
#
# Each of the four AI flows (overall, deep, smart_recap, content_repurposing)
# follows the same shape:
#
#   POST .../{flow}                       enqueues a job; returns either
#                                         { jobId, jobKind, language|tone }
#                                         or — if the cache already has the
#                                         requested bundle — the full
#                                         serialised workspace.
#
#   GET  .../{flow}/job?jobId=...         polled by the frontend. Returns
#                                         the full workspace once the bundle
#                                         is in cache; otherwise the job's
#                                         status + Redis progress.
#
# Source of truth for "is this done":  session_cache (durable).
# Source of truth for "what stage":    Redis progress key (ephemeral, 30m TTL).
# Source of truth for "did it fail":   arq job state (Redis, 1h TTL by default)
#                                       — surfaces the exception message
#                                       from the worker.


# Map of kind → (session_cache field, sub-key extractor) for cache lookup.
# Extractor returns the cached body if present and non-empty, or None.
def _extract_cached_ai_body(cached: Dict[str, Any], kind: str, language: str = "", tone: str = "") -> Optional[str]:
    language = str(language or "").strip()
    tone = str(tone or "").strip().lower()

    if kind == "overall_analysis":
        bundle = _normalize_text_bundle(cached.get("analysis_bundle"), str(cached.get("analysis_md") or ""))
        return bundle.get(language) if bundle.get(language) else None
    if kind == "deep_analysis":
        bundle = _normalize_text_bundle(cached.get("deep_analysis_bundle"), str(cached.get("deep_analysis_md") or ""))
        return bundle.get(language) if bundle.get(language) else None
    if kind == "faq":
        bundle = _normalize_text_bundle(cached.get("faq_bundle"), "")
        return bundle.get(language) if bundle.get(language) else None
    if kind == "decisions":
        bundle = _normalize_text_bundle(cached.get("decisions_bundle"), "")
        return bundle.get(language) if bundle.get(language) else None
    if kind == "team_digest":
        bundle = _normalize_text_bundle(cached.get("team_digest_bundle"), "")
        return bundle.get(language) if bundle.get(language) else None
    if kind == "smart_recap":
        bundle = _normalize_smart_recap_bundle(cached.get("smart_recap_bundle"))
        return bundle.get(tone) if bundle.get(tone) else None
    if kind == "content_repurposing":
        all_bundles = _stored_content_bundles(cached)
        language_bundle = all_bundles.get(language) if isinstance(all_bundles.get(language), dict) else {}
        # Repurposing has 4 sub-bundles; treat as complete when ALL four are present.
        required = {"summary", "blog", "email", "social_media"}
        if all(str(language_bundle.get(slot) or "").strip() for slot in required):
            return "ready"  # sentinel — caller only needs truthiness
        return None
    return None


_ARQ_JOB_FUNCTION_BY_KIND: Dict[str, str] = {
    "overall_analysis": "run_overall_analysis_job",
    "deep_analysis": "run_deep_analysis_job",
    "smart_recap": "run_smart_recap_job",
    "content_repurposing": "run_content_repurposing_job",
    "faq": "run_faq_job",
    "decisions": "run_decisions_job",
    "team_digest": "run_team_digest_job",
}

# The columns `_extract_cached_ai_body` reads for each kind: job starts and
# polls read only these, and load the full row only to return a finished result.
_AI_BODY_FIELDS: Dict[str, List[str]] = {
    "overall_analysis": ["analysis_bundle", "analysis_md"],
    "deep_analysis": ["deep_analysis_bundle", "deep_analysis_md"],
    "smart_recap": ["smart_recap_bundle"],
    "content_repurposing": ["content_repurpose_bundle"],
    "faq": ["faq_bundle"],
    "decisions": ["decisions_bundle"],
    "team_digest": ["team_digest_bundle"],
}


def _load_serialized_workspace(session_id: str, organization_id: OrgScope) -> Dict[str, Any]:
    cached = fetch_cached_session("", session_id, organization_id=organization_id)
    if not isinstance(cached, dict):
        raise SessionNotFoundError()
    return _serialize_cached_session(session_id, cached)


def _start_ai_job(
    kind: str,
    session_id: str,
    *,
    language: str = "",
    tone: str = "",
    organization_id: OrgScope,
) -> Dict[str, Any]:
    """Start an AI job (or return the cached result if already complete).

    `organization_id` is the caller's read scope: a job can only be started
    on a session the caller can read.

    Returns either:
        - the full serialised workspace (cache hit), or
        - { jobId, jobKind, language?/tone? } (job enqueued).
    """
    if kind not in AI_JOB_KINDS:
        raise RuntimeError(f"Unknown AI job kind: {kind!r}")
    # Every distinct language/tone is a separate paid job: accept known values only.
    if kind == "smart_recap":
        if tone not in SMART_RECAP_TONES:
            raise ValueError(f"Unknown Smart Recap tone {tone!r}. Use one of: {', '.join(SMART_RECAP_TONES)}.")
    elif language not in OUTPUT_LANGUAGE_MAP:
        raise ValueError(f"Unsupported output language {language!r}. Use one of: {', '.join(OUTPUT_LANGUAGE_MAP)}.")

    session_id = str(session_id or "").strip()
    cached = fetch_session_fields(session_id, _AI_BODY_FIELDS[kind], organization_id=organization_id)
    if not isinstance(cached, dict):
        raise SessionNotFoundError()

    # Cache hit — return the full workspace synchronously. The user will see
    # the bundle without ever entering the polling loop.
    if _extract_cached_ai_body(cached, kind, language=language, tone=tone):
        return _load_serialized_workspace(session_id, organization_id)

    # English-first enforcement: French (and any non-English variant) must be
    # a translation of the existing English version so outputs stay consistent.
    # Reject if English hasn't been generated yet.
    if kind in ("overall_analysis", "deep_analysis", "content_repurposing", "faq", "decisions", "team_digest") and str(language or "").strip() not in ("", "English"):
        if not _extract_cached_ai_body(cached, kind, language="English"):
            raise RuntimeError("Generate the English version first before translating to another language.")

    # Dedupe: the job id is fixed per (kind, session, language/tone), so
    # double-clicks and teammates generating the same output share one
    # job. A failed or stale earlier run is cleared and re-queued.
    dimension = tone if kind == "smart_recap" else language
    job = enqueue_unique_job_sync(
        _ARQ_JOB_FUNCTION_BY_KIND[kind],
        ai_job_id(kind, session_id, dimension),
        session_id,
        dimension,
    )
    if not job:
        raise RuntimeError(
            f"{kind.replace('_', ' ').title()} queue is unavailable. "
            "Check the worker service and Redis connection."
        )

    payload: Dict[str, Any] = {"jobId": job["jobId"], "jobKind": kind, "jobStatus": job["jobStatus"]}
    if kind == "smart_recap":
        payload["tone"] = tone
    else:
        payload["language"] = language
    return payload


def _get_ai_job_data(
    kind: str,
    session_id: str,
    job_id: str,
    *,
    language: str = "",
    tone: str = "",
    organization_id: OrgScope,
    outputs_only: bool = False,
) -> Dict[str, Any]:
    """Poll an AI job on a session the caller can read.

    `job_id` (from the client) is accepted for compatibility but not used:
    the job is always this session's own `ai_job_id(kind, session, dimension)`.

    Returns:
        - once the result is saved: the full serialised workspace, or with
          `outputs_only` just `{ sessionId, outputsOnly: True, outputs }`
          (the rest of the workspace is already on the caller's screen), OR
        - { jobId, jobKind, jobStatus, progress, error } with the live status.
    """
    if kind not in AI_JOB_KINDS:
        raise RuntimeError(f"Unknown AI job kind: {kind!r}")

    session_id = str(session_id or "").strip()
    cached = fetch_session_fields(session_id, _AI_BODY_FIELDS[kind], organization_id=organization_id)
    if not isinstance(cached, dict):
        raise SessionNotFoundError()

    # Cache hit — the worker already finished and persisted.
    if _extract_cached_ai_body(cached, kind, language=language, tone=tone):
        if not outputs_only:
            return _load_serialized_workspace(session_id, organization_id)
        row = fetch_session_fields(session_id, _OUTPUT_FIELDS, organization_id=organization_id)
        if not isinstance(row, dict):
            raise SessionNotFoundError()
        return _sanitize_json_value({"sessionId": session_id, "outputsOnly": True, "outputs": _serialize_outputs(row)})

    # Pull live status from arq + progress from Redis. Both are best-effort.
    # The job id is derived here, never taken from the client: ids are
    # predictable, and a client-supplied one could name another session's job.
    own_job_id = ai_job_id(kind, session_id, tone if kind == "smart_recap" else language)
    status_payload = read_job_status_sync(own_job_id)
    progress = read_progress(kind, session_id)

    response: Dict[str, Any] = {
        "jobId": own_job_id,
        "jobKind": kind,
        "jobStatus": status_payload.get("jobStatus", "unknown"),
        "progress": progress,
    }
    if status_payload.get("error"):
        response["error"] = status_payload["error"]
    if kind == "smart_recap":
        response["tone"] = tone
    else:
        response["language"] = language
    return response


# ── Thin per-flow start/poll wrappers ──────────────────────────────────────
#
# One pair per flow. Routes call these directly; the routes themselves are
# kept thin to make the per-flow contract obvious from routes/ai.py.


# ── The session analysis (v1) ─────────────────────────────────────────────
#
# One run per session, started by the user, never on its own: it labels the
# chat (`ai/chat_labelling.label_chat`) and stores the labels
# (`db.replace_chat_labels`). Later steps of the v1 analysis join the same run.

SESSION_ANALYSIS_KIND = "session_analysis"
SESSION_ANALYSIS_FUNCTION = "run_session_analysis_job"


def session_analysis_job_id(session_id: str) -> str:
    return ai_job_id(SESSION_ANALYSIS_KIND, session_id, "")


def _chain_session_analysis(session_id: str) -> None:
    """Queue the session analysis after "Add" (or join the run in flight).
    Best effort: a queue that is down leaves the Report's "Analyse session"."""
    if not enqueue_unique_job_sync(SESSION_ANALYSIS_FUNCTION, session_analysis_job_id(session_id), session_id):
        logger.warning("Could not chain the session analysis for session_id=%s", session_id)


def start_session_analysis(session_id: str, *, organization_id: OrgScope) -> Dict[str, Any]:
    """Queue the session's analysis, or join the run already in flight.

    `organization_id` is the caller's read scope: only a session the caller
    can read is analysed. The job gets the session id only. Returns
    `{jobId, jobStatus, enqueued}`. Raises SessionNotFoundError, or
    RuntimeError when the queue is down.
    """
    session_id = str(session_id or "").strip()
    if not isinstance(fetch_session_fields(session_id, ("has_transcript",), organization_id=organization_id), dict):
        raise SessionNotFoundError()
    job = enqueue_unique_job_sync(SESSION_ANALYSIS_FUNCTION, session_analysis_job_id(session_id), session_id)
    if not job:
        raise RuntimeError("The analysis queue is unavailable. Please try again in a moment.")
    return job


def get_session_analysis_job(session_id: str, job_id: str, *, organization_id: OrgScope) -> Dict[str, Any]:
    """The analysis run's status: `{jobId, jobStatus, error?, progress}`.
    `progress` is the run's latest stage from Redis (`{stage, percent,
    started_at_ms, …}`, best effort, null when unknown); the page reads it
    only while the run is in flight. A session has
    one run id, derived from the session; the `job_id` a client sends is
    ignored, so naming another session's job reads nothing of it (as the
    other job polls do)."""
    session_id = str(session_id or "").strip()
    if not isinstance(fetch_session_fields(session_id, ("has_transcript",), organization_id=organization_id), dict):
        raise SessionNotFoundError()
    own = session_analysis_job_id(session_id)
    return {"jobId": own, **read_job_status_sync(own), "progress": read_progress(SESSION_ANALYSIS_KIND, session_id)}


def run_session_analysis(
    api_key: str, session_id: str, report: Optional[Callable[[str], None]] = None
) -> Dict[str, Any]:
    """The worker side of the analysis the user starts, in two steps:

    1. Label the chat and store the labels. Skips the model when the stored
       labels already describe this chat (same `chat_hash` and
       `LABEL_VERSION`).
    2. Read the session (the understanding job, `_read_understanding`), when
       it has a transcript.

    A failed step raises and stores nothing of its own; what earlier steps
    and runs stored is kept. Returns `{chatLabels: "labelled" | "up_to_date",
    messages, understanding: "read" | "up_to_date" | "no_transcript"}`.

    `report(stage)`, when given, is told each stage as it starts
    (`progress.SESSION_ANALYSIS_STAGE_FLOORS`: `labelling`, `reading`, only
    when the model reads the session, `saving`, `done`).
    """
    report = report or (lambda stage: None)
    # The record package is imported here, inside the worker's runner, so
    # loading api_logic never loads it (tests/test_roster.py).
    from livestorm_app.ai.chat_labelling import label_chat
    from livestorm_app.db import fetch_chat_labels, replace_chat_labels
    from livestorm_app.record.chat_labels import LABEL_VERSION, chat_hash
    from livestorm_app.record.inputs import load_record_inputs

    cached = _require_cached_payloads(session_id, organization_id=ALL_ORGS)
    events = load_record_inputs(cached).events
    chat = events[events["kind"] == "chat"] if not events.empty else events
    messages = [
        {"eid": row["eid"], "livestorm_id": row["livestorm_id"] or None, "text": row["text"], "is_team": bool(row["is_team"])}
        for row in chat.to_dict("records")
    ]
    current_hash = chat_hash(messages)
    report("labelling")
    stored = fetch_chat_labels(session_id, ALL_ORGS)
    if stored and stored.get("chat_hash") == current_hash and stored.get("label_version") == LABEL_VERSION:
        labels, labels_state = stored, "up_to_date"
    else:
        ctx = CallContext(
            job_kind="chat_labels",
            session_id=str(session_id or "").strip(),
            organization_id=str(cached.get("organization_id") or "").strip(),
            job_id=session_analysis_job_id(session_id),
        )
        result = label_chat(messages, api_key=api_key, ctx=ctx)
        replace_chat_labels(session_id, result.labels, chat_hash=result.chat_hash, model=result.model)
        labels = {"label_version": LABEL_VERSION, "chat_hash": result.chat_hash, "model": result.model, "labels": result.labels}
        labels_state = "labelled"
    understanding = _read_understanding(api_key, session_id, cached, labels, report)
    report("done")
    return {"chatLabels": labels_state, "messages": len(messages), "understanding": understanding}


def _read_understanding(
    api_key: str,
    session_id: str,
    cached: Mapping[str, Any],
    labels: Optional[Mapping[str, Any]],
    report: Callable[[str], None] = lambda stage: None,
) -> str:
    """The understanding job's step of the analysis: build the session's
    dossier (`record.understanding.build_dossier`, with the chat labels just
    stored and the stored roster), skip the model when the stored result was
    read from the same dossier at the same version, else read it
    (`ai.understanding.read_session`), check it (`check_understanding`) and
    store it whole (`db.replace_understanding`). Returns `read`,
    `up_to_date` or `no_transcript`."""
    from livestorm_app.ai import jobs
    from livestorm_app.ai.understanding import KIND, read_session
    from livestorm_app.db import fetch_understanding, replace_understanding
    from livestorm_app.record.inputs import load_record_inputs
    from livestorm_app.record.roster import stored_roster_rows
    from livestorm_app.record.understanding import UNDERSTANDING_VERSION, build_dossier, check_understanding

    roster_status = fetch_roster_status(session_id, ALL_ORGS) or {}
    stored_roster = fetch_session_roster(session_id, ALL_ORGS) if roster_status.get("roster") is not None else None
    inputs = load_record_inputs(
        dict(cached),
        roster_rows=stored_roster_rows(stored_roster) if stored_roster is not None else None,
        stored_labels=labels,
    )
    dossier, utterances, questions = build_dossier(inputs)
    if not utterances:
        return "no_transcript"
    stored = fetch_understanding(session_id, ALL_ORGS)
    if stored and stored.get("dossier_hash") == dossier.sha256 and stored.get("version") == UNDERSTANDING_VERSION:
        return "up_to_date"
    ctx = CallContext(
        job_kind=KIND,
        session_id=str(session_id or "").strip(),
        organization_id=str(cached.get("organization_id") or "").strip(),
        job_id=session_analysis_job_id(session_id),
    )
    model = jobs.resolve_model(KIND).model_id
    report("reading")
    answer = read_session(dossier, api_key=api_key, ctx=ctx)
    report("saving")
    checked = check_understanding(answer.model_dump(), utterances, questions)
    replace_understanding(session_id, checked, dossier_hash=dossier.sha256, model=model)
    return "read"


def start_overall_analysis(session_id: str, output_language: str, *, organization_id: OrgScope) -> Dict[str, Any]:
    return _start_ai_job("overall_analysis", session_id, language=output_language, organization_id=organization_id)


def get_overall_analysis_job_data(session_id: str, job_id: str, language: str, *, organization_id: OrgScope, outputs_only: bool = False) -> Dict[str, Any]:
    return _get_ai_job_data("overall_analysis", session_id, job_id, language=language, organization_id=organization_id, outputs_only=outputs_only)


def start_faq(session_id: str, output_language: str, *, organization_id: OrgScope) -> Dict[str, Any]:
    return _start_ai_job("faq", session_id, language=output_language, organization_id=organization_id)


def get_faq_job_data(session_id: str, job_id: str, language: str, *, organization_id: OrgScope, outputs_only: bool = False) -> Dict[str, Any]:
    return _get_ai_job_data("faq", session_id, job_id, language=language, organization_id=organization_id, outputs_only=outputs_only)


def start_decisions(session_id: str, output_language: str, *, organization_id: OrgScope) -> Dict[str, Any]:
    return _start_ai_job("decisions", session_id, language=output_language, organization_id=organization_id)


def get_decisions_job_data(session_id: str, job_id: str, language: str, *, organization_id: OrgScope, outputs_only: bool = False) -> Dict[str, Any]:
    return _get_ai_job_data("decisions", session_id, job_id, language=language, organization_id=organization_id, outputs_only=outputs_only)


def start_team_digest(session_id: str, output_language: str, *, organization_id: OrgScope) -> Dict[str, Any]:
    return _start_ai_job("team_digest", session_id, language=output_language, organization_id=organization_id)


def get_team_digest_job_data(session_id: str, job_id: str, language: str, *, organization_id: OrgScope, outputs_only: bool = False) -> Dict[str, Any]:
    return _get_ai_job_data("team_digest", session_id, job_id, language=language, organization_id=organization_id, outputs_only=outputs_only)


def start_deep_analysis(session_id: str, output_language: str, *, organization_id: OrgScope) -> Dict[str, Any]:
    return _start_ai_job("deep_analysis", session_id, language=output_language, organization_id=organization_id)


def get_deep_analysis_job_data(session_id: str, job_id: str, language: str, *, organization_id: OrgScope, outputs_only: bool = False) -> Dict[str, Any]:
    return _get_ai_job_data("deep_analysis", session_id, job_id, language=language, organization_id=organization_id, outputs_only=outputs_only)


def start_smart_recap(session_id: str, tone: str, *, organization_id: OrgScope) -> Dict[str, Any]:
    if tone == "surprise":
        # The session's own organization decides, as stamped on its row.
        row = fetch_session_fields(session_id, ["organization_id"], organization_id=organization_id)
        if not isinstance(row, dict):
            raise SessionNotFoundError()
        _require_surprise_on(row.get("organization_id"))
    return _start_ai_job("smart_recap", session_id, tone=tone, organization_id=organization_id)


def get_smart_recap_job_data(session_id: str, job_id: str, tone: str, *, organization_id: OrgScope, outputs_only: bool = False) -> Dict[str, Any]:
    return _get_ai_job_data("smart_recap", session_id, job_id, tone=tone, organization_id=organization_id, outputs_only=outputs_only)


def start_content_repurposing(session_id: str, output_language: str, *, organization_id: OrgScope) -> Dict[str, Any]:
    return _start_ai_job("content_repurposing", session_id, language=output_language, organization_id=organization_id)


def get_content_repurposing_job_data(session_id: str, job_id: str, language: str, *, organization_id: OrgScope, outputs_only: bool = False) -> Dict[str, Any]:
    return _get_ai_job_data("content_repurposing", session_id, job_id, language=language, organization_id=organization_id, outputs_only=outputs_only)


def format_service_error(exc: Exception, resource_label: str) -> Dict[str, Any]:
    if isinstance(exc, requests.HTTPError):
        return {
            "message": format_livestorm_http_error(exc, resource_label) if resource_label in {"Event sessions", "Session data"} else format_generic_http_error(exc, resource_label),
            "details": build_http_error_debug_details(exc, resource_label),
        }
    if isinstance(exc, requests.RequestException):
        return {
            "message": f"{resource_label} network error: {exc}",
            "details": build_request_exception_debug_details(exc, resource_label),
        }
    return {"message": str(exc), "details": {"resource": resource_label, "exception_type": type(exc).__name__}}
