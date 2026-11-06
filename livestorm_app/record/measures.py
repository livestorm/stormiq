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

Pure functions: no database, network or web framework.
"""

from __future__ import annotations

import hashlib
import json
import math
import numbers
from dataclasses import dataclass, field as dataclass_field
from datetime import date, datetime
from functools import cached_property
from statistics import median
from types import MappingProxyType
from typing import Any, Callable, Dict, FrozenSet, List, Literal, Mapping, NamedTuple, Optional, Tuple

import pandas as pd

from livestorm_app.record.authors import KINDS, UNRESOLVED as AUTHORS_UNRESOLVED, AuthorCoverage
from livestorm_app.record.clock import parse_time
from livestorm_app.record.evidence import CHAT, QUESTION, question_items
from livestorm_app.record.inputs import RecordInputs
from livestorm_app.record.roster import AUDIENCE, COMPLETE, GUEST_SPEAKER, PAGINATED, PARTIAL as ROSTER_PARTIAL, TEAM, RosterMeta

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
SOURCES = (SOURCE_SESSION, SOURCE_ROSTER, SOURCE_CHAT, SOURCE_QUESTIONS)

AUDIENCE_ONLY = "team, guest speakers"

# Version of the whole set: bump it whenever a measure's version changes or
# a measure is added or removed, and pin the new set (`registry_pin`) here
# and in tests/test_measures.py. The pin also covers each measure's unit,
# sources, exclusions and gates, so editing one without a new version fails
# the test. Stored numbers are keyed by it.
METRICS_VERSION = 1
REGISTRY_PIN = "3120ffec1fc37731bd6bfec4e7c863b8f98bfa2507e5617e04f4e0e75c617e07"

# Gate thresholds. Provisional: they move with a version bump.
WROTE_RATE_MIN_ATTENDEES = 10
TIME_TO_ANSWER_MIN_ANSWERED = 3
TIME_IN_ROOM_MIN_LENGTH_S = 1

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
      is how many values it is over; None for counts. `num` is None when
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
    `unresolved`); `quantities` are what its gates read besides `den`."""

    value: Optional[float] = None
    num: Optional[int] = None
    den: Optional[int] = None
    quantities: Mapping[str, Optional[float]] = dataclass_field(default_factory=dict)
    flags: FrozenSet[str] = frozenset()
    coverage: Mapping[str, Any] = dataclass_field(default_factory=dict)
    excluded: Optional[Mapping[str, Any]] = None
    livestorm: Optional[Mapping[str, Any]] = None
    rows: Optional[List[Mapping[str, Any]]] = None


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
    questions are there when they were fetched, even empty."""
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
    den = int(attendees.sum())
    coverage = facts.inputs.authors.coverage
    chat, questions = coverage[CHAT], coverage[QUESTION]
    unresolved = _source_unresolved(facts, CHAT) or _source_unresolved(facts, QUESTION)
    unresolved_items = (chat.items - chat.resolved_items) + (questions.items - questions.resolved_items)
    writers = facts.inputs.authors.contributors()
    num = int(facts.people.loc[attendees, "person_id"].isin(writers).sum())
    return _Draft(
        value=_ratio(num, den),
        num=num,
        den=den,
        flags=_flags(
            unresolved=unresolved,
            partial=facts.roster_partial or unresolved_items > 0 or facts.audience_attendance_unknown > 0,
        ),
        coverage={
            **facts.roster_coverage,
            "chatAuthors": chat.state,
            "questionAuthors": questions.state,
            "unresolvedItems": unresolved_items,
        },
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


def _finish(definition: MeasureDef, draft: _Draft) -> MeasureValue:
    """The state, first match wins (module docstring), the value hidden
    when the state calls for it, and the numerator and denominator hidden
    when unresolved (a count of the resolved part would read as complete)."""
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
        rows=draft.rows,
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
        if item["rows"] is not None and not isinstance(item["rows"], list):
            raise ValueError(f"{measure_id}: rows must be a list or None")
    try:
        json.dumps(measures, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError(f"measures aren't strict JSON: {error}") from error
