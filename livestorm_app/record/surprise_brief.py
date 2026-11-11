"""Surprise Me! (the Smart Recap's `surprise` tone) grounded in the record
(proposal §10): what StormIQ measured, given next to the transcript, and the
take checked against both before it is saved.

- **The facts** (`build_surprise_brief`): the session's title and length;
  the vitals the Report shows (`digest_brief.vital_lines`, the estimated
  ones marked); the key moments with what was being said
  (`digest_brief.moment_lines`, never who); the chapters placed on the
  session clock; and **From the room** (`room_lines`): a handful of the
  most telling audience chat messages (what the key moments' people wrote,
  `record.moments.room_moments`, the moments of content first, greetings
  and one- or two-word messages left out) and the top audience questions
  (the question desk's, the most upvoted first), each with its time on the
  session clock and never who wrote it. Each line is redacted as it is
  built (the caller's `redact`, `api_logic._public_redactor`): names,
  emails, phone numbers and links never reach a model. The whole brief is
  redacted again after, with the transcript (`record.redaction`).
- **The check** (`check_surprise`): every clock time and every number in
  the take must be in the facts or the transcript, written the same way. A
  number said aloud counts too: the transcript often spells it ("fifteen",
  "vingt-cinq"), the take writes "15" (`spoken_numbers`, English and
  French). A small count (0 to 10, no "%") is free: "for the 3rd time" is a
  joke's rhythm, not a statistic. A percentage must match one given.
  Anything else raises `SurpriseRejected`, which names the kind of figure,
  never the text. The room's lines are part of the facts, so a number or a
  time in a chat message or a question may be quoted too.

Pure functions: no database, network or web framework.
"""

from __future__ import annotations

import math
import re
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from livestorm_app.record.digest_brief import chapter_lines, figures, moment_lines, vital_lines
from livestorm_app.record.session_brief import clock

SURPRISE_BRIEF_VERSION = 2
FREE_COUNT = 10

# "From the room": at most this many lines, chat messages and questions.
MAX_ROOM_LINES = 8
MAX_ROOM_CHAT = 5
MAX_ROOM_QUESTIONS = 3
# A chat message shorter than this says little ("hi", "+1", "so true").
MIN_CHAT_WORDS = 3
MAX_ROOM_CHARS = 200
# The moments whose messages tell the story first (`record.moments._kind`):
# the room reacting to the talk or answering the stage, then the rest.
STORY_KINDS = ("content", "reply")


# Number words, English and French, by value. Scales multiply what comes
# before them ("two hundred", "trois mille"); "quatre-vingt" is 80.
_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "zéro": 0, "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "sept": 7, "huit": 8,
    "neuf": 9, "dix": 10, "onze": 11, "douze": 12, "treize": 13, "quatorze": 14, "quinze": 15, "seize": 16,
    "vingt": 20, "vingts": 20, "trente": 30, "quarante": 40, "cinquante": 50, "soixante": 60,
}
_SCALES = {"hundred": 100, "cent": 100, "cents": 100, "thousand": 1000, "mille": 1000, "million": 1_000_000, "millions": 1_000_000}
_JOINERS = {"and", "et"}
# A word, or a mark that ends a number (anything but a letter, a space or a
# hyphen: "soixante-dix, trois mille" is two numbers).
_WORD = re.compile(r"[a-zà-ÿ]+|[^\sa-zà-ÿ-]")


def spoken_numbers(text: str) -> Set[str]:
    """The numbers spelled out in `text` ("twenty-five", "two hundred",
    "quatre-vingt-dix"), as digits ("25", "200", "90"). Each run of number
    words is one number; any other word ends it."""
    found: Set[str] = set()
    total = current = 0
    previous: Optional[int] = None
    active = False

    def close() -> None:
        nonlocal total, current, active, previous
        if active:
            found.add(str(total + current))
        total = current = 0
        active = False
        previous = None

    last = ""
    for word in _WORD.findall(str(text or "").lower()):
        if word == "cent" and last == "pour":  # "pour cent" is "percent", not 100
            close()
            last = word
            continue
        last = word
        if word in _UNITS:
            value = _UNITS[word]
            if value == 20 and previous == 4:  # quatre-vingt
                current += 80 - 4
            else:
                current += value
            previous, active = value, True
        elif word in _SCALES and (active or word in ("hundred", "thousand", "cent", "mille")):
            scale = _SCALES[word]
            if scale == 100:
                current = max(current, 1) * 100
            else:
                total += max(current, 1) * scale
                current = 0
            previous, active = scale, True
        elif word in _JOINERS and active:
            continue
        else:
            close()
    close()
    return found


class SurpriseRejected(ValueError):
    """The take failed the check; the message says which kind of figure."""


def _one_line(value: Any, limit: int = MAX_ROOM_CHARS) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit].rstrip() + "…" if len(text) > limit else text


def _finite_time(value: Any) -> Optional[float]:
    """A time on the session clock (seconds, 0 or more), or None."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def room_lines(
    moments: Mapping[str, Any],
    questions: Sequence[Mapping[str, Any]],
    up_votes: Optional[Mapping[str, int]] = None,
    *,
    redact: Callable[[str], str] = lambda text: text,
) -> List[Dict[str, Any]]:
    """The brief's "From the room": up to `MAX_ROOM_CHAT` chat messages and
    `MAX_ROOM_QUESTIONS` questions, at most `MAX_ROOM_LINES`, in time order,
    numbered `R1`, `R2`…: `{id, kind, t, text, votes}` (`kind` chat or
    question; `votes` a Q&A question's upvotes, else None). Never who wrote.

    - chat: what the key moments' people wrote (`moments["moments"][i]
      ["written"]`, already the longest distinct messages without a link),
      the moments of content first (`STORY_KINDS`), then the busiest; at
      least `MIN_CHAT_WORDS` words;
    - questions: the question desk's rows (`question_desk.audience_questions`:
      `{id, t, text}`), the most upvoted first, then in the desk's order.

    Each text is redacted by `redact` before anything else reads it, so two
    messages that differ only by a name count once."""
    votes = up_votes or {}
    seen: Set[str] = set()

    def fresh(text: str) -> bool:
        key = " ".join(text.casefold().split())
        if not key or key in seen:
            return False
        seen.add(key)
        return True

    chat: List[Tuple[float, str]] = []
    if isinstance(moments, Mapping) and moments.get("state") == "ready":
        ranked = sorted(
            (m for m in moments.get("moments") or [] if isinstance(m, Mapping)),
            key=lambda m: (m.get("kind") not in STORY_KINDS, -int(m.get("messages") or 0), _finite_time(m.get("start")) or 0.0),
        )
        for moment in ranked:
            for line in moment.get("written") or []:
                if len(chat) == MAX_ROOM_CHAT:
                    break
                t = _finite_time(line.get("t")) if isinstance(line, Mapping) else None
                text = _one_line(redact(str(line.get("text") or ""))) if t is not None else ""
                if len(text.split()) >= MIN_CHAT_WORDS and fresh(text):
                    chat.append((t, text))

    asked: List[Tuple[float, str, Optional[int]]] = []
    order = sorted(
        (q for q in questions or [] if isinstance(q, Mapping)),
        key=lambda q: -int(votes.get(str(q.get("id")), 0)),
    )
    for question in order:
        if len(asked) == MAX_ROOM_QUESTIONS:
            break
        t = _finite_time(question.get("t"))
        text = _one_line(redact(str(question.get("text") or ""))) if t is not None else ""
        if text and fresh(text):
            count = votes.get(str(question.get("id")))
            asked.append((t, text, int(count) if isinstance(count, int) and not isinstance(count, bool) else None))

    rows = [{"kind": "chat", "t": t, "text": text, "votes": None} for t, text in chat]
    rows += [{"kind": "question", "t": t, "text": text, "votes": count} for t, text, count in asked]
    rows = sorted(rows, key=lambda row: (row["t"], row["kind"], row["text"]))[:MAX_ROOM_LINES]
    return [{"id": f"R{number}", **row, "t": round(row["t"], 1)} for number, row in enumerate(rows, start=1)]


def room_line_text(row: Mapping[str, Any], *, with_id: bool = False) -> str:
    """One "From the room" line: "- 12:34 chat: "…"", a question with its
    upvotes when it has some; `with_id` puts its id first."""
    label = "question" if row.get("kind") == "question" else "chat"
    votes = row.get("votes")
    if label == "question" and isinstance(votes, int) and votes > 0:
        label += f" ({votes} {'upvote' if votes == 1 else 'upvotes'})"
    lead = f"{row['id']} " if with_id else ""
    return f"- {lead}{clock(row.get('t'))} {label}: \"{row.get('text')}\""


def build_surprise_brief(
    *,
    title: str,
    length_s: Optional[float],
    measures: Mapping[str, Any],
    moments: Mapping[str, Any],
    chapters: Sequence[Mapping[str, Any]],
    room: Sequence[Mapping[str, Any]] = (),
) -> str:
    """The facts a surprise take may use, as plain text: title, length,
    vitals, key moments, chapters and what the room wrote (`room_lines`,
    already redacted), each part "none" when empty."""
    parts = [
        "# What StormIQ measured in this session",
        f"version: {SURPRISE_BRIEF_VERSION}",
        f"title: {' '.join(str(title or 'Untitled session').split())}",
        f"length: {clock(length_s) if length_s else 'unknown'}",
        "times: minutes:seconds from the start of the session",
        "",
        "## Vitals (quote them as written, never compute others)",
        "\n".join(vital_lines(measures, None)) or "- none measured",
        "",
        "## When the chat was busiest",
        "\n".join(moment_lines(moments)) or "- none found",
        "",
        "## Chapters",
        "\n".join(chapter_lines(chapters)) or "- none placed",
        "",
        "## From the room (what the audience wrote; use it only when it makes the story better, never say who wrote it)",
        "\n".join(room_line_text(row) for row in room) or "- nothing quoted",
    ]
    return "\n".join(parts)


def check_surprise(text: str, allowed: str) -> str:
    """The take when every time and number in it is in `allowed` (the facts
    and the transcript, as sent); else `SurpriseRejected`."""
    take = str(text or "").strip()
    if not take:
        raise SurpriseRejected("is empty")
    source = "\n".join(line for line in str(allowed or "").splitlines() if not line.startswith("version:"))
    allowed_times, allowed_numbers = figures(source)
    plain = {value for value, _ in allowed_numbers} | spoken_numbers(source)
    times, numbers = figures(take)
    if times - allowed_times:
        raise SurpriseRejected("gives a time that isn't in the session")
    for value, percent in numbers:
        if percent:
            if (value, True) not in allowed_numbers:
                raise SurpriseRejected("gives a percentage that isn't in the session")
            continue
        small = value.isdigit() and int(value) <= FREE_COUNT
        if not small and value not in plain:
            raise SurpriseRejected("gives a number that isn't in the session")
    return take
