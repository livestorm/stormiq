"""The v1 record as `GET /api/sessions/{id}/record` serves it.

`assemble_record(inputs)` turns one session's `RecordInputs` into the
computed part of the response:

- `meta.clock`: the session clock (`record/clock.py`) and where the
  recording sits on it (`alignment`, `record/alignment.py`);
- `record.roster`: the roster's header, counts only. The caller adds the
  people sync's live state to it on every request
  (`people_sync.roster_live_state`: `sync` and `refreshDue`), outside the
  cached block;
- `record.pulse`: chat, questions and written answers per session minute,
  plus the before and after edges (`clock.minute_bins`), and speech
  (speaking seconds and words) when the alignment allows findings in
  minutes (`alignment.alignment_allows`);
- `record.transcriptHealth`: how far the transcript can be trusted
  (`record/transcript_health.py`, built once by `inputs.load_record_inputs`);
- `record.metricsVersion`, `record.measuresSource` and `record.measures`:
  every measure of the registry (`record/measures.py`), through
  `measures_for_read`.

`chapter_marks(inputs, chapters)` is not part of the record: it places the
session analysis's chapters on the session clock, with the audience's chat
and questions in each, for the Report's cover (`GET /record/chapters`).

The caller (`api_logic.get_session_record`) does the scoped read, caches
this block and adds `sessionId` and `updatedAt`. Bump `RECORD_VERSION`
whenever the block's shape or rules change, or what the caller adds to
`record`: it is part of that cache's key and of the response
(`record.version`).

Only counts and session-level facts leave here: no name, email, company,
person or message id, or message or transcript text (the caller adds the
session's own id). The transcript health and the alignment carry our own
ids (`U0001`, `C0001`, `Q001`) and times, never a word or a speaker label.
A page gets people only through `roster.display_people`, and the header
carries none.

Pure functions: no database, network or web framework.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence

from livestorm_app.record.alignment import MINUTES, alignment_allows
from livestorm_app.record.clock import AFTER, BEFORE, LIVE, MinuteSeries, event_lane, minute_bins, phase, speech_events
from livestorm_app.record.inputs import RecordInputs
from livestorm_app.record.measures import METRICS_VERSION, compute_measures, validate_measures
from livestorm_app.record.roster import Roster

RECORD_VERSION = 10

# Where the measures come from: computed from the cached payloads when the
# block is built (the caller caches the block per process; nothing is
# stored). Stored measures will say so when they exist.
MEASURES_LIVE = "live"

# The Pulse's lanes, in display order: `clock.EVENT_LANES` and their JSON
# names, then the speech lanes (`clock.SPEECH_LANES`), which join only when
# the alignment allows findings in minutes.
PULSE_LANES = (
    ("audience_chat", "audienceChat"),
    ("team_chat", "teamChat"),
    ("questions", "questions"),
    ("answers", "answers"),
)
SPEECH_PULSE_LANES = (
    ("speaking_s", "speakingS"),
    ("words", "words"),
)


def _number(value: Any) -> Optional[float]:
    """A JSON number: an int when whole, else rounded to milliseconds. None
    for None, NaN and infinities."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else round(number, 3)


def _session_attributes(session_payload: Any) -> Mapping[str, Any]:
    data = session_payload.get("data") if isinstance(session_payload, Mapping) else None
    attributes = data.get("attributes") if isinstance(data, Mapping) else None
    return attributes if isinstance(attributes, Mapping) else {}


def clock_meta(inputs: RecordInputs) -> Dict[str, Any]:
    """`meta.clock`. `startedAtUnix` is `started_at` only, never
    `estimated_started_at`: without it there is no clock. `alignment` is the
    probe's result (`Alignment.to_json`): ids and times only."""
    base = inputs.clock
    zone = _session_attributes(inputs.session_payload).get("timezone")
    return {
        "startedAtUnix": _number(base.t0_unix),
        "endS": _number(base.end_s),
        "endSource": base.end_source,
        "lateStartS": _number(base.late_start_s),
        "timezone": (zone.strip() or None) if isinstance(zone, str) else None,
        "alignment": inputs.alignment.to_json(),
    }


def session_meta(inputs: RecordInputs) -> Dict[str, Any]:
    """`meta.session`: what the page header names the session by. `title`
    is the event's title (`event_payload.data.attributes.title`), or None
    when the event wasn't fetched or has none."""
    data = inputs.event_payload.get("data") if isinstance(inputs.event_payload, Mapping) else None
    attributes = data.get("attributes") if isinstance(data, Mapping) else None
    title = attributes.get("title") if isinstance(attributes, Mapping) else None
    return {"title": title.strip() or None if isinstance(title, str) else None}


def roster_header(roster: Roster) -> Dict[str, Any]:
    """`record.roster`: where the people come from and how complete they
    are, and whether the session is internal (`RosterMeta.internal`, the
    classic `audience.is_internal_session` rule). No person in it."""
    meta = roster.meta
    return {
        "source": meta.source,
        "complete": meta.complete,
        "coverage": meta.coverage,
        "peopleCount": meta.people_count,
        "recordCount": meta.record_count,
        "syncedAt": meta.synced_at,
        "internal": meta.internal,
    }


def _lane_values(values: Mapping[str, Any], speech: bool) -> Dict[str, Any]:
    counts: Dict[str, Any] = {name: int(values[lane]) for lane, name in PULSE_LANES}
    if speech:
        counts.update({name: _number(values[lane]) for lane, name in SPEECH_PULSE_LANES})
    return counts


def speech_state(inputs: RecordInputs) -> Dict[str, Any]:
    """`record.pulse.speech`: whether the speech lanes are shown, the offset
    they were placed with (`t = r + offsetS`), and otherwise why not (the
    alignment's reasons). Shown only when there is a transcript and the
    alignment allows findings in minutes (verified or assumed)."""
    alignment = inputs.alignment
    shown = inputs.transcript is not None and alignment_allows(alignment, MINUTES)
    return {
        "shown": shown,
        "offsetS": _number(alignment.offset_s) if shown else None,
        "reasons": [] if shown else list(alignment.reasons),
    }


def pulse(series: MinuteSeries, speech: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """`record.pulse`: one row per live minute (zero-filled) with a count per
    lane, the before and after edges, how many events have no session time
    (`unplaced`), and the speech lanes' state (`speech_state`). Speaking
    seconds and words join each row and edge only when they are shown."""
    speech = speech or {"shown": False, "offsetS": None, "reasons": []}
    shown = bool(speech["shown"])
    lanes = PULSE_LANES + (SPEECH_PULSE_LANES if shown else ())
    rows = series.minutes[["minute"] + [lane for lane, _ in lanes]].to_dict("records")
    before = series.edges[BEFORE]
    after = series.edges[AFTER]
    return {
        "lanes": [name for _, name in lanes],
        "minutes": [{"minute": int(row["minute"]), **_lane_values(row, shown)} for row in rows],
        "edges": {
            "before": {"counts": _lane_values(before["counts"], shown), "firstT": _number(before["first_t"])},
            "after": {"counts": _lane_values(after["counts"], shown), "lastT": _number(after["last_t"])},
        },
        "unplaced": int(series.unplaced),
        "speech": dict(speech),
    }


def measures_for_read(inputs: RecordInputs) -> Dict[str, Any]:
    """`record.metricsVersion`, `record.measuresSource` and `record.measures`
    (id → `MeasureValue.to_json`, in registry order).

    Live only: every measure is computed from `inputs` and checked with
    `measures.validate_measures`, and nothing is written. An invalid set
    raises, so the route answers 500 rather than serve it."""
    measures = {measure_id: value.to_json() for measure_id, value in compute_measures(inputs).items()}
    validate_measures(measures)
    return {"metricsVersion": METRICS_VERSION, "measuresSource": MEASURES_LIVE, "measures": measures}


def pulse_events(inputs: RecordInputs) -> Any:
    """The events the Pulse bins. On an internal session everyone is
    audience, as the measures count it (`measures._Facts.audience`), so every
    message goes in the audience's lanes (`audienceChat`), never in
    `teamChat`: the ridge and the Pulse then show the room's chat."""
    events = inputs.events
    if inputs.roster.meta.internal and events is not None and not events.empty and "is_team" in events:
        return events.assign(is_team=False)
    return events


def assemble_record(inputs: RecordInputs) -> Dict[str, Any]:
    """The computed block of the record response: `{meta: {clock}, record}`.
    The clock, the roster, the authors, the events and the alignment come
    from `inputs`; the events and speech are binned once here, and the
    measures computed once."""
    speech = speech_state(inputs)
    speech_df = speech_events(inputs.transcript.utterances, inputs.alignment.offset_s) if speech["shown"] else None
    return {
        "meta": {"clock": clock_meta(inputs), "session": session_meta(inputs)},
        "record": {
            "version": RECORD_VERSION,
            "roster": roster_header(inputs.roster),
            "pulse": pulse(minute_bins(pulse_events(inputs), speech_df, base=inputs.clock), speech),
            "transcriptHealth": inputs.transcript_health,
            "chatLabels": dict(inputs.chat_labels_info),
            **measures_for_read(inputs),
        },
    }


# A chapter shorter than this has no chat rate on the Report's cover.
CHAPTER_RATE_MIN_S = 120.0


def chapter_marks(inputs: RecordInputs, chapters: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """The chapters on the session clock, for the Report's cover:
    `{placed, marks: [{t, tEnd, audienceChat, questions, chatPerMinute}]}`,
    one mark per chapter, in the chapters' order (`understanding.chapter_list`).

    A chapter is placed (`t = r + offsetS`) only where the Pulse's speech is
    (`speech_state`: a transcript, and an alignment that allows findings in
    minutes); otherwise `placed` is false and there are no marks. A chapter
    owns the live minutes from its start to the next chapter's start, the
    last one to the session's end (or its own end when the end is unknown or
    earlier). `audienceChat` and `questions` count the live events in that
    span (`pulse_events`: on an internal session, everyone's chat), and
    `chatPerMinute` is `audienceChat` over the span's minutes, to 0.1 (None
    for a span shorter than `CHAPTER_RATE_MIN_S`: a few messages in a short
    welcome would outrank the whole talk). `t` and `tEnd` are the chapter's own
    spoken span, clamped to the live session."""
    speech = speech_state(inputs)
    base = inputs.clock
    if not speech["shown"] or not chapters or not base.has_clock:
        return {"placed": False, "marks": []}
    offset = float(inputs.alignment.offset_s or 0.0)
    end = base.end_s

    def clamp(value: float) -> float:
        value = max(value, 0.0)
        return min(value, end) if end is not None else value

    starts = [clamp(float(c["r"]) + offset) for c in chapters]
    ends = [clamp(float(c["rEnd"]) + offset) for c in chapters]
    owned_end = starts[1:] + [max(end, ends[-1]) if end is not None else ends[-1]]
    counts = [{"audience_chat": 0, "questions": 0} for _ in chapters]
    events = pulse_events(inputs)
    if events is not None and not events.empty:
        for kind, is_team, t in events[["kind", "is_team", "t"]].itertuples(index=False, name=None):
            lane = event_lane(kind, is_team)
            if lane not in ("audience_chat", "questions") or phase(t, base) != LIVE:
                continue
            value = float(t)
            for index, start in enumerate(starts):
                last = index == len(starts) - 1
                if start <= value and (value < owned_end[index] or (last and value <= owned_end[index])):
                    counts[index][lane] += 1
                    break
    marks: List[Dict[str, Any]] = []
    for index in range(len(chapters)):
        span = owned_end[index] - starts[index]
        chat = counts[index]["audience_chat"]
        marks.append(
            {
                "t": _number(starts[index]),
                "tEnd": _number(max(ends[index], starts[index])),
                "audienceChat": chat,
                "questions": counts[index]["questions"],
                "chatPerMinute": round(chat * 60.0 / span, 1) if span >= CHAPTER_RATE_MIN_S else None,
            }
        )
    return {"placed": True, "marks": marks}
