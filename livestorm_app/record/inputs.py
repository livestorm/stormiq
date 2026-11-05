"""The single loader of what the v1 record is computed from.

`load_record_inputs(cached)` takes a cached session row (the dict
`db.fetch_cached_session` returns, read with the caller's scope) and gives
one `RecordInputs`: the payloads, the session clock, the roster and the
authors of the chat and the questions, resolved to roster people
(`authors.resolve_authors`). The roster's roles are raised by what each
person did: the team flags of the chat messages resolved to them, and the
answers they wrote (`roster.escalate_roles`), except on an internal session.

The roster comes from stored person rows when the caller has them
(`roster_rows`), and from the session payload's `include=people` list
otherwise. This is the only place that chooses, so a new people source
changes nothing downstream.

Pure: no database, network or web framework. The caller does the scoped read.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from livestorm_app.record.authors import AuthorIndex, resolve_authors
from livestorm_app.record.clock import ClockBase, clock_base
from livestorm_app.record.roster import Roster, RosterRows, escalate_roles, roster_from_rows, roster_from_session_payload


@dataclass(frozen=True)
class RecordInputs:
    """Everything the v1 record reads for one session. Payloads are the
    cached dicts as stored (not copied); treat them as read-only."""

    session_id: str
    clock: ClockBase
    roster: Roster
    authors: AuthorIndex
    session_payload: Mapping[str, Any]
    event_payload: Optional[Mapping[str, Any]]
    chat_payload: Optional[Mapping[str, Any]]
    questions_payload: Optional[Mapping[str, Any]]
    transcript_payload: Optional[Mapping[str, Any]]


def _mapping(value: Any) -> Optional[Mapping[str, Any]]:
    return value if isinstance(value, Mapping) else None


def _session_id(cached: Mapping[str, Any], session_payload: Mapping[str, Any]) -> str:
    own = cached.get("session_id")
    if isinstance(own, str) and own.strip():
        return own.strip()
    data = session_payload.get("data")
    payload_id = data.get("id") if isinstance(data, Mapping) else None
    return payload_id.strip() if isinstance(payload_id, str) else ""


def load_record_inputs(cached: Mapping[str, Any], *, roster_rows: Optional[RosterRows] = None) -> RecordInputs:
    """The record's inputs from a cached session row.

    `roster_rows`, when given, are the session's stored people and wins over
    the payload's `include=people` list. Missing payloads are None; a missing
    session payload gives no clock and an empty roster, so no author
    resolves.
    """
    if not isinstance(cached, Mapping):
        raise TypeError("load_record_inputs needs a cached session row")
    session_payload = _mapping(cached.get("session_payload")) or {}
    event_payload = _mapping(cached.get("event_payload"))
    session_id = _session_id(cached, session_payload)
    data = session_payload.get("data")
    attributes = data.get("attributes") if isinstance(data, Mapping) else None
    attributes = attributes if isinstance(attributes, Mapping) else {}

    if roster_rows is not None:
        roster = roster_from_rows(roster_rows, session_id=session_id, session_attributes=attributes)
    else:
        roster = roster_from_session_payload(session_payload, event_payload=event_payload, session_id=session_id)
    chat_payload = _mapping(cached.get("chat_payload"))
    questions_payload = _mapping(cached.get("questions_payload"))
    authors = resolve_authors(chat_payload, questions_payload, roster)

    return RecordInputs(
        session_id=session_id,
        clock=clock_base(session_payload),
        roster=escalate_roles(roster, authors.role_signals),
        authors=authors,
        session_payload=session_payload,
        event_payload=event_payload,
        chat_payload=chat_payload,
        questions_payload=questions_payload,
        transcript_payload=_mapping(cached.get("transcript_payload")),
    )
