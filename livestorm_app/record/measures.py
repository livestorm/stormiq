"""The measures registry: every number the v1 Report shows, and how it was got.

`compute_measures(inputs)` turns one session's `RecordInputs` into one
`MeasureValue` per registered measure (`REGISTRY`). A value never travels
alone: it comes with its numerator and denominator, a state, the coverage of
what it read, who it left out and, where Livestorm has one, Livestorm's own
figure. The SPA renders these fields and computes nothing.

Each `MeasureDef` holds:
- `id`: stable (`m.show_up_rate`); a new meaning gets a new id;
- `version`: +1 when its formula, exclusions or gates change. Changing any
  version, unit, source, exclusion or gate also bumps `METRICS_VERSION` and
  `REGISTRY_PIN` (`tests/test_measures.py` fails otherwise);
- `sources`: what it reads. One that wasn't fetched makes it `unavailable`;
- `excludes`: who an audience-only measure leaves out ("" for the others);
- `gates`: the minimum each quantity must reach. Below it, the value is
  hidden and the counts are still shown;
- `glossary`: its anchor in docs/product.md §13, where its formula lives.

A measure's state is the first that applies (`STATES`):
- `unavailable`: a source wasn't fetched; nothing is computed;
- `unresolved`: it needs to know who wrote, and a source it reads has items
  of which not one author resolved (each measure says which sources; a chat
  cached before StormIQ fetched people links is one). The value, the
  numerator and the denominator are hidden;
- `insufficient`, or a gate's own `below`: a gate isn't met. The value is
  hidden;
- `approximation`: the method itself is approximate (time in the room);
- `partial`: computed over incomplete inputs: the roster is partial, an
  attendance is unknown, or an author didn't resolve;
- `ok`.

Audience-only measures leave out the team and guest speakers and say how
many (`excluded`, with their `total`). On an internal session (`RosterMeta.internal`, the
classic rule) everyone counts as audience, as in the classic app, and
`excluded.internal` says so: nothing is left out, so such a measure is never
0 of 0.

Grouped measures (the room map, channels, segments, lead time) carry their
numbers in `rows`. A row can have its own gate: below it, its `state` is
`insufficient` and its rates are None, its counts still shown. Each measure
names its rows' values (`_Draft.row_values`), which `_finish` hides
whenever the measure's value is hidden (the row's `state` then becomes the
measure's), and the row counts that depend on who wrote
(`_Draft.row_authored`), which it also hides when unresolved. Channel and
segment quality are never unresolved: where people registered from, who
came and how long they stayed don't depend on authors, so when the chat's
or the questions' authors are unresolved they hide only who wrote
(`QUALITY_AUTHORED`) and read partial.

Pure functions: no database, network or web framework.
"""

from __future__ import annotations

import hashlib
import json
import math
import numbers
from dataclasses import dataclass, field as dataclass_field
from datetime import date, datetime, timezone
from functools import cached_property
from statistics import median
from types import MappingProxyType
from typing import Any, Callable, Dict, FrozenSet, List, Literal, Mapping, NamedTuple, Optional, Tuple

import numpy as np
import pandas as pd

from livestorm_app.record.authors import KINDS, UNRESOLVED as AUTHORS_UNRESOLVED, AuthorCoverage
from livestorm_app.record.clock import parse_time
from livestorm_app.record.evidence import CHAT, QUESTION, question_items
from livestorm_app.record.inputs import RecordInputs
from livestorm_app.record.roster import (
    AFTER_START,
    AUDIENCE,
    COMPLETE,
    DAY_S,
    DIRECT_OR_UNKNOWN,
    GUEST_SPEAKER,
    LEAD_BUCKETS,
    PAGINATED,
    PARTIAL as ROSTER_PARTIAL,
    TEAM,
    RosterMeta,
)

# ── The contract ────────────────────────────────────────────────────────────

State = Literal["ok", "insufficient", "partial", "approximation", "unresolved", "unavailable"]
OK = "ok"
INSUFFICIENT = "insufficient"
PARTIAL = "partial"
APPROXIMATION = "approximation"
UNRESOLVED = "unresolved"
UNAVAILABLE = "unavailable"
STATES: Tuple[str, ...] = (UNAVAILABLE, UNRESOLVED, INSUFFICIENT, APPROXIMATION, PARTIAL, OK)
# States whose value is hidden (None).
HIDDEN_STATES = frozenset({UNAVAILABLE, UNRESOLVED, INSUFFICIENT})

UNITS = ("ratio", "count", "seconds", "rows")

# What a measure reads.
SOURCE_SESSION = "session"  # the session payload's own attributes
SOURCE_ROSTER = "roster"  # the people (record/roster.py)
SOURCE_CHAT = "chat"
SOURCE_QUESTIONS = "questions"
SOURCE_TRANSCRIPT = "transcript"  # a Gladia transcript that parsed (record/transcript.py)
SOURCES = (SOURCE_SESSION, SOURCE_ROSTER, SOURCE_CHAT, SOURCE_QUESTIONS, SOURCE_TRANSCRIPT)

AUDIENCE_ONLY = "team, guest speakers"

# Version of the whole set: bump it whenever a measure's version changes or
# a measure is added or removed, and pin the new set (`registry_pin`) here
# and in tests/test_measures.py. The pin also covers each measure's unit,
# sources, exclusions and gates, so editing one without a new version fails
# the test. Stored numbers are keyed by it.
METRICS_VERSION = 4
REGISTRY_PIN = "371ad80ebb3c49f67537164dae3c8e33b359f0dfb3e9761fc9db464be99f9f46"

# Gate thresholds. Provisional: they move with a version bump.
WROTE_RATE_MIN_ATTENDEES = 10
TIME_TO_ANSWER_MIN_ANSWERED = 3
TIME_IN_ROOM_MIN_LENGTH_S = 1
CHAT_CONCENTRATION_MIN_MESSAGES = 20
ROOM_MAP_MIN_ATTENDEES = 10
CHANNEL_MIN_REGISTRANTS = 20  # per channel; smaller ones fold into OTHER
CHANNEL_MIN_CHANNELS = 2
CHANNEL_MIN_SOURCED_SHARE = 0.5
SEGMENT_MIN_REGISTRANTS = 10  # per value; smaller ones fold into OTHER
SEGMENT_MIN_SHOWN = 2  # values a field needs at or above the minimum
LEAD_TIME_MIN_REGISTRANTS = 100
LEAD_BUCKET_MIN_REGISTRANTS = 20  # per bucket; smaller ones merge with a neighbour
REPLAY_MIN_NO_SHOWS = 10

# Other rules of the grouped measures.
STAYED_MIN_SHARE = 0.75  # the room map's "stayed": at least this share of the session
REPLAY_SETTLED_DAYS = 7  # replay views count as complete once the people were read this long after the end
OTHER = "(other)"  # the row that small channels or values fold into
SELECT_FIELD_TYPES = ("unique_select", "multiple_select")

# The JSON form's keys, in order (`MeasureValue.to_json`).
JSON_KEYS = ("id", "version", "value", "num", "den", "state", "coverage", "excluded", "livestorm", "rows")
FLOAT_PLACES = 4


@dataclass(frozen=True)
class Gate:
    """The value is shown only when `field` reaches `minimum`; below it, the
    state is `below`. `field` is "den" (the denominator) or a quantity the
    measure names (`_Draft.quantities`)."""

    field: str
    minimum: float
    below: State = INSUFFICIENT


@dataclass(frozen=True)
class MeasureValue:
    """One measure for one session. `to_json` is what the record serves.

    - `value`: None when the state hides it, or when there is nothing to
      divide (never 0 of 0);
    - `num`, `den`: a ratio's numerator and denominator; for a median, `den`
      is how many values it is over; None for counts. Both are None when
      the state is `unresolved`;
    - `coverage`: what the measure read and how complete it was;
    - `excluded`: for an audience-only measure, `{team, guestSpeakers,
      total, internal}`; None for the others and when unavailable;
    - `livestorm`: Livestorm's own figure, when it has one;
    - `rows`: a breakdown, each row keyed by `key`.
    """

    id: str
    version: int
    value: Optional[float]
    num: Optional[int]
    den: Optional[int]
    state: State
    coverage: Mapping[str, Any]
    excluded: Optional[Mapping[str, Any]]
    livestorm: Optional[Mapping[str, Any]]
    rows: Optional[List[Mapping[str, Any]]]

    def to_json(self) -> Dict[str, Any]:
        """Plain JSON: floats rounded to `FLOAT_PLACES`, no NaN, times as ISO
        text. Keys in `JSON_KEYS` order."""
        return {key: _plain(getattr(self, key)) for key in JSON_KEYS}


@dataclass(frozen=True)
class _Draft:
    """What a measure's compute returns; `_finish` turns it into a value.
    `flags` are the states its inputs call for (`partial`, `approximation`,
    `unresolved`); `quantities` are what its gates read besides `den`.
    `row_values` are the row fields hidden with the value; `row_authored`
    the row fields that depend on who wrote, hidden when unresolved."""

    value: Optional[float] = None
    num: Optional[int] = None
    den: Optional[int] = None
    quantities: Mapping[str, Optional[float]] = dataclass_field(default_factory=dict)
    flags: FrozenSet[str] = frozenset()
    coverage: Mapping[str, Any] = dataclass_field(default_factory=dict)
    excluded: Optional[Mapping[str, Any]] = None
    livestorm: Optional[Mapping[str, Any]] = None
    rows: Optional[List[Mapping[str, Any]]] = None
    row_values: Tuple[str, ...] = ()
    row_authored: Tuple[str, ...] = ()


@dataclass(frozen=True)
class MeasureDef:
    """One registered measure (see the module docstring). `compute` reads
    the session's `_Facts` and returns a `_Draft`."""

    id: str
    version: int
    unit: str
    sources: Tuple[str, ...]
    excludes: str
    gates: Tuple[Gate, ...]
    glossary: str
    compute: Callable[["_Facts"], _Draft]


# ── Helpers ─────────────────────────────────────────────────────────────────


def _plain(value: Any) -> Any:
    """`value` as plain JSON: rounded finite floats, ints, text, lists, dicts."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if value is pd.NA:
        return None
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, numbers.Real):
        number = float(value)
        return round(number, FLOAT_PLACES) + 0.0 if math.isfinite(number) else None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    raise TypeError(f"a measure can't carry a {type(value).__name__}")


def _flags(**conditions: bool) -> FrozenSet[str]:
    return frozenset(name for name, applies in conditions.items() if applies)


def _ratio(num: Optional[int], den: Optional[int]) -> Optional[float]:
    return num / den if num is not None and den else None


def _bools(series: pd.Series) -> pd.Series:
    return series.fillna(False).astype(bool)


class _Question(NamedTuple):
    written: bool  # response text
    oral: bool  # the oral flag
    marked: bool  # a readable `responded_at`
    asked_at: Optional[float]
    answered_at: Optional[float]

    @property
    def answered(self) -> bool:
        return self.written or self.oral or self.marked


class _Writing(NamedTuple):
    """Who wrote in the chat or the questions, and how sure that is."""

    wrote: pd.Series  # per person: a chat message or a question resolved to them
    unresolved: bool  # the chat or the questions have items and not one author resolved
    unresolved_items: int  # chat messages and questions whose author didn't resolve
    coverage: Dict[str, Any]  # chatAuthors, questionAuthors, unresolvedItems


class _Arrays(NamedTuple):
    """Per-person columns by row position, for the grouped measures, which
    read them once per group: a group is an array of row positions, never a
    full-length mask, so a field with many values stays cheap."""

    audience: np.ndarray  # bool
    attended: np.ndarray  # bool: attended (an unknown attendance is False)
    wrote: np.ndarray  # bool: `_Writing.wrote`
    seconds: np.ndarray  # float: `_Facts.attendance_s`, NaN when unknown


class _Facts:
    """What several measures read, worked out once per session."""

    def __init__(self, inputs: RecordInputs) -> None:
        self.inputs = inputs
        self.meta: RosterMeta = inputs.roster.meta
        self.people: pd.DataFrame = inputs.roster.people
        self.internal = bool(self.meta.internal)
        roles = self.people["role"]
        # On an internal session everyone is audience.
        self.audience = pd.Series(True, index=self.people.index) if self.internal else roles.eq(AUDIENCE)
        self.attended = _bools(self.people["attended"])
        self.attendance_unknown = self.people["attended"].isna().astype(bool)
        self.roster_partial = self.meta.coverage != COMPLETE
        self.roster_coverage = {
            "roster": self.meta.coverage,
            "people": self.meta.people_count,
            "recordCount": self.meta.record_count,
        }

    def excluded(self, *, attendees: bool) -> Dict[str, Any]:
        """Who an audience-only measure left out: team members and guest
        speakers, over the attendees or over everyone, and their total (the
        page's "excludes your team (n)"). Nobody on an internal session,
        which says so."""
        if self.internal:
            return {"team": 0, "guestSpeakers": 0, "total": 0, "internal": True}
        counts = self.meta.attended_role_counts if attendees else self.meta.role_counts
        team, guests = int(counts[TEAM]), int(counts[GUEST_SPEAKER])
        return {"team": team, "guestSpeakers": guests, "total": team + guests, "internal": False}

    @property
    def audience_attendees(self) -> pd.Series:
        return self.audience & self.attended

    @property
    def audience_attendance_unknown(self) -> int:
        return int((self.audience & self.attendance_unknown).sum())

    @cached_property
    def writing(self) -> _Writing:
        """Contributors (`AuthorIndex.contributors`): one person who chats and
        asks counts once. Unresolved as `_source_unresolved` says, for the
        chat or the questions."""
        coverage = self.inputs.authors.coverage
        chat, questions = coverage[CHAT], coverage[QUESTION]
        unresolved_items = (chat.items - chat.resolved_items) + (questions.items - questions.resolved_items)
        return _Writing(
            wrote=self.people["person_id"].isin(self.inputs.authors.contributors()),
            unresolved=_source_unresolved(self, CHAT) or _source_unresolved(self, QUESTION),
            unresolved_items=unresolved_items,
            coverage={"chatAuthors": chat.state, "questionAuthors": questions.state, "unresolvedItems": unresolved_items},
        )

    @cached_property
    def session_length_s(self) -> Optional[float]:
        """The session's length (`clock.end_s`), when it is at least
        `TIME_IN_ROOM_MIN_LENGTH_S`."""
        length = self.inputs.clock.end_s
        return float(length) if length is not None and length >= TIME_IN_ROOM_MIN_LENGTH_S else None

    @cached_property
    def attendance_s(self) -> pd.Series:
        """Each person's attendance in seconds, a negative one as 0 and a
        longer one capped at the session's length; NaN when unknown or when
        the session has no length."""
        length = self.session_length_s
        if length is None:
            return pd.Series(math.nan, index=self.people.index, dtype="float64")
        return self.people["attendance_duration_s"].astype("float64").clip(lower=0.0, upper=length)

    @cached_property
    def arrays(self) -> _Arrays:
        """The per-person columns that `_quality` reads, as arrays."""
        return _Arrays(
            audience=self.audience.to_numpy(dtype=bool),
            attended=self.attended.to_numpy(dtype=bool),
            wrote=self.writing.wrote.to_numpy(dtype=bool),
            seconds=self.attendance_s.to_numpy(dtype="float64"),
        )

    @cached_property
    def questions(self) -> List[_Question]:
        return [
            _Question(
                written=bool(item.response),
                oral=item.answered_orally,
                marked=parse_time(item.responded_at) is not None,
                asked_at=parse_time(item.created_at),
                answered_at=parse_time(item.responded_at),
            )
            for item in question_items(self.inputs.questions_payload)
        ]


def available_sources(inputs: RecordInputs) -> FrozenSet[str]:
    """The sources this session has. The session and its roster come from a
    session payload with a resource (or from stored people); chat and
    questions are there when they were fetched, even empty; the transcript
    when a Gladia transcript parsed, even one without speech."""
    found = set()
    data = inputs.session_payload.get("data") if isinstance(inputs.session_payload, Mapping) else None
    if isinstance(data, Mapping):
        found |= {SOURCE_SESSION, SOURCE_ROSTER}
    elif inputs.roster.meta.source == PAGINATED:
        found.add(SOURCE_ROSTER)
    if isinstance(inputs.chat_payload, Mapping):
        found.add(SOURCE_CHAT)
    if isinstance(inputs.questions_payload, Mapping):
        found.add(SOURCE_QUESTIONS)
    if inputs.transcript is not None:
        found.add(SOURCE_TRANSCRIPT)
    return frozenset(found)


# ── The measures ────────────────────────────────────────────────────────────


def _roster_coverage(facts: _Facts) -> _Draft:
    meta = facts.meta
    return _Draft(
        value=_ratio(meta.people_count, meta.record_count),
        num=meta.people_count,
        den=meta.record_count,
        flags=_flags(partial=facts.roster_partial),
        coverage={**facts.roster_coverage, "source": meta.source, "syncedAt": meta.synced_at},
    )


def _audience_registrants(facts: _Facts) -> _Draft:
    return _Draft(
        value=int(facts.audience.sum()),
        flags=_flags(partial=facts.roster_partial),
        coverage=facts.roster_coverage,
        excluded=facts.excluded(attendees=False),
    )


def _team_count(facts: _Facts) -> _Draft:
    counts, attended = facts.meta.role_counts, facts.meta.attended_role_counts
    return _Draft(
        value=counts[TEAM] + counts[GUEST_SPEAKER],
        flags=_flags(partial=facts.roster_partial),
        coverage=facts.roster_coverage,
        rows=[{"key": role, "people": counts[role], "attendees": attended[role]} for role in (TEAM, GUEST_SPEAKER)],
    )


def _audience_attendees(facts: _Facts) -> _Draft:
    unknown = facts.audience_attendance_unknown
    return _Draft(
        value=int(facts.audience_attendees.sum()),
        flags=_flags(partial=facts.roster_partial or unknown > 0),
        coverage={**facts.roster_coverage, "attendanceUnknown": unknown},
        excluded=facts.excluded(attendees=True),
    )


def _show_up_rate(facts: _Facts) -> _Draft:
    num = int(facts.audience_attendees.sum())
    den = int(facts.audience.sum())
    unknown = facts.audience_attendance_unknown
    registrants, attendees = facts.meta.registrants_count, facts.meta.attendees_count
    return _Draft(
        value=_ratio(num, den),
        num=num,
        den=den,
        flags=_flags(partial=facts.roster_partial or unknown > 0),
        coverage={**facts.roster_coverage, "attendanceUnknown": unknown},
        excluded=facts.excluded(attendees=False),
        livestorm={"attendeesCount": attendees, "registrantsCount": registrants, "value": _ratio(attendees, registrants)},
    )


def _time_in_room(facts: _Facts) -> _Draft:
    """Always `approximation`, which comes before `partial`: a partial
    roster or an unknown duration shows in `coverage.roster` and
    `coverage.durationUnknown` instead. `coverage.medianAttendanceS` is the
    same median in seconds (each attendance capped at the session's length),
    so the page shows "36 of 45 min" without computing it."""
    length = facts.inputs.clock.end_s
    durations = facts.people.loc[facts.audience_attendees, "attendance_duration_s"]
    known = [float(duration) for duration in durations if not pd.isna(duration)]
    measured = length is not None and length >= TIME_IN_ROOM_MIN_LENGTH_S
    seconds = [min(max(duration, 0.0), length) for duration in known] if measured else []
    unknown = len(durations) - len(known)
    middle = median(seconds) if seconds else None
    return _Draft(
        value=middle / length if middle is not None else None,
        den=len(seconds),
        quantities={"attendees": len(known), "session_length_s": length},
        flags=_flags(approximation=True),
        coverage={
            **facts.roster_coverage,
            "durationUnknown": unknown,
            "sessionLengthS": length,
            "medianAttendanceS": middle,
        },
        excluded=facts.excluded(attendees=True),
    )


def _source_unresolved(facts: _Facts, kind: str) -> bool:
    """The source (`chat`, `question`) has items and not one of their
    authors resolved."""
    source = facts.inputs.authors.coverage[kind]
    return source.items > 0 and source.resolved_items == 0


def _wrote_rate(facts: _Facts) -> _Draft:
    """Unresolved when the chat or the questions have items and none of
    their authors resolved: that source's writers are unknown, and the count
    would leave them all out (a chat cached before people links would read
    as "only the askers wrote"). `_finish` then hides the value, the
    numerator and the denominator. When some of a source's authors resolve,
    the count is shown as partial."""
    attendees = facts.audience_attendees
    writing = facts.writing
    den = int(attendees.sum())
    num = int((attendees & writing.wrote).sum())
    return _Draft(
        value=_ratio(num, den),
        num=num,
        den=den,
        flags=_flags(
            unresolved=writing.unresolved,
            partial=facts.roster_partial or writing.unresolved_items > 0 or facts.audience_attendance_unknown > 0,
        ),
        coverage={**facts.roster_coverage, **writing.coverage},
        excluded=facts.excluded(attendees=True),
    )


def _questions_qa(facts: _Facts) -> _Draft:
    questions = facts.questions
    written = sum(1 for q in questions if q.written)
    oral = sum(1 for q in questions if q.oral and not q.written)
    marked = sum(1 for q in questions if q.marked and not (q.written or q.oral))
    unanswered = sum(1 for q in questions if not q.answered)
    num, den = written + oral + marked, len(questions)
    return _Draft(
        value=_ratio(num, den),
        num=num,
        den=den,
        coverage={"questions": den},
        rows=[
            {"key": "written", "questions": written},
            {"key": "oral", "questions": oral},
            {"key": "marked", "questions": marked},
            {"key": "open", "questions": unanswered},
        ],
    )


def _time_to_answer(facts: _Facts) -> _Draft:
    questions = facts.questions
    delays = [
        q.answered_at - q.asked_at
        for q in questions
        if q.answered_at is not None and q.asked_at is not None and q.answered_at >= q.asked_at
    ]
    return _Draft(
        value=median(delays) if delays else None,
        den=len(delays),
        coverage={"answered": sum(1 for q in questions if q.answered), "timed": len(delays)},
    )


def _author_row(kind: str, coverage: AuthorCoverage) -> Dict[str, Any]:
    return {
        "key": kind,
        "items": coverage.items,
        "withoutAuthor": coverage.without_author,
        "resolvedItems": coverage.resolved_items,
        "authors": coverage.authors,
        "resolvedAuthors": coverage.resolved_authors,
        "resolvedShare": coverage.resolved_share,
        "state": coverage.state,
    }


def _author_resolution(facts: _Facts) -> _Draft:
    """Unresolved when the chat has messages and none of their authors
    resolved: a chat cached without people links has no author ids, so the
    share would count the questions and answers alone."""
    index = facts.inputs.authors
    authors = {author.author_id: author.person_id is not None for kind in KINDS for author in index.authors[kind].values()}
    num, den = sum(authors.values()), len(authors)
    not_resolved = any(index.coverage[kind].state in (ROSTER_PARTIAL, AUTHORS_UNRESOLVED) for kind in KINDS)
    return _Draft(
        value=_ratio(num, den),
        num=num,
        den=den,
        flags=_flags(unresolved=_source_unresolved(facts, CHAT), partial=facts.roster_partial or not_resolved),
        coverage=facts.roster_coverage,
        rows=[_author_row(kind, index.coverage[kind]) for kind in KINDS],
    )


def _people_sum(facts: _Facts, column: str) -> Optional[int]:
    values = facts.people[column].dropna()
    return int(values.sum()) if len(values) else None


def _livestorm_counts(facts: _Facts) -> _Draft:
    meta = facts.meta
    return _Draft(
        flags=_flags(partial=facts.roster_partial),
        coverage=facts.roster_coverage,
        rows=[
            {"key": "registrants", "value": meta.registrants_count, "source": SOURCE_SESSION},
            {"key": "attendees", "value": meta.attendees_count, "source": SOURCE_SESSION},
            {"key": "messages", "value": _people_sum(facts, "ls_messages_count"), "source": "people"},
            {"key": "questions", "value": _people_sum(facts, "ls_questions_count"), "source": "people"},
            {"key": "votes", "value": _people_sum(facts, "ls_votes_count"), "source": "people"},
            {"key": "upVotes", "value": _people_sum(facts, "ls_up_votes_count"), "source": "people"},
        ],
    )


# ── The grouped audience measures ───────────────────────────────────────────


def _chat_concentration(facts: _Facts) -> _Draft:
    """The value is k, the fewest audience authors whose messages make at
    least half of the audience messages (the most active first). `den` is
    the audience messages it is over; the rows give the top k and the rest.

    Only messages resolved to an audience member count: an author that
    doesn't resolve is never guessed. Unresolved when the chat has messages
    and none resolved (a chat cached before people links); partial when
    some didn't. On an internal session every resolved message counts."""
    index = facts.inputs.authors
    chat = index.coverage[CHAT]
    audience_ids = set(facts.people.loc[facts.audience, "person_id"])
    counts = sorted(
        (author.items for author in index.authors[CHAT].values() if author.person_id in audience_ids), reverse=True
    )
    total = sum(counts)
    k = top = 0
    for count in counts:
        if 2 * top >= total:
            break
        k += 1
        top += count
    unresolved_items = chat.items - chat.resolved_items
    return _Draft(
        value=k if total else None,
        den=total,
        flags=_flags(
            unresolved=_source_unresolved(facts, CHAT), partial=facts.roster_partial or unresolved_items > 0
        ),
        coverage={
            **facts.roster_coverage,
            "chatAuthors": chat.state,
            "unresolvedItems": unresolved_items,
            "authors": len(counts),
        },
        excluded=facts.excluded(attendees=False),
        rows=[
            {"key": "top", "authors": k, "messages": top, "share": _ratio(top, total)},
            {"key": "rest", "authors": len(counts) - k, "messages": total - top, "share": _ratio(total - top, total)},
        ],
        row_values=("authors", "messages", "share"),
        row_authored=("authors", "messages", "share"),
    )


ROOM_CELLS = (
    ("stayed_wrote", True, True),
    ("stayed_quiet", True, False),
    ("left_wrote", False, True),
    ("left_quiet", False, False),
)


def _room_map(facts: _Facts) -> _Draft:
    """Audience attendees in four cells: stayed (attendance at least
    `STAYED_MIN_SHARE` of the session) or left, and wrote (a chat message
    or a question resolved to them) or stayed quiet. `den` is the
    attendees placed: those with an attendance duration. Always an
    approximation, as time in the room is; unresolved as `m.wrote_rate`
    is, and then every cell's count is hidden. The session-length gate is a
    backstop: without a length nobody is placed, so `den` is 0 and the
    attendee gate, checked first, already fails."""
    attendees = facts.audience_attendees
    seconds = facts.attendance_s
    placed = attendees & seconds.notna()
    length = facts.session_length_s
    stayed = seconds >= STAYED_MIN_SHARE * length if length is not None else pd.Series(False, index=seconds.index)
    writing = facts.writing
    den = int(placed.sum())
    rows = []
    for key, stayed_cell, wrote_cell in ROOM_CELLS:
        people = int((placed & stayed.eq(stayed_cell) & writing.wrote.eq(wrote_cell)).sum())
        rows.append({"key": key, "stayed": stayed_cell, "wrote": wrote_cell, "people": people, "share": _ratio(people, den)})
    return _Draft(
        den=den,
        quantities={"session_length_s": facts.inputs.clock.end_s},
        flags=_flags(
            unresolved=writing.unresolved,
            approximation=True,
            partial=facts.roster_partial or writing.unresolved_items > 0 or facts.audience_attendance_unknown > 0,
        ),
        coverage={
            **facts.roster_coverage,
            **writing.coverage,
            "durationUnknown": int((attendees & facts.people["attendance_duration_s"].isna()).sum()),
            "sessionLengthS": length,
        },
        excluded=facts.excluded(attendees=True),
        rows=rows,
        row_values=("share",),
        row_authored=("people", "share"),
    )


# The row fields of a channel or segment that are values, and those that
# depend on who wrote (None when the authors are unresolved: `_quality`).
QUALITY_VALUES = ("share", "showUpRate", "wroteRate", "medianTimeShare", "medianAttendanceS")
QUALITY_AUTHORED = ("wrote", "wroteRate")


def _positions(mask: Any) -> np.ndarray:
    """The row positions where a boolean mask (array or Series) is true."""
    return np.flatnonzero(np.asarray(mask, dtype=bool))


def _quality(facts: _Facts, members: np.ndarray, total: int, *, gated: bool) -> Dict[str, Any]:
    """One group of the audience (`members`, its people's row positions,
    each once): registrants and their share of `total`, show-up, attendees
    who wrote, and the median attendance (as a share of the session and in
    seconds, capped as in time in the room). `gated` (the row's own gate
    isn't met) hides the rates and medians, keeps the counts, and sets the
    row's state. When the chat's or the questions' authors are unresolved
    (`_Writing.unresolved`), who wrote is unknown: `QUALITY_AUTHORED` are
    None and the rest stays, as it doesn't depend on authors."""
    arrays = facts.arrays
    registrants = len(members)
    came = members[arrays.attended[members]]
    attendees = len(came)
    wrote = int(arrays.wrote[came].sum())
    seconds = arrays.seconds[came]
    seconds = seconds[~np.isnan(seconds)]
    middle = float(np.median(seconds)) if len(seconds) else None
    length = facts.session_length_s
    row = {
        "registrants": registrants,
        "share": _ratio(registrants, total),
        "attendees": attendees,
        "showUpRate": None if gated else _ratio(attendees, registrants),
        "wrote": wrote,
        "wroteRate": None if gated else _ratio(wrote, attendees),
        "medianTimeShare": None if gated or middle is None or length is None else middle / length,
        "medianAttendanceS": None if gated else middle,
        "state": INSUFFICIENT if gated else OK,
    }
    if facts.writing.unresolved:
        row.update(dict.fromkeys(QUALITY_AUTHORED))
    return row


def _quality_flags(facts: _Facts, *, more_partial: bool = False) -> FrozenSet[str]:
    """Never unresolved: unresolved authors only hide who wrote (`_quality`),
    and make the measure partial, as an author that didn't resolve does."""
    writing = facts.writing
    return _flags(
        partial=facts.roster_partial
        or writing.unresolved_items > 0
        or facts.audience_attendance_unknown > 0
        or more_partial,
    )


def _channel_quality(facts: _Facts) -> _Draft:
    """One row per channel (`roster.derive_channel`) of the audience, the
    largest first. A channel under `CHANNEL_MIN_REGISTRANTS` folds into
    `OTHER`, whose rates show only when it reaches the minimum too.
    Registrants without a source (`direct_or_unknown`) are a row of their
    own, never a channel, with the same minimum. Gates: at least
    `CHANNEL_MIN_SOURCED_SHARE` of the audience registrants with a source,
    and `CHANNEL_MIN_CHANNELS` channels at the minimum. A session where
    nobody has a UTM, referrer or registration type (an internal meeting)
    fails both and has no rows: one `direct_or_unknown` row of everyone
    would read as "direct 100%"; `coverage.withSource` says why. Partial,
    never unresolved, when the authors are unresolved (`_quality_flags`)."""
    people = facts.people
    audience = facts.arrays.audience
    total = int(audience.sum())
    sourced = audience & people["channel_source"].ne("none").to_numpy(dtype=bool)
    with_source = int(sourced.sum())
    channels = people["channel"].to_numpy(dtype=object)
    by_channel: Dict[str, List[int]] = {}
    for position in _positions(sourced):
        by_channel.setdefault(channels[position], []).append(position)
    counts = {channel: len(members) for channel, members in by_channel.items()}
    # A channel named like a reserved row (a UTM source "direct_or_unknown")
    # folds into OTHER, so row keys stay unique.
    named = sorted(
        (
            channel
            for channel, count in counts.items()
            if count >= CHANNEL_MIN_REGISTRANTS and channel not in (OTHER, DIRECT_OR_UNKNOWN)
        ),
        key=lambda c: (-counts[c], c),
    )
    named_channels = frozenset(named)
    small = [channel for channel in counts if channel not in named_channels]
    rows = [
        {"key": channel, "kind": "channel", **_quality(facts, np.asarray(by_channel[channel]), total, gated=False)}
        for channel in named
    ]
    if small:
        members = np.sort(np.concatenate([np.asarray(by_channel[channel]) for channel in small]))
        gated = len(members) < CHANNEL_MIN_REGISTRANTS
        rows.append({"key": OTHER, "kind": "other", "channels": len(small), **_quality(facts, members, total, gated=gated)})
    unknown = _positions(audience & ~sourced)
    if with_source and len(unknown):
        gated = len(unknown) < CHANNEL_MIN_REGISTRANTS
        rows.append({"key": DIRECT_OR_UNKNOWN, "kind": "none", **_quality(facts, unknown, total, gated=gated)})
    sourced_share = _ratio(with_source, total)
    return _Draft(
        quantities={"sourced_share": sourced_share, "channels": len(named)},
        flags=_quality_flags(facts),
        coverage={
            **facts.roster_coverage,
            **facts.writing.coverage,
            "attendanceUnknown": facts.audience_attendance_unknown,
            "registrants": total,
            "withSource": with_source,
            "sourcedShare": sourced_share,
            "channels": len(named),
            "channelRule": facts.meta.channel_rule_version,
        },
        excluded=facts.excluded(attendees=False),
        rows=rows,
        row_values=QUALITY_VALUES,
    )


def _answer_values(answer: Any) -> List[str]:
    """A select answer as its values: one for a single select, each one for
    a multiple select."""
    values = answer if isinstance(answer, (list, tuple)) else [answer]
    return list(dict.fromkeys(value.strip() for value in values if isinstance(value, str) and value.strip()))


def _segment_field(facts: _Facts, field: Mapping[str, Any], total: int) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """The rows and the coverage of one select field. One pass over the
    audience's answers gives each value its people's row positions."""
    slug = str(field.get("slug"))
    answers = facts.people["answers"].to_numpy(dtype=object)
    options = {
        str(option.get("value")): option for option in field.get("options") or () if isinstance(option, Mapping)
    }
    by_value: Dict[str, List[int]] = {}
    answered = 0
    for row_position in _positions(facts.arrays.audience):
        person = answers[row_position]  # a dict (`roster._row`)
        values = _answer_values(person.get(slug)) if isinstance(person, dict) else []
        answered += bool(values)
        for value in values:
            by_value.setdefault(value, []).append(row_position)
    members = {value: np.asarray(people) for value, people in by_value.items()}
    counts = {value: len(people) for value, people in by_value.items()}
    position = {value: options.get(value, {}).get("position") for value in members}

    def order(value: str) -> Tuple[bool, float, int, str]:
        """Options' order when known, then the largest first."""
        at = position[value]
        placed = isinstance(at, numbers.Real) and not isinstance(at, bool)
        return (not placed, float(at) if placed else 0.0, -counts[value], value)

    # A value named like the OTHER row folds into it, so row keys stay unique.
    named = sorted(
        (value for value, count in counts.items() if count >= SEGMENT_MIN_REGISTRANTS and value != OTHER), key=order
    )
    named_values = frozenset(named)
    small = [value for value in counts if value not in named_values]
    shown = len(named) >= SEGMENT_MIN_SHOWN
    rows = []
    for value in named:
        label = options.get(value, {}).get("label")
        rows.append(
            {
                "key": f"{slug}:{value}",
                "kind": "value",
                "field": slug,
                "value": value,
                "label": label if isinstance(label, str) and label.strip() else value,
                **_quality(facts, members[value], total, gated=not shown),
            }
        )
    if small:
        # A person who chose two small values counts once.
        other = np.unique(np.concatenate([members[value] for value in small]))
        gated = not shown or len(other) < SEGMENT_MIN_REGISTRANTS
        rows.append(
            {
                "key": f"{slug}:{OTHER}",
                "kind": "other",
                "field": slug,
                "value": None,
                "label": None,
                "values": len(small),
                **_quality(facts, other, total, gated=gated),
            }
        )
    coverage = {
        "field": slug,
        "name": field.get("name"),
        "type": field.get("type"),
        "answered": answered,
        "segments": len(named),
    }
    return rows, coverage


def _segment_quality(facts: _Facts) -> _Draft:
    """One row per value of each select field of the registration form
    (`RosterMeta.fields`), with the same columns as a channel. A value
    under `SEGMENT_MIN_REGISTRANTS` folds into its field's `OTHER`. A
    field's rates show only when it has `SEGMENT_MIN_SHOWN` values at the
    minimum; the gate is one such field. A multiple select counts a person
    under each value they chose, so a field's shares can add up to more
    than 1. Without a select field (none on the form, or the event's fields
    weren't cached), insufficient with `coverage.selectFields` 0. Labels
    come from the field's options when StormIQ has them, else the value.
    Partial, never unresolved, as channel quality is."""
    total = int(facts.audience.sum())
    select = [field for field in facts.meta.fields if str(field.get("type") or "").lower() in SELECT_FIELD_TYPES]
    rows: List[Dict[str, Any]] = []
    fields: List[Dict[str, Any]] = []
    for field in select:
        field_rows, field_coverage = _segment_field(facts, field, total)
        rows += field_rows
        fields.append(field_coverage)
    shown = sum(1 for item in fields if item["segments"] >= SEGMENT_MIN_SHOWN)
    return _Draft(
        quantities={"fields": shown},
        flags=_quality_flags(facts),
        coverage={
            **facts.roster_coverage,
            **facts.writing.coverage,
            "attendanceUnknown": facts.audience_attendance_unknown,
            "registrants": total,
            "fieldsSource": facts.meta.fields_source,
            "selectFields": len(select),
            "fields": fields,
        },
        excluded=facts.excluded(attendees=False),
        rows=rows,
        row_values=QUALITY_VALUES,
    )


def _merge_small(groups: List[List[str]], counts: Mapping[str, int], minimum: int) -> List[List[str]]:
    """Merge the smallest group under `minimum` with its smaller neighbour
    (the earlier one on a tie), again and again, until every group reaches
    it or one group is left. Ties for the smallest go to the earlier one."""
    groups = [list(group) for group in groups]

    def size(group: List[str]) -> int:
        return sum(counts[bucket] for bucket in group)

    while len(groups) > 1:
        smallest = min(range(len(groups)), key=lambda i: (size(groups[i]), i))
        if size(groups[smallest]) >= minimum:
            break
        neighbours = [i for i in (smallest - 1, smallest + 1) if 0 <= i < len(groups)]
        other = min(neighbours, key=lambda i: (size(groups[i]), i))
        first, last = sorted((smallest, other))
        groups[first : last + 1] = [groups[first] + groups[last]]
    return groups


def _lead_time_show_up(facts: _Facts) -> _Draft:
    """Show-up per lead-time bucket (`roster.lead_time_bucket`), over the
    audience registrants with a lead time (`den`). The buckets before the
    start that have people merge with a neighbour while under
    `LEAD_BUCKET_MIN_REGISTRANTS` (`_merge_small`); a merged row's key is
    its first and last bucket (`1-3..8-14`). Registering after the start
    is its own row, never merged: those people registered to join. A row
    still under the minimum hides its rates."""
    people = facts.people
    audience = facts.audience
    buckets = people["lead_bucket"]
    known = audience & buckets.notna()
    den = int(known.sum())
    counts = {bucket: int((known & buckets.eq(bucket)).sum()) for bucket in LEAD_BUCKETS}
    before = [[bucket] for bucket in LEAD_BUCKETS if bucket != AFTER_START and counts[bucket] > 0]
    groups = _merge_small(before, counts, LEAD_BUCKET_MIN_REGISTRANTS)
    if counts[AFTER_START] > 0:
        groups.append([AFTER_START])
    rows = []
    for group in groups:
        members = known & buckets.isin(group)
        registrants = int(members.sum())
        attendees = int((members & facts.attended).sum())
        gated = registrants < LEAD_BUCKET_MIN_REGISTRANTS
        rows.append(
            {
                "key": group[0] if len(group) == 1 else f"{group[0]}..{group[-1]}",
                "buckets": group,
                "registrants": registrants,
                "share": _ratio(registrants, den),
                "attendees": attendees,
                "showUpRate": None if gated else _ratio(attendees, registrants),
                "state": INSUFFICIENT if gated else OK,
            }
        )
    lead_unknown = int((audience & buckets.isna()).sum())
    return _Draft(
        den=den,
        flags=_flags(partial=facts.roster_partial or facts.audience_attendance_unknown > 0 or lead_unknown > 0),
        coverage={
            **facts.roster_coverage,
            "attendanceUnknown": facts.audience_attendance_unknown,
            "leadUnknown": lead_unknown,
        },
        excluded=facts.excluded(attendees=False),
        rows=rows,
        row_values=("share", "showUpRate"),
    )


def _unix(value: Any) -> Optional[float]:
    if isinstance(value, datetime):
        return (value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)).timestamp()
    return parse_time(value)


def _replay_settled(facts: _Facts) -> bool:
    """Whether the people were read at least `REPLAY_SETTLED_DAYS` after the
    session ended, so later replay views are in. Unknown counts as no."""
    synced = _unix(facts.meta.synced_at)
    clock = facts.inputs.clock
    if synced is None or clock.t0_unix is None or clock.end_s is None:
        return False
    return synced >= clock.t0_unix + clock.end_s + REPLAY_SETTLED_DAYS * DAY_S


def _replay_caught_up(facts: _Facts) -> _Draft:
    """Audience no-shows who viewed the replay ÷ audience no-shows. Rows:
    the no-shows, and the attendees who watched it again. People whose
    attendance or replay flag is unknown are left out and counted. Gate:
    `REPLAY_MIN_NO_SHOWS`; below it the counts are shown, not the shares,
    as one or two no-shows would read as 0% or 100%. Partial
    until the replay views are settled (`_replay_settled`): Livestorm's
    flag only shows views made before StormIQ read the people."""
    people = facts.people
    audience = facts.audience
    replay = people["has_viewed_replay"]
    replay_known = replay.notna().astype(bool)
    viewed = _bools(replay)
    no_shows = audience & ~facts.attendance_unknown & ~facts.attended & replay_known
    attendees = facts.audience_attendees & replay_known
    num, den = int((no_shows & viewed).sum()), int(no_shows.sum())
    rewatched, came = int((attendees & viewed).sum()), int(attendees.sum())
    replay_unknown = int((audience & ~replay_known).sum())
    settled = _replay_settled(facts)
    return _Draft(
        value=_ratio(num, den),
        num=num,
        den=den,
        flags=_flags(
            partial=facts.roster_partial
            or not settled
            or replay_unknown > 0
            or facts.audience_attendance_unknown > 0
        ),
        coverage={
            **facts.roster_coverage,
            "syncedAt": facts.meta.synced_at,
            "settled": settled,
            "replayUnknown": replay_unknown,
            "attendanceUnknown": facts.audience_attendance_unknown,
        },
        excluded=facts.excluded(attendees=False),
        rows=[
            {"key": "no_shows", "people": den, "viewedReplay": num, "share": _ratio(num, den)},
            {"key": "attendees", "people": came, "viewedReplay": rewatched, "share": _ratio(rewatched, came)},
        ],
        row_values=("share",),
    )


def _transcript_health(facts: _Facts) -> _Draft:
    """The mean word confidence over the words that have one (`den`), with
    the transcript's health in `coverage` (`record.transcriptHealth`, built
    once by `inputs.load_record_inputs`). Partial when some words have no
    confidence."""
    health = facts.inputs.transcript_health
    words, coverage, speakers = health["words"], health["coverage"], health["speakers"]
    return _Draft(
        value=words["meanConfidence"],
        den=words["withConfidence"],
        flags=_flags(partial=words["count"] > words["withConfidence"] > 0),
        coverage={
            "health": health["state"],
            "reasons": list(health["reasons"]),
            "model": health["model"],
            "words": words["count"],
            "lowConfidenceShare": words["lowConfidenceShare"],
            "speechShareOfAudio": coverage["shareOfAudio"],
            "speakersSubstantial": speakers["substantial"],
            "speakersExpected": speakers["expected"],
            "splitRisk": speakers["splitRisk"],
            "chunks": speakers["chunks"],
        },
    )


# ── The registry ────────────────────────────────────────────────────────────

_ROSTER = (SOURCE_ROSTER,)
_DEFINITIONS = (
    MeasureDef("m.roster_coverage", 1, "ratio", _ROSTER, "", (), "roster-coverage", _roster_coverage),
    MeasureDef("m.audience_registrants", 1, "count", _ROSTER, AUDIENCE_ONLY, (), "audience-registrants", _audience_registrants),
    MeasureDef("m.team_count", 1, "count", _ROSTER, "", (), "team-count", _team_count),
    MeasureDef("m.audience_attendees", 1, "count", _ROSTER, AUDIENCE_ONLY, (), "audience-attendees", _audience_attendees),
    MeasureDef(
        "m.show_up_rate", 1, "ratio", (SOURCE_SESSION, SOURCE_ROSTER), AUDIENCE_ONLY, (Gate("den", 1),),
        "show-up-rate", _show_up_rate,
    ),
    MeasureDef(
        "m.time_in_room", 1, "ratio", _ROSTER, AUDIENCE_ONLY, (Gate("attendees", 1), Gate("session_length_s", TIME_IN_ROOM_MIN_LENGTH_S)),
        "time-in-the-room", _time_in_room,
    ),
    MeasureDef(
        "m.wrote_rate", 1, "ratio", (SOURCE_ROSTER, SOURCE_CHAT, SOURCE_QUESTIONS), AUDIENCE_ONLY,
        (Gate("den", WROTE_RATE_MIN_ATTENDEES),), "wrote-in-chat-or-qa", _wrote_rate,
    ),
    MeasureDef("m.questions_qa", 1, "ratio", (SOURCE_QUESTIONS,), "", (Gate("den", 1),), "questions-and-answers", _questions_qa),
    MeasureDef(
        "m.time_to_answer", 1, "seconds", (SOURCE_QUESTIONS,), "", (Gate("den", TIME_TO_ANSWER_MIN_ANSWERED),),
        "time-to-answer", _time_to_answer,
    ),
    MeasureDef(
        "m.author_resolution", 1, "ratio", (SOURCE_ROSTER, SOURCE_CHAT, SOURCE_QUESTIONS), "", (Gate("den", 1),),
        "author-resolution", _author_resolution,
    ),
    MeasureDef("m.livestorm_counts", 1, "rows", (SOURCE_SESSION,), "", (), "livestorm-counts", _livestorm_counts),
    # The grouped audience measures.
    MeasureDef(
        "m.chat_concentration", 1, "count", (SOURCE_ROSTER, SOURCE_CHAT), AUDIENCE_ONLY,
        (Gate("den", CHAT_CONCENTRATION_MIN_MESSAGES),), "chat-concentration", _chat_concentration,
    ),
    MeasureDef(
        "m.room_map", 1, "rows", (SOURCE_ROSTER, SOURCE_CHAT, SOURCE_QUESTIONS), AUDIENCE_ONLY,
        (Gate("den", ROOM_MAP_MIN_ATTENDEES), Gate("session_length_s", TIME_IN_ROOM_MIN_LENGTH_S)), "room-map", _room_map,
    ),
    MeasureDef(
        "m.channel_quality", 1, "rows", (SOURCE_ROSTER, SOURCE_CHAT, SOURCE_QUESTIONS), AUDIENCE_ONLY,
        (Gate("sourced_share", CHANNEL_MIN_SOURCED_SHARE), Gate("channels", CHANNEL_MIN_CHANNELS)),
        "channel-quality", _channel_quality,
    ),
    MeasureDef(
        "m.segment_quality", 1, "rows", (SOURCE_ROSTER, SOURCE_CHAT, SOURCE_QUESTIONS), AUDIENCE_ONLY,
        (Gate("fields", 1),), "segment-quality", _segment_quality,
    ),
    MeasureDef(
        "m.lead_time_show_up", 1, "rows", (SOURCE_SESSION, SOURCE_ROSTER), AUDIENCE_ONLY,
        (Gate("den", LEAD_TIME_MIN_REGISTRANTS),), "show-up-by-lead-time", _lead_time_show_up,
    ),
    MeasureDef(
        "m.replay_caught_up", 2, "ratio", _ROSTER, AUDIENCE_ONLY, (Gate("den", REPLAY_MIN_NO_SHOWS),), "replay-catch-up",
        _replay_caught_up,
    ),
    MeasureDef(
        "m.transcript_health", 1, "ratio", (SOURCE_TRANSCRIPT,), "", (Gate("den", 1),), "transcript-health", _transcript_health,
    ),
)
REGISTRY: Mapping[str, MeasureDef] = MappingProxyType({definition.id: definition for definition in _DEFINITIONS})


def _declared(definition: MeasureDef) -> List[Any]:
    """What a definition declares besides its compute: id, version, unit,
    sources, exclusions and gates."""
    gates = [[gate.field, gate.minimum, gate.below] for gate in definition.gates]
    return [definition.id, definition.version, definition.unit, list(definition.sources), definition.excludes, gates]


def registry_pin(registry: Mapping[str, MeasureDef] = REGISTRY) -> str:
    """sha256 of every definition's declared part (`_declared`), sorted by
    id: what `REGISTRY_PIN` pins. A gate, source, exclusion or unit edited
    without a new version changes it too."""
    declared = sorted(_declared(definition) for definition in registry.values())
    return hashlib.sha256(json.dumps(declared, separators=(",", ":")).encode("utf-8")).hexdigest()


# ── Computing and checking ──────────────────────────────────────────────────


def _passes(gate: Gate, draft: _Draft) -> bool:
    quantity = draft.den if gate.field == "den" else draft.quantities.get(gate.field)
    return quantity is not None and quantity >= gate.minimum


def _rows(draft: _Draft, state: str) -> Optional[List[Mapping[str, Any]]]:
    """The draft's rows with the fields the state hides set to None: its
    `row_values` in a state that hides the value (and a row's own `state`
    becomes the measure's), plus its `row_authored` when unresolved. Rows
    of a measure that declares neither are left as they are (the author
    coverage rows of `m.author_resolution` keep their own states)."""
    if draft.rows is None or state not in HIDDEN_STATES or not (draft.row_values or draft.row_authored):
        return draft.rows
    hidden = set(draft.row_values) | (set(draft.row_authored) if state == UNRESOLVED else set())
    rows = []
    for row in draft.rows:
        changed = {name: (None if name in hidden else value) for name, value in row.items()}
        if "state" in changed:
            changed["state"] = state
        rows.append(changed)
    return rows


def _finish(definition: MeasureDef, draft: _Draft) -> MeasureValue:
    """The state, first match wins (module docstring), the value hidden
    when the state calls for it, and the numerator and denominator hidden
    when unresolved (a count of the resolved part would read as complete).
    Rows lose the fields the state hides (`_rows`)."""
    if UNRESOLVED in draft.flags:
        state = UNRESOLVED
    else:
        failed = next((gate for gate in definition.gates if not _passes(gate, draft)), None)
        if failed is not None:
            state = failed.below
        elif APPROXIMATION in draft.flags:
            state = APPROXIMATION
        elif PARTIAL in draft.flags:
            state = PARTIAL
        else:
            state = OK
    return MeasureValue(
        id=definition.id,
        version=definition.version,
        value=None if state in HIDDEN_STATES else draft.value,
        num=None if state == UNRESOLVED else draft.num,
        den=None if state == UNRESOLVED else draft.den,
        state=state,
        coverage=draft.coverage,
        excluded=draft.excluded,
        livestorm=draft.livestorm,
        rows=_rows(draft, state),
    )


def _unavailable(definition: MeasureDef, missing: List[str]) -> MeasureValue:
    return MeasureValue(
        id=definition.id,
        version=definition.version,
        value=None,
        num=None,
        den=None,
        state=UNAVAILABLE,
        coverage={"missing": missing},
        excluded=None,
        livestorm=None,
        rows=None,
    )


def compute_measures(inputs: RecordInputs) -> Dict[str, MeasureValue]:
    """Every registered measure for one session, in registry order."""
    facts = _Facts(inputs)
    found = available_sources(inputs)
    measures: Dict[str, MeasureValue] = {}
    for definition in REGISTRY.values():
        missing = [source for source in definition.sources if source not in found]
        measures[definition.id] = (
            _unavailable(definition, missing) if missing else _finish(definition, definition.compute(facts))
        )
    return measures


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def validate_measures(measures: Mapping[str, Any]) -> None:
    """Raise ValueError unless `measures` is a complete set in its JSON form
    (`MeasureValue.to_json`): every registry id and no other, each with the
    registry's version, a known state, no value in a state that hides it,
    no numerator or denominator when unresolved, counts as non-negative ints, and nothing
    strict JSON can't carry."""
    if not isinstance(measures, Mapping):
        raise ValueError("measures must be a mapping of id to measure")
    missing = [measure_id for measure_id in REGISTRY if measure_id not in measures]
    unknown = [measure_id for measure_id in measures if measure_id not in REGISTRY]
    if missing or unknown:
        raise ValueError(f"measures don't match the registry: missing {missing}, unknown {unknown}")
    for measure_id, item in measures.items():
        if not isinstance(item, Mapping) or tuple(item) != JSON_KEYS:
            raise ValueError(f"{measure_id}: keys must be {JSON_KEYS}")
        if item["id"] != measure_id or item["version"] != REGISTRY[measure_id].version:
            raise ValueError(f"{measure_id}: id or version doesn't match the registry")
        state, value = item["state"], item["value"]
        if state not in STATES:
            raise ValueError(f"{measure_id}: unknown state {state!r}")
        if state in HIDDEN_STATES and value is not None:
            raise ValueError(f"{measure_id}: a {state} measure has no value")
        if state == UNRESOLVED and (item["num"] is not None or item["den"] is not None):
            raise ValueError(f"{measure_id}: an unresolved measure has no numerator or denominator")
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0
        ):
            raise ValueError(f"{measure_id}: value must be a non-negative number or None")
        for name in ("num", "den"):
            if item[name] is not None and not _is_count(item[name]):
                raise ValueError(f"{measure_id}: {name} must be a non-negative int or None")
        if not isinstance(item["coverage"], Mapping):
            raise ValueError(f"{measure_id}: coverage must be a mapping")
        for name in ("excluded", "livestorm"):
            if item[name] is not None and not isinstance(item[name], Mapping):
                raise ValueError(f"{measure_id}: {name} must be a mapping or None")
        rows = item["rows"]
        if rows is not None and (
            not isinstance(rows, list) or not all(isinstance(row, Mapping) and isinstance(row.get("key"), str) for row in rows)
        ):
            raise ValueError(f"{measure_id}: rows must be None or a list of rows, each with a text key")
        if rows is not None and len({row["key"] for row in rows}) != len(rows):
            raise ValueError(f"{measure_id}: two rows have the same key")
    try:
        json.dumps(measures, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError(f"measures aren't strict JSON: {error}") from error
