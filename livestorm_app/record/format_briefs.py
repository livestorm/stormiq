"""The v1.1 public formats' briefs and the check on what their writer
returns (proposal §7): what the model that writes a format for the audience
reads (`ai/public_formats.py`, prompt `prompts/<kind>.txt`), and what code
checks in its answer before anything is saved.

A public format goes to attendees, so its brief holds what the session was
about, never how it ran: no event-craft finding (friction, prompts, the
longest monologue, time to answer) and no audience vital (show-up rate,
who wrote). The caller redacts the brief before it reaches a model
(`api_logic._public_redactor`: attendees' names, emails, phone numbers and
links become placeholders; speakers' saved names are kept), and checks the
answer for a leak after (`_require_no_leak`).

**The attendee follow-up** ("You asked, we answered";
`build_attendee_followup_brief`) holds, in this order:

- the session: its title, its date (in the session's time zone), and its
  length, also in whole minutes, so the email may say "30 minutes";
- what was covered: the chapters placed on the session clock
  (`digest_brief.chapter_lines`);
- the key moments of content: the moments the room reacted to the talk or
  answered a call from the stage (`record.moments.room_moments`, kinds
  `content` and `reply`), with what was being said, never who wrote
  (`digest_brief.moment_lines`);
- `MAX_ANSWERED` answered questions from the question desk
  (`record.question_desk.question_desk`: status written, aloud, here or
  covered, with words to quote: Livestorm's written answer, the host's
  reply, or the located passage of the talk), the most upvoted first, then
  in the desk's order (`answered_questions`). Never who asked, never the
  desk's ids.

**The check** (`check_format`, shared by every public format): every clock
time and number in the answer is in the brief, written the same way (a
small count, 0 to `FREE_COUNT` without a "%", is free, as in
`surprise_brief.check_surprise`); at most `max_words` words; each required
token (`REPLAY_LINK`) exactly once; no other `{{...}}` placeholder; and,
when asked, a first line starting a given way ("Subject:"). Anything else
raises `FormatRejected`, which names the kind of problem, never the text.

Pure functions: no database, network or web framework.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence

from livestorm_app.ai.dossier import Dossier, make_dossier
from livestorm_app.livestorm.payloads import extract_questions
from livestorm_app.record.digest_brief import chapter_lines, figures, moment_lines
from livestorm_app.record.session_brief import clock

FORMAT_BRIEF_VERSION = 1
FREE_COUNT = 10
MAX_ANSWERED = 2
MAX_TEXT_CHARS = 600

# The host's email tool fills it with the replay's link.
REPLAY_LINK = "{{replay_link}}"

# The moments that tell what the talk was about (`record.moments._kind`).
CONTENT_KINDS = ("content", "reply")

# How a question was answered, and which words answer it.
_ANSWERED_AS = {
    "written": "answered in writing during the session",
    "aloud": "answered aloud during the session",
    "here": "answered by the host after the session",
    "covered": "answered in the talk",
}

_PLACEHOLDER = re.compile(r"\{\{.*?\}\}", re.DOTALL)


class FormatRejected(ValueError):
    """A public format failed the check. The message says which kind of
    problem, never what the model wrote."""


def _one_line(value: Any, limit: int = MAX_TEXT_CHARS) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit].rstrip() + "…" if len(text) > limit else text


def session_date(started_at_unix: Any, zone: Optional[str] = None) -> Optional[str]:
    """"11 November 2026", in the session's time zone when it is a known
    one (else UTC); None without a start."""
    try:
        moment = datetime.fromtimestamp(float(started_at_unix), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    if zone:
        try:
            from zoneinfo import ZoneInfo

            moment = moment.astimezone(ZoneInfo(zone))
        except Exception:  # an unknown zone: the date in UTC
            pass
    return f"{moment.day} {moment.strftime('%B')} {moment.year}"


def content_moments(moments: Mapping[str, Any]) -> Dict[str, Any]:
    """`room_moments(...)` with only the moments of content (`CONTENT_KINDS`)."""
    if not isinstance(moments, Mapping) or moments.get("state") != "ready":
        return {"state": "none", "moments": []}
    kept = [m for m in moments.get("moments") or [] if isinstance(m, Mapping) and m.get("kind") in CONTENT_KINDS]
    return {"state": "ready", "moments": kept}


def question_up_votes(questions_payload: Any, events: Any) -> Dict[str, int]:
    """Each Q&A question's upvotes by its evidence id (`Q001`), read from
    the questions payload's `up_votes_count` through the events' Livestorm
    ids; a question without the count is absent."""
    by_livestorm_id: Dict[str, int] = {}
    for record in extract_questions(questions_payload):
        if not isinstance(record, Mapping):
            continue
        attributes = record.get("attributes") if isinstance(record.get("attributes"), Mapping) else {}
        votes = attributes.get("up_votes_count")
        if record.get("id") and isinstance(votes, int) and not isinstance(votes, bool):
            by_livestorm_id[str(record["id"])] = votes
    votes_by_eid: Dict[str, int] = {}
    if events is None or getattr(events, "empty", True) or "livestorm_id" not in events:
        return votes_by_eid
    for eid, livestorm_id in events[["eid", "livestorm_id"]].itertuples(index=False, name=None):
        if isinstance(livestorm_id, str) and livestorm_id in by_livestorm_id:
            votes_by_eid[str(eid)] = by_livestorm_id[livestorm_id]
    return votes_by_eid


def _answer_words(question: Mapping[str, Any]) -> Optional[str]:
    status = question.get("answered_as")
    located = question.get("located") if isinstance(question.get("located"), Mapping) else {}
    if status == "written":
        return question.get("answer") or None
    if status == "here":
        return question.get("reply") or located.get("quote") or None
    if status in ("aloud", "covered"):
        return located.get("quote") or None
    return None


def answered_questions(
    questions: Sequence[Mapping[str, Any]],
    up_votes: Optional[Mapping[str, int]] = None,
    limit: int = MAX_ANSWERED,
) -> List[Dict[str, Any]]:
    """Up to `limit` answered desk rows that have words answering them, the
    most upvoted first, then in the desk's order: `{text, answered_as,
    answer, t}` only (never the author or the desk's id). Each of
    `questions` is a desk row with its status as `answered_as` (`{id, text,
    answered_as, answer, reply, located}`); `up_votes` is by the row's id."""
    votes = up_votes or {}
    picked = []
    for index, question in enumerate(questions):
        if not isinstance(question, Mapping) or question.get("answered_as") not in _ANSWERED_AS:
            continue
        answer = _answer_words(question)
        if not _one_line(question.get("text")) or not _one_line(answer):
            continue
        located = question.get("located") if isinstance(question.get("located"), Mapping) else {}
        at = located.get("t") if answer == located.get("quote") else None
        picked.append((-int(votes.get(str(question.get("id")), 0)), index, {
            "text": question["text"], "answered_as": question["answered_as"], "answer": answer, "t": at,
        }))
    picked.sort(key=lambda item: (item[0], item[1]))
    return [row for _, _, row in picked[:limit]]


def _question_block(number: int, question: Mapping[str, Any]) -> str:
    how = _ANSWERED_AS.get(str(question.get("answered_as")), "answered")
    if question.get("t") is not None:
        how += f", at {clock(question['t'])}"
    return "\n".join(
        [
            f"### Question {number}",
            f"asked: \"{_one_line(question.get('text'))}\"",
            f"{how}: \"{_one_line(question.get('answer'))}\"",
        ]
    )


def attendee_followup_text(
    *,
    title: Optional[str],
    date: Optional[str],
    length_s: Any,
    chapters: Sequence[Mapping[str, Any]],
    moments: Mapping[str, Any],
    questions: Sequence[Mapping[str, Any]],
) -> str:
    """The attendee follow-up's brief as text. `questions` is
    `answered_questions(...)`; `moments` is `room_moments(...)`, filtered
    here to the moments of content."""
    try:
        minutes = round(float(length_s) / 60) if length_s is not None else None
    except (TypeError, ValueError):
        minutes = None
    length = f"{clock(length_s)} (about {minutes} minutes)" if minutes else "unknown"
    asked = "\n\n".join(_question_block(n, q) for n, q in enumerate(questions, start=1))
    sections = [
        "\n".join(
            [
                "# Attendee follow-up brief",
                f"version: {FORMAT_BRIEF_VERSION}",
                f"title: {_one_line(title, 200) or 'untitled'}",
                f"date: {date or 'unknown'}",
                f"length: {length}",
                "times: minutes:seconds from the start of the session",
            ]
        ),
        "## What was covered (the chapters)\n" + ("\n".join(chapter_lines(chapters)) or "- none placed"),
        "## Key moments of the talk (when the room reacted, and what was being said)\n"
        + ("\n".join(moment_lines(content_moments(moments))) or "- none found"),
        "## Questions the audience asked, with their answers\n" + (asked or "- none answered"),
    ]
    return "\n\n".join(sections)


def build_attendee_followup_brief(*, redact: Callable[[str], str] = lambda text: text, **parts: Any) -> Dossier:
    """The attendee follow-up's brief (`attendee_followup_text`), passed
    through `redact` (the session's `Redactor.redact`) before it is sealed,
    so one session gives the same bytes every time."""
    text = redact(attendee_followup_text(**parts))
    return make_dossier(text, record_version=f"attendee-followup-brief-{FORMAT_BRIEF_VERSION}")


# ── The check ──


def check_tokens(text: str, required_tokens: Iterable[str] = ()) -> str:
    """`text` without its required tokens, when each is in it exactly once
    and it holds no other `{{...}}` placeholder; else `FormatRejected`. A
    translation is held to this part of the check."""
    rest = str(text or "")
    for token in required_tokens:
        if rest.count(token) != 1:
            raise FormatRejected(f"doesn't hold {token} exactly once")
        rest = rest.replace(token, " ")
    if _PLACEHOLDER.search(rest):
        raise FormatRejected("holds a placeholder it may not use")
    return rest


def check_format(
    text: str,
    brief_text: str,
    *,
    max_words: int,
    required_tokens: Iterable[str] = (),
    starts_with: Optional[str] = None,
    free_count: int = FREE_COUNT,
) -> str:
    """The format's text, stripped, when it passes every check (module
    docstring); else `FormatRejected`."""
    body = str(text or "").strip()
    if not body:
        raise FormatRejected("is empty")
    if starts_with is not None:
        first = next((line.strip() for line in body.splitlines() if line.strip()), "")
        if not first.lstrip("*_# ").lower().startswith(starts_with.lower()):
            raise FormatRejected(f"doesn't start with its {starts_with} line")
    if len(body.split()) > max_words:
        raise FormatRejected(f"is longer than {max_words} words")
    rest = check_tokens(body, required_tokens)
    source = "\n".join(line for line in str(brief_text or "").splitlines() if not line.startswith("version:"))
    brief_times, brief_numbers = figures(source)
    plain = {value for value, _ in brief_numbers}
    times, numbers = figures(rest)
    if times - brief_times:
        raise FormatRejected("gives a time that isn't in the session")
    for value, percent in numbers:
        if percent:
            if (value, True) not in brief_numbers:
                raise FormatRejected("gives a percentage that isn't in the session")
            continue
        small = value.isdigit() and int(value) <= free_count
        if not small and value not in plain:
            raise FormatRejected("gives a number that isn't in the session")
    return body
