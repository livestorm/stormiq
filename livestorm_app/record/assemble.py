"""The v1 record as `GET /api/sessions/{id}/record` serves it.

`assemble_record(inputs)` turns one session's `RecordInputs` into the
computed part of the response:

- `meta.clock`: the session clock (`record/clock.py`);
- `record.roster`: the roster's header, counts only;
- `record.pulse`: chat, questions and written answers per session minute,
  plus the before and after edges (`clock.minute_bins`);
- `record.metricsVersion`, `record.measuresSource` and `record.measures`:
  every measure of the registry (`record/measures.py`), through
  `measures_for_read`.

The caller (`api_logic.get_session_record`) does the scoped read, caches
this block and adds `sessionId` and `updatedAt`. Bump `RECORD_VERSION`
whenever the block's shape or rules change: it is part of that cache's key
and of the response (`record.version`).

Only counts and session-level facts leave here: no name, email, company,
person or message id, or message text (the caller adds the session's own
id). A page gets people only through `roster.display_people`, and the
header carries none.

Pure functions: no database, network or web framework.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional

from livestorm_app.record.clock import AFTER, BEFORE, MinuteSeries, minute_bins
from livestorm_app.record.evidence import audience_events
from livestorm_app.record.inputs import RecordInputs
from livestorm_app.record.measures import METRICS_VERSION, compute_measures, validate_measures
from livestorm_app.record.roster import Roster

RECORD_VERSION = 3

# Where the measures come from: computed from the cached payloads when the
# block is built (the caller caches the block per process; nothing is
# stored). Stored measures will say so when they exist.
MEASURES_LIVE = "live"

# The Pulse's lanes, in display order: `clock.EVENT_LANES` and their JSON
# names. The speech lanes join when the recording is aligned with the clock.
PULSE_LANES = (
    ("audience_chat", "audienceChat"),
    ("team_chat", "teamChat"),
    ("questions", "questions"),
    ("answers", "answers"),
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
    `estimated_started_at`: without it there is no clock. `alignment` stays
    None until the recording is aligned with the session clock."""
    base = inputs.clock
    zone = _session_attributes(inputs.session_payload).get("timezone")
    return {
        "startedAtUnix": _number(base.t0_unix),
        "endS": _number(base.end_s),
        "endSource": base.end_source,
        "lateStartS": _number(base.late_start_s),
        "timezone": (zone.strip() or None) if isinstance(zone, str) else None,
        "alignment": None,
    }


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


def _lane_counts(values: Mapping[str, Any]) -> Dict[str, int]:
    return {name: int(values[lane]) for lane, name in PULSE_LANES}


def pulse(series: MinuteSeries) -> Dict[str, Any]:
    """`record.pulse`: one row per live minute (zero-filled) with a count per
    lane, the before and after edges, and how many events have no session
    time (`unplaced`)."""
    rows = series.minutes[["minute"] + [lane for lane, _ in PULSE_LANES]].to_dict("records")
    before = series.edges[BEFORE]
    after = series.edges[AFTER]
    return {
        "lanes": [name for _, name in PULSE_LANES],
        "minutes": [{"minute": int(row["minute"]), **_lane_counts(row)} for row in rows],
        "edges": {
            "before": {"counts": _lane_counts(before["counts"]), "firstT": _number(before["first_t"])},
            "after": {"counts": _lane_counts(after["counts"]), "lastT": _number(after["last_t"])},
        },
        "unplaced": int(series.unplaced),
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


def assemble_record(inputs: RecordInputs) -> Dict[str, Any]:
    """The computed block of the record response: `{meta: {clock}, record}`.
    The clock, the roster and the authors come from `inputs`; the events are
    placed and binned once here, and the measures computed once."""
    events = audience_events(inputs.chat_payload, inputs.questions_payload, inputs.clock)
    return {
        "meta": {"clock": clock_meta(inputs)},
        "record": {
            "version": RECORD_VERSION,
            "roster": roster_header(inputs.roster),
            "pulse": pulse(minute_bins(events, base=inputs.clock)),
            **measures_for_read(inputs),
        },
    }
