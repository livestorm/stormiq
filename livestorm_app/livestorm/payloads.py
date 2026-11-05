"""Parse Livestorm API (JSON:API) payloads: records, pagination, the event and session pickers, and the join-link strip."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd


# ── Registrant join links ─────────────────────────────────────────────────
#
# Each registrant has a personal join link: `password_key` and
# `connection_link` in a person's `attributes.registrant_detail`. StormIQ
# never stores a registrant's personal join link; it doesn't need it. The
# fetchers in `client.py` strip it before returning a payload, so it never
# reaches the database. `scripts/strip_registrant_join_links.py` cleans rows
# saved before that.

REGISTRANT_JOIN_LINK_KEYS = ("password_key", "connection_link")


def strip_registrant_join_link(record: Any) -> Any:
    """Return one JSON:API resource object without its registrant join link.

    Removes only `attributes.registrant_detail.password_key` and
    `attributes.registrant_detail.connection_link`; every other key is kept.
    In practice the record is a `people` resource, but any record with a
    `registrant_detail` is cleaned. Pure: the input is never changed. A
    record with nothing to remove is returned as is (the same object).
    """
    if not isinstance(record, dict):
        return record
    attributes = record.get("attributes")
    if not isinstance(attributes, dict):
        return record
    detail = attributes.get("registrant_detail")
    if not isinstance(detail, dict) or not any(key in detail for key in REGISTRANT_JOIN_LINK_KEYS):
        return record
    kept = {key: value for key, value in detail.items() if key not in REGISTRANT_JOIN_LINK_KEYS}
    return {**record, "attributes": {**attributes, "registrant_detail": kept}}


def _strip_records(records: List[Any]) -> List[Any]:
    stripped = [strip_registrant_join_link(record) for record in records]
    return records if all(new is old for new, old in zip(stripped, records, strict=True)) else stripped


def strip_payload_join_links(payload: Any) -> Any:
    """Return a Livestorm payload with every registrant join link removed.

    Cleans each record in `data` (one object or a list) and in `included`,
    which covers the session (`include=people`), chat and questions
    payloads. Every other key is kept. Pure and idempotent. A payload with
    nothing to remove is returned as is (the same object).
    """
    if isinstance(payload, list):
        return _strip_records(payload)
    if not isinstance(payload, dict):
        return payload
    changes: Dict[str, Any] = {}
    for section in ("data", "included"):
        value = payload.get(section)
        if isinstance(value, list):
            stripped = _strip_records(value)
        elif isinstance(value, dict):
            stripped = strip_registrant_join_link(value)
        else:
            continue
        if stripped is not value:
            changes[section] = stripped
    return {**payload, **changes} if changes else payload


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


def _states_last_page(payload: Dict[str, Any]) -> bool:
    """True when the payload's pagination says there is no next page.

    The API sends `next_page: null` on the last page, and asking for a page
    past the last one returns HTTP 400. So a pager stops here even when the
    page is full. Only a payload without pagination metadata falls back to
    "a short page is the last page".
    """
    return "next_page" in _extract_pagination(payload) and _extract_next_page(payload) is None
