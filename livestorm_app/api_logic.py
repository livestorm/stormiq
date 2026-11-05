from __future__ import annotations

import json
import logging
import math
import threading
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests

from livestorm_app.config import OUTPUT_LANGUAGE_MAP, SMART_RECAP_TONES
from livestorm_app.llm_client import get_llm_model
from livestorm_app.db import (
    ALL_ORGS,
    OrgScope,
    create_transcript_job,
    fetch_cached_session,
    fetch_session_fields,
    get_transcript_job_for_session,
    list_workspace_sessions,
    merge_cached_bundle_entry,
    update_transcript_job_status,
    upsert_cached_session,
)
from livestorm_app.progress import read_progress
from livestorm_app.queue import (
    AI_JOB_KINDS,
    ai_job_id,
    enqueue_unique_job_sync,
    read_job_status_sync,
    transcription_job_id,
)
from livestorm_app.ai.cover_image import generate_cover_image_with_openai
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
    build_compact_chat_payload_for_llm,
    build_compact_questions_payload_for_llm,
    build_compact_transcript_payload_for_llm,
    build_deep_analysis_chat_payload_for_llm,
    build_deep_analysis_questions_payload_for_llm,
    build_derived_stats,
)
from livestorm_app.ai.prompts import (
    build_analysis_prompt,
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
    fetch_event_past_sessions,
    fetch_workspace_events_page,
    fetch_session_details,
    format_generic_http_error,
    format_livestorm_http_error,
)
from livestorm_app.livestorm.payloads import (
    build_event_session_options,
    build_workspace_event_options,
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
    "content_repurpose_bundle", "smart_recap_bundle",
]


def _serialize_outputs(cached_session: Dict[str, Any]) -> Dict[str, Any]:
    """The `outputs` part of a workspace: every AI result of the session."""
    return {
        "analysisBundle": _normalize_text_bundle(cached_session.get("analysis_bundle"), str(cached_session.get("analysis_md") or "")),
        "deepAnalysisBundle": _normalize_text_bundle(cached_session.get("deep_analysis_bundle"), str(cached_session.get("deep_analysis_md") or "")),
        "contentRepurposeBundle": _stored_content_bundles(cached_session),
        "smartRecapBundle": _normalize_smart_recap_bundle(cached_session.get("smart_recap_bundle")),
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
    session_overview = build_session_overview_data(session_payload) if isinstance(session_payload, dict) else {}
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


def fetch_event_sessions(api_key: str, event_id: str) -> Dict[str, Any]:
    payload = fetch_event_past_sessions(api_key, event_id.strip())
    return {
        "eventId": event_id.strip(),
        "pagesFetched": payload.get("pages_fetched", 1),
        "options": build_event_session_options(payload),
        "payload": payload,
    }


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
    created_by_user_id: str = "",
    created_by_email: str = "",
    created_by_name: str = "",
) -> Dict[str, Any]:
    """Fetch session + chat + questions, then start the transcript job.

    Returns the full workspace when the transcript is already cached;
    otherwise the base workspace plus `transcriptJob: {jobId, jobStatus}`
    to poll. Transcription always runs in the worker, never inside a web
    request. Scope args as in `fetch_session_base_data`.
    """
    identity = {
        "read_scope": read_scope,
        "organization_id": organization_id,
        "created_by_user_id": created_by_user_id,
        "created_by_email": created_by_email,
        "created_by_name": created_by_name,
    }
    base = fetch_session_base_data(api_key, session_id, force_refresh, **identity)
    transcript = fetch_session_transcript_data(
        api_key, session_id, force_refresh, job_connection_id=job_connection_id, **identity,
    )
    if "sessionId" in transcript:
        return transcript
    return {**base, "transcriptJob": transcript}


def fetch_session_base_data(
    api_key: str,
    session_id: str,
    force_refresh: bool = False,
    *,
    read_scope: OrgScope,
    organization_id: str = "",
    created_by_user_id: str = "",
    created_by_email: str = "",
    created_by_name: str = "",
) -> Dict[str, Any]:
    """Fetch session + chat + questions, cache-first.

    `read_scope` governs cache lookups (what the caller may read).
    `organization_id` is the caller's own org, stamped on newly created rows.
    """
    session_id = str(session_id or "").strip()
    cached = (
        None if force_refresh
        else fetch_cached_session(api_key, session_id, organization_id=read_scope)
    )
    if isinstance(cached, dict):
        has_base_payloads = all(isinstance(cached.get(key), dict) for key in ("session_payload", "chat_payload", "questions_payload"))
        if has_base_payloads:
            return _serialize_cached_session(session_id, cached)

    session_payload = fetch_session_details(api_key, session_id)
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
    return _serialize_cached_session(session_id, cached)


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
) -> Dict[str, Any]:
    """Start a transcription job (or attach to the active one).

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
    cached = _require_cached_payloads(session_id, organization_id=ALL_ORGS)
    session_payload = cached.get("session_payload")
    chat_payload = cached.get("chat_payload")
    questions_payload = cached.get("questions_payload")
    transcript_payload = cached.get("transcript_payload")
    if not isinstance(transcript_payload, dict):
        raise RuntimeError("Overall analysis requires a transcript payload.")

    chat_df = build_chat_df_from_payload(chat_payload) if isinstance(chat_payload, dict) else pd.DataFrame()
    questions_df = build_questions_df_from_payload(questions_payload) if isinstance(questions_payload, dict) else pd.DataFrame()
    internal_session = session_is_internal(session_payload) if isinstance(session_payload, dict) else False
    selected_sources = ["transcript"]
    if isinstance(chat_payload, dict) and isinstance(questions_payload, dict):
        selected_sources.extend(["chat", "questions"])

    analysis_bundle = _normalize_text_bundle(cached.get("analysis_bundle"), str(cached.get("analysis_md") or ""))
    if str(output_language or "").strip() not in ("", "English") and not analysis_bundle.get("English", "").strip():
        raise RuntimeError("Generate the English analysis first before translating.")
    source_language, source_markdown = _get_alternate_language_text(analysis_bundle, output_language)
    if source_markdown:
        analysis_md = translate_markdown_with_openai(
            api_key=api_analysis_key,
            model=get_llm_model("default"),
            source_markdown=source_markdown,
            source_language=OUTPUT_LANGUAGE_MAP.get(source_language, source_language),
            target_language=OUTPUT_LANGUAGE_MAP.get(output_language, output_language),
        )
    else:
        analysis_md = analyze_with_openai(
            api_key=api_analysis_key,
            model=get_llm_model("default"),
            system_prompt=build_analysis_prompt(selected_sources),
            output_language=OUTPUT_LANGUAGE_MAP.get(output_language, output_language),
            selected_sources=selected_sources,
            derived_stats=build_derived_stats(
                chat_df=chat_df if not chat_df.empty else None,
                questions_df=questions_df if not questions_df.empty else None,
                transcript_payload=transcript_payload,
                session_payload=session_payload if isinstance(session_payload, dict) else None,
                internal=internal_session,
            ),
            raw_payload=build_compact_chat_payload_for_llm(chat_df) if not chat_df.empty else None,
            questions_payload=build_compact_questions_payload_for_llm(questions_df) if not questions_df.empty else None,
            transcript_text=build_transcript_plain_text(transcript_payload),
            session_payload=(
                build_compact_session_payload_for_llm(session_payload, internal=internal_session)
                if isinstance(session_payload, dict)
                else None
            ),
        )

    analysis_md = _require_generated_text(analysis_md, "overall analysis")
    merge_cached_bundle_entry(
        session_id, "analysis_bundle", output_language, analysis_md,
        **({"analysis_md": analysis_md} if output_language == "English" else {}),
    )
    analysis_bundle[output_language] = analysis_md
    return {"markdown": analysis_md, "bundle": analysis_bundle}


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
            model=get_llm_model("default"),
            source_markdown=source_markdown,
            source_language=OUTPUT_LANGUAGE_MAP.get(source_language, source_language),
            target_language=OUTPUT_LANGUAGE_MAP.get(output_language, output_language),
            max_tokens=8000,
        )
    else:
        deep_analysis_md = analyze_with_openai(
            api_key=api_analysis_key,
            model=get_llm_model("deep"),
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
        )
    deep_analysis_md = _require_generated_text(deep_analysis_md, "deep analysis")
    merge_cached_bundle_entry(
        session_id, "deep_analysis_bundle", output_language, deep_analysis_md,
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
        title = "Analyse globale" if is_french else "Overall Analysis"
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
    markdown = analyze_with_openai(
        api_key=api_analysis_key,
        model=get_llm_model("smart_recap"),
        system_prompt=build_smart_recap_prompt(tone),
        output_language="English",
        selected_sources=["transcript"],
        derived_stats={},
        transcript_text=build_transcript_plain_text(transcript_payload),
        max_tokens=900,
    )
    markdown = _require_generated_text(markdown, "smart recap")
    merge_cached_bundle_entry(session_id, "smart_recap_bundle", tone, markdown)
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
            model=get_llm_model("default"),
            source_bundle=source_bundle,
            source_language=OUTPUT_LANGUAGE_MAP.get(source_language, source_language),
            target_language=OUTPUT_LANGUAGE_MAP.get(output_language, output_language),
        )
    else:
        bundle = generate_content_repurpose_bundle_with_openai(
            api_key=api_analysis_key,
            model=get_llm_model("default"),
            output_language=OUTPUT_LANGUAGE_MAP.get(output_language, output_language),
            transcript_text=build_transcript_plain_text(transcript_payload),
        )
    if not isinstance(bundle, dict) or not _bundle_has_all_sections(bundle):
        missing = missing_bundle_sections(bundle)
        raise RuntimeError(
            f"Content repurposing returned an incomplete result (missing: {', '.join(missing)}). Nothing was saved; please try again."
        )
    merge_cached_bundle_entry(session_id, "content_repurpose_bundle", output_language, bundle)
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
}

# The columns `_extract_cached_ai_body` reads for each kind: job starts and
# polls read only these, and load the full row only to return a finished result.
_AI_BODY_FIELDS: Dict[str, List[str]] = {
    "overall_analysis": ["analysis_bundle", "analysis_md"],
    "deep_analysis": ["deep_analysis_bundle", "deep_analysis_md"],
    "smart_recap": ["smart_recap_bundle"],
    "content_repurposing": ["content_repurpose_bundle"],
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
    if kind in ("overall_analysis", "deep_analysis", "content_repurposing") and str(language or "").strip() not in ("", "English"):
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


def start_overall_analysis(session_id: str, output_language: str, *, organization_id: OrgScope) -> Dict[str, Any]:
    return _start_ai_job("overall_analysis", session_id, language=output_language, organization_id=organization_id)


def get_overall_analysis_job_data(session_id: str, job_id: str, language: str, *, organization_id: OrgScope, outputs_only: bool = False) -> Dict[str, Any]:
    return _get_ai_job_data("overall_analysis", session_id, job_id, language=language, organization_id=organization_id, outputs_only=outputs_only)


def start_deep_analysis(session_id: str, output_language: str, *, organization_id: OrgScope) -> Dict[str, Any]:
    return _start_ai_job("deep_analysis", session_id, language=output_language, organization_id=organization_id)


def get_deep_analysis_job_data(session_id: str, job_id: str, language: str, *, organization_id: OrgScope, outputs_only: bool = False) -> Dict[str, Any]:
    return _get_ai_job_data("deep_analysis", session_id, job_id, language=language, organization_id=organization_id, outputs_only=outputs_only)


def start_smart_recap(session_id: str, tone: str, *, organization_id: OrgScope) -> Dict[str, Any]:
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
