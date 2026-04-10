import json
import logging
import math
import re
import sys
from collections import Counter
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pandas as pd
import requests

from livestorm_app.config import (
    ANALYSIS_ALL_SOURCES_PROMPT_PATH,
    ANALYSIS_BASE_PROMPT_PATH,
    ANALYSIS_CHAT_PROMPT_PATH,
    ANALYSIS_DEEP_PROMPT_PATH,
    ANALYSIS_QUESTIONS_PROMPT_PATH,
    ANALYSIS_TRANSCRIPT_PROMPT_PATH,
    API_BASE,
    CONTENT_REPURPOSE_BLOG_PROMPT_PATH,
    CONTENT_REPURPOSE_EMAIL_PROMPT_PATH,
    CONTENT_REPURPOSE_SOCIAL_MEDIA_PROMPT_PATH,
    CONTENT_REPURPOSE_SUMMARY_PROMPT_PATH,
    DEFAULT_PAGE_SIZE,
    MAX_PAGES,
    OPENAI_CHAT_COMPLETIONS_URL,
    SMART_RECAP_HYPE_PROMPT_PATH,
    SMART_RECAP_PROFESSIONAL_PROMPT_PATH,
    SMART_RECAP_SURPRISE_PROMPT_PATH,
    START_PAGE_NUMBER,
)
from livestorm_app.session_overview import build_session_stats


logger = logging.getLogger(__name__)


COMMON_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "can", "do", "does", "did",
    "for", "from", "had", "has", "have", "hello", "how", "i", "if", "in", "into", "is", "it",
    "it's", "its", "just", "let", "let's", "ll", "look", "me", "my", "not", "now", "of", "okay",
    "on", "one", "or", "our", "ours", "please", "right", "so", "some", "still", "such", "than",
    "that", "that's", "the", "their", "them", "then", "there", "thing", "things", "think",
    "these", "they", "this", "those", "to", "too", "up", "us", "very", "was", "we", "well",
    "what", "when", "where", "which", "who", "why", "with", "would", "yeah", "you", "your",
    "yours", "ah", "eh", "first", "go", "going", "get", "got", "know", "like", "through", "back", "live",
    "will", "work", "working", "really", "also", "make", "made", "want", "need", "maybe",
    "s", "t", "re", "ve", "m", "d",
    "bonjour", "merci", "pour", "avec", "dans", "tout", "tous", "les", "des", "une", "est",
    "que", "qui", "sur", "pas", "oui", "franchement", "ca", "ça", "va",
}


def build_headers(key: str) -> Dict[str, str]:
    return {
        "Authorization": key,
        "accept": "application/vnd.api+json",
    }


def format_livestorm_http_error(exc: requests.HTTPError, resource_label: str) -> str:
    response = exc.response
    status_code = response.status_code if response is not None else None
    details = ""

    if response is not None:
        try:
            payload = response.json()
            if isinstance(payload, dict):
                if isinstance(payload.get("error"), str):
                    details = payload["error"]
                elif isinstance(payload.get("message"), str):
                    details = payload["message"]
        except ValueError:
            details = ""

    if status_code == 400:
        return (
            f"{resource_label} request is invalid (HTTP 400). "
            "Please verify the provided ID and request parameters."
        )
    if status_code in (401, 403):
        return (
            f"{resource_label} request was unauthorized (HTTP {status_code}). "
            "Please check your Livestorm API key permissions."
        )
    if status_code == 404:
        return (
            "Resource not found (HTTP 404). "
            "Please verify the provided Session ID/Event ID exists in your Livestorm workspace."
        )
    if status_code == 429:
        return f"{resource_label} rate limited (HTTP 429). Please wait a few seconds and try again."
    if status_code is not None and status_code >= 500:
        return (
            f"Livestorm server error while fetching {resource_label.lower()} (HTTP {status_code}). "
            "Please retry shortly."
        )
    if details:
        return f"{resource_label} API request failed (HTTP {status_code}): {details}"
    if status_code is not None:
        return f"{resource_label} API request failed (HTTP {status_code})."
    return f"{resource_label} API request failed."


def format_generic_http_error(exc: requests.HTTPError, resource_label: str) -> str:
    response = exc.response
    status_code = response.status_code if response is not None else None
    details = ""

    if response is not None:
        try:
            payload = response.json()
            if isinstance(payload, dict):
                for key in ("error", "message", "detail"):
                    if isinstance(payload.get(key), str):
                        details = payload[key]
                        break
        except ValueError:
            details = ""

    if status_code in (401, 403):
        openai_resources = {
            "Analysis",
            "Deep analysis",
            "Analysis PDF",
            "Smart Recap",
            "Smart Recap PDF",
            "Content Repurposing",
            "Content Repurposing PDF",
        }
        credential_label = "OpenAI API key" if resource_label in openai_resources else "Gladia API key"
        return (
            f"{resource_label} request was unauthorized (HTTP {status_code}). "
            f"Please check the configured {credential_label}."
        )
    if status_code == 404:
        return (
            f"{resource_label} not found (HTTP 404). "
            "Please verify the selected session has an available recording/transcript."
        )
    if status_code == 429:
        return f"{resource_label} rate limited (HTTP 429). Please retry in a moment."
    if status_code is not None and status_code >= 500:
        return f"{resource_label} server error (HTTP {status_code}). Please retry shortly."
    if details and status_code is not None:
        return f"{resource_label} request failed (HTTP {status_code}): {details}"
    if status_code is not None:
        return f"{resource_label} request failed (HTTP {status_code})."
    return f"{resource_label} request failed."


def build_http_error_debug_details(exc: requests.HTTPError, resource_label: str) -> Dict[str, Any]:
    response = exc.response
    details: Dict[str, Any] = {
        "resource": resource_label,
        "exception_type": type(exc).__name__,
    }
    if response is None:
        return details

    details["status_code"] = response.status_code
    details["url"] = response.url
    request = response.request
    if request is not None:
        details["method"] = request.method

    try:
        payload = response.json()
        if isinstance(payload, (dict, list)):
            details["response_json"] = payload
    except ValueError:
        body_text = response.text.strip()
        if body_text:
            details["response_text"] = body_text[:1200]
    return details


def build_request_exception_debug_details(exc: requests.RequestException, resource_label: str) -> Dict[str, Any]:
    details: Dict[str, Any] = {
        "resource": resource_label,
        "exception_type": type(exc).__name__,
        "message": str(exc),
    }
    response = getattr(exc, "response", None)
    if response is not None:
        details["status_code"] = response.status_code
        details["url"] = response.url
    request = getattr(exc, "request", None)
    if request is not None:
        details["method"] = request.method
        details["url"] = details.get("url") or request.url
    return details


def extract_messages(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return payload["data"]
    if isinstance(payload, list):
        return payload
    return []


def extract_questions(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return payload["data"]
    if isinstance(payload, list):
        return payload
    return []


def extract_sessions(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return payload["data"]
    if isinstance(payload, list):
        return payload
    return []


def extract_events(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return payload["data"]
    if isinstance(payload, list):
        return payload
    return []


def _format_unix_label(value: Any) -> str:
    try:
        if value in (None, "", 0):
            return "n/a"
        ts = pd.to_datetime(value, unit="s", utc=True, errors="coerce")
        if pd.isna(ts):
            return "n/a"
        return ts.strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError):
        return "n/a"


def build_event_session_options(payload: Dict[str, Any]) -> List[Dict[str, str]]:
    session_items = extract_sessions(payload)
    options: List[Dict[str, str]] = []
    sortable: List[Dict[str, Any]] = []
    for item in session_items:
        if not isinstance(item, dict):
            continue
        session_id = str(item.get("id") or "").strip()
        if not session_id:
            continue
        attrs = item.get("attributes")
        attrs = attrs if isinstance(attrs, dict) else {}
        started_at = attrs.get("started_at") or attrs.get("estimated_started_at") or 0
        sortable.append({"id": session_id, "attributes": attrs, "started_at": started_at})

    sortable.sort(key=lambda row: row.get("started_at") or 0, reverse=True)
    for row in sortable:
        attrs = row["attributes"]
        name = str(attrs.get("name") or "").strip()
        attendees = attrs.get("attendees_count", "n/a")
        started_label = _format_unix_label(attrs.get("started_at") or attrs.get("estimated_started_at"))
        title = name if name else "Untitled session"
        options.append({"id": row["id"], "label": f"{started_label} - {title} ({attendees} attendees)"})
    return options


def build_workspace_event_options(payload: Dict[str, Any]) -> List[Dict[str, str]]:
    event_items = extract_events(payload)
    sortable: List[Dict[str, Any]] = []
    options: List[Dict[str, str]] = []
    for item in event_items:
        if not isinstance(item, dict):
            continue
        event_id = str(item.get("id") or "").strip()
        if not event_id:
            continue
        attrs = item.get("attributes")
        attrs = attrs if isinstance(attrs, dict) else {}
        updated_at = attrs.get("updated_at") or attrs.get("published_at") or attrs.get("created_at") or 0
        sortable.append({"id": event_id, "attributes": attrs, "updated_at": updated_at})

    sortable.sort(key=lambda row: row.get("updated_at") or 0, reverse=True)
    for row in sortable:
        attrs = row["attributes"]
        title = str(attrs.get("title") or "").strip() or "Untitled event"
        sessions_count = attrs.get("sessions_count", "n/a")
        updated_label = _format_unix_label(attrs.get("updated_at") or attrs.get("published_at") or attrs.get("created_at"))
        scheduling_status = str(attrs.get("scheduling_status") or "").strip()
        language = str(attrs.get("language") or "").strip()
        options.append(
            {
                "id": row["id"],
                "label": f"{title} ({sessions_count} sessions) · {updated_label}",
                "title": title,
                "scheduling_status": scheduling_status,
                "language": language,
                "sessions_count": str(sessions_count),
                "updated_label": updated_label,
            }
        )
    return options


def extract_included_people(payload: Dict[str, Any]) -> Dict[str, str]:
    people_lookup: Dict[str, str] = {}
    included = payload.get("included")
    if not isinstance(included, list):
        return people_lookup

    for item in included:
        if not isinstance(item, dict) or item.get("type") != "people":
            continue
        person_id = str(item.get("id") or "").strip()
        if not person_id:
            continue
        attrs = item.get("attributes")
        if not isinstance(attrs, dict):
            people_lookup[person_id] = person_id
            continue
        first_name = str(attrs.get("first_name") or "").strip()
        last_name = str(attrs.get("last_name") or "").strip()
        people_lookup[person_id] = f"{first_name} {last_name}".strip() or person_id

    return people_lookup


def _extract_pagination(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {}

    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        pagination = metadata.get("pagination")
        if isinstance(pagination, dict):
            return pagination
        return metadata

    meta = payload.get("meta")
    if isinstance(meta, dict):
        pagination = meta.get("pagination")
        if isinstance(pagination, dict):
            return pagination
        return meta

    return {}


def _extract_next_page(payload: Dict[str, Any]) -> Optional[int]:
    pagination = _extract_pagination(payload)
    next_page = pagination.get("next_page")

    if next_page is None or isinstance(next_page, bool):
        return None
    if isinstance(next_page, int):
        return next_page
    if isinstance(next_page, str):
        raw = next_page.strip()
        if not raw or raw.lower() == "null":
            return None
        if raw.isdigit():
            return int(raw)
    return None


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


def fetch_chat_messages(key: str, session: str, page_size: int = DEFAULT_PAGE_SIZE) -> Dict[str, Any]:
    url = f"{API_BASE}/sessions/{session}/chat_messages"
    headers = build_headers(key)

    page_number = START_PAGE_NUMBER
    pages_fetched = 0
    seen_pages = set()
    all_messages: List[Dict[str, Any]] = []
    final_payload: Dict[str, Any] = {}

    while pages_fetched < MAX_PAGES:
        if page_number in seen_pages:
            break
        seen_pages.add(page_number)

        resp = requests.get(
            url,
            headers=headers,
            params={"page[number]": page_number, "page[size]": page_size},
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, dict):
            payload = {"data": extract_messages(payload)}

        final_payload = payload
        messages = extract_messages(payload)
        all_messages.extend(messages)
        pages_fetched += 1

        next_page = _extract_next_page(payload)
        if next_page is not None:
            if next_page in seen_pages:
                break
            page_number = next_page
            continue
        if len(messages) < page_size:
            break
        page_number += 1

    final_payload["data"] = all_messages
    final_payload["pages_fetched"] = pages_fetched
    final_payload["requested_page_size"] = page_size
    return final_payload


def fetch_session_questions(key: str, session: str, page_size: int = DEFAULT_PAGE_SIZE) -> Dict[str, Any]:
    url = f"{API_BASE}/sessions/{session}/questions"
    headers = build_headers(key)

    page_number = START_PAGE_NUMBER
    pages_fetched = 0
    seen_pages = set()
    all_questions: List[Dict[str, Any]] = []
    all_included: Dict[str, Dict[str, Any]] = {}
    final_payload: Dict[str, Any] = {}

    while pages_fetched < MAX_PAGES:
        if page_number in seen_pages:
            break
        seen_pages.add(page_number)

        resp = requests.get(
            url,
            headers=headers,
            params={"page[number]": page_number, "page[size]": page_size, "include": "asker,responder"},
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, dict):
            payload = {"data": extract_questions(payload)}

        final_payload = payload
        page_questions = extract_questions(payload)
        all_questions.extend(page_questions)
        pages_fetched += 1

        included = payload.get("included")
        if isinstance(included, list):
            for item in included:
                if not isinstance(item, dict):
                    continue
                included_id = str(item.get("id") or "")
                included_type = str(item.get("type") or "")
                if included_id and included_type:
                    all_included[f"{included_type}:{included_id}"] = item

        next_page = _extract_next_page(payload)
        if next_page is not None:
            if next_page in seen_pages:
                break
            page_number = next_page
            continue
        if len(page_questions) < page_size:
            break
        page_number += 1

    final_payload["data"] = all_questions
    final_payload["included"] = list(all_included.values())
    final_payload["pages_fetched"] = pages_fetched
    final_payload["requested_page_size"] = page_size
    return final_payload


def fetch_session_details(key: str, session: str) -> Dict[str, Any]:
    resp = requests.get(
        f"{API_BASE}/sessions/{session}",
        headers=build_headers(key),
        params={"include": "people"},
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    return payload if isinstance(payload, dict) else {"data": payload}


def fetch_event_past_sessions(key: str, event: str, page_size: int = DEFAULT_PAGE_SIZE) -> Dict[str, Any]:
    url = f"{API_BASE}/events/{event}/sessions"
    headers = build_headers(key)

    page_number = START_PAGE_NUMBER
    pages_fetched = 0
    seen_pages = set()
    all_sessions: List[Dict[str, Any]] = []
    final_payload: Dict[str, Any] = {}

    while pages_fetched < MAX_PAGES:
        if page_number in seen_pages:
            break
        seen_pages.add(page_number)

        resp = requests.get(
            url,
            headers=headers,
            params={"page[number]": page_number, "page[size]": page_size, "filter[status]": "past"},
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, dict):
            payload = {"data": extract_sessions(payload)}

        final_payload = payload
        page_sessions = extract_sessions(payload)
        all_sessions.extend(page_sessions)
        pages_fetched += 1

        next_page = _extract_next_page(payload)
        if next_page is not None:
            if next_page in seen_pages:
                break
            page_number = next_page
            continue
        if len(page_sessions) < page_size:
            break
        page_number += 1

    final_payload["data"] = all_sessions
    final_payload["pages_fetched"] = pages_fetched
    final_payload["requested_page_size"] = page_size
    return final_payload


def fetch_workspace_events_page(
    key: str,
    page_number: int = START_PAGE_NUMBER,
    page_size: int = 20,
    title: str = "",
    scheduling_status: str = "",
) -> Dict[str, Any]:
    url = f"{API_BASE}/events"
    headers = build_headers(key)
    params: Dict[str, Any] = {"page[number]": page_number, "page[size]": page_size}
    normalized_title = str(title or "").strip()
    normalized_status = str(scheduling_status or "").strip()
    if normalized_title:
        params["filter[title]"] = normalized_title
    if normalized_status:
        params["filter[scheduling_status]"] = normalized_status
    resp = requests.get(
        url,
        headers=headers,
        params=params,
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, dict):
        payload = {"data": extract_events(payload)}

    payload["pages_fetched"] = 1
    payload["requested_page_size"] = page_size
    payload["requested_page_number"] = page_number
    payload["requested_title"] = normalized_title
    payload["requested_scheduling_status"] = normalized_status
    payload["next_page"] = _extract_next_page(payload)
    return payload


def fetch_chat_and_questions_bundle(key: str, session_id: str) -> Dict[str, Any]:
    payload = fetch_chat_messages(key, session_id)
    chat_df = build_chat_df_from_payload(payload)

    raw_questions_payload = fetch_session_questions(key, session_id)
    questions_df = build_questions_df_from_payload(raw_questions_payload)

    return {
        "chat_payload": payload,
        "chat_df": chat_df,
        "questions_payload": raw_questions_payload,
        "questions_df": questions_df,
    }


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


def load_analysis_prompt(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""


def build_analysis_prompt(selected_sources: List[str]) -> str:
    prompt_parts = [load_analysis_prompt(ANALYSIS_BASE_PROMPT_PATH)]
    if "chat" in selected_sources:
        prompt_parts.append(load_analysis_prompt(ANALYSIS_CHAT_PROMPT_PATH))
    if "questions" in selected_sources:
        prompt_parts.append(load_analysis_prompt(ANALYSIS_QUESTIONS_PROMPT_PATH))
    if "transcript" in selected_sources:
        prompt_parts.append(load_analysis_prompt(ANALYSIS_TRANSCRIPT_PROMPT_PATH))
    if set(selected_sources) == {"chat", "questions", "transcript"}:
        prompt_parts.append(load_analysis_prompt(ANALYSIS_ALL_SOURCES_PROMPT_PATH))

    prompt = "\n\n".join(part for part in prompt_parts if part.strip())
    if prompt.strip():
        return prompt

    return (
        "You are a senior analyst. Review the selected Livestorm session sources and return concise, "
        "evidence-based markdown with an executive summary, key themes, engagement insights, risks, "
        "and actionable recommendations. Clearly state any limits caused by missing sources."
    )


def build_deep_analysis_prompt() -> str:
    prompt = load_analysis_prompt(ANALYSIS_DEEP_PROMPT_PATH)
    if prompt.strip():
        return prompt
    return (
        "You are a staff-level technical analyst. Perform a deep analytical review of the provided Livestorm "
        "session data using the full transcript JSON, chat payload, and questions payload. Return a technical "
        "markdown report with sections for executive summary, timeline analysis, communication quality, speaker "
        "dynamics, engagement diagnostics, business signal extraction, risk areas, data limitations, and highly "
        "specific recommendations. Support conclusions with concrete evidence from the payloads."
    )

def build_content_repurpose_prompt(content_type: str) -> str:
    prompt_map = {
        "summary": CONTENT_REPURPOSE_SUMMARY_PROMPT_PATH,
        "email": CONTENT_REPURPOSE_EMAIL_PROMPT_PATH,
        "blog": CONTENT_REPURPOSE_BLOG_PROMPT_PATH,
        "social_media": CONTENT_REPURPOSE_SOCIAL_MEDIA_PROMPT_PATH,
    }
    prompt_path = prompt_map.get(content_type)
    if prompt_path is not None:
        prompt = load_analysis_prompt(prompt_path)
        if prompt.strip():
            return prompt
    return (
        "You are a content repurposing specialist. Use the provided Livestorm transcript, chat, and questions to "
        "generate polished derivative content in the requested format. Be faithful to the source material, avoid "
        "inventing facts, and write in the requested output language."
    )


def build_content_repurpose_bundle_prompt() -> str:
    return (
        "You are a content repurposing specialist.\n\n"
        "Use only the provided transcript text. Do not use chat, questions, metadata, stats, or outside facts.\n"
        "If the transcript text is missing or blank, return exactly: {\"summary\":\"Transcript is empty.\","
        "\"blog\":\"Transcript is empty.\",\"email\":\"Transcript is empty.\",\"social_media\":\"Transcript is empty.\"}\n\n"
        "Return valid JSON only with exactly these string keys:\n"
        "- summary\n"
        "- blog\n"
        "- email\n"
        "- social_media\n\n"
        "All four keys are mandatory. Never omit a key. If you are running out of space, make each section shorter, but still return all four keys.\n\n"
        "Each value must be markdown content in the requested output language.\n\n"
        "Requirements for `summary`:\n"
        "- 500-700 words.\n"
        "- Use heading hierarchy with `#`, `##`, and `###`.\n"
        "- Include only well-supported sections.\n"
        "- Suggested structure: `# Event Summary`, `## Introduction`, `## Key Points Discussed`, `## Notable Quotes`, `## Key Takeaways`, `## Next Steps Or Call To Action`.\n\n"
        "Requirements for `blog`:\n"
        "- 1000-1500 words.\n"
        "- Start with `Meta description: ...` then `# Title`.\n"
        "- Use descriptive `##` and `###` headings.\n"
        "- Write a strong standalone article based strictly on the transcript.\n\n"
        "Requirements for `email`:\n"
        "- Markdown only.\n"
        "- Include `# Subject Line Options`, `# Email Version 1`, and `# Email Version 2`.\n"
        "- Each email should read naturally in paragraphs and stay around 200-300 words.\n\n"
        "Requirements for `social_media`:\n"
        "- Markdown only.\n"
        "- Include `# LinkedIn`, `# Facebook`, and `# X / Twitter`.\n"
        "- For each platform provide one polished post and 2-4 suggested hashtags.\n"
        "- Keep the X / Twitter version within 280 characters.\n\n"
        "Do not wrap the JSON in code fences."
    )


def _extract_json_object_from_text(text: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    fenced_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if fenced_match:
        return fenced_match.group(1).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        return cleaned[start:end + 1].strip()
    return cleaned


def parse_content_repurpose_bundle_response(response_text: str) -> Dict[str, str]:
    empty_bundle = {"summary": "", "blog": "", "email": "", "social_media": ""}
    json_text = _extract_json_object_from_text(response_text)
    if not json_text:
        return empty_bundle
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError:
        return empty_bundle
    if not isinstance(payload, dict):
        return empty_bundle
    return {
        key: str(payload.get(key) or "").strip()
        for key in ["summary", "blog", "email", "social_media"]
    }


def _bundle_has_all_sections(bundle: Dict[str, str]) -> bool:
    required_keys = ["summary", "blog", "email", "social_media"]
    return all(isinstance(bundle.get(key), str) and bundle.get(key, "").strip() for key in required_keys)


def _bundle_language_looks_wrong(bundle: Dict[str, str], output_language: str) -> bool:
    output_language = str(output_language or "").strip().lower()
    if output_language != "french":
        return False

    sample_text = " ".join(
        str(bundle.get(key) or "")
        for key in ["summary", "blog", "email", "social_media"]
    ).lower()
    if not sample_text.strip():
        return False

    english_markers = [
        " the ", " and ", " with ", " for ", " your ", " this ", " that ", " you ", " we ",
        " follow-up ", " key takeaways ", " next steps ", " subject line ", " social media ",
    ]
    french_markers = [
        " le ", " la ", " les ", " de ", " des ", " et ", " avec ", " pour ", " votre ", " vos ",
        " ce ", " cette ", " vous ", " nous ", " suivi ", " prochaines etapes ", " points cles ",
    ]

    english_score = sum(sample_text.count(marker) for marker in english_markers)
    french_score = sum(sample_text.count(marker) for marker in french_markers)
    return english_score > max(french_score + 8, 12)


def _extract_text_fragments(value: Any) -> List[str]:
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if isinstance(value, list):
        fragments: List[str] = []
        for item in value:
            fragments.extend(_extract_text_fragments(item))
        return fragments
    if isinstance(value, dict):
        fragments: List[str] = []
        for key in ("text", "output_text", "content", "value"):
            if key in value:
                fragments.extend(_extract_text_fragments(value.get(key)))
        return fragments
    return []


def _extract_chat_completion_text(payload: Dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    message = choices[0].get("message", {})
    content = message.get("content")
    text_parts = _extract_text_fragments(content)
    if text_parts:
        return "\n".join(text_parts).strip()

    # Fall back to tool-free message fields some model variants may populate.
    for key in ("text", "output_text"):
        text_parts = _extract_text_fragments(message.get(key))
        if text_parts:
            return "\n".join(text_parts).strip()
    return ""


def _build_chat_completions_payload(
    model: str,
    messages: List[Dict[str, str]],
    temperature: float,
    max_tokens: int,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "messages": messages,
    }
    token_param_name = "max_completion_tokens" if str(model or "").strip().startswith("gpt-5") else "max_tokens"
    payload[token_param_name] = max_tokens
    return payload


def translate_markdown_with_openai(
    api_key: str,
    model: str,
    source_markdown: str,
    source_language: str,
    target_language: str,
    max_tokens: int = 6000,
) -> str:
    source_markdown = str(source_markdown or "").strip()
    if not source_markdown:
        return ""

    messages: List[Dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "You translate markdown reports between English and French. Preserve structure, headings, bullets, "
                "tables, timestamps, score values, and factual meaning. Do not summarize, expand, or omit content."
            ),
        },
        {"role": "system", "content": f"Translate from {source_language} to {target_language}. Return only translated markdown."},
        {"role": "user", "content": source_markdown},
    ]
    resp = requests.post(
        OPENAI_CHAT_COMPLETIONS_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=_build_chat_completions_payload(
            model=model,
            messages=messages,
            temperature=0.1,
            max_tokens=max_tokens,
        ),
        timeout=120,
    )
    resp.raise_for_status()
    return _extract_chat_completion_text(resp.json())


def translate_content_repurpose_bundle_with_openai(
    api_key: str,
    model: str,
    source_bundle: Dict[str, str],
    source_language: str,
    target_language: str,
) -> Dict[str, str]:
    normalized_bundle = {
        key: str(value or "").strip()
        for key, value in (source_bundle or {}).items()
        if key in {"summary", "blog", "email", "social_media"}
    }
    if not any(normalized_bundle.values()):
        return {"summary": "", "blog": "", "email": "", "social_media": ""}

    messages: List[Dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "You translate a structured content repurposing JSON bundle between English and French. Return valid JSON "
                "with exactly these keys: summary, blog, email, social_media. Preserve structure, intent, formatting, calls to action, "
                "and platform-appropriate tone. Translate the values only."
            ),
        },
        {"role": "system", "content": f"Translate from {source_language} to {target_language}. Return only JSON."},
        {"role": "user", "content": json.dumps(normalized_bundle, ensure_ascii=False)},
    ]

    bundle = {"summary": "", "blog": "", "email": "", "social_media": ""}
    for attempt in range(3):
        extra_messages: List[Dict[str, str]] = []
        if attempt == 1:
            extra_messages.append(
                {
                    "role": "system",
                    "content": "The previous answer was incomplete. Return valid JSON with all four required keys populated: summary, blog, email, social_media.",
                }
            )
        elif attempt == 2:
            extra_messages.append(
                {
                    "role": "system",
                    "content": f"The previous answer was not fully written in {target_language}. Rewrite all four sections entirely in {target_language}. Do not mix in the source language except unavoidable brand/platform names.",
                }
            )
        resp = requests.post(
            OPENAI_CHAT_COMPLETIONS_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=_build_chat_completions_payload(
                model=model,
                messages=messages + extra_messages,
                temperature=0.1,
                max_tokens=12000,
            ),
            timeout=120,
        )
        resp.raise_for_status()
        bundle = parse_content_repurpose_bundle_response(_extract_chat_completion_text(resp.json()))
        if _bundle_has_all_sections(bundle) and not _bundle_language_looks_wrong(bundle, target_language):
            return bundle
    return bundle


def generate_content_repurpose_bundle_with_openai(
    api_key: str,
    model: str,
    output_language: str,
    transcript_text: str,
) -> Dict[str, str]:
    transcript_text = str(transcript_text or "").strip()
    if not transcript_text:
        return {
            "summary": "Transcript is empty.",
            "blog": "Transcript is empty.",
            "email": "Transcript is empty.",
            "social_media": "Transcript is empty.",
        }

    messages: List[Dict[str, str]] = [
        {"role": "system", "content": build_content_repurpose_bundle_prompt()},
        {"role": "system", "content": f"Respond only in {output_language}."},
        {"role": "user", "content": f"Transcript text:\n\n{transcript_text}"},
    ]
    bundle = {"summary": "", "blog": "", "email": "", "social_media": ""}
    for attempt in range(3):
        extra_messages: List[Dict[str, str]] = []
        if attempt == 1:
            extra_messages.append(
                {
                    "role": "system",
                    "content": "The previous answer was incomplete. Return valid JSON with all four required keys populated: summary, blog, email, social_media.",
                }
            )
        elif attempt == 2:
            extra_messages.append(
                {
                    "role": "system",
                    "content": f"The previous answer was not fully written in {output_language}. Rewrite all four sections entirely in {output_language}. Do not mix in English except unavoidable brand/platform names.",
                }
            )
        resp = requests.post(
            OPENAI_CHAT_COMPLETIONS_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=_build_chat_completions_payload(
                model=model,
                messages=messages + extra_messages,
                temperature=0.2,
                max_tokens=12000,
            ),
            timeout=120,
        )
        resp.raise_for_status()
        payload = resp.json()
        bundle = parse_content_repurpose_bundle_response(_extract_chat_completion_text(payload))
        if _bundle_has_all_sections(bundle) and not _bundle_language_looks_wrong(bundle, output_language):
            return bundle
        if attempt == 0:
            logger.warning("Content repurposing bundle response was incomplete; retrying once with stricter instructions.")
        elif attempt == 1 and _bundle_language_looks_wrong(bundle, output_language):
            logger.warning("Content repurposing bundle response language looked wrong; retrying with stricter language instructions.")

    return bundle


def build_smart_recap_prompt(tone: str) -> str:
    prompt_map = {
        "professional": SMART_RECAP_PROFESSIONAL_PROMPT_PATH,
        "hype": SMART_RECAP_HYPE_PROMPT_PATH,
        "surprise": SMART_RECAP_SURPRISE_PROMPT_PATH,
    }
    prompt_path = prompt_map.get(tone)
    if prompt_path is not None:
        prompt = load_analysis_prompt(prompt_path)
        if prompt.strip():
            return prompt
    return (
        "You create short transcript-only session recaps. Return markdown with exactly `# Title` and "
        "`# Description`. Use only the transcript text, avoid outside facts, and match the requested tone."
    )


def analysis_markdown_to_pdf_bytes(markdown_text: str, title: str) -> bytes:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.lib.utils import ImageReader
        from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer
    except ImportError as exc:
        raise RuntimeError(
            f"PDF export unavailable. Install with: `{sys.executable} -m pip install reportlab`"
        ) from exc

    styles = getSampleStyleSheet()
    title_style = styles["Title"]
    body_style = ParagraphStyle(
        "Body",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=10.5,
        leading=14,
        textColor=colors.HexColor("#0F1D21"),
    )
    heading_style = ParagraphStyle(
        "Heading",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=12.5,
        leading=16,
        textColor=colors.HexColor("#12262B"),
        spaceBefore=8,
    )

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=20 * mm,
        bottomMargin=16 * mm,
        title=title,
    )

    story = []
    logo_path = Path(__file__).resolve().parent.parent / "assets" / "icons" / "Logo-Livestorm-Primary.png"
    if logo_path.exists():
        image_reader = ImageReader(str(logo_path))
        original_width, original_height = image_reader.getSize()
        target_width = 40 * mm
        target_height = target_width * (float(original_height) / float(original_width)) if original_width else 10 * mm
        logo = Image(str(logo_path), width=target_width, height=target_height)
        logo.hAlign = "LEFT"
        story.extend([logo, Spacer(1, 14)])

    story.extend([Paragraph(title, title_style), Spacer(1, 8)])

    cleaned_lines: List[str] = []
    skipped_summary_heading = False
    summary_heading_tokens = {
        "event summary",
        "# event summary",
        "resume de l evenement",
        "# resume de l evenement",
        "resume evenement",
        "# resume evenement",
        "résumé de l'événement",
        "# résumé de l'événement",
        "resume de l'evenement",
        "# resume de l'evenement",
    }

    for raw_line in markdown_text.splitlines():
        stripped = raw_line.strip()
        normalized = (
            stripped.lower()
            .replace("’", "'")
            .replace("é", "e")
            .replace("è", "e")
            .replace("ê", "e")
            .replace("à", "a")
            .replace("ù", "u")
        )
        if not skipped_summary_heading and normalized in summary_heading_tokens:
            skipped_summary_heading = True
            continue
        cleaned_lines.append(raw_line)

    for line in [line.strip() for line in cleaned_lines]:
        if not line:
            story.append(Spacer(1, 5))
            continue
        escaped = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if escaped.startswith("### "):
            story.append(Paragraph(escaped[4:], heading_style))
        elif escaped.startswith("## "):
            story.append(Paragraph(escaped[3:], heading_style))
        elif escaped.startswith("# "):
            story.append(Paragraph(escaped[2:], heading_style))
        elif escaped.startswith("- "):
            story.append(Paragraph(f"&bull; {escaped[2:]}", body_style))
        else:
            story.append(Paragraph(escaped, body_style))

    doc.build(story)
    return buffer.getvalue()


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


def build_transcript_stats(transcript_payload: Dict[str, Any]) -> Dict[str, Any]:
    transcript = _extract_transcript_object(transcript_payload)
    text = str(transcript.get("text") or "")
    stats: Dict[str, Any] = {
        "word_count": len(re.findall(r"\S+", text)),
        "character_count": len(text),
        "model": transcript.get("model"),
        "requested_model": transcript.get("requested_model"),
        "language": transcript.get("language"),
        "timestamped": bool(transcript.get("timestamped")),
        "created_at": transcript.get("created_at"),
        "duration_seconds": transcript.get("duration_seconds"),
    }

    usage = transcript.get("usage")
    if isinstance(usage, dict):
        stats["usage"] = usage
    recording = transcript.get("recording")
    if isinstance(recording, dict):
        stats["recording"] = {
            "id": recording.get("id"),
            "event_id": recording.get("event_id"),
            "file_type": recording.get("file_type"),
            "mime_type": recording.get("mime_type"),
            "file_size": recording.get("file_size"),
            "file_name": recording.get("file_name"),
        }
    return stats


def _coerce_seconds(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def format_seconds_label(value: Optional[float]) -> str:
    if value is None:
        return ""
    total_seconds = max(int(value), 0)
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _extract_transcript_object(transcript_payload: Dict[str, Any]) -> Dict[str, Any]:
    transcript = transcript_payload.get("transcript")
    if isinstance(transcript, dict):
        return transcript
    result = transcript_payload.get("result")
    if isinstance(result, dict):
        nested_transcript = result.get("transcript")
        if isinstance(nested_transcript, dict):
            return nested_transcript
        transcription = result.get("transcription")
        if isinstance(transcription, dict):
            utterances = transcription.get("utterances")
            language = None
            if isinstance(utterances, list):
                for utterance in utterances:
                    if isinstance(utterance, dict) and utterance.get("language"):
                        language = utterance.get("language")
                        break
            metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
            return {
                **transcription,
                "timestamped": bool(utterances),
                "created_at": transcript_payload.get("completed_at") or transcript_payload.get("created_at"),
                "duration_seconds": metadata.get("audio_duration")
                or transcript_payload.get("audio_duration")
                or (transcript_payload.get("file") or {}).get("audio_duration"),
                "language": language,
                "model": (transcript_payload.get("request_params") or {}).get("model"),
                "requested_model": (transcript_payload.get("request_params") or {}).get("model"),
                "usage": metadata,
                "recording": transcript_payload.get("file"),
            }
    return transcript_payload if isinstance(transcript_payload, dict) else {}


def _extract_transcript_segments(transcript: Dict[str, Any]) -> List[Dict[str, Any]]:
    for key in ("segments", "timestamps", "utterances", "chunks"):
        value = transcript.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _extract_speaker_value(segment: Dict[str, Any]) -> Any:
    for key in ("speaker", "speaker_name", "speaker_label", "speaker_id", "participant", "author"):
        if key in segment and segment.get(key) not in (None, ""):
            return segment.get(key)
    return None


def _extract_transcript_words(transcript: Dict[str, Any]) -> List[Dict[str, Any]]:
    direct_words = transcript.get("words")
    if isinstance(direct_words, list):
        return [item for item in direct_words if isinstance(item, dict)]

    segments = _extract_transcript_segments(transcript)
    words: List[Dict[str, Any]] = []
    for segment in segments:
        segment_words = segment.get("words")
        if not isinstance(segment_words, list):
            continue
        speaker = _extract_speaker_value(segment)
        for word in segment_words:
            if not isinstance(word, dict):
                continue
            words.append({**word, "_speaker": speaker})
    if words:
        return words

    nested_result = transcript.get("result")
    if isinstance(nested_result, dict):
        nested_sentences = nested_result.get("sentences")
        if isinstance(nested_sentences, list):
            for sentence in nested_sentences:
                if not isinstance(sentence, dict):
                    continue
                sentence_words = sentence.get("words")
                if not isinstance(sentence_words, list):
                    continue
                speaker = _extract_speaker_value(sentence)
                for word in sentence_words:
                    if not isinstance(word, dict):
                        continue
                    words.append({**word, "_speaker": speaker})
    return words


def _get_segment_start_value(segment: Dict[str, Any]) -> Any:
    start_value = segment.get("start")
    if start_value is None:
        start_value = segment.get("start_time")
    if start_value is None:
        start_value = segment.get("offset")
    if start_value is None and segment.get("start_ms") is not None:
        start_seconds = _coerce_seconds(segment.get("start_ms"))
        start_value = start_seconds / 1000 if start_seconds is not None else None
    return start_value


def _get_segment_end_value(segment: Dict[str, Any]) -> Any:
    end_value = segment.get("end")
    if end_value is None:
        end_value = segment.get("end_time")
    if end_value is None:
        end_value = segment.get("offset_end")
    if end_value is None and segment.get("end_ms") is not None:
        end_seconds = _coerce_seconds(segment.get("end_ms"))
        end_value = end_seconds / 1000 if end_seconds is not None else None
    return end_value


def build_transcript_display_text(transcript_payload: Dict[str, Any]) -> str:
    transcript = _extract_transcript_object(transcript_payload)
    segments = _extract_transcript_segments(transcript)
    if segments:
        lines: List[str] = []
        for segment in segments:
            segment_text = str(segment.get("text") or "").strip()
            if not segment_text:
                continue
            start_value = _get_segment_start_value(segment)
            timestamp_label = format_seconds_label(_coerce_seconds(start_value))
            lines.append(f"[{timestamp_label}] {segment_text}" if timestamp_label else segment_text)
        if lines:
            return "\n".join(lines)
    if isinstance(segments, list) and segments:
        combined_text = " ".join(str(segment.get("text") or "").strip() for segment in segments).strip()
        if combined_text:
            return combined_text
    full_transcript = transcript.get("full_transcript")
    if isinstance(full_transcript, str) and full_transcript.strip():
        return full_transcript.strip()
    sentence_items = _extract_sentence_items(transcript_payload, transcript)
    if sentence_items:
        sentence_lines: List[str] = []
        for sentence in sentence_items:
            sentence_text = str(sentence.get("sentence") or sentence.get("text") or "").strip()
            if not sentence_text:
                continue
            start_value = sentence.get("start") or sentence.get("start_time")
            timestamp_label = format_seconds_label(_coerce_seconds(start_value))
            sentence_lines.append(f"[{timestamp_label}] {sentence_text}" if timestamp_label else sentence_text)
        if sentence_lines:
            return "\n".join(sentence_lines)
    words = _extract_transcript_words(transcript)
    if words:
        joined_words = " ".join(str(word.get("word") or word.get("text") or "").strip() for word in words).strip()
        if joined_words:
            return re.sub(r"\s+", " ", joined_words).strip()
    return str(transcript.get("text") or "").strip()


def build_transcript_plain_text(transcript_payload: Dict[str, Any]) -> str:
    transcript = _extract_transcript_object(transcript_payload)
    direct_text = str(transcript.get("text") or "").strip()
    if direct_text:
        return direct_text
    full_transcript = str(transcript.get("full_transcript") or "").strip()
    if full_transcript:
        return full_transcript

    segments = _extract_transcript_segments(transcript)
    if segments:
        combined_segments = " ".join(str(segment.get("text") or "").strip() for segment in segments).strip()
        if combined_segments:
            return re.sub(r"\s+", " ", combined_segments).strip()

    sentence_items = _extract_sentence_items(transcript_payload, transcript)
    if sentence_items:
        combined_sentences = " ".join(
            str(sentence.get("sentence") or sentence.get("text") or "").strip()
            for sentence in sentence_items
        ).strip()
        if combined_sentences:
            return re.sub(r"\s+", " ", combined_sentences).strip()

    words = _extract_transcript_words(transcript)
    if words:
        joined_words = " ".join(str(word.get("word") or word.get("text") or "").strip() for word in words).strip()
        if joined_words:
            return re.sub(r"\s+", " ", joined_words).strip()
    return ""


def build_transcript_segments_df(transcript_payload: Dict[str, Any]) -> pd.DataFrame:
    transcript = _extract_transcript_object(transcript_payload)
    segments = _extract_transcript_segments(transcript)
    if not segments:
        return pd.DataFrame()

    rows: List[Dict[str, Any]] = []
    for segment in segments:
        text = str(segment.get("text") or "").strip()
        start_value = _get_segment_start_value(segment)
        end_value = _get_segment_end_value(segment)
        speaker = _extract_speaker_value(segment)

        start_seconds = _coerce_seconds(start_value)
        end_seconds = _coerce_seconds(end_value)
        duration_seconds = max(end_seconds - start_seconds, 0.0) if start_seconds is not None and end_seconds is not None else None
        word_count = len(re.findall(r"\S+", text))
        rows.append(
            {
                "segment_id": segment.get("id"),
                "start_seconds": start_seconds,
                "end_seconds": end_seconds,
                "start_label": format_seconds_label(start_seconds),
                "duration_seconds": duration_seconds,
                "text": text,
                "speaker": str(speaker).strip() if speaker not in (None, "") else "Unknown speaker",
                "word_count": word_count,
                "words_per_second": round(word_count / duration_seconds, 2) if duration_seconds and duration_seconds > 0 else None,
            }
        )

    df = pd.DataFrame(rows)
    if not df.empty and "start_seconds" in df.columns:
        df["minute_bucket"] = df["start_seconds"].fillna(0).astype(float).floordiv(60).astype(int)
        df["minute_label"] = df["minute_bucket"].apply(lambda value: format_seconds_label(value * 60))
    return df


def extract_common_terms_from_series(text_series: pd.Series, top_n: int = 12) -> pd.DataFrame:
    def normalize_term(term: str) -> str:
        cleaned = term.lower().strip("'")
        if len(cleaned) > 5 and cleaned.endswith("ing"):
            cleaned = cleaned[:-3]
        elif len(cleaned) > 4 and cleaned.endswith("ed"):
            cleaned = cleaned[:-2]
        elif len(cleaned) > 4 and cleaned.endswith("es"):
            cleaned = cleaned[:-2]
        elif len(cleaned) > 3 and cleaned.endswith("s"):
            cleaned = cleaned[:-1]
        if cleaned.endswith("'"):
            cleaned = cleaned[:-1]
        return cleaned

    text = " ".join(text_series.fillna("").astype(str).tolist()).lower()
    raw_terms = re.findall(r"\b[\w']{3,}\b", text)
    normalized_terms = [normalize_term(term) for term in raw_terms]
    filtered = [
        term for term in normalized_terms
        if term
        and term not in COMMON_STOPWORDS
        and not term.isdigit()
        and len(term) >= 3
    ]
    counts = Counter(filtered).most_common(top_n)
    return pd.DataFrame(counts, columns=["term", "count"]) if counts else pd.DataFrame(columns=["term", "count"])


def extract_raw_terms_from_series(text_series: pd.Series, top_n: int = 10) -> pd.DataFrame:
    text = " ".join(text_series.fillna("").astype(str).tolist()).lower()
    normalized_text = text.replace("’", "'")
    terms = [
        term.strip("'") for term in re.findall(r"\b[\w']+\b", normalized_text)
        if term
        and not term.isdigit()
        and term.strip("'") not in COMMON_STOPWORDS
        and len(term.strip("'")) >= 3
    ]
    counts = Counter(terms).most_common(top_n)
    return pd.DataFrame(counts, columns=["term", "count"]) if counts else pd.DataFrame(columns=["term", "count"])


def extract_meaningful_terms_from_series(text_series: pd.Series, top_n: int = 10) -> pd.DataFrame:
    normalized_segments = text_series.fillna("").astype(str).str.lower().str.replace("’", "'", regex=False)
    token_pattern = re.compile(r"\b[\w']+\b")

    term_counts: Counter[str] = Counter()
    segment_counts: Counter[str] = Counter()
    total_segments = 0

    for segment_text in normalized_segments:
        tokens = [token.strip("'") for token in token_pattern.findall(segment_text)]
        filtered_tokens = [
            token for token in tokens
            if token
            and len(token) >= 3
            and not token.isdigit()
            and token not in COMMON_STOPWORDS
            and "'" not in token
        ]
        if not filtered_tokens:
            continue
        total_segments += 1
        term_counts.update(filtered_tokens)
        segment_counts.update(set(filtered_tokens))

    if not term_counts or total_segments == 0:
        return pd.DataFrame(columns=["term", "count", "segment_count", "score"])

    scored_rows: List[Dict[str, Any]] = []
    for term, count in term_counts.items():
        doc_freq = int(segment_counts.get(term, 0))
        if doc_freq <= 0:
            continue
        coverage_ratio = doc_freq / total_segments
        if coverage_ratio >= 0.8:
            continue
        score = count * math.log1p(total_segments / doc_freq)
        scored_rows.append(
            {
                "term": term,
                "count": int(count),
                "segment_count": doc_freq,
                "score": round(score, 3),
            }
        )

    if not scored_rows:
        return pd.DataFrame(columns=["term", "count", "segment_count", "score"])

    scored_df = pd.DataFrame(scored_rows).sort_values(
        by=["score", "count", "segment_count", "term"],
        ascending=[False, False, True, True],
    )
    return scored_df.head(top_n).reset_index(drop=True)


def _extract_sentence_items(transcript_payload: Dict[str, Any], transcript: Dict[str, Any]) -> List[Dict[str, Any]]:
    direct_sentences = transcript.get("sentences")
    if isinstance(direct_sentences, list):
        return [item for item in direct_sentences if isinstance(item, dict)]

    result = transcript_payload.get("result")
    if isinstance(result, dict):
        transcription = result.get("transcription")
        if isinstance(transcription, dict) and isinstance(transcription.get("sentences"), list):
            return [item for item in transcription.get("sentences") if isinstance(item, dict)]
        sentences_payload = result.get("sentences")
        if isinstance(sentences_payload, dict) and isinstance(sentences_payload.get("results"), list):
            return [item for item in sentences_payload.get("results") if isinstance(item, dict)]
    return []


def _extract_named_entity_items(transcript_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    transcript = _extract_transcript_object(transcript_payload)
    candidate_containers: List[Dict[str, Any]] = []

    if isinstance(transcript_payload, dict):
        candidate_containers.append(transcript_payload)

    result = transcript_payload.get("result")
    if isinstance(result, dict):
        candidate_containers.append(result)

    if isinstance(transcript, dict):
        candidate_containers.append(transcript)
        nested_result = transcript.get("result")
        if isinstance(nested_result, dict):
            candidate_containers.append(nested_result)

    for container in candidate_containers:
        ner_payload = container.get("named_entity_recognition")
        if isinstance(ner_payload, dict) and isinstance(ner_payload.get("results"), list):
            return [item for item in ner_payload.get("results") if isinstance(item, dict)]
        if isinstance(ner_payload, list):
            return [item for item in ner_payload if isinstance(item, dict)]

    direct_entities = transcript_payload.get("named_entities")
    if isinstance(direct_entities, list):
        return [item for item in direct_entities if isinstance(item, dict)]
    return []


def _bucket_seconds(value: Optional[float], bucket_size_seconds: int) -> Optional[int]:
    if value is None or pd.isna(value):
        return None
    return int(float(value) // bucket_size_seconds) * bucket_size_seconds


def _safe_quantile(series: pd.Series, q: float, default: float = 0.0) -> float:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if clean.empty:
        return default
    return float(clean.quantile(q))


def _min_max_scale(value: float, low: float, high: float) -> float:
    if high <= low:
        return 0.5
    return min(max((float(value) - low) / (high - low), 0.0), 1.0)


def apply_speaker_name_map_to_insights(insights: Dict[str, Any], speaker_name_map: Dict[str, str]) -> Dict[str, Any]:
    if not isinstance(insights, dict) or not isinstance(speaker_name_map, dict) or not speaker_name_map:
        return insights

    normalized_map = {
        str(key).strip(): str(value).strip()
        for key, value in speaker_name_map.items()
        if str(key).strip() and str(value).strip()
    }
    if not normalized_map:
        return insights

    def map_speaker(value: Any) -> Any:
        key = str(value).strip()
        return normalized_map.get(key, value)

    def map_speaker_pair(value: Any) -> Any:
        if not isinstance(value, str) or "->" not in value:
            return value
        left, right = [part.strip() for part in value.split("->", 1)]
        return f"{normalized_map.get(left, left)} -> {normalized_map.get(right, right)}"

    transformed: Dict[str, Any] = {}
    for key, value in insights.items():
        if isinstance(value, pd.DataFrame):
            df = value.copy()
            for speaker_column in ["speaker", "from_speaker", "to_speaker", "dominant_speaker"]:
                if speaker_column in df.columns:
                    df[speaker_column] = df[speaker_column].apply(map_speaker)
            if "speaker_pair" in df.columns:
                df["speaker_pair"] = df["speaker_pair"].apply(map_speaker_pair)
            transformed[key] = df
        else:
            transformed[key] = value
    return transformed


def build_transcript_insights(transcript_payload: Dict[str, Any]) -> Dict[str, Any]:
    segments_df = build_transcript_segments_df(transcript_payload)
    if segments_df.empty:
        return {
            "segments_df": segments_df,
            "words_df": pd.DataFrame(),
            "sentence_df": pd.DataFrame(),
            "timeline_df": pd.DataFrame(),
            "pace_df": pd.DataFrame(),
            "silence_df": pd.DataFrame(),
            "pause_type_df": pd.DataFrame(),
            "pause_distribution_df": pd.DataFrame(),
            "speaker_df": pd.DataFrame(),
            "speaker_turns_df": pd.DataFrame(),
            "filler_df": pd.DataFrame(),
            "sentence_length_df": pd.DataFrame(),
            "confidence_distribution_df": pd.DataFrame(),
            "engagement_df": pd.DataFrame(),
            "low_energy_df": pd.DataFrame(),
            "burst_df": pd.DataFrame(),
            "burst_distribution_df": pd.DataFrame(),
            "utterance_duration_distribution_df": pd.DataFrame(),
            "topic_timeline_df": pd.DataFrame(),
            "named_entities_df": pd.DataFrame(),
            "numbers_df": pd.DataFrame(),
            "interruptions_df": pd.DataFrame(),
            "response_time_df": pd.DataFrame(),
            "key_moments_df": pd.DataFrame(),
            "replay_navigation_df": pd.DataFrame(),
            "segment_mix_df": pd.DataFrame(),
            "terms_df": pd.DataFrame(),
            "summary": {},
        }

    transcript = _extract_transcript_object(transcript_payload)
    ordered_df = segments_df.sort_values(by=["start_seconds", "end_seconds"], na_position="last").reset_index(drop=True)
    transcript_duration_seconds = _coerce_seconds(transcript.get("duration_seconds"))
    bucket_size_seconds = 60

    word_rows: List[Dict[str, Any]] = []
    for word in _extract_transcript_words(transcript):
        word_text = str(word.get("word") or word.get("text") or "").strip()
        start_seconds = _coerce_seconds(word.get("start") or word.get("start_time"))
        end_seconds = _coerce_seconds(word.get("end") or word.get("end_time"))
        speaker = word.get("_speaker")
        if speaker in (None, ""):
            for key in ("speaker", "speaker_id", "speaker_name"):
                if key in word and word.get(key) not in (None, ""):
                    speaker = word.get(key)
                    break
        if not word_text or start_seconds is None or end_seconds is None:
            continue
        word_rows.append(
            {
                "word": word_text,
                "start_seconds": start_seconds,
                "end_seconds": end_seconds,
                "duration_seconds": max(end_seconds - start_seconds, 0.0),
                "speaker": str(speaker).strip() if speaker not in (None, "") else "Unknown speaker",
                "confidence": _coerce_seconds(word.get("confidence")),
            }
        )
    words_df = (
        pd.DataFrame(word_rows).sort_values(by=["start_seconds", "end_seconds"], na_position="last").reset_index(drop=True)
        if word_rows else pd.DataFrame(columns=["word", "start_seconds", "end_seconds", "duration_seconds", "speaker", "confidence"])
    )
    if not words_df.empty:
        words_df["bucket_start_seconds"] = words_df["start_seconds"].apply(lambda value: _bucket_seconds(value, bucket_size_seconds))
        words_df["bucket_label"] = words_df["bucket_start_seconds"].apply(format_seconds_label)

    sentence_rows: List[Dict[str, Any]] = []
    for sentence in _extract_sentence_items(transcript_payload, transcript):
        sentence_text = str(sentence.get("sentence") or sentence.get("text") or "").strip()
        start_seconds = _coerce_seconds(sentence.get("start") or sentence.get("start_time"))
        end_seconds = _coerce_seconds(sentence.get("end") or sentence.get("end_time"))
        speaker = sentence.get("speaker")
        if speaker in (None, ""):
            speaker = _extract_speaker_value(sentence)
        sentence_words = sentence.get("words") if isinstance(sentence.get("words"), list) else []
        if not sentence_words and sentence_text:
            sentence_word_count = len(re.findall(r"\S+", sentence_text))
            avg_confidence = _coerce_seconds(sentence.get("confidence"))
        else:
            sentence_word_count = len([word for word in sentence_words if isinstance(word, dict)])
            confidences = [
                _coerce_seconds(word.get("confidence"))
                for word in sentence_words
                if isinstance(word, dict) and _coerce_seconds(word.get("confidence")) is not None
            ]
            avg_confidence = round(sum(confidences) / len(confidences), 3) if confidences else _coerce_seconds(sentence.get("confidence"))
        sentence_rows.append(
            {
                "sentence": sentence_text,
                "start_seconds": start_seconds,
                "end_seconds": end_seconds,
                "speaker": str(speaker).strip() if speaker not in (None, "") else "Unknown speaker",
                "word_count": sentence_word_count,
                "duration_seconds": max(float(end_seconds) - float(start_seconds), 0.0) if start_seconds is not None and end_seconds is not None else None,
                "confidence": avg_confidence,
            }
        )
    sentence_df = pd.DataFrame(sentence_rows)
    if not sentence_df.empty:
        sentence_df = sentence_df.sort_values(by=["start_seconds", "end_seconds"], na_position="last").reset_index(drop=True)
        sentence_df["bucket_start_seconds"] = sentence_df["start_seconds"].apply(lambda value: _bucket_seconds(value, bucket_size_seconds))
        sentence_df["bucket_label"] = sentence_df["bucket_start_seconds"].apply(format_seconds_label)

    timeline_rows: List[Dict[str, Any]] = []
    for _, row in ordered_df.iterrows():
        start_seconds = row.get("start_seconds")
        end_seconds = row.get("end_seconds")
        duration_seconds = row.get("duration_seconds")
        word_count = int(row.get("word_count") or 0)

        if pd.isna(start_seconds):
            continue

        if pd.isna(end_seconds) or pd.isna(duration_seconds) or float(duration_seconds) <= 0:
            minute_bucket = int(float(start_seconds) // 60)
            timeline_rows.append(
                {
                    "minute_bucket": minute_bucket,
                    "word_count": word_count,
                    "speaking_seconds": 0.0,
                    "segment_count": 1,
                }
            )
            continue

        start_seconds = float(start_seconds)
        end_seconds = float(end_seconds)
        duration_seconds = float(duration_seconds)
        current_minute = int(start_seconds // 60)
        last_minute = int(max(end_seconds - 1e-9, start_seconds) // 60)

        for minute_bucket in range(current_minute, last_minute + 1):
            bucket_start = minute_bucket * 60
            bucket_end = bucket_start + 60
            overlap_seconds = max(0.0, min(end_seconds, bucket_end) - max(start_seconds, bucket_start))
            if overlap_seconds <= 0:
                continue
            timeline_rows.append(
                {
                    "minute_bucket": minute_bucket,
                    "word_count": (word_count * overlap_seconds) / duration_seconds if duration_seconds > 0 else word_count,
                    "speaking_seconds": overlap_seconds,
                    "segment_count": 1,
                }
            )

    timeline_df = pd.DataFrame(timeline_rows)
    if not timeline_df.empty:
        timeline_df = (
            timeline_df.groupby("minute_bucket", as_index=False)
            .agg(
                word_count=("word_count", "sum"),
                speaking_seconds=("speaking_seconds", "sum"),
                segment_count=("segment_count", "sum"),
            )
        )
        timeline_df["word_count"] = timeline_df["word_count"].round(1)
        timeline_df["minute_label"] = timeline_df["minute_bucket"].apply(lambda value: format_seconds_label(value * 60))
    else:
        timeline_df = pd.DataFrame(columns=["minute_bucket", "word_count", "speaking_seconds", "segment_count", "minute_label"])

    timeline_df["words_per_minute"] = timeline_df.apply(
        lambda row: round((row["word_count"] / row["speaking_seconds"]) * 60, 1)
        if pd.notna(row["speaking_seconds"]) and row["speaking_seconds"] and row["speaking_seconds"] > 0
        else 0.0,
        axis=1,
    )

    pace_df = ordered_df.copy()
    pace_df["time_seconds"] = pace_df["start_seconds"]
    pace_df["time_label"] = pace_df["time_seconds"].apply(format_seconds_label)
    pace_df["segment_wpm"] = pace_df["words_per_second"].apply(
        lambda value: round(float(value) * 60, 1) if pd.notna(value) else 0.0
    )

    silence_rows: List[Dict[str, Any]] = []
    pause_threshold_seconds = 0.75
    silence_source_df = words_df.copy() if not words_df.empty else ordered_df.copy()
    first_start = silence_source_df["start_seconds"].dropna().min() if not silence_source_df.empty else None
    last_end = silence_source_df["end_seconds"].dropna().max() if not silence_source_df.empty else None

    def classify_pause(gap_seconds: float) -> str:
        if gap_seconds < 0.3:
            return "Natural flow"
        if gap_seconds < 1.0:
            return "Thinking pause"
        if gap_seconds < 2.0:
            return "Hesitation"
        return "Strong silence"

    if first_start is not None and not pd.isna(first_start) and float(first_start) >= pause_threshold_seconds:
        silence_rows.append(
            {
                "silence_start_seconds": 0.0,
                "silence_end_seconds": float(first_start),
                "silence_start_label": format_seconds_label(0.0),
                "silence_end_label": format_seconds_label(float(first_start)),
                "gap_seconds": round(float(first_start), 2),
                "pause_type": classify_pause(float(first_start)),
                "speaker_transition": False,
                "bucket_start_seconds": _bucket_seconds(float(first_start), bucket_size_seconds),
            }
        )

    for idx in range(1, len(silence_source_df.index)):
        previous_end = silence_source_df.loc[idx - 1, "end_seconds"]
        current_start = silence_source_df.loc[idx, "start_seconds"]
        if pd.isna(previous_end) or pd.isna(current_start):
            continue
        gap_seconds = float(current_start) - float(previous_end)
        if gap_seconds >= pause_threshold_seconds:
            previous_speaker = silence_source_df.loc[idx - 1, "speaker"]
            current_speaker = silence_source_df.loc[idx, "speaker"]
            silence_rows.append(
                {
                    "silence_start_seconds": float(previous_end),
                    "silence_end_seconds": float(current_start),
                    "silence_start_label": format_seconds_label(float(previous_end)),
                    "silence_end_label": format_seconds_label(float(current_start)),
                    "gap_seconds": round(gap_seconds, 2),
                    "pause_type": classify_pause(gap_seconds),
                    "speaker_transition": (
                        previous_speaker not in (None, "")
                        and current_speaker not in (None, "")
                        and previous_speaker != current_speaker
                    ),
                    "bucket_start_seconds": _bucket_seconds(float(previous_end), bucket_size_seconds),
                }
            )
    if (
        transcript_duration_seconds is not None
        and last_end is not None
        and not pd.isna(last_end)
        and float(transcript_duration_seconds) - float(last_end) >= pause_threshold_seconds
    ):
        silence_rows.append(
            {
                "silence_start_seconds": float(last_end),
                "silence_end_seconds": float(transcript_duration_seconds),
                "silence_start_label": format_seconds_label(float(last_end)),
                "silence_end_label": format_seconds_label(float(transcript_duration_seconds)),
                "gap_seconds": round(float(transcript_duration_seconds) - float(last_end), 2),
                "pause_type": classify_pause(float(transcript_duration_seconds) - float(last_end)),
                "speaker_transition": False,
                "bucket_start_seconds": _bucket_seconds(float(last_end), bucket_size_seconds),
            }
        )
    silence_df = pd.DataFrame(silence_rows)
    if not silence_df.empty:
        silence_df["bucket_label"] = silence_df["bucket_start_seconds"].apply(format_seconds_label)

    pause_type_df = pd.DataFrame(columns=["pause_type", "count", "total_gap_seconds"])
    if not silence_df.empty:
        pause_type_df = (
            silence_df.groupby("pause_type", as_index=False)
            .agg(
                count=("gap_seconds", "size"),
                total_gap_seconds=("gap_seconds", "sum"),
            )
        )
        pause_order = ["Thinking pause", "Hesitation", "Strong silence"]
        pause_type_df["pause_type"] = pd.Categorical(
            pause_type_df["pause_type"],
            categories=pause_order,
            ordered=True,
        )
        pause_type_df = pause_type_df.sort_values("pause_type").reset_index(drop=True)

    pause_distribution_df = pd.DataFrame(columns=["duration_bin", "count"])
    if not silence_df.empty:
        histogram_source = silence_df.copy()
        histogram_source["duration_bin"] = pd.cut(
            histogram_source["gap_seconds"],
            bins=[0.75, 1.0, 2.0, 3.0, float("inf")],
            labels=["0.75-1s", "1-2s", "2-3s", "3s+"],
            include_lowest=True,
            right=False,
        )
        pause_distribution_df = (
            histogram_source.groupby("duration_bin", as_index=False, observed=False)
            .agg(count=("gap_seconds", "size"))
        )

    speaker_df = (
        ordered_df.groupby("speaker", as_index=False)
        .agg(
            speaking_seconds=("duration_seconds", "sum"),
            segments=("text", "size"),
            words=("word_count", "sum"),
        )
        if "speaker" in ordered_df.columns
        else pd.DataFrame(columns=["speaker", "speaking_seconds", "segments", "words"])
    )
    if not speaker_df.empty:
        total_speaking = float(speaker_df["speaking_seconds"].fillna(0).sum())
        speaker_df["speaking_seconds"] = speaker_df["speaking_seconds"].fillna(0.0)
        speaker_df["speaking_label"] = speaker_df["speaking_seconds"].apply(format_seconds_label)
        speaker_df["share_pct"] = speaker_df["speaking_seconds"].apply(
            lambda value: round((float(value) / total_speaking) * 100, 1) if total_speaking > 0 else 0.0
        )
        speaker_df = speaker_df.sort_values(by=["speaking_seconds", "words"], ascending=[False, False]).reset_index(drop=True)

    speaker_turns_df = ordered_df.copy()
    if not speaker_turns_df.empty:
        speaker_turns_df["end_seconds"] = speaker_turns_df["end_seconds"].fillna(speaker_turns_df["start_seconds"])
        speaker_turns_df["duration_seconds"] = speaker_turns_df["duration_seconds"].fillna(0.0)
        speaker_turns_df["excerpt"] = speaker_turns_df["text"].fillna("").astype(str).str.slice(0, 90)

    normalized_text = " ".join(words_df["word"].astype(str).tolist()).lower() if not words_df.empty else " ".join(ordered_df["text"].fillna("").astype(str).tolist()).lower()
    normalized_text = normalized_text.replace("’", "'")
    filler_patterns = {
        "uh": r"\buh+\b",
        "um": r"\bum+\b",
        "you know": r"\byou know\b",
        "so": r"\bso\b",
    }
    filler_rows = []
    total_words = int(ordered_df["word_count"].sum())
    for filler, pattern in filler_patterns.items():
        count = len(re.findall(pattern, normalized_text))
        filler_rows.append(
            {
                "filler": filler,
                "count": count,
                "per_1000_words": round((count / total_words) * 1000, 2) if total_words > 0 else 0.0,
            }
        )
    filler_df = pd.DataFrame(filler_rows).sort_values(by=["count", "filler"], ascending=[False, True]).reset_index(drop=True)

    sentence_length_df = pd.DataFrame(columns=["sentence_length_bin", "count"])
    if not sentence_df.empty:
        sentence_length_working = sentence_df.copy()
        sentence_length_working["sentence_length_bin"] = pd.cut(
            sentence_length_working["word_count"],
            bins=[0, 5, 10, 20, 30, float("inf")],
            labels=["1-5", "6-10", "11-20", "21-30", "31+"],
            include_lowest=True,
            right=True,
        )
        sentence_length_df = (
            sentence_length_working.groupby("sentence_length_bin", as_index=False, observed=False)
            .agg(count=("word_count", "size"))
        )

    confidence_distribution_df = pd.DataFrame(columns=["confidence_bin", "count"])
    if not words_df.empty and "confidence" in words_df.columns:
        confidence_working = words_df.dropna(subset=["confidence"]).copy()
        if not confidence_working.empty:
            confidence_working["confidence_bin"] = pd.cut(
                confidence_working["confidence"],
                bins=[0.0, 0.2, 0.4, 0.6, 0.8, 1.01],
                labels=["0.0-0.2", "0.2-0.4", "0.4-0.6", "0.6-0.8", "0.8-1.0"],
                include_lowest=True,
                right=False,
            )
            confidence_distribution_df = (
                confidence_working.groupby("confidence_bin", as_index=False, observed=False)
                .agg(count=("confidence", "size"))
            )

    interruption_rows: List[Dict[str, Any]] = []
    response_rows: List[Dict[str, Any]] = []
    for idx in range(1, len(ordered_df.index)):
        prev_row = ordered_df.loc[idx - 1]
        curr_row = ordered_df.loc[idx]
        if prev_row.get("speaker") == curr_row.get("speaker"):
            continue
        prev_end = prev_row.get("end_seconds")
        curr_start = curr_row.get("start_seconds")
        if pd.isna(prev_end) or pd.isna(curr_start):
            continue
        gap_seconds = round(float(curr_start) - float(prev_end), 3)
        response_rows.append(
            {
                "from_speaker": prev_row.get("speaker"),
                "to_speaker": curr_row.get("speaker"),
                "gap_seconds": gap_seconds,
                "from_end_label": format_seconds_label(float(prev_end)),
                "to_start_label": format_seconds_label(float(curr_start)),
            }
        )
        if gap_seconds <= 0.2:
            interruption_rows.append(
                {
                    "from_speaker": prev_row.get("speaker"),
                    "to_speaker": curr_row.get("speaker"),
                    "gap_seconds": gap_seconds,
                    "kind": "Overlap" if gap_seconds < 0 else "Rapid handoff",
                    "time_seconds": float(curr_start),
                    "time_label": format_seconds_label(float(curr_start)),
                }
            )
    interruptions_df = pd.DataFrame(interruption_rows)
    response_time_df = pd.DataFrame(response_rows)
    if not response_time_df.empty:
        response_time_df["speaker_pair"] = response_time_df["from_speaker"].astype(str) + " -> " + response_time_df["to_speaker"].astype(str)
    if not interruptions_df.empty:
        interruptions_df["speaker_pair"] = interruptions_df["from_speaker"].astype(str) + " -> " + interruptions_df["to_speaker"].astype(str)

    burst_rows: List[Dict[str, Any]] = []
    if not ordered_df.empty:
        current_burst = None
        for _, row in ordered_df.iterrows():
            start_seconds = row.get("start_seconds")
            end_seconds = row.get("end_seconds")
            if pd.isna(start_seconds) or pd.isna(end_seconds):
                continue
            if current_burst is None:
                current_burst = {
                    "speaker": row.get("speaker"),
                    "start_seconds": float(start_seconds),
                    "end_seconds": float(end_seconds),
                    "word_count": int(row.get("word_count") or 0),
                }
                continue
            gap_from_previous = float(start_seconds) - float(current_burst["end_seconds"])
            if row.get("speaker") == current_burst["speaker"] and gap_from_previous < pause_threshold_seconds:
                current_burst["end_seconds"] = float(end_seconds)
                current_burst["word_count"] += int(row.get("word_count") or 0)
            else:
                burst_rows.append(current_burst)
                current_burst = {
                    "speaker": row.get("speaker"),
                    "start_seconds": float(start_seconds),
                    "end_seconds": float(end_seconds),
                    "word_count": int(row.get("word_count") or 0),
                }
        if current_burst is not None:
            burst_rows.append(current_burst)
    burst_df = pd.DataFrame(burst_rows)
    burst_distribution_df = pd.DataFrame(columns=["burst_duration_bin", "count"])
    if not burst_df.empty:
        burst_df["duration_seconds"] = (burst_df["end_seconds"] - burst_df["start_seconds"]).round(2)
        burst_df["start_label"] = burst_df["start_seconds"].apply(format_seconds_label)
        burst_df["burst_duration_bin"] = pd.cut(
            burst_df["duration_seconds"],
            bins=[0, 10, 30, 60, float("inf")],
            labels=["0-10s", "10-30s", "30-60s", "60s+"],
            include_lowest=True,
            right=False,
        )
        burst_distribution_df = burst_df.groupby("burst_duration_bin", as_index=False, observed=False).agg(count=("speaker", "size"))

    utterance_duration_distribution_df = pd.DataFrame(columns=["duration_bin", "count"])
    if not ordered_df.empty:
        utterance_duration_working = ordered_df.dropna(subset=["duration_seconds"]).copy()
        if not utterance_duration_working.empty:
            utterance_duration_working["duration_bin"] = pd.cut(
                utterance_duration_working["duration_seconds"],
                bins=[0, 5, 10, 20, 30, float("inf")],
                labels=["0-5s", "5-10s", "10-20s", "20-30s", "30s+"],
                include_lowest=True,
                right=False,
            )
            utterance_duration_distribution_df = (
                utterance_duration_working.groupby("duration_bin", as_index=False, observed=False)
                .agg(count=("duration_seconds", "size"))
            )

    segment_mix_df = ordered_df.copy()
    segment_mix_df["segment_style"] = segment_mix_df["duration_seconds"].apply(
        lambda value: "Quick hits" if pd.notna(value) and float(value) < 5
        else "Steady beats" if pd.notna(value) and float(value) < 10
        else "Long stretches"
    )
    segment_mix_df = (
        segment_mix_df.groupby("segment_style", as_index=False)
        .agg(
            segments=("text", "size"),
            words=("word_count", "sum"),
            speaking_seconds=("duration_seconds", "sum"),
        )
    )
    segment_style_order = ["Quick hits", "Steady beats", "Long stretches"]
    if not segment_mix_df.empty:
        segment_mix_df["segment_style"] = pd.Categorical(
            segment_mix_df["segment_style"],
            categories=segment_style_order,
            ordered=True,
        )
        segment_mix_df = segment_mix_df.sort_values("segment_style").reset_index(drop=True)

    total_words = int(ordered_df["word_count"].sum())
    if not segment_mix_df.empty:
        segment_mix_df["word_share_pct"] = segment_mix_df["words"].apply(
            lambda value: round((float(value) / total_words) * 100, 1) if total_words > 0 else 0.0
        )
        segment_mix_df["speaking_seconds"] = segment_mix_df["speaking_seconds"].fillna(0.0)
        segment_mix_df["speaking_time_label"] = segment_mix_df["speaking_seconds"].apply(format_seconds_label)

    terms_df = extract_meaningful_terms_from_series(ordered_df["text"], top_n=10)

    duration_values = ordered_df["duration_seconds"].dropna()
    total_speaking_seconds = float(duration_values.sum()) if not duration_values.empty else 0.0
    full_span_seconds = 0.0
    valid_starts = ordered_df["start_seconds"].dropna()
    valid_ends = ordered_df["end_seconds"].dropna()
    if not valid_starts.empty and not valid_ends.empty:
        full_span_seconds = max(float(valid_ends.max()) - float(valid_starts.min()), 0.0)

    raw_named_entity_rows = []
    for entity in _extract_named_entity_items(transcript_payload):
        entity_text = str(entity.get("text") or "").strip()
        entity_type = str(entity.get("entity_type") or entity.get("type") or "Unknown").strip()
        start_seconds = _coerce_seconds(entity.get("start"))
        if not entity_text:
            continue
        raw_named_entity_rows.append(
            {
                "entity": entity_text,
                "entity_type": entity_type,
                "start_seconds": start_seconds,
                "time_label": format_seconds_label(start_seconds),
            }
        )
    named_entities_df = pd.DataFrame(raw_named_entity_rows)
    if not named_entities_df.empty:
        named_entities_df = (
            named_entities_df.groupby(["entity", "entity_type"], as_index=False)
            .agg(
                count=("entity", "size"),
                first_seen_seconds=("start_seconds", "min"),
            )
            .sort_values(by=["count", "first_seen_seconds", "entity"], ascending=[False, True, True])
            .reset_index(drop=True)
        )
        named_entities_df["first_seen_label"] = named_entities_df["first_seen_seconds"].apply(format_seconds_label)

    numbers_rows = []
    source_for_entities = sentence_df if not sentence_df.empty else ordered_df
    entity_type_priority = {
        "MONEY": 0,
        "DATE_INTERVAL": 1,
        "EVENT": 2,
        "ORGANIZATION": 3,
        "LOCATION_CITY": 4,
        "LOCATION_COUNTRY": 5,
        "NAME": 6,
        "NAME_GIVEN": 7,
        "OCCUPATION": 8,
        "DURATION": 9,
        "FILENAME": 10,
    }

    def _find_best_entity_context(entity_text: str, entity_start_seconds: Optional[float]) -> Dict[str, Any]:
        if source_for_entities.empty:
            return {}

        best_row = None
        best_score = None
        entity_text_lower = entity_text.lower()
        for _, candidate_row in source_for_entities.iterrows():
            candidate_text = str(candidate_row.get("sentence") or candidate_row.get("text") or "").strip()
            candidate_start = _coerce_seconds(candidate_row.get("start_seconds"))
            candidate_end = _coerce_seconds(candidate_row.get("end_seconds"))
            contains_entity = bool(candidate_text) and entity_text_lower in candidate_text.lower()
            contains_time = (
                entity_start_seconds is not None
                and candidate_start is not None
                and candidate_end is not None
                and float(candidate_start) <= float(entity_start_seconds) <= float(candidate_end)
            )
            if contains_entity and contains_time:
                return candidate_row.to_dict()

            time_distance = abs(float(candidate_start) - float(entity_start_seconds)) if (
                candidate_start is not None and entity_start_seconds is not None
            ) else float("inf")
            score = (
                0 if contains_entity else 1,
                0 if contains_time else 1,
                time_distance,
            )
            if best_score is None or score < best_score:
                best_score = score
                best_row = candidate_row
        return best_row.to_dict() if best_row is not None else {}

    for entity_row in raw_named_entity_rows:
        entity_text = str(entity_row.get("entity") or "").strip()
        entity_type = str(entity_row.get("entity_type") or "Unknown").strip()
        start_seconds = _coerce_seconds(entity_row.get("start_seconds"))
        if not entity_text:
            continue
        context_row = _find_best_entity_context(entity_text, start_seconds)
        numbers_rows.append(
            {
                "mention": entity_text,
                "kind": entity_type,
                "speaker": context_row.get("speaker"),
                "time_seconds": start_seconds if start_seconds is not None else context_row.get("start_seconds"),
                "time_label": format_seconds_label(start_seconds if start_seconds is not None else _coerce_seconds(context_row.get("start_seconds"))),
                "context": str(context_row.get("sentence") or context_row.get("text") or "").strip()[:180],
                "ner_context": "",
                "_priority": entity_type_priority.get(entity_type, 99),
            }
        )
    numbers_df = pd.DataFrame(numbers_rows)
    if not numbers_df.empty:
        numbers_df = (
            numbers_df.drop_duplicates(subset=["mention", "kind", "time_label", "context"])
            .sort_values(by=["_priority", "time_seconds", "mention"], ascending=[True, True, True], na_position="last")
            .drop(columns=["_priority"], errors="ignore")
            .reset_index(drop=True)
        )

    topic_rows = []
    if not ordered_df.empty:
        topic_working = ordered_df.copy()
        topic_working["bucket_start_seconds"] = topic_working["start_seconds"].apply(lambda value: _bucket_seconds(value, bucket_size_seconds))
        for bucket_start, bucket_df in topic_working.groupby("bucket_start_seconds"):
            bucket_terms = extract_meaningful_terms_from_series(bucket_df["text"], top_n=1)
            top_topic = bucket_terms.iloc[0]["term"] if not bucket_terms.empty else None
            dominant_speaker = (
                bucket_df.groupby("speaker")["duration_seconds"].sum().sort_values(ascending=False).index[0]
                if "speaker" in bucket_df.columns and not bucket_df.empty else None
            )
            topic_rows.append(
                {
                    "bucket_start_seconds": bucket_start,
                    "bucket_label": format_seconds_label(bucket_start),
                    "topic": top_topic or "General discussion",
                    "speaker": dominant_speaker,
                }
            )
    topic_timeline_df = pd.DataFrame(topic_rows)

    engagement_df = timeline_df.copy()
    if not engagement_df.empty:
        pause_by_bucket = (
            silence_df.groupby("bucket_start_seconds", as_index=False)
            .agg(
                pause_seconds=("gap_seconds", "sum"),
                pause_count=("gap_seconds", "size"),
            )
            if not silence_df.empty else pd.DataFrame(columns=["bucket_start_seconds", "pause_seconds", "pause_count"])
        )
        if not interruptions_df.empty:
            interruptions_by_bucket = interruptions_df.copy()
            interruptions_by_bucket["bucket_start_seconds"] = interruptions_by_bucket["time_seconds"].apply(
                lambda value: _bucket_seconds(_coerce_seconds(value), bucket_size_seconds)
            )
            interruptions_by_bucket = interruptions_by_bucket.groupby("bucket_start_seconds", as_index=False).agg(
                interruption_count=("kind", "size")
            )
        else:
            interruptions_by_bucket = pd.DataFrame(columns=["bucket_start_seconds", "interruption_count"])

        engagement_df = engagement_df.rename(columns={"minute_bucket": "bucket_index"})
        engagement_df["bucket_start_seconds"] = engagement_df["bucket_index"] * 60
        engagement_df = engagement_df.merge(pause_by_bucket, on="bucket_start_seconds", how="left")
        engagement_df = engagement_df.merge(interruptions_by_bucket, on="bucket_start_seconds", how="left")
        engagement_df["pause_seconds"] = engagement_df["pause_seconds"].fillna(0.0)
        engagement_df["pause_count"] = engagement_df["pause_count"].fillna(0).astype(int)
        engagement_df["interruption_count"] = engagement_df["interruption_count"].fillna(0).astype(int)
        engagement_df["bucket_label"] = engagement_df["bucket_start_seconds"].apply(format_seconds_label)
        engagement_df["silence_ratio"] = engagement_df["pause_seconds"].apply(lambda value: min(float(value) / bucket_size_seconds, 1.0))
        engagement_df["pace_delta"] = engagement_df["words_per_minute"].diff().abs().fillna(0.0)

        wpm_low = _safe_quantile(engagement_df["words_per_minute"], 0.1, 0.0)
        wpm_high = _safe_quantile(engagement_df["words_per_minute"], 0.9, 1.0)
        delta_high = _safe_quantile(engagement_df["pace_delta"], 0.9, 1.0)
        pause_high = _safe_quantile(engagement_df["pause_seconds"], 0.9, 1.0)
        interrupt_high = max(float(engagement_df["interruption_count"].max()), 1.0)

        engagement_scores = []
        low_energy_labels = []
        clarity_scores = []
        cognitive_load_values = []
        for _, row in engagement_df.iterrows():
            pace_score = _min_max_scale(float(row["words_per_minute"]), wpm_low, wpm_high)
            silence_penalty = _min_max_scale(float(row["pause_seconds"]), 0.0, pause_high)
            interruption_score = _min_max_scale(float(row["interruption_count"]), 0.0, interrupt_high)
            variation_score = _min_max_scale(float(row["pace_delta"]), 0.0, delta_high)

            engagement_score = round(((pace_score * 0.45) + ((1 - silence_penalty) * 0.35) + (interruption_score * 0.2)) * 100, 1)
            cognitive_load = round(((silence_penalty * 0.45) + ((1 - pace_score) * 0.2) + (variation_score * 0.2)) * 100, 1)
            clarity_score = round((((1 - silence_penalty) * 0.3) + pace_score * 0.35 + ((1 - min(variation_score, 1.0)) * 0.1)) * 100, 1)
            engagement_scores.append(engagement_score)
            cognitive_load_values.append(cognitive_load)
            clarity_scores.append(clarity_score)

            if row["pause_seconds"] >= 8 or (row["words_per_minute"] <= wpm_low and row["pause_seconds"] >= 4):
                low_energy_labels.append("Low energy")
            elif row["pause_seconds"] >= 4:
                low_energy_labels.append("Watch")
            else:
                low_energy_labels.append("")

        engagement_df["engagement_score"] = engagement_scores
        engagement_df["cognitive_load_index"] = cognitive_load_values
        engagement_df["clarity_score"] = clarity_scores
        engagement_df["energy_label"] = low_energy_labels

        filler_total = float(filler_df["count"].sum()) if not filler_df.empty else 0.0
        filler_per_bucket = pd.DataFrame(columns=["bucket_start_seconds", "filler_count"])
        if not words_df.empty:
            filler_words = {"uh", "um", "so"}
            filler_phrase_counts = []
            filler_word_working = words_df.copy()
            filler_word_working["normalized_word"] = filler_word_working["word"].str.lower().str.replace(r"[^\w']+", "", regex=True)
            filler_hits = filler_word_working[filler_word_working["normalized_word"].isin(filler_words)]
            if not filler_hits.empty:
                filler_phrase_counts.append(
                    filler_hits.groupby("bucket_start_seconds", as_index=False).agg(filler_count=("normalized_word", "size"))
                )
            if not sentence_df.empty:
                sentence_you_know = sentence_df[sentence_df["sentence"].str.lower().str.contains(r"\byou know\b", regex=True, na=False)]
                if not sentence_you_know.empty:
                    filler_phrase_counts.append(
                        sentence_you_know.groupby("bucket_start_seconds", as_index=False).agg(filler_count=("sentence", "size"))
                    )
            if filler_phrase_counts:
                filler_per_bucket = pd.concat(filler_phrase_counts, ignore_index=True).groupby("bucket_start_seconds", as_index=False).agg(
                    filler_count=("filler_count", "sum")
                )
        engagement_df = engagement_df.merge(filler_per_bucket, on="bucket_start_seconds", how="left")
        engagement_df["filler_count"] = engagement_df["filler_count"].fillna(0).astype(int)
        engagement_df["clarity_score"] = (
            engagement_df["clarity_score"] - engagement_df["filler_count"].apply(lambda value: min(value * 3, 20))
        ).clip(lower=0, upper=100)
        engagement_df["cognitive_load_index"] = (
            engagement_df["cognitive_load_index"] + engagement_df["filler_count"].apply(lambda value: min(value * 3, 20))
        ).clip(lower=0, upper=100)

    low_energy_df = engagement_df[engagement_df["energy_label"] != ""].copy() if not engagement_df.empty else pd.DataFrame()

    key_moment_rows = []
    strong_statement_pattern = re.compile(r"\b(?:important|key|must|need to|significant|critical|huge)\b", re.IGNORECASE)
    high_pace_threshold = _safe_quantile(pace_df["segment_wpm"], 0.9, 0.0) if not pace_df.empty else 0.0
    number_lookup = set()
    numeric_signal_kinds = {"MONEY", "DATE_INTERVAL", "DURATION", "EVENT"}
    if not numbers_df.empty:
        for _, number_row in numbers_df.iterrows():
            row_kind = str(number_row.get("kind") or "").strip().upper()
            if row_kind in numeric_signal_kinds:
                number_lookup.add((number_row.get("time_label"), number_row.get("context")))
    entity_texts = set(named_entities_df["entity"].head(25).astype(str).tolist()) if not named_entities_df.empty else set()
    source_for_moments = sentence_df if not sentence_df.empty else ordered_df
    for _, row in source_for_moments.iterrows():
        text = str(row.get("sentence") or row.get("text") or "").strip()
        if not text:
            continue
        normalized_text = re.sub(r"\s+", " ", text).strip()
        word_count = len(re.findall(r"\b\w+\b", normalized_text))
        alpha_word_count = len(re.findall(r"\b[a-zA-Z]{2,}\b", normalized_text))
        if len(normalized_text) < 24 or word_count < 4 or alpha_word_count < 3:
            continue

        score = 0
        reasons = []
        signal_count = 0

        has_numbers_signal = (format_seconds_label(_coerce_seconds(row.get("start_seconds"))), text[:180]) in number_lookup
        if has_numbers_signal:
            score += 2
            reasons.append("numbers")
            signal_count += 1

        has_strong_statement_signal = bool(strong_statement_pattern.search(text))
        if has_strong_statement_signal:
            score += 2
            reasons.append("strong statement")
            signal_count += 1

        has_named_entity_signal = entity_texts and any(entity.lower() in text.lower() for entity in list(entity_texts)[:10])
        if has_named_entity_signal:
            score += 1
            reasons.append("named entity")

        pace_value = 0.0
        if row.get("duration_seconds") and row.get("word_count"):
            pace_value = (float(row.get("word_count")) / float(row.get("duration_seconds"))) * 60 if float(row.get("duration_seconds")) > 0 else 0.0
        has_pace_signal = pace_value >= high_pace_threshold and high_pace_threshold > 0
        if has_pace_signal:
            score += 1
            reasons.append("pace spike")
            signal_count += 1

        if has_named_entity_signal and not (has_numbers_signal or has_strong_statement_signal or has_pace_signal):
            continue

        if signal_count < 2 and score < 3:
            continue

        if score > 0:
            key_moment_rows.append(
                {
                    "time_seconds": row.get("start_seconds"),
                    "time_label": format_seconds_label(_coerce_seconds(row.get("start_seconds"))),
                    "speaker": row.get("speaker"),
                    "score": score,
                    "reasons": ", ".join(reasons),
                    "excerpt": text[:160],
                }
            )
    key_moments_df = pd.DataFrame(key_moment_rows)
    if not key_moments_df.empty:
        key_moments_df = key_moments_df.sort_values(by=["score", "time_seconds"], ascending=[False, True]).head(20).reset_index(drop=True)

    replay_navigation_df = pd.DataFrame()
    if not engagement_df.empty:
        dominant_speakers_by_bucket = (
            ordered_df.assign(bucket_start_seconds=ordered_df["start_seconds"].apply(lambda value: _bucket_seconds(value, bucket_size_seconds)))
            .groupby(["bucket_start_seconds", "speaker"], as_index=False)
            .agg(speaking_seconds=("duration_seconds", "sum"))
        )
        dominant_speaker_df = pd.DataFrame(columns=["bucket_start_seconds", "dominant_speaker"])
        if not dominant_speakers_by_bucket.empty:
            dominant_speaker_df = dominant_speakers_by_bucket.sort_values(
                by=["bucket_start_seconds", "speaking_seconds"], ascending=[True, False]
            ).drop_duplicates(subset=["bucket_start_seconds"]).rename(columns={"speaker": "dominant_speaker"})[
                ["bucket_start_seconds", "dominant_speaker"]
            ]
        key_counts = pd.DataFrame(columns=["bucket_start_seconds", "key_moments"])
        if not key_moments_df.empty:
            key_counts = key_moments_df.copy()
            key_counts["bucket_start_seconds"] = key_counts["time_seconds"].apply(lambda value: _bucket_seconds(_coerce_seconds(value), bucket_size_seconds))
            key_counts = key_counts.groupby("bucket_start_seconds", as_index=False).agg(key_moments=("score", "size"))
        replay_navigation_df = engagement_df[[
            "bucket_start_seconds",
            "bucket_label",
            "engagement_score",
            "clarity_score",
            "cognitive_load_index",
            "pause_seconds",
            "words_per_minute",
            "energy_label",
        ]].merge(topic_timeline_df[["bucket_start_seconds", "topic"]], on="bucket_start_seconds", how="left")
        replay_navigation_df = replay_navigation_df.merge(dominant_speaker_df, on="bucket_start_seconds", how="left")
        replay_navigation_df = replay_navigation_df.merge(key_counts, on="bucket_start_seconds", how="left")
        replay_navigation_df["key_moments"] = replay_navigation_df["key_moments"].fillna(0).astype(int)
        replay_navigation_df["highlight"] = replay_navigation_df.apply(
            lambda row: "Key moment" if row["key_moments"] > 0
            else "Low energy" if str(row["energy_label"]).strip()
            else "Steady",
            axis=1,
        )

    avg_pause_seconds = round(float(silence_df["gap_seconds"].mean()), 2) if not silence_df.empty else 0.0
    pause_count_over_1s = int((silence_df["gap_seconds"] > 1.0).sum()) if not silence_df.empty else 0
    avg_response_seconds = round(float(response_time_df["gap_seconds"].mean()), 2) if not response_time_df.empty else 0.0
    interruption_count = int(len(interruptions_df.index)) if not interruptions_df.empty else 0
    filler_total = int(filler_df["count"].sum()) if not filler_df.empty else 0
    pace_wpm_values = pace_df["segment_wpm"].dropna() if not pace_df.empty else pd.Series(dtype=float)

    summary = {
        "total_segments": int(len(ordered_df.index)),
        "total_words": total_words,
        "avg_words_per_minute": round((total_words / total_speaking_seconds) * 60, 1) if total_speaking_seconds > 0 else 0.0,
        "total_speaking_seconds": round(total_speaking_seconds, 2),
        "full_span_seconds": round(full_span_seconds, 2),
        "silence_count": int(len(silence_df.index)),
        "total_silence_seconds": round(float(silence_df["gap_seconds"].sum()), 2) if not silence_df.empty else 0.0,
        "longest_silence_seconds": round(float(silence_df["gap_seconds"].max()), 2) if not silence_df.empty else 0.0,
        "avg_pause_seconds": avg_pause_seconds,
        "pause_count_over_1s": pause_count_over_1s,
        "pause_timing_source": "word" if not words_df.empty else "segment",
        "global_wpm": round((total_words / total_speaking_seconds) * 60, 1) if total_speaking_seconds > 0 else 0.0,
        "min_wpm": round(float(pace_wpm_values.min()), 1) if not pace_wpm_values.empty else 0.0,
        "max_wpm": round(float(pace_wpm_values.max()), 1) if not pace_wpm_values.empty else 0.0,
        "avg_response_seconds": avg_response_seconds,
        "interruption_count": interruption_count,
        "filler_total": filler_total,
        "entity_count": int(named_entities_df["count"].sum()) if not named_entities_df.empty else 0,
        "kpi_mentions": int(len(numbers_df.index)) if not numbers_df.empty else 0,
        "avg_confidence": round(float(words_df["confidence"].dropna().mean()), 3) if not words_df.empty and not words_df["confidence"].dropna().empty else 0.0,
        "avg_sentence_length": round(float(sentence_df["word_count"].mean()), 1) if not sentence_df.empty else 0.0,
        "avg_engagement_score": round(float(engagement_df["engagement_score"].mean()), 1) if not engagement_df.empty else 0.0,
        "avg_clarity_score": round(float(engagement_df["clarity_score"].mean()), 1) if not engagement_df.empty else 0.0,
        "avg_cognitive_load_index": round(float(engagement_df["cognitive_load_index"].mean()), 1) if not engagement_df.empty else 0.0,
        "low_energy_zone_count": int(len(low_energy_df.index)) if not low_energy_df.empty else 0,
        "top_term": terms_df.iloc[0]["term"] if not terms_df.empty else None,
        "dominant_segment_style": segment_mix_df.iloc[0]["segment_style"] if not segment_mix_df.empty else None,
    }

    return {
        "segments_df": ordered_df,
        "words_df": words_df,
        "sentence_df": sentence_df,
        "timeline_df": timeline_df,
        "pace_df": pace_df,
        "silence_df": silence_df,
        "pause_type_df": pause_type_df,
        "pause_distribution_df": pause_distribution_df,
        "speaker_df": speaker_df,
        "speaker_turns_df": speaker_turns_df,
        "filler_df": filler_df,
        "sentence_length_df": sentence_length_df,
        "confidence_distribution_df": confidence_distribution_df,
        "engagement_df": engagement_df,
        "low_energy_df": low_energy_df,
        "burst_df": burst_df,
        "burst_distribution_df": burst_distribution_df,
        "utterance_duration_distribution_df": utterance_duration_distribution_df,
        "topic_timeline_df": topic_timeline_df,
        "named_entities_df": named_entities_df,
        "numbers_df": numbers_df,
        "interruptions_df": interruptions_df,
        "response_time_df": response_time_df,
        "key_moments_df": key_moments_df,
        "replay_navigation_df": replay_navigation_df,
        "segment_mix_df": segment_mix_df,
        "terms_df": terms_df,
        "summary": summary,
    }


def _normalize_series_to_progress(series: pd.Series) -> pd.Series:
    timestamps = pd.to_datetime(series, utc=True, errors="coerce")
    valid = timestamps.dropna()
    if valid.empty:
        return pd.Series([None] * len(series), index=series.index, dtype="float64")
    min_ts = valid.min()
    max_ts = valid.max()
    duration_seconds = (max_ts - min_ts).total_seconds()
    if duration_seconds <= 0:
        return pd.Series([50.0 if pd.notna(value) else None for value in timestamps], index=series.index, dtype="float64")
    return timestamps.apply(
        lambda value: round(((value - min_ts).total_seconds() / duration_seconds) * 100, 2) if pd.notna(value) else None
    )


def _format_session_stage_label(start_pct: float, end_pct: float) -> str:
    midpoint = (float(start_pct) + float(end_pct)) / 2
    if midpoint < 15:
        stage = "Opening"
    elif midpoint < 35:
        stage = "Early"
    elif midpoint < 65:
        stage = "Middle"
    elif midpoint < 85:
        stage = "Late"
    else:
        stage = "Closing"
    return f"{stage} ({int(start_pct)}-{int(min(end_pct, 100))}%)"


def build_cross_source_insights(
    chat_df: Optional[pd.DataFrame],
    questions_df: Optional[pd.DataFrame],
    transcript_payload: Optional[Dict[str, Any]],
    bucket_size_pct: int = 10,
) -> Dict[str, Any]:
    if not isinstance(chat_df, pd.DataFrame) or not isinstance(questions_df, pd.DataFrame) or not isinstance(transcript_payload, dict):
        return {"combined_timeline_df": pd.DataFrame(), "reaction_moments_df": pd.DataFrame()}

    transcript_insights = build_transcript_insights(transcript_payload)
    segments_df = transcript_insights.get("segments_df", pd.DataFrame())
    if segments_df.empty:
        return {"combined_timeline_df": pd.DataFrame(), "reaction_moments_df": pd.DataFrame()}

    transcript_duration = float(segments_df["end_seconds"].dropna().max()) if not segments_df["end_seconds"].dropna().empty else 0.0
    if transcript_duration <= 0:
        return {"combined_timeline_df": pd.DataFrame(), "reaction_moments_df": pd.DataFrame()}

    working_segments = segments_df.copy()
    working_segments["progress_pct"] = working_segments["start_seconds"].apply(
        lambda value: round((float(value) / transcript_duration) * 100, 2) if pd.notna(value) and transcript_duration > 0 else None
    )
    working_segments["bucket_start_pct"] = working_segments["progress_pct"].apply(
        lambda value: int((float(value) // bucket_size_pct) * bucket_size_pct) if pd.notna(value) else None
    )

    combined_frames: List[pd.DataFrame] = []

    transcript_bins = (
        working_segments.dropna(subset=["bucket_start_pct"])
        .groupby("bucket_start_pct", as_index=False)
        .agg(
            transcript_words=("word_count", "sum"),
            transcript_segments=("text", "size"),
            transcript_pace=("words_per_second", "mean"),
        )
    )
    if not transcript_bins.empty:
        transcript_bins["transcript_wpm"] = transcript_bins["transcript_pace"].apply(
            lambda value: round(float(value) * 60, 1) if pd.notna(value) else 0.0
        )
        combined_frames.append(transcript_bins[["bucket_start_pct", "transcript_words", "transcript_segments", "transcript_wpm"]])

    if "created_at" in chat_df.columns:
        chat_progress = _normalize_series_to_progress(chat_df["created_at"])
        chat_working = chat_df.copy()
        chat_working["progress_pct"] = chat_progress
        chat_working["bucket_start_pct"] = chat_working["progress_pct"].apply(
            lambda value: int((float(value) // bucket_size_pct) * bucket_size_pct) if pd.notna(value) else None
        )
        chat_bins = (
            chat_working.dropna(subset=["bucket_start_pct"])
            .groupby("bucket_start_pct", as_index=False)
            .agg(chat_messages=("text_content", "size"))
        )
        if not chat_bins.empty:
            combined_frames.append(chat_bins)
    else:
        chat_working = chat_df.copy()

    question_time_col = "asked_at" if "asked_at" in questions_df.columns else "created_at" if "created_at" in questions_df.columns else None
    if question_time_col is not None:
        question_progress = _normalize_series_to_progress(questions_df[question_time_col])
        question_working = questions_df.copy()
        question_working["progress_pct"] = question_progress
        question_working["bucket_start_pct"] = question_working["progress_pct"].apply(
            lambda value: int((float(value) // bucket_size_pct) * bucket_size_pct) if pd.notna(value) else None
        )
        question_bins = (
            question_working.dropna(subset=["bucket_start_pct"])
            .groupby("bucket_start_pct", as_index=False)
            .agg(question_count=("question", "size"))
        )
        if not question_bins.empty:
            combined_frames.append(question_bins)
    else:
        question_working = questions_df.copy()

    if combined_frames:
        combined_timeline_df = combined_frames[0]
        for frame in combined_frames[1:]:
            combined_timeline_df = combined_timeline_df.merge(frame, on="bucket_start_pct", how="outer")
        all_buckets_df = pd.DataFrame({"bucket_start_pct": list(range(0, 100, bucket_size_pct))})
        combined_timeline_df = (
            all_buckets_df.merge(combined_timeline_df, on="bucket_start_pct", how="left")
            .fillna(0)
            .sort_values("bucket_start_pct")
            .reset_index(drop=True)
        )
        combined_timeline_df["bucket_end_pct"] = combined_timeline_df["bucket_start_pct"] + bucket_size_pct
        combined_timeline_df["progress_window"] = combined_timeline_df.apply(
            lambda row: f"{int(row['bucket_start_pct'])}-{int(min(row['bucket_end_pct'], 100))}%",
            axis=1,
        )
        combined_timeline_df["session_stage"] = combined_timeline_df.apply(
            lambda row: _format_session_stage_label(row["bucket_start_pct"], row["bucket_end_pct"]),
            axis=1,
        )
        for column in ("chat_messages", "question_count", "transcript_words", "transcript_segments"):
            if column in combined_timeline_df.columns:
                combined_timeline_df[column] = combined_timeline_df[column].astype(float)
        for column in ("chat_messages", "question_count", "transcript_words", "transcript_segments", "transcript_wpm"):
            if column not in combined_timeline_df.columns:
                combined_timeline_df[column] = 0.0
    else:
        combined_timeline_df = pd.DataFrame()

    reaction_windows: List[Dict[str, Any]] = []
    if not combined_timeline_df.empty:
        grouped_segments = (
            working_segments.dropna(subset=["bucket_start_pct"])
            .groupby("bucket_start_pct", as_index=False)
            .agg(
                start_seconds=("start_seconds", "min"),
                start_label=("start_label", "first"),
                speaker=("speaker", lambda values: next((str(v).strip() for v in values if str(v).strip()), "")),
                transcript_excerpt=("text", lambda values: " ".join([str(v).strip() for v in values if str(v).strip()])),
                transcript_segments=("text", "size"),
                transcript_words=("word_count", "sum"),
            )
        )
        reaction_moments_df = combined_timeline_df.merge(grouped_segments, on="bucket_start_pct", how="left")
        reaction_moments_df["chat_messages"] = reaction_moments_df["chat_messages"].fillna(0.0)
        reaction_moments_df["question_count"] = reaction_moments_df["question_count"].fillna(0.0)
        reaction_moments_df = reaction_moments_df[
            ((reaction_moments_df["chat_messages"] > 0) | (reaction_moments_df["question_count"] > 0))
            & reaction_moments_df["transcript_excerpt"].fillna("").astype(str).str.strip().ne("")
        ].copy()
        if not reaction_moments_df.empty:
            reaction_moments_df["total_reactions"] = (
                reaction_moments_df["chat_messages"].astype(float)
                + reaction_moments_df["question_count"].astype(float)
            )
            reaction_moments_df["excerpt"] = reaction_moments_df["transcript_excerpt"].apply(
                lambda value: str(value)[:120] + ("..." if len(str(value)) > 120 else "")
            )
            reaction_moments_df = reaction_moments_df.sort_values(
                by=["total_reactions", "question_count", "chat_messages", "bucket_start_pct"],
                ascending=[False, False, False, True],
            ).head(8).reset_index(drop=True)
    else:
        reaction_moments_df = pd.DataFrame()

    return {"combined_timeline_df": combined_timeline_df, "reaction_moments_df": reaction_moments_df}


def mark_analysis_source_defaults(
    session_state: Any,
    include_chat: bool = False,
    include_questions: bool = False,
    include_transcript: bool = False,
) -> None:
    if include_chat:
        session_state["analysis_include_chat"] = True
        session_state["analysis_include_chat_questions"] = True
    if include_questions:
        session_state["analysis_include_questions"] = True
        session_state["analysis_include_chat_questions"] = True
    if include_transcript:
        session_state["analysis_include_transcript"] = True


def build_derived_stats(
    chat_df: Optional[pd.DataFrame] = None,
    questions_df: Optional[pd.DataFrame] = None,
    transcript_payload: Optional[Dict[str, Any]] = None,
    session_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    stats: Dict[str, Any] = {}

    if isinstance(chat_df, pd.DataFrame):
        unique_authors = 0
        if "author_id" in chat_df.columns:
            author_series = chat_df["author_id"].fillna("").astype(str).str.strip()
            unique_authors = int(author_series[author_series != ""].nunique())
        chat_stats: Dict[str, Any] = {
            "total_messages": int(len(chat_df.index)),
            "unique_authors": unique_authors,
        }
        if "created_at" in chat_df.columns:
            series = pd.to_datetime(chat_df["created_at"], utc=True, errors="coerce").dropna()
            if not series.empty:
                chat_stats["time_range_utc"] = {
                    "start": series.min().strftime("%Y-%m-%d %H:%M:%S UTC"),
                    "end": series.max().strftime("%Y-%m-%d %H:%M:%S UTC"),
                }
        if "author_id" in chat_df.columns:
            top_authors = chat_df["author_id"].value_counts().head(10)
            chat_stats["top_authors_by_message_count"] = {str(idx): int(val) for idx, val in top_authors.items()}
        if "text_content" in chat_df.columns:
            text_lengths = chat_df["text_content"].fillna("").astype(str).str.len()
            chat_stats["text_length"] = {
                "avg_chars": float(round(text_lengths.mean(), 2)),
                "median_chars": float(round(text_lengths.median(), 2)),
                "max_chars": int(text_lengths.max()) if len(text_lengths.index) else 0,
            }
        stats["chat"] = chat_stats

    if isinstance(questions_df, pd.DataFrame):
        stats["questions"] = build_question_stats(questions_df)
    if isinstance(transcript_payload, dict):
        stats["transcript"] = build_transcript_stats(transcript_payload)
    if isinstance(session_payload, dict):
        stats["session"] = build_session_stats(session_payload)
    return stats


def build_compact_transcript_payload_for_llm(transcript_payload: Dict[str, Any], max_segments: int = 120) -> Dict[str, Any]:
    transcript = _extract_transcript_object(transcript_payload)
    segments = _extract_transcript_segments(transcript)
    compact_segments: List[Dict[str, Any]] = []

    for segment in segments[:max_segments] if max_segments > 0 else segments:
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
        for sentence in sentence_items[:max_segments] if max_segments > 0 else sentence_items:
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
            "responded_orally": flattened.get("responded_orally"),
            "question_author_id": flattened.get("question_author_id"),
        }
        response = str(flattened.get("response") or "").strip()
        if response:
            event["response"] = response
        events.append(event)
    return {"question_events": events}

def build_compact_chat_payload_for_llm(chat_df: pd.DataFrame, max_rows: int = 150) -> Dict[str, Any]:
    columns = [column for column in ["created_at", "author_id", "text_content"] if column in chat_df.columns]
    return {
        "messages": chat_df.head(max_rows)[columns].fillna("").to_dict("records") if columns else [],
    }


def build_compact_questions_payload_for_llm(questions_df: pd.DataFrame, max_rows: int = 80) -> Dict[str, Any]:
    columns = [
        column for column in ["asked_at", "asked_by", "question", "response"]
        if column in questions_df.columns
    ]
    return {
        "questions": questions_df.head(max_rows)[columns].fillna("").to_dict("records") if columns else [],
    }


def extract_common_terms(df: pd.DataFrame, top_n: int = 12) -> pd.DataFrame:
    if "text_content" not in df.columns:
        return pd.DataFrame(columns=["term", "count"])
    return extract_common_terms_from_series(df["text_content"], top_n=top_n)


def analyze_with_openai(
    api_key: str,
    model: str,
    system_prompt: str,
    output_language: str,
    selected_sources: List[str],
    derived_stats: Dict[str, Any],
    raw_payload: Optional[Dict[str, Any]] = None,
    questions_payload: Optional[Dict[str, Any]] = None,
    transcript_payload: Optional[Dict[str, Any]] = None,
    session_payload: Optional[Dict[str, Any]] = None,
    transcript_text: str = "",
    max_tokens: int = 2500,
) -> str:
    messages: List[Dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        {"role": "system", "content": f"Respond only in {output_language}."},
    ]
    user_payload = {
        "task": "Use this Livestorm session data to complete the requested task.",
        "selected_sources": selected_sources,
        "derived_stats": derived_stats,
    }
    transcript_text = str(transcript_text or "").strip()
    if transcript_text:
        messages.append({"role": "user", "content": f"Transcript full text:\n\n{transcript_text}"})
    if raw_payload is not None:
        user_payload["chat_api_response"] = raw_payload
    if questions_payload is not None:
        user_payload["questions_api_response"] = questions_payload
    if transcript_payload is not None:
        user_payload["transcript_api_response"] = transcript_payload
        transcript_segments = transcript_payload.get("segments")
        if isinstance(transcript_segments, list) and transcript_segments:
            readable_segments: List[str] = []
            for segment in transcript_segments[:20]:
                if not isinstance(segment, dict):
                    continue
                speaker = str(segment.get("speaker") or "").strip()
                text = str(segment.get("text") or "").strip()
                start_label = str(segment.get("start_label") or "").strip()
                if not text:
                    continue
                prefix_parts = [part for part in [start_label, speaker] if part]
                prefix = " | ".join(prefix_parts)
                readable_segments.append(f"{prefix}: {text}" if prefix else text)
            if readable_segments:
                user_payload["transcript_excerpt"] = readable_segments
    if session_payload is not None:
        user_payload["session_api_response"] = session_payload

    has_structured_context = any(
        [
            bool(selected_sources),
            bool(derived_stats),
            raw_payload is not None,
            questions_payload is not None,
            transcript_payload is not None,
            session_payload is not None,
        ]
    )
    if has_structured_context:
        messages.append({"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)})

    resp = requests.post(
        OPENAI_CHAT_COMPLETIONS_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=_build_chat_completions_payload(
            model=model,
            messages=messages,
            temperature=0.2,
            max_tokens=max_tokens,
        ),
        timeout=120,
    )
    resp.raise_for_status()
    payload = resp.json()
    extracted_text = _extract_chat_completion_text(payload)
    return extracted_text if extracted_text else "No analysis text returned by model."
