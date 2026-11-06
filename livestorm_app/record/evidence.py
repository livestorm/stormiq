"""Audience evidence: chat, questions and written answers on the session clock.

`audience_events` turns the cached chat and questions payloads into one table,
with an evidence id on every row:

- `C0001`, `C0002`, ... for chat messages, ordered by `(created_at,
  livestorm_id, text)`;
- `Q001`, ... for questions, ordered the same way;
- `A001`, ... for written answers (a question with response text), ordered by
  `(responded_at, livestorm_id, text)`.

Ids depend on the payloads only, never on the clock or the authors, so two
parses of the same payloads give the same ids. Payload order doesn't change
them either, with one exception: when a Livestorm id repeats with different
content, the first copy is kept. Each row keeps its Livestorm id: anything
stored later should record both. Times are read from the raw payload values,
never from the formatted strings of the classic tables.

`chat_items` and `question_items` are the only readers of those payloads in
`record/`: `audience_events` and `authors.resolve_authors` both read through
them, so both count the same items and the same author ids. A chat
message's author is its people link (`people_link`), never its `author_id`
attribute, which is another id. Given an `authors.AuthorIndex`, each row
also gets its author's `P-` ref when the author resolved to a person.

Pure functions: no database, network or web framework.
"""

from __future__ import annotations

import html
import re
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, NamedTuple, Optional

import pandas as pd

from livestorm_app.livestorm.payloads import extract_messages, extract_questions
from livestorm_app.record.clock import ClockBase, minute_index, parse_time, phase
from livestorm_app.record.roster import GUEST_SPEAKER, TEAM

if TYPE_CHECKING:
    from livestorm_app.record.authors import Author, AuthorIndex

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
    "author_id",  # the author's people id (a chat message's people link); None without one
    "author_ref",  # the author's P- ref when it resolved to a roster person (authors.py); None otherwise
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
    """A Livestorm id as stripped text; None when missing, blank or a boolean."""
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


# ── Items: the one reading of the chat and questions payloads ──────────────


class ChatItem(NamedTuple):
    """One chat message, as the record reads it."""

    livestorm_id: Optional[str]
    created_at: Any  # as sent
    author_id: Optional[str]  # the author's people id (`people_link`); None without one
    team_flag: bool  # `from_team_member` (true, or the string "true")
    guest_flag: bool  # `from_guest_speaker`
    text: str  # `text_content`, or the text of `html_content`
    markup: str  # `html_content`


class QuestionItem(NamedTuple):
    """One question and its written answer, as the record reads them."""

    livestorm_id: Optional[str]
    created_at: Any  # as sent
    asker_id: Optional[str]  # `question_author_id`: a people id
    text: str
    responded_at: Any  # as sent
    responder_id: Optional[str]  # `response_author_id`: a people id
    response: str  # the written answer; "" when unanswered or answered orally only
    answered_orally: bool = False  # `response_orally`, else `responded_orally` (`oral_flag`)


def people_link(record: Mapping[str, Any]) -> Optional[str]:
    """A chat message's author as a people id: `relationships.people.data.id`,
    whose `type` is "people".

    Livestorm sends it when the chat is fetched with `include=people`
    (`livestorm/client.fetch_chat_messages`). A chat cached before that has
    no relationships, so this is None. The message's `author_id` attribute
    is another id: it is never read as a people id.
    """
    relationships = record.get("relationships")
    people = relationships.get("people") if isinstance(relationships, Mapping) else None
    data = people.get("data") if isinstance(people, Mapping) else None
    if not isinstance(data, Mapping) or _text(data.get("type")) != "people":
        return None
    return _clean_id(data.get("id"))


def chat_items(chat_payload: Any) -> List[ChatItem]:
    """The chat's messages in payload order; a repeated Livestorm id counts once."""
    items = []
    for record in _unique_records(extract_messages(chat_payload)):
        attributes = _attributes(record)
        markup = _text(attributes.get("html_content"))
        team_flag, guest_flag = (_flag(attributes.get(name)) for name in TEAM_MESSAGE_FLAGS)
        items.append(
            ChatItem(
                livestorm_id=_clean_id(record.get("id")),
                created_at=attributes.get("created_at"),
                author_id=people_link(record),
                team_flag=team_flag,
                guest_flag=guest_flag,
                text=_text(attributes.get("text_content")) or _html_to_text(markup),
                markup=markup,
            )
        )
    return items


def oral_flag(attributes: Mapping[str, Any]) -> bool:
    """Whether a question was answered orally (live). The API spells the
    field `response_orally`; older payloads spell it `responded_orally`. The
    API's spelling wins when it is sent, as in the classic rule
    (`analytics/audience._answered_orally_series`)."""
    value = attributes.get("response_orally")
    if value is None:
        value = attributes.get("responded_orally")
    return _flag(value)


def question_items(questions_payload: Any) -> List[QuestionItem]:
    """The questions in payload order; a repeated Livestorm id counts once."""
    items = []
    for record in _unique_records(extract_questions(questions_payload)):
        attributes = _attributes(record)
        items.append(
            QuestionItem(
                livestorm_id=_clean_id(record.get("id")),
                created_at=attributes.get("created_at"),
                asker_id=_clean_id(attributes.get("question_author_id")),
                text=_text(attributes.get("question")),
                responded_at=attributes.get("responded_at"),
                responder_id=_clean_id(attributes.get("response_author_id")),
                response=_text(attributes.get("response")),
                answered_orally=oral_flag(attributes),
            )
        )
    return items


# ── The table ───────────────────────────────────────────────────────────────


def _resolved(kind: str, author_id: Optional[str], authors: Optional[AuthorIndex]) -> Optional[Author]:
    """The item's author when it resolved to a person, or None."""
    if authors is None:
        return None
    author = authors.author(kind, author_id)
    return author if author is not None and author.person_id is not None else None


def _resolved_to_team(author: Optional[Author]) -> bool:
    """True for an author resolved to a team member or a guest speaker."""
    return author is not None and author.role in (TEAM, GUEST_SPEAKER)


def _ref(author: Optional[Author]) -> Optional[str]:
    return author.person_ref if author is not None else None


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


def _chat_rows(chat_payload: Any, base: ClockBase, authors: Optional[AuthorIndex]) -> List[Dict[str, Any]]:
    rows = []
    for item in chat_items(chat_payload):
        rows.append(
            _row(
                CHAT,
                item.livestorm_id,
                item.created_at,
                base,
                # A chat message's own flags decide, resolved or not.
                is_team=item.team_flag or item.guest_flag,
                author_id=item.author_id,
                author_ref=_ref(_resolved(CHAT, item.author_id, authors)),
                text=item.text,
                has_link=_has_link(item.text, item.markup),
            )
        )
    return _assign_ids(rows, "C", 4)


def _question_and_answer_rows(
    questions_payload: Any, base: ClockBase, authors: Optional[AuthorIndex]
) -> List[Dict[str, Any]]:
    questions = []
    answers = []
    for item in question_items(questions_payload):
        asker = _resolved(QUESTION, item.asker_id, authors)
        question = _row(
            QUESTION,
            item.livestorm_id,
            item.created_at,
            base,
            is_team=_resolved_to_team(asker),
            author_id=item.asker_id,
            author_ref=_ref(asker),
            text=item.text,
            has_link=_has_link(item.text, item.text),
        )
        questions.append(question)

        if not item.response:
            continue  # unanswered, or answered orally only
        responder = _resolved(ANSWER, item.responder_id, authors)
        answer = _row(
            ANSWER,
            item.livestorm_id,
            item.responded_at,
            base,
            # Always team, whatever the responder's role: one who reads as
            # audience is most likely a team member registered as a participant.
            is_team=True,
            author_id=item.responder_id,
            author_ref=_ref(responder),
            text=item.response,
            has_link=_has_link(item.response, item.response),
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
    authors: Optional[AuthorIndex] = None,
) -> pd.DataFrame:
    """Chat messages, questions and written answers as one table on the clock.

    Rows come in id order: chat, then questions, then answers. Columns are
    `EVIDENCE_COLUMNS`.

    `is_team`: a chat message's own flags (`from_team_member` or
    `from_guest_speaker`) decide, whether or not its author resolved. A
    question is team when its asker resolved (`authors`, from
    `authors.resolve_authors`) to a team member or a guest speaker, and not
    team otherwise. A written answer is always team.

    `author_id` is the author's people id: a chat message's people link
    (`people_link`), a question's `question_author_id`, an answer's
    `response_author_id`; None without one. `author_ref` is the resolved
    author's `P-` ref, and None without `authors` or when the author didn't
    resolve.
    """
    rows = _chat_rows(chat_payload, base, authors) + _question_and_answer_rows(questions_payload, base, authors)
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
            "author_ref": pd.Series([row["author_ref"] for row in rows], dtype=object),
            "text": pd.Series([row["text"] for row in rows], dtype=object),
            "has_link": pd.Series([row["has_link"] for row in rows], dtype=bool),
        },
        columns=list(EVIDENCE_COLUMNS),
    )
