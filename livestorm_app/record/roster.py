"""The v1 roster: one row per person in a session, with a role, a channel and a lead time.

Two sources give the same `Roster` (`RosterMeta.source`):

- `session_include`: the `include=people` list that every cached
  `session_payload` holds (`roster_from_session_payload`);
- `paginated`: stored rows from Livestorm's paginated people endpoint, once
  StormIQ syncs them (`roster_from_rows`, fed a `RosterRows`).

`record/inputs.py` picks the source (`load_record_inputs`, and
`count_people_on_stage` for the speaker cap), so nothing else depends on it.

What a row holds comes from `livestorm.payloads.parse_person`: the email
domain, never the address; the referrer's host, never the URL; no phone,
street address, city, browser, OS, IP city, avatar or join link. Names stay
in memory for `display_people`, which fails closed. A prompt gets people
only through `prompt_view`.

The rules (docs/product.md §13, "Roster"):
- role: team, then guest speaker, then audience (`classify_role`). What a
  person did can raise their role (`RoleSignals`, `escalate_roles`): the
  team flags of their own chat messages, and writing an answer, once
  `record/authors.py` resolves those items to them. Never on an internal
  session. An organizer (`session_role`) is a flag beside the role, never a
  role. In an internal meeting everyone is team: the roster says so in its
  meta (`internal`, counts by role) and never relabels anyone;
- channel: UTM source, then the referrer's host, then the registration type
  (`derive_channel`, `CHANNEL_RULE_VERSION`);
- lead time: 24-hour periods from registration to `started_at`
  (`lead_time_bucket`); time zones play no part;
- coverage: complete only when the source says so and the people count
  matches Livestorm's own count of the list. The include list has no such
  count yet, so it reads partial;
- people on stage: organizers and guest speakers (`people_on_stage`). The
  worker caps the speakers of a new transcript with that count, read through
  `record/inputs.count_people_on_stage`.

Pure functions: no database, network or web framework.
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, NamedTuple, Optional, Sequence, Tuple

import pandas as pd

from livestorm_app.analytics.audience import is_internal_session
from livestorm_app.livestorm.payloads import is_answer_field, parse_person, referrer_domain
from livestorm_app.record.clock import parse_time

# Roles, in precedence order.
TEAM = "team"
GUEST_SPEAKER = "guest_speaker"
AUDIENCE = "audience"
ROLES = (TEAM, GUEST_SPEAKER, AUDIENCE)

# Where a role came from.
ROLE_FROM_LIVESTORM_ROLE = "livestorm_role"
ROLE_FROM_GUEST_FLAG = "guest_speaker_flag"
ROLE_FROM_CHAT_FLAG = "chat_flag"  # a team flag on the person's own chat message
ROLE_FROM_WRITTEN_ANSWER = "written_answer"  # the person wrote an answer to a question
ROLE_BY_DEFAULT = "default"

ORGANIZER = "organizer"  # the only `session_role` value seen so far

# Roster sources and coverage.
SESSION_INCLUDE = "session_include"
PAGINATED = "paginated"
COMPLETE = "complete"
PARTIAL = "partial"

# Field definitions: where their labels come from.
FIELDS_FROM_PEOPLE_ATTRIBUTES = "people_attributes"
FIELDS_FROM_EVENT = "event_fields"
NO_FIELDS = "none"

# The channel rule. Bump the version when the rule changes.
CHANNEL_RULE_VERSION = 1
DIRECT_OR_UNKNOWN = "direct_or_unknown"
LIVESTORM_HOST_SUFFIXES = ("livestorm.co", "livestorm.io")

# Lead-time buckets, in 24-hour periods before `started_at`.
SAME_DAY = "same_day"
AFTER_START = "after_start"
LEAD_BUCKETS = (SAME_DAY, "1-3", "4-7", "8-14", "15+", AFTER_START)
DAY_S = 86400

PERSON_REF_PREFIX = "P-"
PERSON_REF_HEX = 8

PUBLIC_PROFILE = "public"
ROLE_UNKNOWN = "role unknown"

ROSTER_COLUMNS = (
    "person_id",  # the Livestorm people id
    "person_ref",  # P- + 8 hex; the only id a model sees
    "display_name",  # first and last name; read only through display_people
    "email_domain",
    "company",
    "job_title",
    "role",  # team | guest_speaker | audience
    "role_source",  # livestorm_role | guest_speaker_flag | chat_flag | written_answer | default
    "livestorm_role",
    "is_guest_speaker",
    "session_role",  # as sent
    "is_organizer",  # session_role is "organizer"
    "registered_at",  # unix seconds
    "lead_time_days",  # (started_at - registered_at) / 86400; negative after the start
    "lead_bucket",
    "registration_type",
    "referrer_domain",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "channel",
    "channel_detail",
    "channel_source",  # utm | referrer | registration_type | none
    "answers",  # {field slug: text or list of texts}
    "timezone",
    "country_code",
    "country_name",
    "attended",  # True, False, or <NA> when unknown
    "attendance_duration_s",
    "attendance_rate",
    "has_viewed_replay",
    "replay_watched_at",  # unix seconds
    "ls_messages_count",  # Livestorm's own counters, for reconciliation only
    "ls_questions_count",
    "ls_votes_count",
    "ls_up_votes_count",
)

_DTYPES = {
    "is_guest_speaker": "bool",
    "is_organizer": "bool",
    "attended": "boolean",
    "has_viewed_replay": "boolean",
    "registered_at": "float64",
    "lead_time_days": "float64",
    "attendance_rate": "float64",
    "replay_watched_at": "float64",
    "attendance_duration_s": "Int64",
    "ls_messages_count": "Int64",
    "ls_questions_count": "Int64",
    "ls_votes_count": "Int64",
    "ls_up_votes_count": "Int64",
}


# ── Small readers ───────────────────────────────────────────────────────────


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _flag(value: Any) -> bool:
    """True for a true boolean or the string "true" (any case)."""
    if pd.api.types.is_bool(value):
        return bool(value)
    return _text(value).lower() == "true"


def _flag_or_none(value: Any) -> Optional[bool]:
    if pd.api.types.is_bool(value):
        return bool(value)
    return {"true": True, "false": False}.get(_text(value).lower())


def _unix(value: Any) -> Optional[float]:
    """Unix seconds from a payload time (`clock.parse_time`) or a datetime."""
    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return moment.timestamp()
    return parse_time(value)


def _number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _count(value: Any) -> Optional[int]:
    number = _number(value)
    return int(number) if number is not None else None


# ── Role ────────────────────────────────────────────────────────────────────


class RoleSignals(NamedTuple):
    """What a person did in the session that can raise their role
    (`classify_role`). Only items resolved to the person count
    (`record/authors.py`), never a guess."""

    team_message: bool = False  # one of their chat messages is `from_team_member`
    guest_message: bool = False  # one of their chat messages is `from_guest_speaker`
    wrote_answer: bool = False  # they wrote an answer to a question: a team action


NO_ROLE_SIGNALS = RoleSignals()


def classify_role(person: Mapping[str, Any], signals: RoleSignals = NO_ROLE_SIGNALS) -> Tuple[str, str]:
    """(role, source) for one person row.

    Team when Livestorm's role is exactly `team_member` (surrounding spaces
    aside), as the classic rules read it (`audience.is_internal_session`).
    Then team when one of the person's own chat messages is
    `from_team_member`, or when they wrote an answer (`signals`). Then guest
    speaker when `is_guest_speaker` is true, or when one of their messages
    is `from_guest_speaker`. Then audience. A team member who is also a
    guest speaker is team. An unknown or missing role is audience. Booleans
    may be the strings "true" or "false". `session_role` plays no part: see
    `is_organizer`.
    """
    if _text(person.get("livestorm_role")) == "team_member":
        return TEAM, ROLE_FROM_LIVESTORM_ROLE
    if signals.team_message:
        return TEAM, ROLE_FROM_CHAT_FLAG
    if signals.wrote_answer:
        return TEAM, ROLE_FROM_WRITTEN_ANSWER
    if _flag(person.get("is_guest_speaker")):
        return GUEST_SPEAKER, ROLE_FROM_GUEST_FLAG
    if signals.guest_message:
        return GUEST_SPEAKER, ROLE_FROM_CHAT_FLAG
    return AUDIENCE, ROLE_BY_DEFAULT


def is_organizer(person: Mapping[str, Any]) -> bool:
    """Whether Livestorm marks the person as an organizer of the session
    (`session_role`), a host on stage. It never changes the role."""
    return _text(person.get("session_role")).lower() == ORGANIZER


# ── Channel ─────────────────────────────────────────────────────────────────


class Channel(NamedTuple):
    channel: str
    detail: Optional[str]
    source: str  # utm | referrer | registration_type | none


def _livestorm_host(host: str) -> bool:
    return any(host == suffix or host.endswith("." + suffix) for suffix in LIVESTORM_HOST_SUFFIXES)


def derive_channel(person: Mapping[str, Any]) -> Channel:
    """How the person came to register (rule `CHANNEL_RULE_VERSION`), first match wins:

    1. `utm_source`, lower-cased, with `utm_medium` (lower-cased) as the detail;
    2. the referrer's host (`referrer_domain`, without "www."), unless it is
       a Livestorm host;
    3. `type:` and the registration type, lower-cased;
    4. `direct_or_unknown`.

    Folding small channels into "Other" belongs to the measures, not here.
    """
    utm_source = _text(person.get("utm_source")).lower()
    if utm_source:
        return Channel(utm_source, _text(person.get("utm_medium")).lower() or None, "utm")
    host = referrer_domain(person.get("referrer_domain"))
    if host and not _livestorm_host(host):
        return Channel(host, None, "referrer")
    registration_type = _text(person.get("registration_type")).lower()
    if registration_type:
        return Channel(f"type:{registration_type}", None, "registration_type")
    return Channel(DIRECT_OR_UNKNOWN, None, "none")


# ── Lead time ───────────────────────────────────────────────────────────────


def lead_time_days(registered_at: Any, started_at: Any) -> Optional[float]:
    """Days from registration to the session's `started_at`; negative when the
    person registered after the start. None when either time is missing."""
    registered = _unix(registered_at)
    start = _unix(started_at)
    if registered is None or start is None:
        return None
    return (start - registered) / DAY_S


def lead_time_bucket(registered_at: Any, started_at: Any) -> Optional[str]:
    """The lead-time bucket, in 24-hour periods before the start (time zones
    play no part): `same_day` (under 24 hours), `1-3`, `4-7`, `8-14`, `15+`,
    or `after_start`. Registering exactly at the start is `same_day`. None
    when either time is missing: `estimated_started_at` is never used."""
    days = lead_time_days(registered_at, started_at)
    if days is None:
        return None
    if days < 0:
        return AFTER_START
    if days < 1:
        return SAME_DAY
    if days < 4:
        return "1-3"
    if days < 8:
        return "4-7"
    if days < 15:
        return "8-14"
    return "15+"


# ── Field definitions ───────────────────────────────────────────────────────


def event_fields(event_payload: Any) -> List[Mapping[str, Any]]:
    """The registration `fields` of a cached `event_payload`."""
    data = event_payload.get("data") if isinstance(event_payload, Mapping) else None
    attributes = data.get("attributes") if isinstance(data, Mapping) else None
    fields = attributes.get("fields") if isinstance(attributes, Mapping) else None
    return [field for field in fields if isinstance(field, Mapping)] if isinstance(fields, list) else []


def _pretty(slug: str) -> str:
    words = " ".join(slug.replace("_", " ").replace("-", " ").split())
    return words[:1].upper() + words[1:]


def _options(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [
        {"value": option.get("value"), "label": option.get("label"), "position": option.get("position")}
        for option in value
        if isinstance(option, Mapping)
    ]


def resolve_field_definitions(
    fields: Iterable[Mapping[str, Any]],
    people_attributes: Optional[Iterable[Mapping[str, Any]]] = None,
) -> Tuple[Tuple[Dict[str, Any], ...], str]:
    """(field definitions, source) for the answer fields of an event.

    `fields` are the event's registration fields (`event_fields`). Only fields
    whose answers `parse_person` keeps are defined (`payloads.is_answer_field`).
    Each definition is `{slug, name, type, order, required, options}`.

    `people_attributes`, when given, are the workspace's attribute
    definitions (`{slug, name, type, options}`); an event field's id is
    matched to an attribute's slug, which gives the label and the select
    options. Without them (not fetched, or refused) the name is the slug made
    readable. The source is `people_attributes` when at least one label came
    from them, `event_fields` otherwise, and `none` with no answer field.
    """
    labels: Dict[str, Mapping[str, Any]] = {}
    for attribute in people_attributes or ():
        if isinstance(attribute, Mapping) and _text(attribute.get("slug")):
            labels.setdefault(_text(attribute.get("slug")), attribute)

    definitions: List[Dict[str, Any]] = []
    seen = set()
    labelled = False
    for field in fields or ():
        if not isinstance(field, Mapping):
            continue
        slug = _text(field.get("id"))
        attribute = labels.get(slug) or {}
        field_type = _text(field.get("type")).lower() or _text(attribute.get("type")).lower()
        if not slug or slug in seen or not is_answer_field(slug, field_type):
            continue
        seen.add(slug)
        name = _text(attribute.get("name"))
        labelled = labelled or bool(name)
        definitions.append(
            {
                "slug": slug,
                "name": name or _pretty(slug),
                "type": field_type,
                "order": _count(field.get("order")),
                "required": _flag(field.get("required")),
                "options": _options(attribute.get("options")),
            }
        )
    definitions.sort(key=lambda item: (item["order"] is None, item["order"] or 0, item["slug"]))
    if not definitions:
        return (), NO_FIELDS
    return tuple(definitions), FIELDS_FROM_PEOPLE_ATTRIBUTES if labelled else FIELDS_FROM_EVENT


# ── Person refs ─────────────────────────────────────────────────────────────


def _digest(session_id: str, person_id: str) -> str:
    return hashlib.sha256(f"{session_id}:{person_id}".encode("utf-8")).hexdigest()


def person_ref(session_id: str, person_id: str) -> str:
    """`P-` and the first 8 hex characters of sha256("session_id:person_id").
    `assign_person_refs` lengthens it when two people of a session collide."""
    return PERSON_REF_PREFIX + _digest(str(session_id or ""), str(person_id))[:PERSON_REF_HEX]


def assign_person_refs(session_id: str, person_ids: Iterable[str]) -> Dict[str, str]:
    """{person id: ref}, unique within the session.

    A ref comes from the session and the person, so the same people get the
    same refs in every parse and every refresh, in any order. When two
    people's 8-character refs collide (about 3 in 1,000 sessions of 5,000
    people), the one with the smallest Livestorm id keeps it and the others
    get the shortest longer prefix that tells them apart. So a ref can change
    only when a person whose 8-character ref collides with it joins or leaves
    the list: it can move to a longer prefix, or back to the short one.
    """
    session_key = str(session_id or "")
    digests = {person_id: _digest(session_key, person_id) for person_id in dict.fromkeys(person_ids)}
    groups: Dict[str, List[str]] = defaultdict(list)
    for person_id, digest in digests.items():
        groups[digest[:PERSON_REF_HEX]].append(person_id)

    refs: Dict[str, str] = {}
    for prefix, members in groups.items():
        members.sort()
        refs[members[0]] = PERSON_REF_PREFIX + prefix
        others = members[1:]
        length = PERSON_REF_HEX + 1
        while others and len({digests[member][:length] for member in others}) < len(others):
            length += 1
        for member in others:
            refs[member] = PERSON_REF_PREFIX + digests[member][:length]
    return refs


# ── The roster ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RosterMeta:
    """What the roster covers, and its counts.

    - `complete`: True only when the source says the list is complete and
      `people_count` equals `record_count`, Livestorm's own count; False when
      the source says complete but the counts differ, or says incomplete;
      None when unknown. `coverage` is `complete` when it is True, `partial`
      otherwise.
    - `record_count`: Livestorm's count of the people list, when the source
      has one. `registrants_count` and `attendees_count` are the session's
      own counters, as sent.
    - `role_counts` and `attended_role_counts`: people per role, over
      everyone and over attendees (`attended` true), after any role raised
      by `escalate_roles`.
    - `internal`: the session is internal (`audience.is_internal_session`,
      the rule classic uses). Roles are never changed for it. That rule
      doesn't count a team member who is also a guest speaker as team,
      while `role` does, so read this flag; never recompute it from
      `role_counts`.
    """

    session_id: str
    source: str
    complete: Optional[bool]
    coverage: str
    people_count: int
    record_count: Optional[int]
    registrants_count: Optional[int]
    attendees_count: Optional[int]
    role_counts: Dict[str, int]
    attended_role_counts: Dict[str, int]
    organizers: int
    internal: bool
    fields: Tuple[Dict[str, Any], ...]
    fields_source: str
    synced_at: Any
    channel_rule_version: int


@dataclass(frozen=True)
class Roster:
    """`people`: one row per person, columns `ROSTER_COLUMNS`. `meta`: `RosterMeta`."""

    people: pd.DataFrame
    meta: RosterMeta


@dataclass(frozen=True)
class RosterRows:
    """A stored roster: person rows in the `payloads.parse_person` shape, and
    their header. This is what a synced roster hands `load_record_inputs`."""

    people: Sequence[Mapping[str, Any]]
    source: str = PAGINATED
    complete: Optional[bool] = None
    record_count: Optional[int] = None
    fields: Sequence[Mapping[str, Any]] = ()
    fields_source: str = NO_FIELDS
    synced_at: Any = None


def stored_roster_rows(stored: Mapping[str, Any]) -> RosterRows:
    """A stored roster as `RosterRows`.

    `stored` is what `db.fetch_session_roster` returns: `{roster, people}`,
    the header and the person rows of one roster version. The header's
    `synced_at` is kept as an ISO string, so everything built from it stays
    plain JSON. A header value of the wrong type is read as unknown.
    """
    header = stored.get("roster")
    header = header if isinstance(header, Mapping) else {}
    people = stored.get("people")
    fields = header.get("fields")
    complete = header.get("complete")
    synced_at = header.get("synced_at")
    return RosterRows(
        people=[person for person in (people if isinstance(people, (list, tuple)) else []) if isinstance(person, Mapping)],
        source=_text(header.get("source")) or PAGINATED,
        complete=complete if isinstance(complete, bool) else None,
        record_count=_count(header.get("record_count")),
        fields=[field for field in (fields if isinstance(fields, (list, tuple)) else []) if isinstance(field, Mapping)],
        fields_source=_text(header.get("fields_source")) or NO_FIELDS,
        synced_at=synced_at.isoformat() if isinstance(synced_at, datetime) else (_text(synced_at) or None),
    )


def _unique_people(people: Iterable[Any]) -> List[Mapping[str, Any]]:
    """Person rows with an id, without repeats (the first copy wins)."""
    seen = set()
    unique = []
    for person in people:
        if not isinstance(person, Mapping):
            continue
        person_id = _text(person.get("person_id"))
        if not person_id or person_id in seen:
            continue
        seen.add(person_id)
        unique.append(person)
    return unique


def _display_name(person: Mapping[str, Any]) -> Optional[str]:
    name = " ".join(part for part in (_text(person.get("first_name")), _text(person.get("last_name"))) if part)
    return name or None


def _row(person: Mapping[str, Any], ref: str, started_at: Any) -> Dict[str, Any]:
    role, role_source = classify_role(person)
    channel = derive_channel(person)
    answers = person.get("answers")
    return {
        "person_id": _text(person.get("person_id")),
        "person_ref": ref,
        "display_name": _display_name(person),
        "email_domain": _text(person.get("email_domain")).lower() or None,
        "company": _text(person.get("company")) or None,
        "job_title": _text(person.get("job_title")) or None,
        "role": role,
        "role_source": role_source,
        "livestorm_role": _text(person.get("livestorm_role")) or None,
        "is_guest_speaker": _flag(person.get("is_guest_speaker")),
        "session_role": _text(person.get("session_role")) or None,
        "is_organizer": is_organizer(person),
        "registered_at": _unix(person.get("registered_at")),
        "lead_time_days": lead_time_days(person.get("registered_at"), started_at),
        "lead_bucket": lead_time_bucket(person.get("registered_at"), started_at),
        "registration_type": _text(person.get("registration_type")) or None,
        "referrer_domain": referrer_domain(person.get("referrer_domain")),
        "utm_source": _text(person.get("utm_source")) or None,
        "utm_medium": _text(person.get("utm_medium")) or None,
        "utm_campaign": _text(person.get("utm_campaign")) or None,
        "utm_term": _text(person.get("utm_term")) or None,
        "utm_content": _text(person.get("utm_content")) or None,
        "channel": channel.channel,
        "channel_detail": channel.detail,
        "channel_source": channel.source,
        "answers": dict(answers) if isinstance(answers, Mapping) else {},
        "timezone": _text(person.get("timezone")) or None,
        "country_code": _text(person.get("country_code")) or None,
        "country_name": _text(person.get("country_name")) or None,
        "attended": _flag_or_none(person.get("attended")),
        "attendance_duration_s": _count(person.get("attendance_duration_s")),
        "attendance_rate": _number(person.get("attendance_rate")),
        "has_viewed_replay": _flag_or_none(person.get("has_viewed_replay")),
        "replay_watched_at": _unix(person.get("replay_watched_at")),
        "ls_messages_count": _count(person.get("ls_messages_count")),
        "ls_questions_count": _count(person.get("ls_questions_count")),
        "ls_votes_count": _count(person.get("ls_votes_count")),
        "ls_up_votes_count": _count(person.get("ls_up_votes_count")),
    }


def _people_frame(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(
        {column: pd.Series([row[column] for row in rows], dtype=object) for column in ROSTER_COLUMNS},
        columns=list(ROSTER_COLUMNS),
    )
    for column, dtype in _DTYPES.items():
        if dtype in ("float64", "Int64"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce").astype(dtype)
        else:
            frame[column] = frame[column].astype(dtype)
    return frame


def _role_counts(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = {role: 0 for role in ROLES}
    for row in rows:
        counts[row["role"]] += 1
    return counts


def _internal(people: List[Mapping[str, Any]]) -> bool:
    """`audience.is_internal_session` on the raw facts it reads."""
    frame = pd.DataFrame(
        {
            "role": [_text(person.get("livestorm_role")) or None for person in people],
            "is_guest_speaker": [_flag(person.get("is_guest_speaker")) for person in people],
            "attended": [_flag_or_none(person.get("attended")) for person in people],
        },
        dtype=object,
    )
    return bool(is_internal_session(frame))


def build_roster(
    people: Iterable[Mapping[str, Any]],
    *,
    session_id: str,
    session_attributes: Optional[Mapping[str, Any]] = None,
    source: str,
    complete: Optional[bool] = None,
    record_count: Optional[int] = None,
    fields: Sequence[Mapping[str, Any]] = (),
    fields_source: str = NO_FIELDS,
    synced_at: Any = None,
) -> Roster:
    """The roster of a session from person rows in the `parse_person` shape.

    `session_attributes` is the session's `data.attributes`: its `started_at`
    sets the lead times, and its counters are kept in the meta. A person
    repeated by id counts once (the first copy wins); a row without an id is
    skipped. `complete` is the source's own claim: it holds only when the
    people left after that equal `record_count` (see `RosterMeta`).
    """
    attributes = session_attributes if isinstance(session_attributes, Mapping) else {}
    unique = _unique_people(people)
    refs = assign_person_refs(session_id, [_text(person.get("person_id")) for person in unique])
    started_at = attributes.get("started_at")
    rows = [_row(person, refs[_text(person.get("person_id"))], started_at) for person in unique]
    attended_rows = [row for row in rows if row["attended"] is True]
    counted = _count(record_count)
    if complete is True:
        complete = counted is not None and counted == len(rows)
    elif complete is not None:
        complete = False

    meta = RosterMeta(
        session_id=str(session_id or ""),
        source=source,
        complete=complete,
        coverage=COMPLETE if complete is True else PARTIAL,
        people_count=len(rows),
        record_count=counted,
        registrants_count=_count(attributes.get("registrants_count")),
        attendees_count=_count(attributes.get("attendees_count")),
        role_counts=_role_counts(rows),
        attended_role_counts=_role_counts(attended_rows),
        organizers=sum(1 for row in rows if row["is_organizer"]),
        internal=_internal(unique),
        fields=tuple(dict(field) for field in fields),
        fields_source=fields_source,
        synced_at=synced_at,
        channel_rule_version=CHANNEL_RULE_VERSION,
    )
    return Roster(people=_people_frame(rows), meta=meta)


def _session_resource(session_payload: Any) -> Mapping[str, Any]:
    data = session_payload.get("data") if isinstance(session_payload, Mapping) else None
    return data if isinstance(data, Mapping) else {}


def roster_from_session_payload(
    session_payload: Any,
    *,
    event_payload: Any = None,
    session_id: Optional[str] = None,
) -> Roster:
    """The roster from the `include=people` list of a cached `session_payload`.

    Source `session_include`. `complete` stays None, so coverage is partial:
    it isn't known yet whether that list is capped, nor whether the session's
    `registrants_count` counts the team, so the list can't be reconciled
    with it. Field labels come from the event's registration fields
    (`event_payload`). `session_id` defaults to the payload's own id.
    """
    resource = _session_resource(session_payload)
    attributes = resource.get("attributes")
    included = session_payload.get("included") if isinstance(session_payload, Mapping) else None
    records = [
        item
        for item in (included if isinstance(included, list) else [])
        if isinstance(item, Mapping) and _text(item.get("type")) == "people"
    ]
    people = [person for person in (parse_person(dict(record)) for record in records) if person is not None]
    fields, fields_source = resolve_field_definitions(event_fields(event_payload))
    return build_roster(
        people,
        session_id=_text(session_id) or _text(resource.get("id")),
        session_attributes=attributes if isinstance(attributes, Mapping) else {},
        source=SESSION_INCLUDE,
        complete=None,
        record_count=None,
        fields=fields,
        fields_source=fields_source,
    )


def roster_from_rows(
    rows: RosterRows,
    *,
    session_id: str,
    session_attributes: Optional[Mapping[str, Any]] = None,
) -> Roster:
    """The roster from stored person rows and their header (`RosterRows`)."""
    return build_roster(
        rows.people,
        session_id=session_id,
        session_attributes=session_attributes,
        source=rows.source,
        complete=rows.complete,
        record_count=rows.record_count,
        fields=rows.fields,
        fields_source=rows.fields_source,
        synced_at=rows.synced_at,
    )


def escalate_roles(roster: Roster, signals: Mapping[str, RoleSignals]) -> Roster:
    """The roster with roles raised by what each person did (`classify_role`).

    `signals` maps a person id to their `RoleSignals`: the team flags of the
    chat messages resolved to them, and whether they wrote an answer
    (`authors.AuthorIndex.role_signals`). A role only ever rises: audience
    to guest speaker or team, guest speaker to team. The counts by role
    follow; the internal flag doesn't change, since it reads Livestorm's own
    roles (the classic rule). An internal session's roles never change, so
    it is returned as is, and so is a roster with nothing to raise.
    """
    if roster.meta.internal or not signals or roster.people.empty:
        return roster
    people = roster.people
    roles = list(people["role"])
    sources = list(people["role_source"])
    changed = False
    for index, (person_id, livestorm_role, guest) in enumerate(
        zip(people["person_id"], people["livestorm_role"], people["is_guest_speaker"], strict=True)
    ):
        person_signals = signals.get(person_id)
        if person_signals is None or not any(person_signals):
            continue
        role, source = classify_role({"livestorm_role": livestorm_role, "is_guest_speaker": guest}, person_signals)
        if (role, source) != (roles[index], sources[index]):
            roles[index], sources[index] = role, source
            changed = True
    if not changed:
        return roster
    escalated = people.copy()
    escalated["role"] = pd.Series(roles, index=people.index, dtype=object)
    escalated["role_source"] = pd.Series(sources, index=people.index, dtype=object)
    rows = [{"role": role} for role in roles]
    attended = people["attended"].fillna(False).astype(bool)
    attended_rows = [{"role": role} for role, came in zip(roles, attended, strict=True) if came]
    meta = replace(roster.meta, role_counts=_role_counts(rows), attended_role_counts=_role_counts(attended_rows))
    return Roster(people=escalated, meta=meta)


def people_on_stage(roster: Roster) -> int:
    """How many people of the roster Livestorm marks as on stage: organizers
    (`is_organizer`) and guest speakers (`is_guest_speaker`), each person
    once. Attendance plays no part, so a host whose attendance flag is wrong
    still counts."""
    people = roster.people
    if people.empty:
        return 0
    return int((people["is_organizer"] | people["is_guest_speaker"]).sum())


# ── Views ───────────────────────────────────────────────────────────────────


def prompt_view(roster: Roster) -> List[Dict[str, str]]:
    """The roster as a model may see it: `{ref, role, job_title}` per person,
    with "role unknown" when there is no job title, ordered by ref. Never a
    name, an email domain, a company or a Livestorm id."""
    people = roster.people
    view = [
        {"ref": ref, "role": role, "job_title": job_title if isinstance(job_title, str) and job_title else ROLE_UNKNOWN}
        for ref, role, job_title in zip(people["person_ref"], people["role"], people["job_title"], strict=True)
    ]
    return sorted(view, key=lambda item: item["ref"])


def display_people(roster: Roster, *, profile: Optional[str], limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    """People with their names, for a person list in the app.

    Fails closed: only a session whose profile is exactly "public" gets
    anyone. An internal session, an unknown profile or no profile gets [].
    Ordered by name, then ref; `offset` and `limit` page through it.
    `attended` is None when attendance is unknown, never a guessed False.
    """
    if profile != PUBLIC_PROFILE:
        return []
    people = roster.people
    rows = [
        {
            "ref": ref,
            "role": role,
            "name": name if isinstance(name, str) else None,
            "company": company if isinstance(company, str) else None,
            "job_title": job_title if isinstance(job_title, str) else None,
            "attended": None if pd.isna(attended) else bool(attended),
        }
        for ref, role, name, company, job_title, attended in zip(
            people["person_ref"],
            people["role"],
            people["display_name"],
            people["company"],
            people["job_title"],
            people["attended"],
            strict=True,
        )
    ]
    rows.sort(key=lambda row: (row["name"] is None, (row["name"] or "").casefold(), row["ref"]))
    start = max(int(offset), 0)
    return rows[start : start + max(int(limit), 0)]
