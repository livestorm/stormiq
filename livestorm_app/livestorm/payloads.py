"""Parse Livestorm API (JSON:API) payloads: records, pagination and record counts, the event and session pickers, the join-link strip, one person's facts, and the workspace's people attributes."""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

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


# ── One person ────────────────────────────────────────────────────────────
#
# `parse_person` turns one `people` record into the facts the v1 roster reads
# (`record/roster.py`). It keeps what Livestorm says about the person and
# leaves out what nothing uses or what v1 code must not read: the email
# address (only its domain is kept), the full referrer URL (only its host),
# phone, street address, city, social profiles, browser, OS, screen, IP city,
# avatar, and the registrant join link.

# Registration field types whose answers are kept. The API's other types
# (email, avatar, url, consent) are not.
ANSWER_FIELD_TYPES = ("text", "unique_select", "multiple_select")

# Built-in registration fields that are never answers: the identity fields;
# Livestorm's built-in contact fields (phone, street address, city and social
# profiles), which StormIQ doesn't keep; and company and job title, which
# have their own keys. `country` is the only built-in text field kept.
NOT_ANSWER_FIELDS = (
    "email",
    "first_name",
    "last_name",
    "avatar",
    "phone",
    "address",
    "city",
    "twitter",
    "linkedin",
    "facebook",
    "website",
    "company",
    "job_title",
)

PERSON_KEYS = (
    "person_id",  # the Livestorm people id
    "livestorm_role",  # `role`: "participant" or "team_member", as sent
    "is_guest_speaker",
    "session_role",  # as sent; "organizer" is the only value seen so far
    "first_name",
    "last_name",
    "email_domain",  # lower-cased; never the address
    "company",
    "job_title",
    "answers",  # {field slug: text, or a list of texts}, answer fields only
    "registered_at",  # registrant_detail.created_at, as sent (unix seconds)
    "registration_type",
    "referrer_domain",  # the referrer's host, lower-cased, without "www."
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "timezone",
    "country_code",  # ip_country_code
    "country_name",  # ip_country_name
    "attended",
    "attendance_duration_s",
    "attendance_rate",  # Livestorm's 0-100 share of the session
    "has_viewed_replay",
    "replay_watched_at",  # replay_view_detail.watched_at, as sent
    "ls_messages_count",
    "ls_questions_count",
    "ls_votes_count",
    "ls_up_votes_count",
    "ls_updated_at",  # the person's updated_at, as sent
)

_EMAIL = re.compile(r"^[^@\s]+@([^@\s]+)$")
_HOST = re.compile(r"^[\w-]+(\.[\w-]+)+$")


def _clean_text(value: Any) -> Optional[str]:
    """A stripped string, or None. Numbers become text; anything else is None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (str, int, float)):
        text = str(value).strip()
        return text or None
    return None


def _flag_or_none(value: Any) -> Optional[bool]:
    """True or False for a boolean or the string "true" or "false"; None otherwise."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return {"true": True, "false": False}.get(value.strip().lower())
    return None


def _number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).strip()) if isinstance(value, str) else float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _whole(value: Any) -> Optional[int]:
    number = _number(value)
    return int(number) if number is not None else None


def _raw_time(value: Any) -> Any:
    """A payload time as sent (unix seconds, or an ISO string), or None.
    Zero counts as missing: the API uses it for "not set"."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value if math.isfinite(value) and value > 0 else None
    if isinstance(value, str):
        return value.strip() or None
    return None


def email_domain(value: Any) -> Optional[str]:
    """The domain of an email address, lower-cased, or None. Never the address."""
    text = _clean_text(value)
    match = _EMAIL.match(text) if text else None
    if match is None:
        return None
    return match.group(1).strip(".").lower() or None


def referrer_domain(value: Any) -> Optional[str]:
    """The host of a referrer URL (or of a bare host), lower-cased and without
    "www.", or None. The rest of the URL is never kept: its query string can
    hold personal values."""
    text = _clean_text(value)
    if not text:
        return None
    try:
        host = urlparse(text if "//" in text else "//" + text).hostname or ""
    except ValueError:  # a malformed URL, such as an unclosed IPv6 bracket
        return None
    host = host.strip(".").lower()
    if host.startswith("www."):
        host = host[4:]
    return host if _HOST.match(host) else None


def is_answer_field(slug: Any, field_type: Any) -> bool:
    """Whether a registration field's answer is kept: a text or select field
    that is not in `NOT_ANSWER_FIELDS` (identity, contact, company, job title)."""
    slug_text = _clean_text(slug)
    type_text = (_clean_text(field_type) or "").lower()
    return bool(slug_text) and slug_text.lower() not in NOT_ANSWER_FIELDS and type_text in ANSWER_FIELD_TYPES


def _answer_value(value: Any) -> Any:
    """A text answer, or a list of texts for a multiple select; None when empty."""
    if isinstance(value, (list, tuple)):
        texts = [text for text in (_clean_text(item) for item in value) if text]
        return texts or None
    return _clean_text(value)


def parse_person(record: Any) -> Optional[Dict[str, Any]]:
    """One Livestorm `people` record as a flat dict with the keys in `PERSON_KEYS`.

    Never returns the email address, the full referrer URL, phone, street
    address, city, social profiles, browser, OS, screen, IP city, avatar,
    `password_key` or `connection_link`. A person
    without `registrant_detail` gets None for everything it holds (and
    `is_guest_speaker` False). Booleans may be sent as "true" or "false"
    strings. None when the record is not an object or has no id. Pure: the
    record is not changed.
    """
    if not isinstance(record, dict):
        return None
    person_id = _clean_text(record.get("id"))
    if person_id is None:
        return None
    attributes = record.get("attributes")
    attributes = attributes if isinstance(attributes, dict) else {}
    detail = attributes.get("registrant_detail")
    detail = detail if isinstance(detail, dict) else {}
    replay = attributes.get("replay_view_detail")
    replay = replay if isinstance(replay, dict) else {}
    fields = detail.get("fields")
    fields = fields if isinstance(fields, list) else []

    field_values: Dict[str, Any] = {}
    answers: Dict[str, Any] = {}
    for field in fields:
        if not isinstance(field, dict):
            continue
        slug = _clean_text(field.get("id"))
        if slug is None:
            continue
        field_values.setdefault(slug.lower(), field.get("value"))
        if slug not in answers and is_answer_field(slug, field.get("type")):
            answer = _answer_value(field.get("value"))
            if answer is not None:
                answers[slug] = answer

    return {
        "person_id": person_id,
        "livestorm_role": _clean_text(attributes.get("role")),
        "is_guest_speaker": _flag_or_none(detail.get("is_guest_speaker")) is True,
        "session_role": _clean_text(detail.get("session_role")),
        "first_name": _clean_text(attributes.get("first_name")) or _clean_text(field_values.get("first_name")),
        "last_name": _clean_text(attributes.get("last_name")) or _clean_text(field_values.get("last_name")),
        "email_domain": email_domain(attributes.get("email")) or email_domain(field_values.get("email")),
        "company": _clean_text(field_values.get("company")),
        "job_title": _clean_text(field_values.get("job_title")),
        "answers": answers,
        "registered_at": _raw_time(detail.get("created_at")),
        "registration_type": _clean_text(detail.get("registration_type")),
        "referrer_domain": referrer_domain(detail.get("referrer")),
        "utm_source": _clean_text(detail.get("utm_source")),
        "utm_medium": _clean_text(detail.get("utm_medium")),
        "utm_campaign": _clean_text(detail.get("utm_campaign")),
        "utm_term": _clean_text(detail.get("utm_term")),
        "utm_content": _clean_text(detail.get("utm_content")),
        "timezone": _clean_text(attributes.get("timezone")) or _clean_text(detail.get("timezone")),
        "country_code": _clean_text(detail.get("ip_country_code")),
        "country_name": _clean_text(detail.get("ip_country_name")),
        "attended": _flag_or_none(detail.get("attended")),
        "attendance_duration_s": _whole(detail.get("attendance_duration")),
        "attendance_rate": _number(detail.get("attendance_rate")),
        "has_viewed_replay": _flag_or_none(detail.get("has_viewed_replay")),
        "replay_watched_at": _raw_time(replay.get("watched_at")),
        "ls_messages_count": _whole(attributes.get("messages_count")),
        "ls_questions_count": _whole(attributes.get("questions_count")),
        "ls_votes_count": _whole(attributes.get("votes_count")),
        "ls_up_votes_count": _whole(attributes.get("up_votes_count")),
        "ls_updated_at": _raw_time(attributes.get("updated_at")),
    }


# ── The workspace's people attributes ─────────────────────────────────────
#
# `GET /people_attributes` lists the registration fields defined in the
# workspace: their slug, label, type and select options. They hold no
# personal data. StormIQ keeps the keys below and drops the rest (the id,
# placeholder, description, timestamps and the `user_id` of whoever made the
# field).

PEOPLE_ATTRIBUTE_KEYS = ("slug", "name", "type", "builtin", "options")
PEOPLE_ATTRIBUTE_OPTION_KEYS = ("value", "label", "position")


def _attribute_options(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    options = []
    for option in value:
        if not isinstance(option, dict):
            continue
        option_value = _clean_text(option.get("value"))
        label = _clean_text(option.get("label"))
        if option_value is None and label is None:
            continue
        options.append({"value": option_value, "label": label, "position": _whole(option.get("position"))})
    return options


def parse_people_attributes(payload: Any) -> List[Dict[str, Any]]:
    """The workspace's people attributes as `[{slug, name, type, builtin, options}]`.

    Takes a `/people_attributes` payload (its `data` list) or a list of its
    records. Each option is `{value, label, position}`. A record without a
    slug is skipped, and so is a repeated slug (the first copy wins). Never
    returns the record's id, placeholder, description, timestamps or
    `user_id`. Pure.
    """
    records = extract_records(payload)
    attributes: List[Dict[str, Any]] = []
    seen = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        fields = record.get("attributes")
        fields = fields if isinstance(fields, dict) else {}
        slug = _clean_text(fields.get("slug"))
        if slug is None or slug in seen:
            continue
        seen.add(slug)
        attributes.append(
            {
                "slug": slug,
                "name": _clean_text(fields.get("name")),
                "type": (_clean_text(fields.get("type")) or "").lower() or None,
                "builtin": _flag_or_none(fields.get("builtin")) is True,
                "options": _attribute_options(fields.get("options")),
            }
        )
    return attributes


def extract_records(payload: Any) -> List[Any]:
    """The records of a list payload: its `data` list, or the payload itself
    when it is a list. [] otherwise."""
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return payload["data"]
    if isinstance(payload, list):
        return payload
    return []


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


def _non_negative_count(value: Any) -> Optional[int]:
    """`value` as a count: a non-negative integer (or a string of digits), else None.

    Livestorm has returned a negative `sessions_count` for an event.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str):
        text = value.strip()
        if text.isascii() and text.isdigit():
            return int(text)
    return None


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
        # A count that isn't a non-negative integer (missing or negative) is
        # left out of the label, and `sessions_count` says "n/a".
        sessions_count = _non_negative_count(attrs.get("sessions_count"))
        sessions_label = f" ({sessions_count} sessions)" if sessions_count is not None else ""
        updated_label = _format_unix_label(attrs.get("updated_at") or attrs.get("published_at") or attrs.get("created_at"))
        scheduling_status = str(attrs.get("scheduling_status") or "").strip()
        language = str(attrs.get("language") or "").strip()
        options.append(
            {
                "id": row["id"],
                "label": f"{title}{sessions_label} · {updated_label}",
                "title": title,
                "scheduling_status": scheduling_status,
                "language": language,
                "sessions_count": str(sessions_count) if sessions_count is not None else "n/a",
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


def extract_record_count(payload: Any) -> Optional[int]:
    """`meta.record_count` of a paginated payload: how many records the list
    holds in all, across its pages. None when it is missing, negative or not
    a whole number."""
    return _non_negative_count(_extract_pagination(payload).get("record_count"))


def _states_last_page(payload: Dict[str, Any]) -> bool:
    """True when the payload's pagination says there is no next page.

    The API sends `next_page: null` on the last page, and asking for a page
    past the last one returns HTTP 400. So a pager stops here even when the
    page is full. Only a payload without pagination metadata falls back to
    "a short page is the last page".
    """
    return "next_page" in _extract_pagination(payload) and _extract_next_page(payload) is None
