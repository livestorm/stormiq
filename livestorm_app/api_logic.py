from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests

from livestorm_app.config import DEFAULT_OPENAI_MODEL, DEEP_ANALYSIS_OPENAI_MODEL, OUTPUT_LANGUAGE_MAP, SMART_RECAP_OPENAI_MODEL
from livestorm_app.db import fetch_cached_session, upsert_cached_session
from livestorm_app.services import (
    analyze_with_openai,
    analysis_markdown_to_pdf_bytes,
    build_analysis_prompt,
    build_chat_df_from_payload,
    build_compact_chat_payload_for_llm,
    build_compact_questions_payload_for_llm,
    build_compact_transcript_payload_for_llm,
    build_content_repurpose_bundle_prompt,
    build_cross_source_insights,
    build_deep_analysis_chat_payload_for_llm,
    build_deep_analysis_prompt,
    build_deep_analysis_questions_payload_for_llm,
    build_derived_stats,
    build_event_session_options,
    build_workspace_event_options,
    build_questions_df_from_payload,
    build_request_exception_debug_details,
    build_http_error_debug_details,
    build_smart_recap_prompt,
    build_transcript_display_text,
    build_transcript_insights,
    build_transcript_plain_text,
    fetch_chat_and_questions_bundle,
    fetch_event_past_sessions,
    fetch_workspace_events_page,
    fetch_session_details,
    format_generic_http_error,
    format_livestorm_http_error,
    generate_content_repurpose_bundle_with_openai,
    translate_content_repurpose_bundle_with_openai,
    translate_markdown_with_openai,
)
from livestorm_app.session_overview import build_compact_session_payload_for_llm, build_session_overview_data
from livestorm_app.transcript_client import fetch_session_transcript


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


def _serialize_cached_session(session_id: str, cached_session: Dict[str, Any]) -> Dict[str, Any]:
    session_payload = cached_session.get("session_payload")
    chat_payload = cached_session.get("chat_payload")
    questions_payload = cached_session.get("questions_payload")
    transcript_payload = cached_session.get("transcript_payload")
    speaker_names = cached_session.get("transcript_speaker_names") if isinstance(cached_session.get("transcript_speaker_names"), dict) else {}

    chat_df = build_chat_df_from_payload(chat_payload) if isinstance(chat_payload, dict) else pd.DataFrame()
    questions_df = build_questions_df_from_payload(questions_payload) if isinstance(questions_payload, dict) else pd.DataFrame()
    transcript_text = build_transcript_display_text(transcript_payload) if isinstance(transcript_payload, dict) else ""
    transcript_plain_text = build_transcript_plain_text(transcript_payload) if isinstance(transcript_payload, dict) else ""
    transcript_insights = build_transcript_insights(transcript_payload) if isinstance(transcript_payload, dict) else {}
    session_overview = build_session_overview_data(session_payload) if isinstance(session_payload, dict) else {}
    derived_stats = build_derived_stats(
        chat_df=chat_df if not chat_df.empty else None,
        questions_df=questions_df if not questions_df.empty else None,
        transcript_payload=transcript_payload if isinstance(transcript_payload, dict) else None,
        session_payload=session_payload if isinstance(session_payload, dict) else None,
    )
    cross_source = build_cross_source_insights(
        chat_df=chat_df if not chat_df.empty else None,
        questions_df=questions_df if not questions_df.empty else None,
        transcript_payload=transcript_payload if isinstance(transcript_payload, dict) else None,
    )

    serialized = {
        "sessionId": session_id,
        "updatedAt": cached_session.get("updated_at"),
        "speakerNames": speaker_names,
        "payloads": {
            "session": session_payload,
            "chat": chat_payload,
            "questions": questions_payload,
            "transcript": transcript_payload,
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
            "transcriptKeyMoments": _df_records(transcript_insights.get("key_moments_df")),
            "transcriptNumbers": _df_records(transcript_insights.get("numbers_df")),
            "transcriptSpeakers": _df_records(transcript_insights.get("speaker_df")),
            "transcriptSpeakerTurns": _df_records(transcript_insights.get("speaker_turns_df")),
            "transcriptPace": _df_records(transcript_insights.get("pace_df")),
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
            "transcriptPlain": transcript_plain_text,
        },
        "outputs": {
            "analysisBundle": _normalize_text_bundle(cached_session.get("analysis_bundle"), str(cached_session.get("analysis_md") or "")),
            "deepAnalysisBundle": _normalize_text_bundle(cached_session.get("deep_analysis_bundle"), str(cached_session.get("deep_analysis_md") or "")),
            "contentRepurposeBundle": cached_session.get("content_repurpose_bundle") if isinstance(cached_session.get("content_repurpose_bundle"), dict) else {},
            "smartRecapBundle": _normalize_smart_recap_bundle(cached_session.get("smart_recap_bundle")),
        },
    }
    return _sanitize_json_value(serialized)


def get_cached_workspace(session_id: str) -> Optional[Dict[str, Any]]:
    cached = fetch_cached_session("", session_id)
    if not isinstance(cached, dict):
        return None
    return _serialize_cached_session(session_id, cached)


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


def fetch_all_session_data(api_key: str, transcript_api_key: str, session_id: str, force_refresh: bool = False) -> Dict[str, Any]:
    session_id = str(session_id or "").strip()
    cached = None if force_refresh else fetch_cached_session(api_key, session_id)
    if isinstance(cached, dict):
        has_all_payloads = all(isinstance(cached.get(key), dict) for key in ("session_payload", "chat_payload", "questions_payload", "transcript_payload"))
        if has_all_payloads:
            return _serialize_cached_session(session_id, cached)

    transcript_api_key = str(transcript_api_key or "").strip()
    if not transcript_api_key:
        raise RuntimeError("Gladia API key is not configured on the server. Set GLADIA_KEY before fetching uncached sessions.")

    session_payload = fetch_session_details(api_key, session_id)
    chat_bundle = fetch_chat_and_questions_bundle(api_key, session_id)
    transcript_payload = fetch_session_transcript(
        transcript_api_key,
        session_id,
        livestorm_api_key=api_key,
    )
    upsert_cached_session(
        api_key,
        session_id,
        session_payload=session_payload,
        chat_payload=chat_bundle.get("chat_payload"),
        questions_payload=chat_bundle.get("questions_payload"),
        transcript_payload=transcript_payload,
    )
    cached = fetch_cached_session(api_key, session_id)
    if not isinstance(cached, dict):
        raise RuntimeError("Fetched session data but failed to reload it from the cache.")
    return _serialize_cached_session(session_id, cached)


def fetch_session_base_data(api_key: str, session_id: str, force_refresh: bool = False) -> Dict[str, Any]:
    session_id = str(session_id or "").strip()
    cached = None if force_refresh else fetch_cached_session(api_key, session_id)
    if isinstance(cached, dict):
        has_base_payloads = all(isinstance(cached.get(key), dict) for key in ("session_payload", "chat_payload", "questions_payload"))
        if has_base_payloads:
            return _serialize_cached_session(session_id, cached)

    session_payload = fetch_session_details(api_key, session_id)
    chat_bundle = fetch_chat_and_questions_bundle(api_key, session_id)
    upsert_cached_session(
        api_key,
        session_id,
        session_payload=session_payload,
        chat_payload=chat_bundle.get("chat_payload"),
        questions_payload=chat_bundle.get("questions_payload"),
    )
    cached = fetch_cached_session(api_key, session_id)
    if not isinstance(cached, dict):
        raise RuntimeError("Fetched session overview/chat data but failed to reload it from the cache.")
    return _serialize_cached_session(session_id, cached)


def fetch_session_transcript_data(api_key: str, transcript_api_key: str, session_id: str, force_refresh: bool = False) -> Dict[str, Any]:
    session_id = str(session_id or "").strip()
    cached = None if force_refresh else fetch_cached_session(api_key, session_id)
    if isinstance(cached, dict) and isinstance(cached.get("transcript_payload"), dict):
        return _serialize_cached_session(session_id, cached)

    transcript_api_key = str(transcript_api_key or "").strip()
    if not transcript_api_key:
        raise RuntimeError("Gladia API key is not configured on the server. Set GLADIA_KEY before fetching uncached sessions.")

    transcript_payload = fetch_session_transcript(
        transcript_api_key,
        session_id,
        livestorm_api_key=api_key,
    )
    upsert_cached_session(
        api_key,
        session_id,
        transcript_payload=transcript_payload,
    )
    cached = fetch_cached_session(api_key, session_id)
    if not isinstance(cached, dict):
        raise RuntimeError("Fetched transcript data but failed to reload it from the cache.")
    return _serialize_cached_session(session_id, cached)


def save_speaker_labels(api_key: str, session_id: str, speaker_names: Dict[str, str]) -> Dict[str, Any]:
    normalized = {
        str(speaker): str(label).strip()
        for speaker, label in (speaker_names or {}).items()
        if str(label).strip()
    }
    upsert_cached_session(api_key, session_id, transcript_speaker_names=normalized)
    cached = fetch_cached_session(api_key, session_id)
    if not isinstance(cached, dict):
        raise RuntimeError("Speaker labels were saved, but the updated session could not be reloaded.")
    return _serialize_cached_session(session_id, cached)


def _require_cached_payloads(session_id: str) -> Dict[str, Any]:
    cached = fetch_cached_session("", session_id)
    if not isinstance(cached, dict):
        raise RuntimeError("No cached session was found. Fetch the session data first.")
    return cached


def run_overall_analysis(api_analysis_key: str, session_id: str, output_language: str) -> Dict[str, Any]:
    cached = _require_cached_payloads(session_id)
    session_payload = cached.get("session_payload")
    chat_payload = cached.get("chat_payload")
    questions_payload = cached.get("questions_payload")
    transcript_payload = cached.get("transcript_payload")
    if not isinstance(transcript_payload, dict):
        raise RuntimeError("Overall analysis requires a transcript payload.")

    chat_df = build_chat_df_from_payload(chat_payload) if isinstance(chat_payload, dict) else pd.DataFrame()
    questions_df = build_questions_df_from_payload(questions_payload) if isinstance(questions_payload, dict) else pd.DataFrame()
    selected_sources = ["transcript"]
    if isinstance(chat_payload, dict) and isinstance(questions_payload, dict):
        selected_sources.extend(["chat", "questions"])

    analysis_bundle = _normalize_text_bundle(cached.get("analysis_bundle"), str(cached.get("analysis_md") or ""))
    source_language, source_markdown = _get_alternate_language_text(analysis_bundle, output_language)
    if source_markdown:
        analysis_md = translate_markdown_with_openai(
            api_key=api_analysis_key,
            model=DEFAULT_OPENAI_MODEL,
            source_markdown=source_markdown,
            source_language=OUTPUT_LANGUAGE_MAP.get(source_language, source_language),
            target_language=OUTPUT_LANGUAGE_MAP.get(output_language, output_language),
        )
    else:
        analysis_md = analyze_with_openai(
            api_key=api_analysis_key,
            model=DEFAULT_OPENAI_MODEL,
            system_prompt=build_analysis_prompt(selected_sources),
            output_language=OUTPUT_LANGUAGE_MAP.get(output_language, output_language),
            selected_sources=selected_sources,
            derived_stats=build_derived_stats(
                chat_df=chat_df if not chat_df.empty else None,
                questions_df=questions_df if not questions_df.empty else None,
                transcript_payload=transcript_payload,
                session_payload=session_payload if isinstance(session_payload, dict) else None,
            ),
            raw_payload=build_compact_chat_payload_for_llm(chat_df) if not chat_df.empty else None,
            questions_payload=build_compact_questions_payload_for_llm(questions_df) if not questions_df.empty else None,
            transcript_text=build_transcript_plain_text(transcript_payload),
            session_payload=build_compact_session_payload_for_llm(session_payload) if isinstance(session_payload, dict) else None,
        )

    if str(analysis_md or "").strip():
        analysis_bundle[output_language] = str(analysis_md).strip()
        upsert_cached_session(api_analysis_key, session_id, analysis_md=analysis_md, analysis_bundle=analysis_bundle)
    return {"markdown": str(analysis_md or "").strip(), "bundle": analysis_bundle}


def run_deep_analysis(api_analysis_key: str, session_id: str, output_language: str) -> Dict[str, Any]:
    cached = _require_cached_payloads(session_id)
    session_payload = cached.get("session_payload")
    chat_payload = cached.get("chat_payload")
    questions_payload = cached.get("questions_payload")
    transcript_payload = cached.get("transcript_payload")
    if not all(isinstance(item, dict) for item in (chat_payload, questions_payload, transcript_payload)):
        raise RuntimeError("Deep analysis requires transcript, chat, and questions.")

    chat_df = build_chat_df_from_payload(chat_payload)
    questions_df = build_questions_df_from_payload(questions_payload)
    deep_bundle = _normalize_text_bundle(cached.get("deep_analysis_bundle"), str(cached.get("deep_analysis_md") or ""))
    source_language, source_markdown = _get_alternate_language_text(deep_bundle, output_language)
    if source_markdown:
        deep_analysis_md = translate_markdown_with_openai(
            api_key=api_analysis_key,
            model=DEFAULT_OPENAI_MODEL,
            source_markdown=source_markdown,
            source_language=OUTPUT_LANGUAGE_MAP.get(source_language, source_language),
            target_language=OUTPUT_LANGUAGE_MAP.get(output_language, output_language),
            max_tokens=8000,
        )
    else:
        deep_analysis_md = analyze_with_openai(
            api_key=api_analysis_key,
            model=DEEP_ANALYSIS_OPENAI_MODEL,
            system_prompt=build_deep_analysis_prompt(),
            output_language=OUTPUT_LANGUAGE_MAP.get(output_language, output_language),
            selected_sources=["session", "chat", "questions", "transcript"],
            derived_stats=build_derived_stats(
                chat_df=chat_df,
                questions_df=questions_df,
                transcript_payload=transcript_payload,
                session_payload=session_payload if isinstance(session_payload, dict) else None,
            ),
            raw_payload=build_deep_analysis_chat_payload_for_llm(chat_payload, max_rows=0),
            questions_payload=build_deep_analysis_questions_payload_for_llm(questions_payload, max_rows=0),
            transcript_payload=build_compact_transcript_payload_for_llm(transcript_payload, max_segments=0),
            session_payload=build_compact_session_payload_for_llm(session_payload) if isinstance(session_payload, dict) else None,
            max_tokens=5000,
        )
    if str(deep_analysis_md or "").strip():
        deep_bundle[output_language] = str(deep_analysis_md).strip()
        upsert_cached_session(api_analysis_key, session_id, deep_analysis_md=deep_analysis_md, deep_analysis_bundle=deep_bundle)
    return {"markdown": str(deep_analysis_md or "").strip(), "bundle": deep_bundle}


def build_analysis_pdf(session_id: str, analysis_kind: str, language: str) -> Dict[str, Any]:
    cached = _require_cached_payloads(session_id)
    language = str(language or "English").strip() or "English"
    kind = str(analysis_kind or "").strip().lower()
    is_french = language.lower() == "french"

    if kind == "overall":
        bundle = _normalize_text_bundle(cached.get("analysis_bundle"), str(cached.get("analysis_md") or ""))
        title = "Analyse globale" if is_french else "Overall Analysis"
        filename = f"livestorm-overall-analysis-{language.lower()}-{session_id}.pdf"
    elif kind == "deep":
        bundle = _normalize_text_bundle(cached.get("deep_analysis_bundle"), str(cached.get("deep_analysis_md") or ""))
        title = "Analyse approfondie" if is_french else "Deep Analysis"
        filename = f"livestorm-deep-analysis-{language.lower()}-{session_id}.pdf"
    else:
        raise RuntimeError("Unknown analysis kind. Expected `overall` or `deep`.")

    markdown = str(bundle.get(language) or "").strip()
    if not markdown:
        raise RuntimeError(f"No {kind} analysis is available yet for {language}.")

    pdf_bytes = analysis_markdown_to_pdf_bytes(markdown, title=title)
    return {"filename": filename, "content": pdf_bytes}


def run_smart_recap(api_analysis_key: str, session_id: str, tone: str) -> Dict[str, Any]:
    cached = _require_cached_payloads(session_id)
    transcript_payload = cached.get("transcript_payload")
    if not isinstance(transcript_payload, dict):
        raise RuntimeError("Smart recap requires a transcript payload.")
    smart_bundle = _normalize_smart_recap_bundle(cached.get("smart_recap_bundle"))
    markdown = analyze_with_openai(
        api_key=api_analysis_key,
        model=SMART_RECAP_OPENAI_MODEL,
        system_prompt=build_smart_recap_prompt(tone),
        output_language="English",
        selected_sources=["transcript"],
        derived_stats={},
        transcript_text=build_transcript_plain_text(transcript_payload),
        max_tokens=900,
    )
    if str(markdown or "").strip():
        smart_bundle[tone] = str(markdown).strip()
        upsert_cached_session(api_analysis_key, session_id, smart_recap_bundle=smart_bundle)
    return {"markdown": str(markdown or "").strip(), "bundle": smart_bundle}


def build_smart_recap_pdf(session_id: str, tone: str) -> Dict[str, Any]:
    cached = _require_cached_payloads(session_id)
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
    filename = f"livestorm-smart-recap-{tone_key}-{session_id}.pdf"
    pdf_bytes = analysis_markdown_to_pdf_bytes(markdown, title=title)
    return {"filename": filename, "content": pdf_bytes}


def build_content_repurposing_pdf(session_id: str, language: str, item: str) -> Dict[str, Any]:
    cached = _require_cached_payloads(session_id)
    language_key = str(language or "English").strip() or "English"
    is_french = language_key.lower() == "french"
    item_key = str(item or "").strip().lower()
    all_bundles = cached.get("content_repurpose_bundle") if isinstance(cached.get("content_repurpose_bundle"), dict) else {}
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
    filename = f"livestorm-content-{language_key.lower()}-{item_key}-{session_id}.pdf"
    pdf_bytes = analysis_markdown_to_pdf_bytes(markdown, title=title)
    return {"filename": filename, "content": pdf_bytes}


def run_content_repurposing(api_analysis_key: str, session_id: str, output_language: str) -> Dict[str, Any]:
    cached = _require_cached_payloads(session_id)
    transcript_payload = cached.get("transcript_payload")
    if not isinstance(transcript_payload, dict):
        raise RuntimeError("Content repurposing requires a transcript payload.")

    all_bundles = cached.get("content_repurpose_bundle") if isinstance(cached.get("content_repurpose_bundle"), dict) else {}
    source_language, source_bundle = _get_alternate_language_bundle(all_bundles, output_language)
    if source_bundle:
        bundle = translate_content_repurpose_bundle_with_openai(
            api_key=api_analysis_key,
            model=DEFAULT_OPENAI_MODEL,
            source_bundle=source_bundle,
            source_language=OUTPUT_LANGUAGE_MAP.get(source_language, source_language),
            target_language=OUTPUT_LANGUAGE_MAP.get(output_language, output_language),
        )
    else:
        bundle = generate_content_repurpose_bundle_with_openai(
            api_key=api_analysis_key,
            model=DEFAULT_OPENAI_MODEL,
            output_language=OUTPUT_LANGUAGE_MAP.get(output_language, output_language),
            transcript_text=build_transcript_plain_text(transcript_payload),
        )
    if isinstance(bundle, dict) and any(str(value or "").strip() for value in bundle.values()):
        all_bundles[output_language] = bundle
        upsert_cached_session(api_analysis_key, session_id, content_repurpose_bundle=all_bundles)
    return {"bundle": all_bundles, "currentLanguage": output_language, "current": all_bundles.get(output_language, {})}


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
