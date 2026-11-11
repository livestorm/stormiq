"""The Report's findings: what stood out in one session, found by code.

Each detector looks at the session against its own baseline and answers in
one of three states:

- `found`: something stood out; the finding carries its values, never a
  sentence (the page words it, `frontend/src/report/findings.js`);
- `nothing`: it looked and nothing stood out (the "Checked, nothing
  unusual" list);
- `not_checked`: it couldn't look, with the reason (`reason`).

Findings are ranked by `strength` (0 to 1, the detector's own scale of how
far past its threshold it is), strongest first.

The detectors of this version:

- **chapters** (#7, what landed chapter by chapter): each chapter's
  unprompted audience chat per minute as an index, the session's median
  chapter at 100. Unprompted leaves out greetings, thanks and logistics
  (the chat labels' `social` and `logistics`) and any message within
  `REPLY_WINDOW_S` after a speaker's prompt to the room (the stored
  understanding's prompts). A Q&A chapter shows its questions instead of an
  index (it would always win). It needs the chapters on the session clock
  (`assemble.chapter_marks`), the chat labelled, a session of
  `CHAPTERS_MIN_SESSION_S` or more, `CHAPTERS_MIN_MESSAGES` unprompted
  messages and two chapters with an index. It fires when a chapter reaches
  `INDEX_HIGH` or falls to `INDEX_LOW`.
- **friction** (#10, production friction): an incident is
  `INCIDENT_MIN_MESSAGES` or more audience messages labelled `tech`, each
  within `INCIDENT_GAP_S` of the last, with the dead air (gaps of
  `DEAD_AIR_MIN_S` or more between utterances, on the session clock) from a
  minute before it to a minute after. A late start (`clock.late_start_s`)
  of `LATE_START_MIN_S` or more is reported with the audience's messages
  in the waiting room. Without chat labels, only the late start is looked
  at.
- **monologue** (#6, monologue stretches): the longest stretch of one
  speaker's utterances in a row, broken by a change of speaker, a prompt to
  the room or an utterance that answers an audience question (the stored
  understanding's answers). When it lasts `MONOLOGUE_MIN_S` or more, the
  unprompted chat rate of its first third is compared with its last third;
  it fires when the last is `MONOLOGUE_DROP` or less of the first, from at
  least `MONOLOGUE_MIN_FIRST` messages in the first third. It needs speech
  on the session clock (minutes) and the chat labelled.
- **prompts** (#4, did the room have time to answer): for each prompt to
  the room, the speaker's wait (the prompt's end to the next utterance's
  start), the audience's messages in the `PROMPT_WINDOW_S` after it, the
  number the session's live rate would give anyway (`expected`), and how
  many came after the speaker spoke again. Replies aren't marked one by
  one: the excess over the expected number stands for them, and the page
  says so. It fires when a prompt drew `PROMPT_MIN_EXCESS` or more messages
  over the expected, most of them (`PROMPT_LATE_SHARE`) after a wait under
  `PROMPT_SHORT_WAIT_S`. Measured in seconds, so it needs a verified clock.

Never a name or a message's text: counts, times and our own ids.

Pure: no database, network or model call.
"""

from __future__ import annotations

import math
import statistics
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from livestorm_app.record.alignment import MINUTES, SECONDS, alignment_allows
from livestorm_app.record.assemble import chapter_marks, pulse_events
from livestorm_app.record.clock import LIVE, event_lane, phase

FINDINGS_VERSION = 1

FOUND = "found"
NOTHING = "nothing"
NOT_CHECKED = "not_checked"

# What isn't unprompted interest: greetings, thanks, logistics.
NOT_UNPROMPTED = ("social", "logistics")
REPLY_WINDOW_S = 60.0

CHAPTERS_MIN_SESSION_S = 600.0
CHAPTERS_MIN_MESSAGES = 20
CHAPTER_INDEX_MIN_S = 120.0
INDEX_HIGH = 150
INDEX_LOW = 50

INCIDENT_MIN_MESSAGES = 3
INCIDENT_GAP_S = 120.0
DEAD_AIR_MIN_S = 5.0
DEAD_AIR_MARGIN_S = 60.0
LATE_START_MIN_S = 180.0

MONOLOGUE_MIN_S = 480.0
MONOLOGUE_DROP = 0.5
MONOLOGUE_MIN_FIRST = 6

PROMPT_WINDOW_S = 120.0
PROMPT_MIN_EXCESS = 3
PROMPT_LATE_SHARE = 0.5
PROMPT_SHORT_WAIT_S = 10.0


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _labels(inputs: Any) -> Optional[Mapping[str, Mapping[str, Any]]]:
    return inputs.chat_labels if isinstance(inputs.chat_labels, Mapping) else None


def _offset(inputs: Any) -> Optional[float]:
    """The offset that puts speech on the session clock, when the alignment
    allows findings in minutes (as the Pulse's speech lanes)."""
    if inputs.transcript is None or not alignment_allows(inputs.alignment, MINUTES):
        return None
    return _finite(inputs.alignment.offset_s) or 0.0


def _speech_spans(inputs: Any, offset: Optional[float]) -> List[Tuple[str, float, float]]:
    """`(utterance id, t_start, t_end)` on the session clock, in order."""
    if offset is None:
        return []
    frame = inputs.transcript.utterances
    spans = []
    for uid, start, end in frame[["uid", "r_start", "r_end"]].itertuples(index=False, name=None):
        start, end = _finite(start), _finite(end)
        if start is not None and end is not None:
            spans.append((str(uid), start + offset, max(end, start) + offset))
    return sorted(spans, key=lambda span: span[1])


def _audience(inputs: Any) -> List[Dict[str, Any]]:
    """The live audience chat and questions: `{eid, lane, t, author}`."""
    events = pulse_events(inputs)
    rows: List[Dict[str, Any]] = []
    if events is None or events.empty:
        return rows
    for eid, kind, is_team, t, author in events[["eid", "kind", "is_team", "t", "author_ref"]].itertuples(index=False, name=None):
        lane = event_lane(kind, is_team)
        if lane in ("audience_chat", "questions") and phase(t, inputs.clock) == LIVE:
            rows.append({"eid": str(eid), "lane": lane, "t": float(t), "author": author})
    return rows


# ── #7: chapter by chapter ──


def chapter_index(rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """`rows`: `{seconds, unprompted, qa}` per chapter. Adds `perMinute` and
    `index` (100 = the median of the chapters that have a rate): none for a
    Q&A chapter or a span under `CHAPTER_INDEX_MIN_S`."""
    rates = [
        row["unprompted"] * 60.0 / row["seconds"]
        for row in rows
        if not row["qa"] and row["seconds"] >= CHAPTER_INDEX_MIN_S
    ]
    median = statistics.median(rates) if rates else 0.0
    out = []
    for row in rows:
        rated = not row["qa"] and row["seconds"] >= CHAPTER_INDEX_MIN_S
        per_minute = row["unprompted"] * 60.0 / row["seconds"] if rated else None
        index = round(per_minute / median * 100) if per_minute is not None and median > 0 else None
        out.append({**row, "perMinute": round(per_minute, 1) if per_minute is not None else None, "index": index})
    return out


def detect_chapters(inputs: Any, chapters: Sequence[Mapping[str, Any]], prompts: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    base = {"id": "chapters", "confidence": "estimated"}
    labels = _labels(inputs)
    marks = chapter_marks(inputs, chapters)
    if not chapters:
        return {**base, "state": NOT_CHECKED, "reason": "no_chapters"}
    if not marks["placed"]:
        return {**base, "state": NOT_CHECKED, "reason": "clock"}
    if labels is None:
        return {**base, "state": NOT_CHECKED, "reason": "labels"}
    end = _finite(inputs.clock.end_s)
    if end is None or end < CHAPTERS_MIN_SESSION_S:
        return {**base, "state": NOT_CHECKED, "reason": "short"}

    offset = _offset(inputs) or 0.0
    ends = {uid: t_end for uid, _, t_end in _speech_spans(inputs, offset)}
    prompt_ends = sorted(ends.get(str(p.get("utterance")), _finite(p.get("t")) or -1e9) for p in prompts)

    def replies(t: float) -> bool:
        return any(0.0 <= t - stop <= REPLY_WINDOW_S for stop in prompt_ends)

    audience = _audience(inputs)
    starts = [m["t"] for m in marks["marks"]]
    owned = starts[1:] + [max(end, marks["marks"][-1]["tEnd"])]
    rows = []
    for index, chapter in enumerate(chapters):
        lo, hi = starts[index], owned[index]
        inside = [e for e in audience if lo <= e["t"] < hi or (index == len(chapters) - 1 and e["t"] == hi)]
        unprompted = [
            e
            for e in inside
            if e["lane"] == "audience_chat"
            and str((labels.get(e["eid"]) or {}).get("class")) not in NOT_UNPROMPTED
            and not replies(e["t"])
        ]
        questions = sum(1 for e in inside if e["lane"] == "questions") + sum(
            1 for e in inside if e["lane"] == "audience_chat" and (labels.get(e["eid"]) or {}).get("class") == "question"
        )
        rows.append(
            {
                "title": chapter["title"],
                "t": lo,
                "seconds": max(hi - lo, 0.0),
                "qa": chapter.get("kind") == "qa",
                "unprompted": len(unprompted),
                "chatters": len({e["author"] for e in unprompted if e["author"]}),
                "questions": questions,
            }
        )
    total = sum(row["unprompted"] for row in rows)
    indexed = chapter_index(rows)
    rated = [row for row in indexed if row["index"] is not None]
    values = {"chapters": [{**row, "t": round(row["t"], 1), "seconds": round(row["seconds"], 1)} for row in indexed]}
    if total < CHAPTERS_MIN_MESSAGES:
        return {**base, "state": NOT_CHECKED, "reason": "few_messages", "values": {"messages": total}}
    if len(rated) < 2:
        return {**base, "state": NOT_CHECKED, "reason": "few_chapters"}
    high = max(rated, key=lambda row: row["index"])
    low = min(rated, key=lambda row: row["index"])
    up = high["index"] >= INDEX_HIGH
    down = low["index"] <= INDEX_LOW
    if not up and not down:
        return {**base, "state": NOTHING, "values": values}
    strength = max(
        min((high["index"] - 100) / 200, 1.0) if up else 0.0,
        min((100 - low["index"]) / 100, 1.0) if down else 0.0,
    )
    return {
        **base,
        "state": FOUND,
        "strength": round(strength, 2),
        "values": {**values, "high": high["title"] if up else None, "low": low["title"] if down else None},
    }


# ── #10: production friction ──


def incidents(tech: Sequence[Mapping[str, Any]], dead_air: Sequence[Tuple[float, float]]) -> List[Dict[str, Any]]:
    """`tech`: the audience's tech messages, `{t, author}`. `dead_air`:
    `(start, end)` gaps in the speech. Returns `{start, end, messages,
    people, deadAirS}` per incident, in time order."""
    ordered = sorted(tech, key=lambda m: m["t"])
    groups: List[List[Mapping[str, Any]]] = []
    for message in ordered:
        if groups and message["t"] - groups[-1][-1]["t"] <= INCIDENT_GAP_S:
            groups[-1].append(message)
        else:
            groups.append([message])
    out = []
    for group in groups:
        if len(group) < INCIDENT_MIN_MESSAGES:
            continue
        start, end = group[0]["t"], group[-1]["t"]
        lo, hi = start - DEAD_AIR_MARGIN_S, end + DEAD_AIR_MARGIN_S
        silent = sum(max(0.0, min(b, hi) - max(a, lo)) for a, b in dead_air)
        out.append(
            {
                "start": round(start, 1),
                "end": round(end, 1),
                "messages": len(group),
                "people": len({m["author"] for m in group if m.get("author")}),
                "deadAirS": round(silent, 1),
            }
        )
    return out


def dead_air(spans: Sequence[Tuple[str, float, float]]) -> List[Tuple[float, float]]:
    """Gaps of `DEAD_AIR_MIN_S` or more between consecutive utterances (never
    before the first word)."""
    gaps = []
    reach = None
    for _, start, end in spans:
        if reach is not None and start - reach >= DEAD_AIR_MIN_S:
            gaps.append((reach, start))
        reach = end if reach is None else max(reach, end)
    return gaps


def detect_friction(inputs: Any) -> Dict[str, Any]:
    base = {"id": "friction"}
    labels = _labels(inputs)
    late = _finite(inputs.clock.late_start_s)
    waiting = 0
    events = pulse_events(inputs)
    if events is not None and not events.empty:
        for kind, is_team, t in events[["kind", "is_team", "t"]].itertuples(index=False, name=None):
            if event_lane(kind, is_team) == "audience_chat" and _finite(t) is not None and float(t) < 0:
                waiting += 1
    late_start = {"lateS": round(late, 1), "waitingMessages": waiting} if late is not None and late >= LATE_START_MIN_S else None
    found: List[Dict[str, Any]] = []
    if labels is not None:
        tech = [
            {"t": e["t"], "author": e["author"]}
            for e in _audience(inputs)
            if e["lane"] == "audience_chat" and (labels.get(e["eid"]) or {}).get("class") == "tech"
        ]
        found = incidents(tech, dead_air(_speech_spans(inputs, _offset(inputs))))
    values = {"incidents": found, "lateStart": late_start, "speech": _offset(inputs) is not None}
    if not found and not late_start:
        if labels is None:
            return {**base, "state": NOT_CHECKED, "reason": "labels", "confidence": "measured"}
        return {**base, "state": NOTHING, "confidence": "estimated", "values": values}
    strength = 0.0
    if found:
        strength = max(strength, min(0.4 + 0.1 * max(i["messages"] for i in found), 1.0))
    if late_start:
        strength = max(strength, min(late_start["lateS"] / 900.0, 1.0))
    return {
        **base,
        "state": FOUND,
        "confidence": "estimated" if found else "measured",
        "strength": round(strength, 2),
        "values": values,
    }


# ── #6: monologue stretches ──


def longest_stretch(
    utterances: Sequence[Tuple[str, str, float, float]], breaks: Sequence[str]
) -> Optional[Dict[str, Any]]:
    """`utterances`: `(id, speaker, t_start, t_end)` in order. The longest run
    of one speaker's utterances, a break (an id in `breaks`) ending the run
    it is in, the next utterance starting a new one:
    `{start, end, speaker}`, or None without utterances."""
    stop = set(breaks)
    best = None
    run = None
    for uid, speaker, start, end in utterances:
        if run is None or speaker != run["speaker"]:
            run = {"start": start, "end": end, "speaker": speaker}
        else:
            run["end"] = max(run["end"], end)
        if best is None or run["end"] - run["start"] > best["end"] - best["start"]:
            best = dict(run)
        if uid in stop:
            run = None
    return best


def _unprompted_times(inputs: Any, labels: Mapping[str, Mapping[str, Any]]) -> List[float]:
    return [
        e["t"]
        for e in _audience(inputs)
        if e["lane"] == "audience_chat" and str((labels.get(e["eid"]) or {}).get("class")) not in NOT_UNPROMPTED
    ]


def detect_monologue(inputs: Any, prompts: Sequence[Mapping[str, Any]], answers: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    base = {"id": "monologue", "confidence": "estimated"}
    offset = _offset(inputs)
    labels = _labels(inputs)
    if offset is None:
        return {**base, "state": NOT_CHECKED, "reason": "clock"}
    if labels is None:
        return {**base, "state": NOT_CHECKED, "reason": "labels"}
    frame = inputs.transcript.utterances
    rows = []
    for uid, speaker, start, end in frame[["uid", "speaker_key", "r_start", "r_end"]].itertuples(index=False, name=None):
        start, end = _finite(start), _finite(end)
        if start is not None and end is not None:
            rows.append((str(uid), str(speaker), start + offset, max(end, start) + offset))
    rows.sort(key=lambda row: row[2])
    breaks = [str(p.get("utterance")) for p in prompts] + [str(u) for a in answers for u in (a.get("utterances") or [])]
    stretch = longest_stretch(rows, breaks)
    if stretch is None:
        return {**base, "state": NOT_CHECKED, "reason": "no_speech"}
    length = stretch["end"] - stretch["start"]
    values: Dict[str, Any] = {"start": round(stretch["start"], 1), "end": round(stretch["end"], 1), "seconds": round(length, 1)}
    if length < MONOLOGUE_MIN_S:
        return {**base, "state": NOTHING, "values": values}
    third = length / 3.0
    times = _unprompted_times(inputs, labels)
    first = sum(1 for t in times if stretch["start"] <= t < stretch["start"] + third)
    last = sum(1 for t in times if stretch["end"] - third <= t <= stretch["end"])
    minutes = third / 60.0
    values.update({"firstPerMinute": round(first / minutes, 1), "lastPerMinute": round(last / minutes, 1), "first": first, "last": last})
    if first < MONOLOGUE_MIN_FIRST or last > first * MONOLOGUE_DROP:
        return {**base, "state": NOTHING, "values": values}
    strength = min(0.3 + (length - MONOLOGUE_MIN_S) / 1800.0 + (1 - last / first) * 0.3, 1.0)
    return {**base, "state": FOUND, "strength": round(strength, 2), "values": values}


# ── #4: did the room have time to answer ──


def prompt_rows(
    prompts: Sequence[Tuple[str, float, float]],
    starts: Sequence[float],
    audience: Sequence[float],
    rate_per_s: float,
) -> List[Dict[str, Any]]:
    """`prompts`: `(quote, t_start, t_end)`; `starts`: every utterance's
    start; `audience`: the audience's live message times; `rate_per_s`: the
    session's live rate. One row per prompt."""
    rows = []
    for quote, t, stop in prompts:
        later = [start for start in starts if start > stop]
        resumed = min(later) if later else None
        window = [m for m in audience if stop <= m <= stop + PROMPT_WINDOW_S]
        expected = rate_per_s * PROMPT_WINDOW_S
        late = [m for m in window if resumed is not None and m > resumed]
        delays = sorted(m - stop for m in window)
        rows.append(
            {
                "t": round(t, 1),
                "quote": quote,
                "waitS": round(resumed - stop, 1) if resumed is not None else None,
                "messages": len(window),
                "expected": round(expected, 1),
                "excess": max(0, round(len(window) - expected)),
                "late": len(late),
                "medianDelayS": round(statistics.median(delays), 1) if delays else None,
            }
        )
    return rows


def detect_prompts(inputs: Any, prompts: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    base = {"id": "prompts", "confidence": "estimated"}
    if inputs.transcript is None or not alignment_allows(inputs.alignment, SECONDS):
        return {**base, "state": NOT_CHECKED, "reason": "clock"}
    if not prompts:
        return {**base, "state": NOT_CHECKED, "reason": "no_prompts"}
    spans = _speech_spans(inputs, _finite(inputs.alignment.offset_s) or 0.0)
    by_id = {uid: (start, end) for uid, start, end in spans}
    asked = [
        (str(p.get("quote") or ""), *by_id[str(p.get("utterance"))])
        for p in prompts
        if str(p.get("utterance")) in by_id
    ]
    if not asked:
        return {**base, "state": NOT_CHECKED, "reason": "no_prompts"}
    audience = [e["t"] for e in _audience(inputs)]
    end = _finite(inputs.clock.end_s) or (max(audience) if audience else 0.0)
    rate = len(audience) / end if end > 0 else 0.0
    rows = prompt_rows(sorted(asked, key=lambda p: p[1]), [start for _, start, _ in spans], audience, rate)
    values = {"prompts": rows}
    missed = [
        row
        for row in rows
        if row["excess"] >= PROMPT_MIN_EXCESS
        and row["waitS"] is not None
        and row["waitS"] < PROMPT_SHORT_WAIT_S
        and row["late"] >= row["messages"] * PROMPT_LATE_SHARE
    ]
    if not missed:
        return {**base, "state": NOTHING, "values": values}
    top = max(missed, key=lambda row: row["late"])
    strength = min(0.3 + 0.07 * top["late"] + 0.15 * (len(missed) - 1), 1.0)
    return {**base, "state": FOUND, "strength": round(strength, 2), "values": {**values, "missed": [row["t"] for row in missed]}}


def findings(inputs: Any, chapters: Sequence[Mapping[str, Any]], understanding: Mapping[str, Any]) -> Dict[str, Any]:
    """`{version, found, nothing, notChecked}`: every detector's answer, the
    found ones strongest first. `chapters` are `understanding.chapter_list`'s
    and `understanding` the current reading's result (its `prompts`)."""
    prompts = [p for p in (understanding.get("prompts") or []) if isinstance(p, Mapping)]
    answered = [a for a in (understanding.get("answers") or []) if isinstance(a, Mapping)]
    answers = [
        detect_chapters(inputs, chapters, prompts),
        detect_friction(inputs),
        detect_monologue(inputs, prompts, answered),
        detect_prompts(inputs, prompts),
    ]
    return {
        "version": FINDINGS_VERSION,
        "found": sorted((a for a in answers if a["state"] == FOUND), key=lambda a: -a["strength"]),
        "nothing": [a for a in answers if a["state"] == NOTHING],
        "notChecked": [a for a in answers if a["state"] == NOT_CHECKED],
    }
