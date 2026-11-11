"""The editable FAQ, for the Follow-up tab (`GET`/`PUT /api/sessions/{id}/
record/faq`): a list of entries the user edits, saves and exports.

An entry is `{id, question, answer, source, t, origin, deskIds}`:

- `source`, where the answer comes from: `written` (Livestorm's written
  answer), `host` (the host's reply saved on the question desk), `talk` (a
  passage of the talk the session analysis located, at `t` seconds on the
  session clock), `aloud` (answered live, no words found: a fixed line
  pointing to the replay at `t`, the question's time), `manual` (typed by
  the user) or `none` (no answer yet: an open question).
- `origin`: `desk` (drafted from a desk question) or `manual` (added by the
  user).
- `deskIds`: the desk questions it came from (`desk_id`: the desk's stable
  key, source and Livestorm id, as `question_desk.saved_key` gives it, so it
  survives a fetch that renumbers the evidence ids). Several when the user
  merged entries.

The first draft is built by code from the desk (`draft_entries`): one entry
per desk question in the desk's order (the desk already leaves out the
team's questions, except on an internal session, and the chat's logistics
questions), the question as the audience wrote it and the answer, both
redacted for a public format (`record.redaction`: names, emails, phone
numbers and links become placeholders). No model call.

Staying in step with the desk (`desk_updates`): what the desk looked like at
the last save is kept (`desk_stamp`: each desk id, `answered` or `open`). A
desk question answered since, and in no entry with an answer, is offered
again; so is a desk question that is new since the save. An entry's answer
is never replaced here: the page merges an update only into an entry that
is still open, on the user's click.

Pure functions: no database, network or web framework.
"""

from __future__ import annotations

import csv
import io
import math
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

FAQ_VERSION = 1
FAQ_LANGUAGE = "English"  # French comes later.

WRITTEN = "written"
HOST = "host"
TALK = "talk"
ALOUD = "aloud"
MANUAL = "manual"
NONE = "none"
SOURCES = (WRITTEN, HOST, TALK, ALOUD, MANUAL, NONE)

DESK = "desk"
ORIGINS = (DESK, MANUAL)

MAX_ENTRIES = 200
MAX_QUESTION_LENGTH = 500
MAX_ANSWER_LENGTH = 4000
MAX_DESK_IDS = 50
MAX_ID_LENGTH = 100
MAX_T = 7 * 86400

# What the desk stamp holds for each desk id.
STAMP_ANSWERED = "answered"
STAMP_OPEN = "open"

# What a desk update is: a question answered on the desk since the save, or
# one that wasn't on the desk then.
UPDATE_ANSWERED = "answered"
UPDATE_NEW = "new"

_ID = re.compile(r"^[A-Za-z0-9:_.-]{1,%d}$" % MAX_ID_LENGTH)
_UNSAFE_ID_CHARS = re.compile(r"[^A-Za-z0-9:_.-]+")
# Control characters but tab and newline: the text is plain, never markup.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

SOURCE_WORDS = {
    WRITTEN: "Written answer",
    HOST: "Host's reply",
    TALK: "From the talk",
    ALOUD: "Answered live",
    MANUAL: "Added by you",
    NONE: "Not answered yet",
}

CSV_COLUMNS = ("Question", "Answer", "Source", "Time in the replay")
# A CSV cell that a spreadsheet would read as a formula gets a leading
# apostrophe, as `record/question_desk.py` and `frontend/src/utils/csv.js` do.
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


class FaqInvalid(ValueError):
    """The entries can't be saved; the message says why, never their text."""


def replay_time(seconds: Any) -> str:
    """`m:ss`, or `h:mm:ss` from an hour; "" without a time (or before 0)."""
    t = _time(seconds)
    if t is None:
        return ""
    total = int(t)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def aloud_line(seconds: Any) -> str:
    """The answer of a question answered live with no words found."""
    at = replay_time(seconds)
    return f"Answered live: see the replay at {at}." if at else "Answered live: see the replay."


def _time(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0 or number > MAX_T:
        return None
    return int(number) if number.is_integer() else round(number, 3)


def _text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return _CONTROL.sub("", text).strip()


def desk_id(question: Mapping[str, Any], keys: Mapping[str, str]) -> str:
    """A desk question's id in the FAQ: its stable key (`keys`, the desk's
    evidence id to `question_desk.saved_key`), or its evidence id without a
    Livestorm id."""
    evidence_id = str(question.get("id") or "")
    return str(keys.get(evidence_id) or evidence_id)


def _entry_id(desk: str) -> str:
    return ("d-" + _UNSAFE_ID_CHARS.sub("-", desk))[:MAX_ID_LENGTH]


def _desk_answer(question: Mapping[str, Any]) -> tuple:
    """(answer, source, t) for one desk row, first match wins: Livestorm's
    written answer, the host's reply, the located passage, the fixed line
    for a question answered live, else nothing."""
    status = question.get("answered_as")
    located = question.get("located") if isinstance(question.get("located"), Mapping) else None
    if question.get("answer"):
        return str(question["answer"]), WRITTEN, None
    if status == "here" and question.get("reply"):
        return str(question["reply"]), HOST, None
    if located and str(located.get("quote") or "").strip():
        return str(located["quote"]), TALK, _time(located.get("t"))
    if status == "aloud":
        return aloud_line(question.get("t")), ALOUD, _time(question.get("t"))
    return "", NONE, None


def draft_entries(questions: Sequence[Mapping[str, Any]], keys: Mapping[str, str], redact: Any) -> List[Dict[str, Any]]:
    """The FAQ's first draft from the desk's rows (`question_desk(...)
    ["questions"]`, in the desk's order, each with its desk status as
    `answered_as`, as `record.faq_brief` takes them): one entry each,
    question and answer passed through `redact` (the session's public
    redactor), never who asked."""
    entries = []
    for question in questions:
        desk = desk_id(question, keys)
        if not desk:
            continue
        answer, source, t = _desk_answer(question)
        entries.append(
            {
                "id": _entry_id(desk),
                "question": _text(redact(question.get("text")))[:MAX_QUESTION_LENGTH],
                # The fixed line holds no one's words.
                "answer": _text(answer if source == ALOUD else redact(answer))[:MAX_ANSWER_LENGTH],
                "source": source,
                "t": t,
                "origin": DESK,
                "deskIds": [desk],
            }
        )
    return entries


def validate_entries(raw: Any) -> List[Dict[str, Any]]:
    """The entries to save, cleaned: plain text (control characters
    removed), within the limits, sources and origins in their sets, ids
    unique. An empty answer makes the entry open (`none`); a typed answer on
    an open entry is the user's (`manual`); a time is kept only for an
    answer from the talk or answered live. Raises `FaqInvalid`."""
    if not isinstance(raw, (list, tuple)):
        raise FaqInvalid("The FAQ is a list of entries.")
    if len(raw) > MAX_ENTRIES:
        raise FaqInvalid(f"A FAQ holds at most {MAX_ENTRIES} entries.")
    seen = set()
    entries = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, Mapping):
            raise FaqInvalid(f"Entry {index} isn't an entry.")
        entry_id = str(item.get("id") or "").strip() or f"e-{index}"
        if not _ID.match(entry_id) or entry_id in seen:
            raise FaqInvalid(f"Entry {index} has a missing, malformed or repeated id.")
        seen.add(entry_id)
        for field in ("question", "answer"):
            if item.get(field) is not None and not isinstance(item.get(field), str):
                raise FaqInvalid(f"Entry {index}'s {field} must be text.")
        question = _text(item.get("question"))
        answer = _text(item.get("answer"))
        if not question:
            raise FaqInvalid(f"Entry {index} needs a question.")
        if len(question) > MAX_QUESTION_LENGTH:
            raise FaqInvalid(f"Entry {index}'s question is longer than {MAX_QUESTION_LENGTH} characters.")
        if len(answer) > MAX_ANSWER_LENGTH:
            raise FaqInvalid(f"Entry {index}'s answer is longer than {MAX_ANSWER_LENGTH} characters.")
        source = str(item.get("source") or NONE)
        origin = str(item.get("origin") or MANUAL)
        if source not in SOURCES:
            raise FaqInvalid(f"Entry {index} has an unknown source.")
        if origin not in ORIGINS:
            raise FaqInvalid(f"Entry {index} has an unknown origin.")
        desk_ids_raw = item.get("deskIds") or []
        if not isinstance(desk_ids_raw, (list, tuple)) or len(desk_ids_raw) > MAX_DESK_IDS:
            raise FaqInvalid(f"Entry {index} lists too many desk questions.")
        desk_ids: List[str] = []
        for value in desk_ids_raw:
            value = str(value or "").strip()
            if not _ID.match(value):
                raise FaqInvalid(f"Entry {index} has a malformed desk id.")
            if value not in desk_ids:
                desk_ids.append(value)
        if not answer:
            source = NONE
        elif source == NONE:
            source = MANUAL
        entries.append(
            {
                "id": entry_id,
                "question": question,
                "answer": answer,
                "source": source,
                "t": _time(item.get("t")) if source in (TALK, ALOUD) else None,
                "origin": origin,
                "deskIds": desk_ids,
            }
        )
    return entries


def _ids(entries: Iterable[Mapping[str, Any]], *, answered: Optional[bool] = None) -> set:
    out = set()
    for entry in entries:
        if answered is not None and bool(str(entry.get("answer") or "").strip()) != answered:
            continue
        out.update(entry.get("deskIds") or [])
    return out


def desk_stamp(
    draft: Sequence[Mapping[str, Any]],
    entries: Sequence[Mapping[str, Any]],
    previous: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    """What the desk looked like at a save, for `desk_updates`: each desk id
    (`draft`, the desk as `draft_entries` reads it now) `answered` or
    `open`. A desk answer the user didn't take yet into an entry that is
    still open keeps its previous mark, so it is offered again."""
    previous = previous or {}
    still_open = _ids(entries, answered=False)
    stamp: Dict[str, str] = {}
    for item in draft:
        for desk in item.get("deskIds") or []:
            answered = bool(str(item.get("answer") or "").strip())
            if answered and desk in still_open:
                stamp[desk] = previous.get(desk, STAMP_OPEN)
            else:
                stamp[desk] = STAMP_ANSWERED if answered else STAMP_OPEN
    return stamp


def desk_updates(
    entries: Sequence[Mapping[str, Any]],
    stamp: Optional[Mapping[str, str]],
    draft: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """The desk questions to offer a saved FAQ, in the desk's order, each a
    draft entry with its `kind`:

    - `answered`: it has an answer on the desk that it didn't have at the
      last save, and no entry holding it has an answer;
    - `new`: it wasn't on the desk at the last save and isn't in any entry
      (with or without an answer).

    A question the user removed from the FAQ is offered again only once it
    is answered on the desk after the save."""
    stamp = stamp or {}
    in_entries = _ids(entries)
    answered_in_entries = _ids(entries, answered=True)
    updates = []
    for item in draft:
        desk = (item.get("deskIds") or [""])[0]
        if not desk:
            continue
        has_answer = bool(str(item.get("answer") or "").strip())
        if has_answer:
            if desk in answered_in_entries or stamp.get(desk) == STAMP_ANSWERED:
                continue
            kind = UPDATE_ANSWERED if (desk in in_entries or desk in stamp) else UPDATE_NEW
        elif desk not in in_entries and desk not in stamp:
            kind = UPDATE_NEW
        else:
            continue
        updates.append({**item, "kind": kind})
    return updates


def faq_title(session_title: Any) -> str:
    title = _text(session_title)
    return f"FAQ: {title}" if title else "FAQ"


def _answer_lines(entry: Mapping[str, Any]) -> List[str]:
    answer = str(entry.get("answer") or "").strip()
    if not answer:
        return ["Not answered yet."]
    lines = answer.split("\n")
    if entry.get("source") == TALK and replay_time(entry.get("t")):
        lines.append(f"({replay_time(entry.get('t'))} in the replay)")
    return lines


def faq_markdown(entries: Sequence[Mapping[str, Any]], title: Any = "") -> str:
    """The FAQ as Markdown: the title, then each question as a heading and
    its answer below (a passage of the talk with its time in the replay; an
    open question says so). `frontend/src/followup/faq.js` `toMarkdown`
    writes the same text."""
    blocks = [f"# {faq_title(title)}"]
    for entry in entries:
        blocks.append("\n".join([f"## {str(entry.get('question') or '').strip()}", "", *_answer_lines(entry)]))
    return "\n\n".join(blocks) + "\n"


def pdf_body(entries: Sequence[Mapping[str, Any]]) -> str:
    """The FAQ's body for `pdf.build_pdf_story` (which writes the title):
    each question as a heading, its answer as paragraphs."""
    blocks = []
    for entry in entries:
        blocks.append("\n".join([f"### {str(entry.get('question') or '').strip()}", *_answer_lines(entry)]))
    return "\n\n".join(blocks) if blocks else "No questions yet."


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA_START) else text


def faq_csv(entries: Sequence[Mapping[str, Any]]) -> str:
    """The FAQ as CSV, in its order: question, answer, source (in words) and
    time in the replay (`m:ss` or `h:mm:ss`, for an answer from the talk or
    answered live). Cells a spreadsheet would read as a formula get a
    leading apostrophe: attendees wrote the questions."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    for entry in entries:
        source = str(entry.get("source") or NONE)
        t = replay_time(entry.get("t")) if source in (TALK, ALOUD) else ""
        cells = [entry.get("question"), entry.get("answer"), SOURCE_WORDS.get(source, ""), t]
        writer.writerow([_cell(cell) for cell in cells])
    return out.getvalue()
