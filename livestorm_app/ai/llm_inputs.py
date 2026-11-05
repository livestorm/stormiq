"""What the LLM is sent: derived stats and compact, size-bounded versions of the session data."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from livestorm_app.analytics.audience import (
    TEAM_MESSAGE_FLAGS,
    build_question_stats,
    clean_table_headers,
    flatten_message,
    flatten_question,
    split_audience_chat,
)
from livestorm_app.analytics.session_overview import build_session_stats, session_is_internal
from livestorm_app.analytics.transcript import (
    _coerce_seconds,
    _extract_sentence_items,
    _extract_speaker_value,
    _extract_transcript_object,
    _extract_transcript_segments,
    _get_segment_end_value,
    _get_segment_start_value,
    build_transcript_stats,
)
from livestorm_app.livestorm.payloads import (
    extract_included_people,
    extract_messages,
    extract_questions,
)


def build_derived_stats(
    chat_df: Optional[pd.DataFrame] = None,
    questions_df: Optional[pd.DataFrame] = None,
    transcript_payload: Optional[Dict[str, Any]] = None,
    session_payload: Optional[Dict[str, Any]] = None,
    *,
    internal: Optional[bool] = None,
    session_chat_df: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    """`internal` is the session's `audience.is_internal_session` decision;
    when it is None it is decided from `session_payload` (False without one).

    The session summary counts chat messages from `session_chat_df`, or
    from `chat_df` when it is None (`session_overview.build_session_stats`).
    Pass it when `chat_df` is left out although a chat is cached: an empty
    cached chat counts 0, not the people's `messages_count`."""
    if internal is None:
        internal = session_is_internal(session_payload) if isinstance(session_payload, dict) else False
    stats: Dict[str, Any] = {}
    if internal:
        # Tells the model why team messages count as audience activity.
        stats["internal_session"] = True

    if isinstance(chat_df, pd.DataFrame):
        # Engagement stats are the audience's: team and guest-speaker
        # messages are counted apart, not as audience activity (except on
        # an internal session, where everyone counts).
        audience_df, excluded_team_messages = split_audience_chat(chat_df, internal=internal)
        unique_authors = 0
        if "author_id" in audience_df.columns:
            author_series = audience_df["author_id"].fillna("").astype(str).str.strip()
            unique_authors = int(author_series[author_series != ""].nunique())
        chat_stats: Dict[str, Any] = {
            "total_messages": int(len(audience_df.index)),
            "unique_authors": unique_authors,
            "excluded_team_messages": excluded_team_messages,
        }
        if "created_at" in audience_df.columns:
            series = pd.to_datetime(audience_df["created_at"], utc=True, errors="coerce").dropna()
            if not series.empty:
                chat_stats["time_range_utc"] = {
                    "start": series.min().strftime("%Y-%m-%d %H:%M:%S UTC"),
                    "end": series.max().strftime("%Y-%m-%d %H:%M:%S UTC"),
                }
        if "author_id" in audience_df.columns:
            top_authors = audience_df["author_id"].value_counts().head(10)
            chat_stats["top_authors_by_message_count"] = {str(idx): int(val) for idx, val in top_authors.items()}
        if "text_content" in audience_df.columns:
            text_lengths = audience_df["text_content"].fillna("").astype(str).str.len()
            if len(text_lengths.index):
                chat_stats["text_length"] = {
                    "avg_chars": float(round(text_lengths.mean(), 2)),
                    "median_chars": float(round(text_lengths.median(), 2)),
                    "max_chars": int(text_lengths.max()),
                }
        stats["chat"] = chat_stats

    if isinstance(questions_df, pd.DataFrame):
        stats["questions"] = build_question_stats(questions_df)
    if isinstance(transcript_payload, dict):
        stats["transcript"] = build_transcript_stats(transcript_payload)
    if isinstance(session_payload, dict):
        session_chat = session_chat_df if isinstance(session_chat_df, pd.DataFrame) else chat_df
        stats["session"] = build_session_stats(
            session_payload,
            internal=internal,
            chat_df=session_chat if isinstance(session_chat, pd.DataFrame) else None,
        )
    return stats


def _sample_evenly(items: list, max_items: int) -> list:
    """Return at most max_items elements sampled evenly across the full list."""
    if max_items <= 0 or len(items) <= max_items:
        return items
    step = len(items) / max_items
    return [items[int(i * step)] for i in range(max_items)]


def build_compact_transcript_payload_for_llm(transcript_payload: Dict[str, Any], max_segments: int = 120) -> Dict[str, Any]:
    transcript = _extract_transcript_object(transcript_payload)
    segments = _extract_transcript_segments(transcript)
    compact_segments: List[Dict[str, Any]] = []

    for segment in _sample_evenly(segments, max_segments):
        if not isinstance(segment, dict):
            continue
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        compact_segment = {
            "start": _coerce_seconds(_get_segment_start_value(segment)),
            "end": _coerce_seconds(_get_segment_end_value(segment)),
            "speaker": _extract_speaker_value(segment),
            "text": text,
        }
        confidence = segment.get("confidence")
        if confidence not in (None, ""):
            compact_segment["confidence"] = confidence
        compact_segments.append(compact_segment)

    if not compact_segments:
        sentence_items = _extract_sentence_items(transcript_payload, transcript)
        for sentence in _sample_evenly(sentence_items, max_segments):
            if not isinstance(sentence, dict):
                continue
            text = str(sentence.get("sentence") or sentence.get("text") or "").strip()
            if not text:
                continue
            compact_segment = {
                "start": _coerce_seconds(sentence.get("start") or sentence.get("start_time")),
                "end": _coerce_seconds(sentence.get("end") or sentence.get("end_time")),
                "speaker": sentence.get("speaker"),
                "text": text,
            }
            confidence = sentence.get("confidence")
            if confidence not in (None, ""):
                compact_segment["confidence"] = confidence
            compact_segments.append(compact_segment)

    speaker_values = [str(item.get("speaker") or "").strip() for item in compact_segments if str(item.get("speaker") or "").strip()]
    compact_payload: Dict[str, Any] = {
        "language": transcript.get("language"),
        "duration_seconds": transcript.get("duration_seconds"),
        "speaker_count": len(set(speaker_values)),
        "transcript_segments": compact_segments,
    }
    return compact_payload


def build_deep_analysis_chat_payload_for_llm(chat_payload: Dict[str, Any], max_rows: int = 0) -> Dict[str, Any]:
    messages = extract_messages(chat_payload)
    events: List[Dict[str, Any]] = []
    effective_messages = messages[:max_rows] if max_rows and max_rows > 0 else messages
    for message in effective_messages:
        flattened = clean_table_headers(pd.DataFrame([flatten_message(message)])).to_dict("records")[0]
        text = str(flattened.get("text_content") or "").strip()
        if not text:
            continue
        events.append(
            {
                "created_at": flattened.get("created_at"),
                "author_id": flattened.get("author_id"),
                "from_team_member": flattened.get("from_team_member"),
                "from_guest_speaker": flattened.get("from_guest_speaker"),
                "text_content": text,
            }
        )
    return {"chat_events": events}


def build_deep_analysis_questions_payload_for_llm(questions_payload: Dict[str, Any], max_rows: int = 0) -> Dict[str, Any]:
    people_lookup = extract_included_people(questions_payload)
    questions = extract_questions(questions_payload)
    events: List[Dict[str, Any]] = []
    effective_questions = questions[:max_rows] if max_rows and max_rows > 0 else questions
    for question in effective_questions:
        flattened = clean_table_headers(pd.DataFrame([flatten_question(question, people_lookup)])).to_dict("records")[0]
        question_text = str(flattened.get("question") or "").strip()
        if not question_text:
            continue
        event: Dict[str, Any] = {
            "created_at": flattened.get("created_at"),
            "question": question_text,
            "responded_at": flattened.get("responded_at"),
            # The API spec spells it `response_orally`; older payloads say `responded_orally`.
            "responded_orally": (
                flattened.get("response_orally")
                if flattened.get("response_orally") is not None
                else flattened.get("responded_orally")
            ),
            "question_author_id": flattened.get("question_author_id"),
        }
        response = str(flattened.get("response") or "").strip()
        if response:
            event["response"] = response
        events.append(event)
    return {"question_events": events}

def build_compact_chat_payload_for_llm(chat_df: pd.DataFrame, max_rows: int = 150) -> Dict[str, Any]:
    columns = [
        column for column in ["created_at", "author_id", "text_content", *TEAM_MESSAGE_FLAGS]
        if column in chat_df.columns
    ]
    return {
        "messages": chat_df.head(max_rows)[columns].fillna("").to_dict("records") if columns else [],
    }


def build_compact_questions_payload_for_llm(questions_df: pd.DataFrame, max_rows: int = 80) -> Dict[str, Any]:
    columns = [
        # `answered_by` and `answered_orally` let the model see a question
        # answered live, which has no response text.
        column for column in ["asked_at", "asked_by", "question", "response", "answered_by", "answered_orally"]
        if column in questions_df.columns
    ]
    return {
        "questions": questions_df.head(max_rows)[columns].fillna("").to_dict("records") if columns else [],
    }
