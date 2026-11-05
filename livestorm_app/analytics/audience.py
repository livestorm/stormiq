"""Chat and questions: flatten Livestorm payloads into tables, and question stats."""

from __future__ import annotations

from typing import Any, Dict, Tuple

import pandas as pd

from livestorm_app.livestorm.payloads import (
    extract_included_people,
    extract_messages,
    extract_questions,
)


def flatten_message(msg: Dict[str, Any]) -> Dict[str, Any]:
    """One chat message's id, type and attributes, as one flat row.

    Relationships are left out. A message fetched with `include=people`
    links to its author's people record (`relationships.people`); only the
    v1 record reads that link (`record/authors.py`), so the classic chat
    table, and the workspace response built from it, stay as they were.
    """
    base: Dict[str, Any] = {}
    if not isinstance(msg, dict):
        return base

    base.update({"id": msg.get("id"), "type": msg.get("type")})
    attrs = msg.get("attributes")
    if isinstance(attrs, dict):
        for key, value in attrs.items():
            base[key] = value
    return base


def flatten_question(question: Dict[str, Any], people_lookup: Dict[str, str]) -> Dict[str, Any]:
    base: Dict[str, Any] = {}
    if not isinstance(question, dict):
        return base

    base.update({"id": question.get("id"), "type": question.get("type")})
    attrs = question.get("attributes")
    if isinstance(attrs, dict):
        for key, value in attrs.items():
            base[key] = value

    asker_id = str(base.get("question_author_id") or "").strip()
    responder_id = str(base.get("response_author_id") or "").strip()
    base["asked_by"] = people_lookup.get(asker_id, asker_id) if asker_id else None
    base["answered_by"] = people_lookup.get(responder_id, responder_id) if responder_id else None
    return base


def clean_table_headers(df: pd.DataFrame) -> pd.DataFrame:
    cleaned = df.copy()
    cleaned.columns = [
        col.replace("attr.", "").replace("rel.", "").replace(".", "_") for col in cleaned.columns
    ]
    return cleaned


def format_unix_datetime_columns(df: pd.DataFrame) -> pd.DataFrame:
    formatted = df.copy()
    for col in ("created_at", "updated_at", "responded_at"):
        if col in formatted.columns:
            formatted[col] = pd.to_datetime(formatted[col], unit="s", utc=True, errors="coerce")
            formatted[col] = formatted[col].dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    return formatted


# Per-message flags from the Livestorm chat API. A message carrying either one
# was written by the host team or a guest speaker, not by the audience.
TEAM_MESSAGE_FLAGS = ("from_team_member", "from_guest_speaker")


def _is_true(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() == "true"


def drop_unwanted_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Drop display noise and normalize the team flags to booleans.

    The flags are kept: they decide which messages count as audience
    activity. Cached payloads fetched before the flags were read may lack
    them; those messages count as audience (False).
    """
    cleaned = df.copy()
    existing = [col for col in ["type", "html_content"] if col in cleaned.columns]
    if existing:
        cleaned = cleaned.drop(columns=existing)
    for flag in TEAM_MESSAGE_FLAGS:
        if flag in cleaned.columns:
            cleaned[flag] = cleaned[flag].apply(_is_true).astype(bool)
        else:
            cleaned[flag] = False
    return cleaned


def is_internal_session(people_df: pd.DataFrame) -> bool:
    """True when the host team is more than half of the session's attendees.

    In an internal meeting (an all-hands, say) everyone is a team member, so
    leaving the team out would leave nothing. Team members are people with
    `role == "team_member"`; guest speakers don't count as team. The count is
    over attendees (`attended` true), or over every registrant when
    attendance is unknown or nobody attended. An empty frame is not internal.
    """
    if not isinstance(people_df, pd.DataFrame) or people_df.empty or "role" not in people_df.columns:
        return False
    is_team = people_df["role"].fillna("").astype(str).str.strip().eq("team_member")
    if "is_guest_speaker" in people_df.columns:
        is_team &= ~people_df["is_guest_speaker"].apply(_is_true).astype(bool)
    counted = pd.Series(True, index=people_df.index)
    if "attended" in people_df.columns:
        attended = people_df["attended"].apply(_is_true).astype(bool)
        if attended.any():
            counted = attended
    return int((is_team & counted).sum()) * 2 > int(counted.sum())


def audience_chat_mask(chat_df: pd.DataFrame, *, internal: bool = False) -> pd.Series:
    """True for messages written by the audience (not the team or a guest speaker).

    On an internal session (`is_internal_session`) every message counts.
    """
    mask = pd.Series(True, index=chat_df.index)
    if internal:
        return mask
    for flag in TEAM_MESSAGE_FLAGS:
        if flag in chat_df.columns:
            mask &= ~chat_df[flag].apply(_is_true).astype(bool)
    return mask


def split_audience_chat(chat_df: pd.DataFrame, *, internal: bool = False) -> Tuple[pd.DataFrame, int]:
    """Return (audience-only messages, number of team and guest-speaker messages left out).

    On an internal session every message is kept and none is left out.
    """
    if not isinstance(chat_df, pd.DataFrame) or chat_df.empty:
        return chat_df, 0
    mask = audience_chat_mask(chat_df, internal=internal)
    return chat_df.loc[mask].copy(), int((~mask).sum())


def _answered_orally_series(df: pd.DataFrame) -> pd.Series:
    """Whether each question was answered live.

    The API spec spells the field `response_orally`; older code and cached
    payloads use `responded_orally`. The spec spelling wins when both exist.
    """
    result = pd.Series(False, index=df.index)
    for column in ("responded_orally", "response_orally"):  # later wins
        if column in df.columns:
            values = df[column]
            result = values.where(values.notna(), result).apply(_is_true)
    return result.astype(bool)


def clean_questions_table(df: pd.DataFrame) -> pd.DataFrame:
    cleaned = df.copy()
    cleaned["answered_orally"] = _answered_orally_series(cleaned)
    existing = [
        col
        for col in [
            "type",
            "session_id",
            "event_id",
            "question_author_id",
            "response_author_id",
            "responded_orally",
            "response_orally",
            "updated_at",
        ]
        if col in cleaned.columns
    ]
    if existing:
        cleaned = cleaned.drop(columns=existing)

    if "created_at" in cleaned.columns:
        cleaned = cleaned.rename(columns={"created_at": "asked_at"})

    preferred_order = ["id", "question", "response", "asked_by", "answered_by", "asked_at", "responded_at"]
    cols = [col for col in preferred_order if col in cleaned.columns] + [
        col for col in cleaned.columns if col not in preferred_order
    ]
    return cleaned[cols]


def build_chat_df_from_payload(payload: Dict[str, Any]) -> pd.DataFrame:
    messages = extract_messages(payload)
    if not messages:
        return pd.DataFrame()

    rows = [flatten_message(message) for message in messages]
    chat_df = clean_table_headers(pd.DataFrame(rows))
    chat_df = format_unix_datetime_columns(chat_df)
    return drop_unwanted_columns(chat_df)


def build_questions_df_from_payload(raw_questions_payload: Dict[str, Any]) -> pd.DataFrame:
    raw_questions = extract_questions(raw_questions_payload)
    if not raw_questions:
        return pd.DataFrame()

    people_lookup = extract_included_people(raw_questions_payload)
    question_rows = [flatten_question(question, people_lookup) for question in raw_questions]
    questions_df = clean_table_headers(pd.DataFrame(question_rows))
    questions_df = format_unix_datetime_columns(questions_df)
    return clean_questions_table(questions_df)


def build_question_stats(questions_df: pd.DataFrame) -> Dict[str, Any]:
    stats: Dict[str, Any] = {"total_questions": int(len(questions_df.index))}
    asker_col = "asked_by" if "asked_by" in questions_df.columns else "question_author_id" if "question_author_id" in questions_df.columns else None

    if asker_col is not None:
        stats["unique_askers"] = int(questions_df[asker_col].nunique())
        top_askers = questions_df[asker_col].value_counts().head(10)
        stats["top_askers_by_question_count"] = {str(idx): int(val) for idx, val in top_askers.items()}

    responder_col = None
    if "answered_by" in questions_df.columns:
        responder_col = "answered_by"
    elif "responded_by" in questions_df.columns:
        responder_col = "responded_by"
    elif "response_author_id" in questions_df.columns:
        responder_col = "response_author_id"

    # Answered = has a responder, or was answered orally (live).
    oral_columns = [col for col in ("answered_orally", "response_orally", "responded_orally") if col in questions_df.columns]
    if responder_col is not None or oral_columns:
        answered = pd.Series(False, index=questions_df.index)
        if responder_col is not None:
            answered |= questions_df[responder_col].fillna("").astype(str).str.strip() != ""
        if "answered_orally" in questions_df.columns:
            answered |= questions_df["answered_orally"].apply(_is_true).astype(bool)
        elif oral_columns:
            answered |= _answered_orally_series(questions_df)
        stats["answered_questions"] = int(answered.sum())
        stats["unanswered_questions"] = int((~answered).sum())

    return stats
