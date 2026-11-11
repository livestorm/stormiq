"""The Pulse's key moments, told: when the room reacted, what was being said,
what kind of reaction it was, what people wrote, and the questions asked
then. Served by `GET /api/sessions/{id}/record/moments`, read on demand like
the Transcript's room view; the record itself never carries a name or text.

- **Which moments.** The live minutes where the audience wrote the most:
  the surges of `findings.surge_threshold` (adjacent minutes merged), else,
  in a session with none, its busiest minutes (`BUSY_MIN_MESSAGES` or more,
  never adjacent). The `MAX_MOMENTS` with the most messages, in time order,
  numbered from 1.
- **What kind.** `reply` when a prompt to the room (the stored
  understanding's) ended in the `REPLY_WINDOW_S` before its first minute or
  during it, with that prompt; else, from the chat labels, `tech` (half or
  more sound and connection complaints), `social` (half or more greetings,
  thanks, logistics or complaints) or `content` (the room's own reaction);
  `unknown` without labels.
- **What was said** is the speech around the moment's first message (moved
  back by `DEFAULT_LAG_S`, the room's usual reaction lag, unless it replies
  to a prompt): only when the alignment allows minutes, and `precise` only
  when it allows seconds. Speakers are named as the user saved them, else
  "Speaker n".
- **What people wrote**: up to `MAX_MESSAGES` chat messages from the
  moment, the longest distinct ones without a link, leaving out greetings,
  logistics and complaints unless the moment is made of them, and leaving
  out questions (listed apart). **Questions**: the Q&A questions and the
  chat's labelled questions asked in it, up to `MAX_QUESTIONS`.
- **Authors** are short names only through `roster.display_people`, which
  fails closed (None when a person can't be named).

Pure functions: no database, network or web framework.
"""

from __future__ import annotations

import math
import statistics
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from livestorm_app.record.alignment import MINUTES, SECONDS, alignment_allows
from livestorm_app.record.assemble import pulse_events
from livestorm_app.record.clock import LIVE, event_lane, phase
from livestorm_app.record.evidence import QUESTION
from livestorm_app.record.findings import DEFAULT_LAG_S, REPLY_WINDOW_S, surge_threshold, surges
from livestorm_app.record.transcript_view import speaker_name
from livestorm_app.record.voices import named_people

MOMENTS_VERSION = 1

MIN_MESSAGES = 10
BUSY_MIN_MESSAGES = 3
MAX_MOMENTS = 5
MAX_MESSAGES = 3
MAX_QUESTIONS = 5
SAID_BEFORE_S = 20.0
SAID_AFTER_S = 40.0
MAX_SAID_LINES = 3
MAX_SAID_CHARS = 360
MAX_TEXT_CHARS = 280

SOCIAL = ("social", "logistics")
TECH = ("tech",)


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _one_line(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit].rstrip() + "…" if len(text) > limit else text


def _room(inputs: Any) -> List[Dict[str, Any]]:
    """The live audience chat and questions, in time order."""
    events = pulse_events(inputs)
    rows: List[Dict[str, Any]] = []
    if events is None or events.empty:
        return rows
    columns = ["eid", "kind", "is_team", "t", "author_ref", "author_id", "text", "has_link"]
    for eid, kind, is_team, t, ref, author_id, text, link in events[columns].itertuples(index=False, name=None):
        lane = event_lane(kind, is_team)
        if lane not in ("audience_chat", "questions") or phase(t, inputs.clock) != LIVE:
            continue
        ref = ref if isinstance(ref, str) and ref else None
        rows.append(
            {
                "eid": str(eid),
                "question": kind == QUESTION,
                "t": float(t),
                "ref": ref,
                "author": ref or (author_id if isinstance(author_id, str) and author_id else None),
                "text": str(text or "").strip(),
                "link": bool(link),
            }
        )
    return sorted(rows, key=lambda row: (row["t"], row["eid"]))


def _busiest(counts: Sequence[int]) -> List[Tuple[int, int]]:
    """A session without a surge: its busiest single minutes, never adjacent."""
    picked: List[int] = []
    for minute in sorted(range(len(counts)), key=lambda m: (-counts[m], m)):
        if counts[minute] < BUSY_MIN_MESSAGES or len(picked) == MAX_MOMENTS:
            break
        if all(abs(minute - other) > 1 for other in picked):
            picked.append(minute)
    return [(m, m) for m in picked]


def _label(labels: Optional[Mapping[str, Mapping[str, Any]]], eid: str) -> str:
    return str(((labels or {}).get(eid) or {}).get("class") or "")


def _kind(chat: Sequence[Mapping[str, Any]], labels: Optional[Mapping[str, Mapping[str, Any]]], prompt: Any) -> str:
    if prompt is not None:
        return "reply"
    if labels is None:
        return "unknown"
    classes = [_label(labels, row["eid"]) for row in chat]
    if classes and sum(c in TECH for c in classes) * 2 >= len(classes):
        return "tech"
    if classes and sum(c in SOCIAL + TECH for c in classes) * 2 >= len(classes):
        return "social"
    return "content"


def _said(inputs: Any, saved: Mapping[str, Any], at: float) -> Optional[Dict[str, Any]]:
    """The speech around `at` on the session clock: up to `MAX_SAID_LINES`
    utterances overlapping [at - SAID_BEFORE_S, at + SAID_AFTER_S]."""
    transcript = inputs.transcript
    if transcript is None or transcript.utterances.empty or not alignment_allows(inputs.alignment, MINUTES):
        return None
    offset = _finite(inputs.alignment.offset_s) or 0.0
    frame = transcript.utterances.sort_values(["r_start", "order"], na_position="last", kind="stable")
    order: Dict[Optional[str], int] = {}
    lines: List[Tuple[float, Optional[str], str]] = []
    for key, start, end, text in frame[["speaker_key", "r_start", "r_end", "text"]].itertuples(index=False, name=None):
        key = key if isinstance(key, str) else None
        if key not in order:
            order[key] = len(order) + 1
        start, end = _finite(start), _finite(end)
        words = str(text or "").strip()
        if start is None or not words:
            continue
        t_start = start + offset
        t_end = max(end if end is not None else start, start) + offset
        if t_end >= at - SAID_BEFORE_S and t_start <= at + SAID_AFTER_S:
            lines.append((t_start, key, words))
    if not lines:
        return None
    lines = lines[:MAX_SAID_LINES]
    speakers: List[str] = []
    for _, key, _ in lines:
        name = speaker_name(key, saved, order.get(key, 1))
        if name not in speakers:
            speakers.append(name)
    return {
        "t": round(lines[0][0], 1),
        "speakers": speakers,
        "text": _one_line(" ".join(words for _, _, words in lines), MAX_SAID_CHARS),
        "precise": alignment_allows(inputs.alignment, SECONDS),
    }


def _line(row: Mapping[str, Any], names: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    person = names.get(row["ref"]) if row["ref"] else None
    return {
        "id": row["eid"],
        "t": round(row["t"], 1),
        "text": _one_line(row["text"], MAX_TEXT_CHARS),
        "author": person["name"] if person else None,
    }


def _written(chat: Sequence[Mapping[str, Any]], labels: Optional[Mapping[str, Mapping[str, Any]]], kind: str) -> List[Mapping[str, Any]]:
    """Up to `MAX_MESSAGES` messages that say what the room said."""
    pool = [row for row in chat if row["text"] and _label(labels, row["eid"]) != "question"]
    if labels is not None and kind not in ("social", "tech"):
        pool = [row for row in pool if _label(labels, row["eid"]) not in SOCIAL + TECH] or pool
    plain = [row for row in pool if not row["link"]] or pool
    seen, picked = set(), []
    for row in sorted(plain, key=lambda r: (-len(r["text"].split()), r["t"])):
        key = " ".join(row["text"].lower().split())
        if key in seen:
            continue
        seen.add(key)
        picked.append(row)
        if len(picked) == MAX_MESSAGES:
            break
    return sorted(picked, key=lambda r: r["t"])


def _prompts(inputs: Any, understanding: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """The stored prompts to the room, with their end on the session clock."""
    if inputs.transcript is None or not alignment_allows(inputs.alignment, MINUTES):
        return []
    offset = _finite(inputs.alignment.offset_s) or 0.0
    frame = inputs.transcript.utterances
    ends = {str(uid): _finite(r_end) for uid, r_end in frame[["uid", "r_end"]].itertuples(index=False, name=None)}
    kept = []
    for prompt in understanding.get("prompts") or []:
        end = ends.get(str(prompt.get("utterance"))) if isinstance(prompt, Mapping) else None
        if end is not None:
            kept.append({"t": _finite(prompt.get("t")), "end": end + offset, "quote": str(prompt.get("quote") or "")})
    return kept


def room_moments(inputs: Any, understanding: Mapping[str, Any], saved_names: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """`{version, state, usualPerMinute, surges, speech, moments}`. `state`:
    `ready`, `few_messages` (under `MIN_MESSAGES` live audience messages)
    or `no_clock`. `surges` is False when the moments are only the busiest
    minutes. A moment: `{n, start, end, kind, messages, people, questions,
    peak, prompt, said, written, asked, themes}`."""
    saved = saved_names if isinstance(saved_names, Mapping) else {}
    base = {"version": MOMENTS_VERSION, "usualPerMinute": None, "surges": False, "speech": False, "moments": []}
    end = _finite(inputs.clock.end_s)
    if end is None or end <= 0:
        return {**base, "state": "no_clock"}
    room = _room(inputs)
    if len(room) < MIN_MESSAGES:
        return {**base, "state": "few_messages"}
    labels = inputs.chat_labels if isinstance(inputs.chat_labels, Mapping) else None
    counts = [0] * max(1, math.ceil(end / 60.0))
    for row in room:
        counts[min(max(int(row["t"] // 60), 0), len(counts) - 1)] += 1
    runs = surges(counts, surge_threshold(counts))
    found = bool(runs)
    if not runs:
        runs = _busiest(counts)
    prompts = _prompts(inputs, understanding)
    names = named_people(inputs)

    moments = []
    for first, last in runs:
        lo, hi = first * 60.0, (last + 1) * 60.0
        inside = [row for row in room if lo <= row["t"] < hi]
        if not inside:
            continue
        chat = [row for row in inside if not row["question"]]
        prompt = next((p for p in prompts if lo - REPLY_WINDOW_S <= p["end"] < hi), None)
        kind = _kind(chat, labels, prompt)
        asked = [row for row in inside if row["question"] or _label(labels, row["eid"]) == "question"]
        themes: Dict[str, int] = {}
        if labels is not None:
            for row in chat:
                cls = _label(labels, row["eid"]) or "unlabelled"
                themes[cls] = themes.get(cls, 0) + 1
        moments.append(
            {
                "start": round(lo, 1),
                "end": round(hi, 1),
                "kind": kind,
                "messages": len(inside),
                "people": len({row["author"] for row in inside if row["author"]}),
                "questions": len(asked),
                "peak": max(counts[first : last + 1]),
                "prompt": {"t": prompt["t"], "quote": prompt["quote"]} if prompt else None,
                "said": _said(inputs, saved, inside[0]["t"] - (0.0 if prompt else DEFAULT_LAG_S)),
                "written": [_line(row, names) for row in _written(chat, labels, kind)],
                "asked": [_line(row, names) for row in asked[:MAX_QUESTIONS]],
                "themes": themes,
            }
        )
    ranked = sorted(sorted(moments, key=lambda m: -m["messages"])[:MAX_MOMENTS], key=lambda m: m["start"])
    for number, moment in enumerate(ranked, start=1):
        moment["n"] = number
    return {
        **base,
        "state": "ready",
        "usualPerMinute": round(statistics.median(counts), 1),
        "surges": found,
        "speech": inputs.transcript is not None and alignment_allows(inputs.alignment, MINUTES),
        "moments": ranked,
    }
