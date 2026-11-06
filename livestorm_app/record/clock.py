"""The session clock: one time base for every event of a session.

`t` is seconds since the session's `started_at`. Chat, questions and answers
are placed from their raw `created_at` and `responded_at` values. Speech is
placed through an alignment offset (`speech_events`): `t = r + offset_s`,
where `r` is seconds into the recording.

The rules (formulas in docs/product.md §13, "Session clock"):
- the session ends at `ended_at - started_at`, else after its `duration`;
- no `started_at` means no clock: `estimated_started_at` is never used as the
  start, because a late start would shift every event by the delay;
- an event before the start goes to the "before" edge, one after the end to
  the "after" edge, and one exactly at the end to the last minute.

Pure functions: no database, network or web framework.
"""

from __future__ import annotations

import math
import numbers
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, Mapping, Optional, Tuple

import pandas as pd

BEFORE = "before"
LIVE = "live"
AFTER = "after"

# Per-minute lanes. The event lanes count rows of `audience_events`; the
# speech lanes sum speaking seconds and words.
EVENT_LANES = ("audience_chat", "team_chat", "questions", "answers")
SPEECH_LANES = ("speaking_s", "words")
MINUTE_COLUMNS = ("minute",) + EVENT_LANES + SPEECH_LANES

_NUMERIC_STRING = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)$")
_MINUS = "−"  # the typographic minus, for labels before the start


# ── Reading raw times ───────────────────────────────────────────────────────


def parse_time(value: Any) -> Optional[float]:
    """Unix seconds from a raw payload time, or None.

    Accepts epoch seconds (an int, a float or a numeric string) and ISO 8601
    strings. An ISO string without a zone is read as UTC. Booleans, zero and
    negative values count as missing: the API uses 0 for "not set".
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, numbers.Real):
        seconds = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if _NUMERIC_STRING.match(text):
            seconds = float(text)
        else:
            try:
                moment = datetime.fromisoformat(text)
            except ValueError:
                return None
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            seconds = moment.timestamp()
    else:
        return None
    if not math.isfinite(seconds) or seconds <= 0:
        return None
    return seconds


def _length_s(value: Any) -> Optional[float]:
    """A positive length in seconds (the session's `duration`), or None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, numbers.Real):
        seconds = float(value)
    elif isinstance(value, str) and _NUMERIC_STRING.match(value.strip()):
        seconds = float(value.strip())
    else:
        return None
    return seconds if math.isfinite(seconds) and seconds > 0 else None


def _finite(value: Any) -> Optional[float]:
    """A finite float, or None for None, NaN, pd.NA and non-numbers."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


# ── The clock ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ClockBase:
    """Where a session's clock starts and ends.

    `t0_unix` is `started_at`; when it is None there is no session clock and
    nothing can be placed on it. `end_s` is a length, so it can still come
    from `duration` without a start.
    """

    t0_unix: Optional[float]
    end_s: Optional[float]
    end_source: str  # "ended_at" | "duration" | "none"
    planned_t0_unix: Optional[float]  # estimated_started_at, for the late start only
    late_start_s: Optional[float]  # started_at - estimated_started_at
    duration_attr_s: Optional[float]  # the payload's own `duration`, to compare with the end

    @property
    def has_clock(self) -> bool:
        return self.t0_unix is not None


def _session_attributes(session_payload: Any) -> Mapping[str, Any]:
    data = session_payload.get("data") if isinstance(session_payload, Mapping) else None
    attributes = data.get("attributes") if isinstance(data, Mapping) else None
    return attributes if isinstance(attributes, Mapping) else {}


def clock_base(session_payload: Any) -> ClockBase:
    """The clock of a cached `session_payload` (`data.attributes`)."""
    attributes = _session_attributes(session_payload)
    t0 = parse_time(attributes.get("started_at"))
    ended = parse_time(attributes.get("ended_at"))
    planned = parse_time(attributes.get("estimated_started_at"))
    duration = _length_s(attributes.get("duration"))

    if t0 is not None and ended is not None and ended > t0:
        end_s, end_source = ended - t0, "ended_at"
    elif duration is not None:
        end_s, end_source = duration, "duration"
    else:
        end_s, end_source = None, "none"

    return ClockBase(
        t0_unix=t0,
        end_s=end_s,
        end_source=end_source,
        planned_t0_unix=planned,
        late_start_s=(t0 - planned) if t0 is not None and planned is not None else None,
        duration_attr_s=duration,
    )


def to_session_s(value: Any, base: ClockBase) -> Optional[float]:
    """Seconds since `started_at` for a raw payload time, or None."""
    moment = parse_time(value)
    if moment is None or base.t0_unix is None:
        return None
    return moment - base.t0_unix


def phase(t: Any, base: ClockBase) -> Optional[str]:
    """The phase of `t`: before when t < 0, after when t > end_s, else live.

    None without a time, or without a session clock: then `t` means nothing.
    """
    value = _finite(t)
    if value is None or not base.has_clock:
        return None
    if value < 0:
        return BEFORE
    if base.end_s is not None and value > base.end_s:
        return AFTER
    return LIVE


def minute_count(base: ClockBase) -> Optional[int]:
    """How many live minutes the session has, or None when its end is unknown."""
    if base.end_s is None:
        return None
    return max(1, math.ceil(base.end_s / 60))


def minute_index(t: Any, base: ClockBase) -> Optional[int]:
    """The live minute of `t`: floor(t / 60). An event exactly at the end goes
    in the last minute. None when `t` is not live, or without a clock."""
    if phase(t, base) != LIVE:
        return None
    minute = int(math.floor(float(t) / 60))
    count = minute_count(base)
    return min(minute, count - 1) if count is not None else minute


def _mmss(seconds: int) -> str:
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def session_label(t: Any, base: ClockBase) -> str:
    """A display label: `12:30` into the session, `−02:10` before the start,
    `+01:05` after the end.

    Minutes keep counting past 59 ("125:30"). Before and after the session the
    distance is rounded up, so a label never reads "−00:00". Empty without a
    time or without a clock.
    """
    value = _finite(t)
    where = phase(value, base)
    if where is None:
        return ""
    if where == BEFORE:
        return _MINUS + _mmss(math.ceil(-value))
    if where == AFTER:
        return "+" + _mmss(math.ceil(value - base.end_s))
    return _mmss(math.floor(value))


# ── Speech ──────────────────────────────────────────────────────────────────

SPEECH_EVENT_COLUMNS = ("eid", "r_start", "r_end", "t_start", "t_end", "speaker_key", "n_words", "conf_mean")


def speech_events(utterances_df: Optional[pd.DataFrame], offset_s: Any) -> pd.DataFrame:
    """Utterances as speech events (`SPEECH_EVENT_COLUMNS`), one per row of
    `transcript.parse_utterances`, in its order: `eid` is the utterance's
    `U` id, and `t = r + offset_s` puts it on the session clock.

    `offset_s` comes from the alignment of the recording with the session;
    when it is None (not aligned), `t_start` and `t_end` are NaN and
    `minute_bins` places nothing."""
    columns = list(SPEECH_EVENT_COLUMNS)
    if not isinstance(utterances_df, pd.DataFrame) or utterances_df.empty:
        return pd.DataFrame(columns=columns).astype(
            {"r_start": "float64", "r_end": "float64", "t_start": "float64", "t_end": "float64", "conf_mean": "float64"}
        )
    offset = _finite(offset_s)
    events = pd.DataFrame(
        {
            "eid": utterances_df["uid"].to_numpy(),
            "r_start": utterances_df["r_start"].astype("float64").to_numpy(),
            "r_end": utterances_df["r_end"].astype("float64").to_numpy(),
        }
    )
    shift = math.nan if offset is None else offset
    events["t_start"] = events["r_start"] + shift
    events["t_end"] = events["r_end"] + shift
    events["speaker_key"] = utterances_df["speaker_key"].to_numpy()
    events["n_words"] = utterances_df["n_words"].astype("int64").to_numpy()
    events["conf_mean"] = utterances_df["conf_mean"].astype("float64").to_numpy()
    return events[columns]


# ── Minute bins ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MinuteSeries:
    """Per-minute activity on the session clock.

    - `minutes`: one row per live minute, 0 to the last, zero-filled, with the
      columns in `MINUTE_COLUMNS`.
    - `edges`: `{"before": {"counts", "first_t"}, "after": {"counts", "last_t"}}`,
      the waiting room and anything after the end, kept out of the minutes.
    - `unplaced`: audience events with no session time (no clock, or a time
      that could not be read).
    """

    minutes: pd.DataFrame
    edges: Dict[str, Dict[str, Any]]
    unplaced: int


def _empty_lanes() -> Dict[str, float]:
    return {lane: 0 for lane in EVENT_LANES + SPEECH_LANES}


def _flag(value: Any) -> bool:
    return value is True or str(value).strip().lower() == "true"


def _event_lane(kind: Any, is_team: Any) -> Optional[str]:
    if kind == "chat":
        return "team_chat" if _flag(is_team) else "audience_chat"
    if kind == "question":
        return "questions"
    if kind == "answer":
        return "answers"
    return None


def _speech_pieces(start: float, end: float, base: ClockBase) -> Iterator[Tuple[str, Optional[int], float]]:
    """Split a speech span into (phase, minute, seconds) pieces at the start,
    the end and every minute border. A span with no length is one piece of 0 s."""
    if end <= start:
        where = phase(start, base)
        yield where, minute_index(start, base), 0.0
        return
    if start < 0:
        yield BEFORE, None, min(end, 0.0) - start
    live_end = end if base.end_s is None else min(end, base.end_s)
    live_start = max(start, 0.0)
    if live_start < live_end:
        count = minute_count(base)
        for minute in range(int(live_start // 60), int(live_end // 60) + 1):
            seconds = min(live_end, (minute + 1) * 60) - max(live_start, minute * 60)
            if seconds > 0:
                yield LIVE, min(minute, count - 1) if count is not None else minute, seconds
    if base.end_s is not None and end > base.end_s:
        yield AFTER, None, end - max(start, base.end_s)


def minute_bins(
    audience_df: Optional[pd.DataFrame],
    speech_df: Optional[pd.DataFrame] = None,
    *,
    base: ClockBase,
) -> MinuteSeries:
    """Bin audience events, and speech when given, into session minutes.

    `audience_df` is `evidence.audience_events(...)` (columns `kind`,
    `is_team`, `t`). `speech_df`, when given, has `t_start` and `t_end` on the
    session clock and optionally `n_words`. Speaking seconds are split across
    minute borders, and words in proportion to the seconds in each piece.

    Without a session clock there are no minutes, and every audience event is
    unplaced. When the end is unknown, minutes run to the last live event.
    """
    edge_lanes = {BEFORE: _empty_lanes(), AFTER: _empty_lanes()}
    live: Dict[int, Dict[str, float]] = {}
    first_before: Optional[float] = None
    last_after: Optional[float] = None
    unplaced = 0

    if isinstance(audience_df, pd.DataFrame) and not audience_df.empty:
        for kind, is_team, t in audience_df[["kind", "is_team", "t"]].itertuples(index=False, name=None):
            lane = _event_lane(kind, is_team)
            if lane is None:
                continue
            value = _finite(t) if base.has_clock else None
            if value is None:
                unplaced += 1
                continue
            where = phase(value, base)
            if where == BEFORE:
                edge_lanes[BEFORE][lane] += 1
                first_before = value if first_before is None else min(first_before, value)
            elif where == AFTER:
                edge_lanes[AFTER][lane] += 1
                last_after = value if last_after is None else max(last_after, value)
            else:
                live.setdefault(minute_index(value, base), _empty_lanes())[lane] += 1

    if base.has_clock and isinstance(speech_df, pd.DataFrame) and not speech_df.empty:
        words_column = speech_df["n_words"] if "n_words" in speech_df.columns else pd.Series(0, index=speech_df.index)
        for t_start, t_end, n_words in zip(speech_df["t_start"], speech_df["t_end"], words_column, strict=True):
            start = _finite(t_start)
            if start is None:
                continue
            end = _finite(t_end)
            end = start if end is None or end < start else end
            words = _finite(n_words) or 0.0
            length = end - start
            for where, minute, seconds in _speech_pieces(start, end, base):
                share = seconds / length if length > 0 else 1.0
                target = live.setdefault(minute, _empty_lanes()) if where == LIVE else edge_lanes[where]
                target["speaking_s"] += seconds
                target["words"] += words * share
                if where == BEFORE:
                    first_before = start if first_before is None else min(first_before, start)
                elif where == AFTER:
                    last_after = end if last_after is None else max(last_after, end)

    if not base.has_clock:
        count = 0
    else:
        count = minute_count(base)
        if count is None:
            count = max(live) + 1 if live else 0

    rows = [_lane_values(live.get(minute) or _empty_lanes(), minute=minute) for minute in range(count)]
    minutes = pd.DataFrame(rows, columns=list(MINUTE_COLUMNS))
    minutes = minutes.astype({column: "int64" for column in ("minute",) + EVENT_LANES})
    minutes = minutes.astype({column: "float64" for column in SPEECH_LANES})

    return MinuteSeries(
        minutes=minutes,
        edges={
            BEFORE: {"counts": _lane_values(edge_lanes[BEFORE]), "first_t": first_before},
            AFTER: {"counts": _lane_values(edge_lanes[AFTER]), "last_t": last_after},
        },
        unplaced=unplaced,
    )


def _lane_values(lanes: Mapping[str, float], **extra: Any) -> Dict[str, Any]:
    values: Dict[str, Any] = dict(extra)
    for lane in EVENT_LANES:
        values[lane] = int(lanes[lane])
    values["speaking_s"] = round(float(lanes["speaking_s"]), 2)
    values["words"] = round(float(lanes["words"]), 1)
    return values
