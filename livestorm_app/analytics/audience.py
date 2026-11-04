"""Chat and questions: flatten Livestorm payloads into tables, and question stats."""

from __future__ import annotations

from typing import Any, Dict

import pandas as pd

from livestorm_app.livestorm.payloads import (
    extract_included_people,
    extract_messages,
    extract_questions,
)


def flatten_message(msg: Dict[str, Any]) -> Dict[str, Any]:
    base: Dict[str, Any] = {}
    if not isinstance(msg, dict):
        return base

    base.update({"id": msg.get("id"), "type": msg.get("type")})
    attrs = msg.get("attributes")
    if isinstance(attrs, dict):
        for key, value in attrs.items():
            base[key] = value

    rels = msg.get("relationships")
    if isinstance(rels, dict):
        for rel_name, rel_val in rels.items():
            if isinstance(rel_val, dict) and "data" in rel_val:
                rel_data = rel_val.get("data")
                if isinstance(rel_data, dict):
                    base[f"rel.{rel_name}.id"] = rel_data.get("id")
                    base[f"rel.{rel_name}.type"] = rel_data.get("type")
                elif isinstance(rel_data, list):
                    base[f"rel.{rel_name}.ids"] = ",".join(
                        [str(item.get("id")) for item in rel_data if isinstance(item, dict)]
                    )
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


def drop_unwanted_columns(df: pd.DataFrame) -> pd.DataFrame:
    cleaned = df.copy()
    existing = [col for col in ["type", "from_guest_speaker", "from_team_member", "html_content"] if col in cleaned.columns]
    if existing:
        cleaned = cleaned.drop(columns=existing)
    return cleaned


def clean_questions_table(df: pd.DataFrame) -> pd.DataFrame:
    cleaned = df.copy()
    existing = [
        col
        for col in [
            "type",
            "session_id",
            "event_id",
            "question_author_id",
            "response_author_id",
            "responded_orally",
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

    if responder_col is not None:
        has_responder = questions_df[responder_col].fillna("").astype(str).str.strip() != ""
        stats["answered_questions"] = int(has_responder.sum())
        stats["unanswered_questions"] = int((~has_responder).sum())

    return stats
