"""The team digest's brief and its checks: what the model that writes the
Follow-up tab's "Team digest" (`ai/team_digest.py`, prompt
`prompts/team_digest.txt`) reads, and what code checks in its answer before
anything is saved.

The brief holds, in this order:

- the session: its title, length and whether it is internal;
- the vitals, as the Report shows them (`record.measures`): only measures
  whose value the Report shows (`ok`; `partial` and `approximation`, which
  it marks "Estimated", marked "(estimated)"; never a hidden value), each
  written as the digest may quote it (a share as
  "71%", a count as "5", a time as "22:30"): show-up rate, the share of
  attendees who wrote, the audience's questions, how many are still open on
  the question desk, and the median time in the room;
- the key moments (`record.moments.room_moments`): their span on the
  session clock, kind, message count and what was being said (never who);
- the chapters, when they are placed on the session clock;
- up to `MAX_OPEN_QUESTIONS` open questions' texts (never who asked);
- the decisions and commitments, when they were listed
  (`record.decisions.decision_lines`: what and when, never an owner or a
  speaker).

Never a name, an email, a company, a people id or a `P-` ref. The speakers'
names the host saved are replaced wherever the talk says them
(`hidden_names`). Built with `ai.dossier.make_dossier`, so one session gives
the same bytes every time.

**The checks** (`check_digest`): the digest's first line marks it internal
(`INTERNAL_MARKER`); every number and every clock time in it is in the
brief (a share with its "%", "41 %" and "1,200" read as "41%" and "1200");
it is at most `MAX_WORDS` words. Anything else raises `DigestRejected`,
whose message never holds the model's words.

Pure: no database, network or model call.
"""

from __future__ import annotations

import math
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from livestorm_app.ai.dossier import Dossier, make_dossier
from livestorm_app.record.session_brief import clock

DIGEST_BRIEF_VERSION = 1
MAX_OPEN_QUESTIONS = 3
MAX_SAID_CHARS = 240
MAX_TEXT_CHARS = 240
# About 100 words are asked for; the cap leaves room for the marker and bullets.
MAX_WORDS = 180

INTERNAL_MARKER = "*Internal: not for sharing outside the team.*"
_MARKER_WORDS = "internal: not for sharing outside the team"

# What a key moment's kind means (`record.moments._kind`).
_MOMENT_KIND = {
    "reply": "the room answering a call from the stage",
    "tech": "sound or connection trouble",
    "social": "greetings and logistics",
    "content": "reacting to the talk",
    "unknown": "chat",
}


class DigestRejected(ValueError):
    """The model's digest failed a check. The message says which check,
    never what the model wrote."""


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _one_line(value: Any, limit: int = MAX_TEXT_CHARS) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit].rstrip() + "…" if len(text) > limit else text


# The states whose value the Report shows: `ok`, and `partial` or
# `approximation`, which it marks "Estimated". `insufficient`, `unresolved`
# and `unavailable` hide the value, so the brief leaves it out too.
SHOWN_STATES = ("ok", "partial", "approximation")
ESTIMATED = " (estimated)"


def _shown(measures: Mapping[str, Any], measure_id: str) -> Optional[Mapping[str, Any]]:
    measure = measures.get(measure_id)
    return measure if isinstance(measure, Mapping) and measure.get("state") in SHOWN_STATES else None


def _mark(measure: Mapping[str, Any]) -> str:
    return "" if measure.get("state") == "ok" else ESTIMATED


def _percent(value: Any) -> Optional[str]:
    number = _finite(value)
    return None if number is None else f"{round(number * 100)}%"


def vital_lines(measures: Mapping[str, Any], questions_open: Optional[int]) -> List[str]:
    """The vitals, one line each, from the measures whose value the Report
    shows (`SHOWN_STATES`), those it marks "Estimated" marked "(estimated)";
    `questions_open` is the question desk's count of open questions."""
    lines: List[str] = []
    m = _shown(measures, "m.show_up_rate")
    if m and _percent(m.get("value")):
        lines.append(f"- Show-up rate: {_percent(m['value'])} of those registered came live{_mark(m)}")
    m = _shown(measures, "m.wrote_rate")
    if m and _percent(m.get("value")):
        lines.append(f"- Attendees who wrote in the chat or the Q&A: {_percent(m['value'])}{_mark(m)}")
    m = _shown(measures, "m.questions_total")
    if m and _finite(m.get("value")) is not None:
        lines.append(f"- Audience questions (Q&A and chat): {int(m['value'])}{_mark(m)}")
        if isinstance(questions_open, int) and questions_open >= 0:
            lines.append(f"- Questions still open on the question desk: {questions_open}")
    m = _shown(measures, "m.time_in_room")
    if m:
        seconds = _finite((m.get("coverage") or {}).get("medianAttendanceS"))
        share = _percent(m.get("value"))
        if seconds is not None and share:
            lines.append(f"- Median time in the room: {clock(seconds)}, {share} of the session{_mark(m)}")
    return lines


def moment_lines(moments: Mapping[str, Any]) -> List[str]:
    """The key moments (`room_moments(...)`, state `ready`): span, kind,
    messages and what was being said, without the speakers."""
    if not isinstance(moments, Mapping) or moments.get("state") != "ready":
        return []
    lines = []
    for moment in moments.get("moments") or []:
        if not isinstance(moment, Mapping):
            continue
        count = int(moment.get("messages") or 0)
        line = (
            f"- {clock(moment.get('start'))} to {clock(moment.get('end'))}: "
            f"{count} {'message' if count == 1 else 'messages'}, {_MOMENT_KIND.get(str(moment.get('kind')), 'chat')}"
        )
        said = moment.get("said")
        if isinstance(said, Mapping) and said.get("text"):
            line += f"; being said on stage: \"{_one_line(said['text'], MAX_SAID_CHARS)}\""
        lines.append(line)
    return lines


def chapter_lines(chapters: Sequence[Mapping[str, Any]]) -> List[str]:
    """Placed chapters, `{title, t}` with `t` on the session clock."""
    return [
        f"- {clock(c.get('t'))} {_one_line(c.get('title'), 120)}"
        for c in chapters
        if isinstance(c, Mapping) and c.get("title") and _finite(c.get("t")) is not None
    ]


def _redact(text: str, hidden_names: Iterable[str]) -> str:
    """`text` with each hidden name, and each word of it, replaced."""
    words: Set[str] = set()
    for name in hidden_names:
        full = " ".join(str(name or "").split())
        if full:
            words.add(full)
            words.update(part for part in full.split() if len(part) >= 2)
    for word in sorted(words, key=lambda w: (-len(w), w)):
        text = re.sub(rf"(?<!\w){re.escape(word)}(?!\w)", "a speaker", text, flags=re.IGNORECASE)
    return text


def build_digest_brief(
    *,
    title: Optional[str],
    length_s: Any,
    internal: bool,
    measures: Mapping[str, Any],
    questions_open: Optional[int],
    moments: Mapping[str, Any],
    chapters: Sequence[Mapping[str, Any]],
    open_questions: Sequence[str],
    decisions: Optional[Sequence[Mapping[str, Any]]],
    hidden_names: Iterable[str] = (),
) -> Dossier:
    """The team digest's brief. `decisions` is `record.decisions.
    decision_lines` of the English list, or None when it wasn't listed."""
    if decisions is None:
        decided = ["- not listed"]
    else:
        decided = [
            f"- {d['kind']}: {_one_line(d['text'])}" + (f" Due: {_one_line(d['due'], 80)}." if d.get("due") else "")
            for d in decisions
        ] or ["- none stated on stage"]
    sections = [
        "\n".join(
            [
                "# Team digest brief",
                f"version: {DIGEST_BRIEF_VERSION}",
                f"title: {_one_line(title, 200) or 'untitled'}",
                f"length: {clock(length_s)}",
                f"audience: {'an internal meeting, everyone is a team member' if internal else 'a public session'}",
                "times: minutes:seconds from the start of the session",
            ]
        ),
        "## Vitals (quote them as written, never compute others)\n" + ("\n".join(vital_lines(measures, questions_open)) or "- none measured"),
        "## Key moments (when the chat was busiest)\n" + ("\n".join(moment_lines(moments)) or "- none found"),
        "## Chapters\n" + ("\n".join(chapter_lines(chapters)) or "- none placed"),
        "## Open questions\n"
        + ("\n".join(f"- \"{_one_line(q)}\"" for q in list(open_questions)[:MAX_OPEN_QUESTIONS] if _one_line(q)) or "- none"),
        "## Decisions and commitments\n" + "\n".join(decided),
    ]
    text = _redact("\n\n".join(sections), list(hidden_names))
    return make_dossier(text, record_version=f"digest-brief-{DIGEST_BRIEF_VERSION}")


# ── The checks ──

# "12:05" or "1:02:05", also before a colon ("2:00: 3 messages").
_TIME = re.compile(r"(?<![\d.,])(?<!\d:)\d{1,2}(?::[0-5]\d){1,2}(?!\d)(?!:\d)")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?(?:\s?%)?")


def _seconds(stamp: str) -> int:
    total = 0
    for part in stamp.split(":"):
        total = total * 60 + int(part)
    return total


def _canonical(token: str) -> Optional[Tuple[str, bool]]:
    """("1200", False) for "1,200"; ("41", True) for "41 %"; None if no number."""
    percent = token.rstrip().endswith("%")
    digits = token.rstrip("% ").rstrip(",").replace(",", "")
    try:
        value = Decimal(digits)
    except InvalidOperation:
        return None
    text = format(value.normalize(), "f") if value != value.to_integral_value() else str(int(value))
    return text, percent


def figures(text: str) -> Tuple[Set[int], Set[Tuple[str, bool]]]:
    """The clock times (in seconds) and numbers (canonical, with whether a
    "%" follows) in `text`. Times are read first, so their digits are not
    numbers too."""
    times = {_seconds(stamp) for stamp in _TIME.findall(text)}
    rest = _TIME.sub(" ", text)
    numbers = {found for found in (_canonical(token) for token in _NUMBER.findall(rest)) if found}
    return times, numbers


def _first_line(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


def check_digest(text: str, brief_text: str) -> str:
    """The digest, its first line set to `INTERNAL_MARKER`, when it passes
    every check; else `DigestRejected`."""
    digest = str(text or "").strip()
    if not digest:
        raise DigestRejected("is empty")
    marker = _first_line(digest).strip("*_ ").rstrip(".").casefold()
    if marker != _MARKER_WORDS:
        raise DigestRejected("doesn't start by marking itself internal")
    if len(digest.split()) > MAX_WORDS:
        raise DigestRejected(f"is longer than {MAX_WORDS} words")
    allowed = "\n".join(line for line in brief_text.splitlines() if not line.startswith("version:"))
    brief_times, brief_numbers = figures(allowed)
    plain = {value for value, _ in brief_numbers}
    times, numbers = figures(digest)
    if times - brief_times:
        raise DigestRejected("gives a time that isn't in its brief")
    for value, percent in numbers:
        if (value, True) not in brief_numbers if percent else value not in plain:
            raise DigestRejected("gives a number that isn't in its brief")
    lines = digest.splitlines()
    first = next(index for index, line in enumerate(lines) if line.strip())
    return "\n".join([INTERNAL_MARKER, *lines[first + 1 :]]).strip()
