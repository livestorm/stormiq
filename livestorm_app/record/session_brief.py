"""The session analysis's brief: what StormIQ computed and checked about one
session, written out for the model that writes the Follow-up tab's
"Session analysis" (`ai/session_analysis.py`, prompt
`prompts/session_analysis.txt`).

The brief replaces the raw Livestorm payloads the classic overall analysis
read. It holds, in this order:

- the session: its title, length, language and whether it is internal;
- the numbers, as the Report shows them (`record.measures`): only measures
  whose state lets them be shown (`ok`, `partial`, `approximation`), worded
  with their unit, so the model quotes them and never works one out;
- the chat: the busiest live minutes of the audience's chat
  (`record.pulse`) and what it was about (the chat labels' classes);
- the chapters, the moments the speakers asked the room to respond, and the
  figures said on stage with their flags, from the stored understanding
  (`understanding.current_reading`: only while it was read from the session
  as it is now);
- the audience's questions with their status (`question_desk`), and where
  the talk answered them;
- the transcript, by speaker key.

Never a name, an email, a company, a people id or a `P-` ref: speakers are
`S0`, `S1`… as in the understanding's dossier, and questions carry no
author. Times are on the session clock when the understanding was read on
it, else on the recording's. Built with `ai.dossier.make_dossier`, so one
session gives the same bytes every time.

Pure: no database, network or model call.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence

from livestorm_app.ai.dossier import Dossier, make_dossier

BRIEF_VERSION = 1

SHOWN_STATES = ("ok", "partial", "approximation")
BUSIEST_MINUTES = 5
# A speaker under this share of the talk (a "right, exactly") isn't listed.
MIN_TALK_SHARE = 0.01
MAX_QUESTION_CHARS = 400

# What a question's desk status says, for the model.
_QUESTION_STATUS = {
    "written": "answered in writing in Livestorm",
    "aloud": "marked answered aloud in Livestorm",
    "here": "marked answered by the host in StormIQ",
    "covered": "not marked, but the talk seems to answer it",
    "open": "still open",
}

# What each chat label class means (`record.chat_labels.LABEL_CLASSES`).
_CLASS_WORDS = {
    "content": "reacting to what was said or shown",
    "question": "questions",
    "tech": "sound, video or connection issues",
    "logistics": "replay, slides or schedule",
    "social": "greetings and thanks",
    "laughter": "laughter",
    "off_topic": "off topic",
}


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def clock(seconds: Any) -> str:
    """"5:19" or "1:02:05"; "-" without a time; "before" before the start."""
    value = _finite(seconds)
    if value is None:
        return "-"
    if value < 0:
        return "before"
    total = int(value)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _percent(value: Any) -> Optional[str]:
    number = _finite(value)
    return None if number is None else f"{round(number * 100)}%"


def _shown(measures: Mapping[str, Any], measure_id: str) -> Optional[Mapping[str, Any]]:
    measure = measures.get(measure_id)
    if not isinstance(measure, Mapping) or measure.get("state") not in SHOWN_STATES:
        return None
    return measure


def _about(measure: Mapping[str, Any]) -> str:
    return "about " if measure.get("state") == "approximation" else ""


def number_lines(measures: Mapping[str, Any]) -> List[str]:
    """The Report's numbers as lines, each with its unit: only the measures
    the Report would show."""
    lines: List[str] = []
    m = _shown(measures, "m.audience_registrants")
    if m and _finite(m.get("value")) is not None:
        lines.append(f"- Registered (audience): {int(m['value'])}")
    m = _shown(measures, "m.audience_attendees")
    if m and _finite(m.get("value")) is not None:
        line = f"- Attended live (audience): {int(m['value'])}"
        rate = _shown(measures, "m.show_up_rate")
        if rate and _percent(rate.get("value")):
            line += f", {_about(rate)}{_percent(rate['value'])} of those registered"
        lines.append(line)
    m = _shown(measures, "m.time_in_room")
    if m and _percent(m.get("value")):
        lines.append(f"- Median time in the room: {_about(m)}{_percent(m['value'])} of the session")
    m = _shown(measures, "m.wrote_rate")
    if m and _percent(m.get("value")):
        lines.append(f"- Attendees who wrote in the chat or the Q&A: {_about(m)}{_percent(m['value'])}")
    m = _shown(measures, "m.questions_total")
    if m and _finite(m.get("value")) is not None:
        lines.append(f"- Audience questions (Q&A and chat): {int(m['value'])}")
    m = _shown(measures, "m.questions_qa")
    if m and _percent(m.get("value")):
        lines.append(f"- Q&A questions answered in Livestorm: {_percent(m['value'])}")
    m = _shown(measures, "m.time_to_answer")
    if m and _finite(m.get("value")) is not None:
        lines.append(f"- Median time to answer a Q&A question: {clock(m['value'])}")
    m = _shown(measures, "m.speaking_pace")
    if m and _finite(m.get("value")) is not None:
        lines.append(f"- Speaking pace: {round(float(m['value']))} words a minute")
    m = _shown(measures, "m.speaker_talk_time")
    if m and isinstance(m.get("rows"), list):
        shares = [
            f"S{row['key']} {_percent(row.get('share'))}"
            for row in m["rows"]
            if isinstance(row, Mapping)
            and row.get("key") not in (None, "unknown")
            and (_finite(row.get("share")) or 0) >= MIN_TALK_SHARE
        ]
        if shares:
            lines.append(f"- Talk time: {', '.join(shares)}")
    return lines


def chat_lines(record: Mapping[str, Any]) -> List[str]:
    """The audience's chat: the busiest live minutes, and what it was about
    (the labels' classes, with their message counts)."""
    lines: List[str] = []
    minutes = (record.get("pulse") or {}).get("minutes") or []
    busy = sorted(
        (row for row in minutes if isinstance(row, Mapping) and (row.get("audienceChat") or 0) > 0),
        key=lambda row: (-row["audienceChat"], row["minute"]),
    )[:BUSIEST_MINUTES]
    if busy:
        peaks = ", ".join(f"{clock(row['minute'] * 60)} ({_messages(row['audienceChat'])})" for row in busy)
        lines.append(f"- Busiest minutes of the audience's chat: {peaks}")
    themes = (record.get("measures") or {}).get("m.chat_themes")
    rows = themes.get("rows") if isinstance(themes, Mapping) else None
    if isinstance(rows, list):
        counts = [
            f"{_CLASS_WORDS.get(row['key'], row['key'])} {row['messages']}"
            for row in rows
            if isinstance(row, Mapping) and isinstance(row.get("messages"), int) and row["messages"] > 0
        ]
        if counts:
            lines.append(f"- What the audience's chat was about (messages): {', '.join(counts)}")
    return lines


def _messages(count: int) -> str:
    return f"{count} message" if count == 1 else f"{count} messages"


def _one_line(value: Any, limit: Optional[int] = None) -> str:
    text = " ".join(str(value or "").split())
    if limit is not None and len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


def understanding_lines(result: Mapping[str, Any], times: Mapping[str, Any]) -> Dict[str, List[str]]:
    """The chapters, the prompts to the room and the figures said on stage,
    from a current understanding result; `times` maps an utterance id to its
    time in the brief."""
    chapters = [
        f"- {clock(times.get(c.get('first')))} {c['title']} ({c.get('kind') or 'other'})"
        for c in result.get("chapters") or []
        if isinstance(c, Mapping) and c.get("title")
    ]
    prompts = [
        f"- {clock(times.get(p.get('utterance')))} \"{_one_line(p.get('quote'))}\""
        for p in result.get("prompts") or []
        if isinstance(p, Mapping) and p.get("quote")
    ]
    figures = []
    for f in result.get("figures") or []:
        if not isinstance(f, Mapping) or not f.get("span"):
            continue
        flags = []
        if "two_values" in (f.get("flags") or []):
            flags.append("the same metric was said with two values: do not state it as a fact")
        if "acronym" in (f.get("flags") or []):
            flags.append("its acronym is unclear in the recording: do not state it as a fact")
        note = f" [{'; '.join(flags)}]" if flags else ""
        figures.append(f"- {clock(times.get(f.get('utterance')))} \"{_one_line(f['span'])}\"{note}")
    return {"chapters": chapters, "prompts": prompts, "figures": figures}


def question_lines(questions: Sequence[Mapping[str, Any]]) -> List[str]:
    """The audience's questions, `{t, source, text, answered_as, located}`
    (the question desk's rows, its status as `answered_as`, never an
    author): time, where, status, text, and the passage of the talk that
    answers it when one was found."""
    lines = []
    for q in questions:
        status = _QUESTION_STATUS.get(str(q.get("answered_as")), "still open")
        source = "chat" if q.get("source") == "chat" else "Q&A"
        line = f"- {clock(q.get('t'))} ({source}, {status}) \"{_one_line(q.get('text'), MAX_QUESTION_CHARS)}\""
        located = q.get("located")
        if isinstance(located, Mapping) and located.get("quote"):
            line += f"; answered in the talk at {clock(located.get('t'))}: \"{_one_line(located['quote'])}\""
        lines.append(line)
    return lines


def build_brief(
    *,
    title: Optional[str],
    length_s: Any,
    language: str,
    internal: bool,
    record: Mapping[str, Any],
    understanding: Mapping[str, Any],
    utterances: Sequence[Mapping[str, Any]],
    questions: Sequence[Mapping[str, Any]],
) -> Dossier:
    """The brief for one session. `record` is the record block
    (`assemble.assemble_record(...)["record"]`), `understanding` a current
    result (`understanding.current_reading`), `utterances` the understanding
    dossier's rows and `questions` as `question_lines` takes them."""
    on_clock = bool(understanding.get("onClock")) and any(u.get("t") is not None for u in utterances)
    when = "t" if on_clock else "r"
    times = {u.get("id"): u.get(when) for u in utterances}
    parts = understanding_lines(understanding, times)
    sections = [
        "\n".join(
            [
                "# Session brief",
                f"version: {BRIEF_VERSION}",
                f"title: {_one_line(title) or 'untitled'}",
                f"length: {clock(length_s)}",
                f"language spoken: {language or 'unknown'}",
                f"audience: {'an internal meeting, everyone is a team member' if internal else 'a public session'}",
                "times: "
                + ("minutes:seconds from the start of the session" if on_clock else "minutes:seconds into the recording"),
            ]
        ),
        "## Numbers (as StormIQ measured them: quote them as given, never compute others)\n"
        + ("\n".join(number_lines(record.get("measures") or {})) or "- none measured"),
        "## The chat\n" + ("\n".join(chat_lines(record)) or "- no audience chat"),
        "## Chapters\n" + ("\n".join(parts["chapters"]) or "- none found"),
        "## Moments the speakers asked the room to respond\n" + ("\n".join(parts["prompts"]) or "- none found"),
        "## Figures said on stage\n" + ("\n".join(parts["figures"]) or "- none found"),
        "## Audience questions\n" + ("\n".join(question_lines(questions)) or "- none"),
        "## Transcript (speakers by key)\n"
        + "\n".join(f"{clock(u.get(when))} {u.get('speaker') or 'S?'}: {u.get('text') or ''}" for u in utterances),
    ]
    return make_dossier(sections, record_version=f"session-brief-{BRIEF_VERSION}")
