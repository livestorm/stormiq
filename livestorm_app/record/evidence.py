"""Audience evidence: chat, questions and written answers on the session clock.

`audience_events` turns the cached chat and questions payloads into one table,
with an evidence id on every row:

- `C0001`, `C0002`, ... for chat messages, ordered by `(created_at,
  livestorm_id, text)`;
- `Q001`, ... for questions, ordered the same way;
- `A001`, ... for written answers (a question with response text), ordered by
  `(responded_at, livestorm_id, text)`.

Ids depend on the payloads only, never on the clock, so two parses of the same
payloads give the same ids. Payload order doesn't change them either, with one
exception: when a Livestorm id repeats with different content, the first copy
is kept. Each row keeps its Livestorm id: anything stored later should record
both. Times are read from the raw payload values, never from the formatted
strings of the classic tables.

Pure functions: no database, network or web framework.
"""

from __future__ import annotations

import html
import re
from typing import Any, Dict, List, Mapping, Optional

import pandas as pd

from livestorm_app.livestorm.payloads import extract_messages, extract_questions
from livestorm_app.record.clock import ClockBase, minute_index, parse_time, phase

CHAT = "chat"
QUESTION = "question"
ANSWER = "answer"

EVIDENCE_COLUMNS = (
    "eid",  # C0001 | Q001 | A001
    "kind",  # chat | question | answer
    "livestorm_id",  # the message or question id (an answer keeps its question's id)
    "reply_to",  # an answer's question eid; None otherwise
    "at_unix",  # the raw created_at (responded_at for an answer), in unix seconds
    "t",  # seconds since started_at; NaN without a clock
    "phase",  # before | live | after; None without a clock
    "minute",  # the live minute; <NA> outside the live session
    "is_team",
    "author_id",  # the Livestorm people id
    "text",
    "has_link",  # a URL in the text, or a link in html_content
)

# Message flags from the chat API: the team or a guest speaker wrote it.
TEAM_MESSAGE_FLAGS = ("from_team_member", "from_guest_speaker")

_URL_IN_TEXT = re.compile(r"(?i)(?:\bhttps?://|\bwww\.)\S")
_LINK_HREF = re.compile(r"""(?i)\bhref\s*=\s*["']?\s*(?:https?://|www\.)""")
_TAG = re.compile(r"<[^>]*>")


def _flag(value: Any) -> bool:
    return value is True or str(value).strip().lower() == "true"


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _clean_id(value: Any) -> Optional[str]:
    if value is None or isinstance(value, bool):
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _html_to_text(markup: str) -> str:
    return " ".join(html.unescape(_TAG.sub(" ", markup)).split())


def _has_link(text: str, markup: str = "") -> bool:
    """A URL in the text, or a link whose href is a URL in the HTML."""
    return bool(_URL_IN_TEXT.search(text) or _LINK_HREF.search(markup))


def _attributes(record: Mapping[str, Any]) -> Mapping[str, Any]:
    attributes = record.get("attributes")
    return attributes if isinstance(attributes, Mapping) else {}


def _unique_records(items: List[Any]) -> List[Mapping[str, Any]]:
    """Records in payload order, without repeats of the same id (a page that
    shifted during pagination can return a record twice). The first copy wins."""
    seen = set()
    records = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        record_id = _clean_id(item.get("id"))
        if record_id is not None:
            if record_id in seen:
                continue
            seen.add(record_id)
        records.append(item)
    return records


def _roster_flag(value: Any) -> Optional[bool]:
    """A roster's team flag: a real boolean, or the string "true" or "false".
    None for anything else (missing, NaN, `pd.NA`), so the default decides."""
    if pd.api.types.is_bool(value):
        return bool(value)
    if isinstance(value, str):
        return {"true": True, "false": False}.get(value.strip().lower())
    return None


def _is_team(author_id: Optional[str], roster: Optional[Mapping[str, Any]], default: bool) -> bool:
    if roster is not None and author_id is not None and author_id in roster:
        flag = _roster_flag(roster[author_id])
        if flag is not None:
            return flag
    return default


def _row(kind: str, livestorm_id: Optional[str], raw_time: Any, base: ClockBase, **fields: Any) -> Dict[str, Any]:
    at_unix = parse_time(raw_time)
    t = at_unix - base.t0_unix if at_unix is not None and base.t0_unix is not None else None
    return {
        "eid": None,
        "kind": kind,
        "livestorm_id": livestorm_id or "",
        "reply_to": None,
        "at_unix": at_unix,
        "t": t,
        "phase": phase(t, base),
        "minute": minute_index(t, base),
        **fields,
    }


def _assign_ids(rows: List[Dict[str, Any]], prefix: str, width: int) -> List[Dict[str, Any]]:
    """Sort by (time, Livestorm id, text) and number from 1. Rows without a
    readable time come last. The text breaks ties between rows that have no
    Livestorm id, so their order doesn't follow the payload's."""
    ordered = sorted(
        rows,
        key=lambda row: (row["at_unix"] is None, row["at_unix"] or 0.0, row["livestorm_id"], row["text"]),
    )
    for number, row in enumerate(ordered, start=1):
        row["eid"] = f"{prefix}{number:0{width}d}"
    return ordered


def _chat_rows(chat_payload: Any, base: ClockBase, roster: Optional[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for record in _unique_records(extract_messages(chat_payload)):
        attributes = _attributes(record)
        markup = _text(attributes.get("html_content"))
        text = _text(attributes.get("text_content")) or _html_to_text(markup)
        author_id = _clean_id(attributes.get("author_id"))
        flagged = any(_flag(attributes.get(flag)) for flag in TEAM_MESSAGE_FLAGS)
        rows.append(
            _row(
                CHAT,
                _clean_id(record.get("id")),
                attributes.get("created_at"),
                base,
                is_team=_is_team(author_id, roster, flagged),
                author_id=author_id,
                text=text,
                has_link=_has_link(text, markup),
            )
        )
    return _assign_ids(rows, "C", 4)


def _question_and_answer_rows(
    questions_payload: Any, base: ClockBase, roster: Optional[Mapping[str, Any]]
) -> List[Dict[str, Any]]:
    questions = []
    answers = []
    for record in _unique_records(extract_questions(questions_payload)):
        attributes = _attributes(record)
        livestorm_id = _clean_id(record.get("id"))
        question_text = _text(attributes.get("question"))
        asker_id = _clean_id(attributes.get("question_author_id"))
        question = _row(
            QUESTION,
            livestorm_id,
            attributes.get("created_at"),
            base,
            is_team=_is_team(asker_id, roster, False),
            author_id=asker_id,
            text=question_text,
            has_link=_has_link(question_text, question_text),
        )
        questions.append(question)

        response = _text(attributes.get("response"))
        if not response:
            continue  # unanswered, or answered orally only
        responder_id = _clean_id(attributes.get("response_author_id"))
        answer = _row(
            ANSWER,
            livestorm_id,
            attributes.get("responded_at"),
            base,
            is_team=_is_team(responder_id, roster, True),  # the team answers questions
            author_id=responder_id,
            text=response,
            has_link=_has_link(response, response),
        )
        answer["_question"] = question
        answers.append(answer)

    ordered = _assign_ids(questions, "Q", 3) + _assign_ids(answers, "A", 3)
    for row in ordered:
        question = row.pop("_question", None)
        if question is not None:
            row["reply_to"] = question["eid"]
    return ordered


def audience_events(
    chat_payload: Any,
    questions_payload: Any,
    base: ClockBase,
    roster: Optional[Mapping[str, Any]] = None,
) -> pd.DataFrame:
    """Chat messages, questions and written answers as one table on the clock.

    Rows come in id order: chat, then questions, then answers. Columns are
    `EVIDENCE_COLUMNS`. `is_team` comes from the message flags for chat, is
    False for questions and True for answers. When `roster` maps an author's
    Livestorm people id to True or False (a boolean, or the string "true" or
    "false"), that decides instead. Any other roster value, such as a missing
    one, leaves the default.
    """
    rows = _chat_rows(chat_payload, base, roster) + _question_and_answer_rows(questions_payload, base, roster)
    return pd.DataFrame(
        {
            "eid": pd.Series([row["eid"] for row in rows], dtype=object),
            "kind": pd.Series([row["kind"] for row in rows], dtype=object),
            "livestorm_id": pd.Series([row["livestorm_id"] for row in rows], dtype=object),
            "reply_to": pd.Series([row["reply_to"] for row in rows], dtype=object),
            "at_unix": pd.Series([row["at_unix"] for row in rows], dtype="float64"),
            "t": pd.Series([row["t"] for row in rows], dtype="float64"),
            "phase": pd.Series([row["phase"] for row in rows], dtype=object),
            "minute": pd.Series([row["minute"] for row in rows], dtype="Int64"),
            "is_team": pd.Series([row["is_team"] for row in rows], dtype=bool),
            "author_id": pd.Series([row["author_id"] for row in rows], dtype=object),
            "text": pd.Series([row["text"] for row in rows], dtype=object),
            "has_link": pd.Series([row["has_link"] for row in rows], dtype=bool),
        },
        columns=list(EVIDENCE_COLUMNS),
    )
