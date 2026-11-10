"""The question desk, for the Follow-up tab: every audience question, its
status, and what the host saved (`GET /api/sessions/{id}/record/questions`).

A row is an audience question:

- a Q&A question (`Q001`), from the questions payload, or
- a chat message (`C0001`) that the session analysis labelled a question
  about the content (`class` "question", `question` "content"). Chat
  questions about logistics (the replay, the slides) are counted apart, not
  listed, and chat rows exist only while the stored labels describe this
  chat (`inputs.chat_labels`; `summary.chatQuestions` says `ready`, `none`
  or `stale`).

A team member's question is left out, except on an internal session, where
everyone is audience (as the measures count it).

A row's status, first match wins:

1. `written`: answered in writing in Livestorm (the answer's text is sent).
2. `aloud`: marked answered aloud in Livestorm.
3. `here`: the host marked it answered in StormIQ (`reply`, optional, is
   what they wrote).
4. `open`.

What the host saves (`db.save_question_reply`) is keyed by the row's source
and Livestorm id, never by its evidence id alone, which can shift when the
session is fetched again; `saved_key(row)` is that key. A row without a
Livestorm id can't be saved (`canSave` false).

Authors are short names through `roster.display_people`, which fails closed
(an internal session names nobody), as on the Transcript tab. The CSV
(`open_questions_csv`) is the one exception: it names askers on every
session through `roster.export_people` (the owner's decision), and never
carries an email.

Pure functions: no database, network or web framework.
"""

from __future__ import annotations

import csv
import io
import math
from typing import Any, Dict, List, Mapping, Optional, Tuple

from livestorm_app.record.evidence import CHAT, QUESTION, question_items
from livestorm_app.record.roster import PUBLIC_PROFILE, display_people, export_people
from livestorm_app.record.transcript_view import ATTENDEE, INTERNAL_PROFILE, TEAM, short_name

DESK_VERSION = 1

QA = "qa"
CHAT_SOURCE = "chat"

WRITTEN = "written"
ALOUD = "aloud"
HERE = "here"
OPEN = "open"
STATUSES = (WRITTEN, ALOUD, HERE, OPEN)

MAX_REPLY_LENGTH = 4000

_ALL_PEOPLE = 1_000_000

# A CSV cell that a spreadsheet would read as a formula gets a leading
# apostrophe, as `frontend/src/utils/csv.js` does: attendees wrote the text.
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _seconds(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else round(number, 3)


def saved_key(source: str, livestorm_id: Optional[str]) -> Optional[str]:
    """The key a saved reply is stored under, or None without a Livestorm id."""
    livestorm_id = str(livestorm_id or "").strip()
    return f"{source}:{livestorm_id}" if livestorm_id else None


def _short_names(inputs: Any) -> Dict[str, str]:
    profile = INTERNAL_PROFILE if inputs.roster.meta.internal else PUBLIC_PROFILE
    names: Dict[str, str] = {}
    for person in display_people(inputs.roster, profile=profile, limit=_ALL_PEOPLE):
        name = short_name(person.get("name"))
        if name:
            names[person["ref"]] = name
    return names


def _candidates(inputs: Any) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """The audience's question rows from the events, in time order, plus
    counts that aren't rows (logistics chat questions)."""
    events = inputs.events
    internal = bool(inputs.roster.meta.internal)
    labels = inputs.chat_labels or {}
    rows: List[Dict[str, Any]] = []
    logistics = 0
    if events is None or events.empty:
        return rows, {"logistics": 0}
    for row in events[events["kind"].isin([CHAT, QUESTION])].to_dict("records"):
        if row.get("is_team") and not internal:
            continue
        if row["kind"] == CHAT:
            label = labels.get(str(row["eid"]))
            if label is None or label.get("class") != "question":
                continue
            if label.get("question") == "logistics":
                logistics += 1
                continue
        rows.append(row)
    rows.sort(key=lambda r: (r["at_unix"] is None, r["at_unix"] or 0.0, str(r["eid"])))
    return rows, {"logistics": logistics}


def _status(livestorm: Optional[Any], saved: Optional[Mapping[str, Any]]) -> str:
    if livestorm is not None and livestorm.response:
        return WRITTEN
    if livestorm is not None and livestorm.answered_orally:
        return ALOUD
    if saved and saved.get("answered"):
        return HERE
    return OPEN


def _desk_rows(inputs: Any, saved: Mapping[str, Mapping[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    candidates, extra = _candidates(inputs)
    by_livestorm_id = {item.livestorm_id: item for item in question_items(inputs.questions_payload) if item.livestorm_id}
    rows = []
    for row in candidates:
        source = QA if row["kind"] == QUESTION else CHAT_SOURCE
        livestorm_id = row["livestorm_id"] or None
        key = saved_key(source, livestorm_id)
        stored = saved.get(key) if key else None
        livestorm = by_livestorm_id.get(livestorm_id) if source == QA and livestorm_id else None
        status = _status(livestorm, stored)
        reply = stored.get("reply") if stored and isinstance(stored.get("reply"), str) and stored.get("reply") else None
        rows.append(
            {
                "id": str(row["eid"]),
                "source": source,
                "t": _seconds(row["t"]),
                "text": str(row["text"] or ""),
                "authorRef": row["author_ref"] if isinstance(row["author_ref"], str) else None,
                "team": bool(row.get("is_team")),
                # `desk_status`, not `status`: record code reads an alignment's
                # status only through `alignment_allows` (tests/test_alignment.py).
                "desk_status": status,
                "answer": livestorm.response if status == WRITTEN else None,
                "reply": reply,
                "canSave": key is not None,
                "_key": key,
            }
        )
    return rows, extra


def question_desk(inputs: Any, saved: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    """`{version, summary, questions}`. `saved` is the host's saved replies
    by `saved_key` (`db.fetch_question_replies`)."""
    rows, extra = _desk_rows(inputs, saved)
    names = _short_names(inputs)
    counts = {status: sum(1 for row in rows if row["desk_status"] == status) for status in STATUSES}
    questions = []
    for row in rows:
        author = names.get(row["authorRef"]) if row["authorRef"] else None
        questions.append(
            {
                "id": row["id"],
                "source": row["source"],
                "t": row["t"],
                "text": row["text"],
                "author": author or (TEAM if row["team"] else ATTENDEE),
                "status": row["desk_status"],
                "answer": row["answer"],
                "reply": row["reply"],
                "canSave": row["canSave"],
            }
        )
    return {
        "version": DESK_VERSION,
        "summary": {
            "total": len(questions),
            **counts,
            "logistics": extra["logistics"],
            "chatQuestions": str((inputs.chat_labels_info or {}).get("state") or "none"),
        },
        "questions": questions,
    }


def find_question(inputs: Any, question_id: str) -> Optional[Dict[str, Any]]:
    """The desk row `question_id` names (`Q001` or `C0001`), with its source
    and Livestorm id, or None when the desk has no such row (a team message,
    a chat message not labelled a content question, an unknown id)."""
    rows, _ = _desk_rows(inputs, {})
    for row in rows:
        if row["id"] == question_id:
            livestorm_id = row["_key"].split(":", 1)[1] if row["_key"] else None
            return {"id": row["id"], "source": row["source"], "livestormId": livestorm_id, "status": row["desk_status"]}
    return None


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA_START) else text


def _clock(seconds: Optional[float]) -> str:
    if seconds is None:
        return ""
    sign = "-" if seconds < 0 else ""
    total = int(abs(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{sign}{hours}:{minutes:02d}:{secs:02d}" if hours else f"{sign}{minutes}:{secs:02d}"


CSV_COLUMNS = ("Question", "Asked by", "Company", "Where", "When")


def open_questions_csv(inputs: Any, saved: Mapping[str, Mapping[str, Any]]) -> str:
    """The open questions as CSV text, with who asked each one: name and
    company through `roster.export_people`, on every session (internal ones
    included), never an email. "When" is the time since the session's start
    ("" without a clock). Cells that a spreadsheet would read as a formula
    get a leading apostrophe."""
    rows, _ = _desk_rows(inputs, saved)
    people = export_people(inputs.roster)
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    for row in rows:
        if row["desk_status"] != OPEN:
            continue
        person = people.get(row["authorRef"]) if row["authorRef"] else None
        writer.writerow(
            [
                _cell(row["text"]),
                _cell((person or {}).get("name") or ""),
                _cell((person or {}).get("company") or ""),
                "Q&A" if row["source"] == QA else "Chat",
                _clock(row["t"]),
            ]
        )
    return out.getvalue()
