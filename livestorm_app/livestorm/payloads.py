"""Parse Livestorm API (JSON:API) payloads: records, pagination, and the event and session pickers."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd


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
