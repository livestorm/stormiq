"""The single loader of what the v1 record is computed from.

`load_record_inputs(cached)` takes a cached session row (the dict
`db.fetch_cached_session` returns, read with the caller's scope) and gives
one `RecordInputs`: the payloads, the session clock, the roster and the
authors of the chat and the questions, resolved to roster people
(`authors.resolve_authors`). The roster's roles are raised by what each
person did: the team flags of the chat messages resolved to them, and the
answers they wrote (`roster.escalate_roles`), except on an internal session.
The transcript, when there is one, is parsed once here
(`transcript.parse_transcript`) and its health built once
(`transcript_health.build_transcript_health`), for the record block and the
measures alike.

The roster comes from stored person rows when the caller has them
(`roster_rows`), and from the session payload's `include=people` list
otherwise (`_roster`). This is the only place that chooses, so a new people
source changes nothing downstream. The transcription job's speaker cap reads
its count of people on stage here too (`count_people_on_stage`), from the
same source.

Pure: no database, network or web framework. The caller does the scoped read.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from livestorm_app.record.authors import AuthorIndex, resolve_authors
from livestorm_app.record.clock import ClockBase, clock_base
from livestorm_app.record.roster import (
    Roster,
    RosterRows,
    escalate_roles,
    people_on_stage,
    roster_from_rows,
    roster_from_session_payload,
)
from livestorm_app.record.transcript import ParsedTranscript, parse_transcript
from livestorm_app.record.transcript_health import build_transcript_health


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
    # The parsed transcript (None without a Gladia transcript) and its health
    # (`record.transcriptHealth`), built once per load.
    transcript: Optional[ParsedTranscript]
    transcript_health: Mapping[str, Any]


def _mapping(value: Any) -> Optional[Mapping[str, Any]]:
    return value if isinstance(value, Mapping) else None


def _session_id(cached: Mapping[str, Any], session_payload: Mapping[str, Any]) -> str:
    own = cached.get("session_id")
    if isinstance(own, str) and own.strip():
        return own.strip()
    data = session_payload.get("data")
    payload_id = data.get("id") if isinstance(data, Mapping) else None
    return payload_id.strip() if isinstance(payload_id, str) else ""


def _roster(
    cached: Mapping[str, Any],
    session_payload: Mapping[str, Any],
    session_id: str,
    roster_rows: Optional[RosterRows],
) -> Roster:
    """The session's roster from its one people source: the stored person
    rows when given (`roster_rows`), the session payload's `include=people`
    list otherwise."""
    if roster_rows is not None:
        data = session_payload.get("data")
        attributes = data.get("attributes") if isinstance(data, Mapping) else None
        return roster_from_rows(
            roster_rows,
            session_id=session_id,
            session_attributes=attributes if isinstance(attributes, Mapping) else {},
        )
    return roster_from_session_payload(
        session_payload, event_payload=_mapping(cached.get("event_payload")), session_id=session_id
    )


def count_people_on_stage(cached: Any, *, roster_rows: Optional[RosterRows] = None) -> Optional[int]:
    """How many people of a cached session are on stage (`roster.people_on_stage`),
    from the same people source as the record (`_roster`). The speaker cap of
    a new transcript is built from it (docs/product.md §13, "Speaker cap").

    `cached` holds at least the `session_payload`. None when unknown: no
    people, or nobody marked as an organizer or a guest speaker (an internal
    meeting can mark nobody). A cap built from 0 would be the floor, too low
    for a meeting where many people talk, so 0 reads as unknown.
    """
    if not isinstance(cached, Mapping):
        return None
    session_payload = _mapping(cached.get("session_payload")) or {}
    roster = _roster(cached, session_payload, _session_id(cached, session_payload), roster_rows)
    count = people_on_stage(roster)
    return count if count > 0 else None


def _event_language(event_payload: Optional[Mapping[str, Any]]) -> Any:
    data = event_payload.get("data") if isinstance(event_payload, Mapping) else None
    attributes = data.get("attributes") if isinstance(data, Mapping) else None
    return attributes.get("language") if isinstance(attributes, Mapping) else None


def load_record_inputs(cached: Mapping[str, Any], *, roster_rows: Optional[RosterRows] = None) -> RecordInputs:
    """The record's inputs from a cached session row.

    `roster_rows`, when given, are the session's stored people and wins over
    the payload's `include=people` list. Missing payloads are None; a missing
    session payload gives no clock and an empty roster, so no author
    resolves. The row's `transcript_payload` and `transcript_speaker_names`,
    when present, give the parsed transcript and its health.
    """
    if not isinstance(cached, Mapping):
        raise TypeError("load_record_inputs needs a cached session row")
    session_payload = _mapping(cached.get("session_payload")) or {}
    event_payload = _mapping(cached.get("event_payload"))
    session_id = _session_id(cached, session_payload)
    roster = _roster(cached, session_payload, session_id, roster_rows)
    chat_payload = _mapping(cached.get("chat_payload"))
    questions_payload = _mapping(cached.get("questions_payload"))
    authors = resolve_authors(chat_payload, questions_payload, roster)
    clock = clock_base(session_payload)
    transcript_payload = _mapping(cached.get("transcript_payload"))
    transcript = parse_transcript(transcript_payload)
    on_stage = people_on_stage(roster)  # as the speaker cap counts them (`count_people_on_stage`)
    transcript_health = build_transcript_health(
        transcript,
        live_s=clock.end_s,
        people_on_stage=on_stage if on_stage > 0 else None,
        expected_language=_event_language(event_payload),
        speaker_names=cached.get("transcript_speaker_names"),
    )

    return RecordInputs(
        session_id=session_id,
        clock=clock,
        roster=escalate_roles(roster, authors.role_signals),
        authors=authors,
        session_payload=session_payload,
        event_payload=event_payload,
        chat_payload=chat_payload,
        questions_payload=questions_payload,
        transcript_payload=transcript_payload,
        transcript=transcript,
        transcript_health=transcript_health,
    )
