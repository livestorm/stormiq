"""The FAQ's brief: the audience's questions and every answer StormIQ holds
for them, written out for the model that writes the Follow-up tab's "FAQ"
(`ai/faq.py`, prompt `prompts/faq.txt`).

It reads the question desk (`question_desk.question_desk`), never the raw
payloads; a row's status comes as `answered_as`. Each question has an id
of the brief's own (`q1`, `q2`… in the desk's order), where it was asked,
when, its status and its text, then the answers it has:

- Livestorm's written answer (status `written`);
- the host's reply saved in StormIQ (status `here`, when they wrote one);
- the passage of the talk that answers it (`located`: a covered question,
  or one marked answered aloud or here), with its time.

A question marked answered aloud with no passage found has no words to
quote: the brief says so, and the FAQ points to the replay.

Never a name, an email, a company or a people id: the desk's `author` is
left out, and so is the desk's own id. Attendees' wording stays as they
wrote it here; the prompt has the model paraphrase it and drop any name.
Built with `ai.dossier.make_dossier`, so one desk gives the same bytes.

Pure: no database, network or model call.
"""

from __future__ import annotations

from typing import Any, List, Mapping, Optional, Sequence

from livestorm_app.ai.dossier import Dossier, make_dossier
from livestorm_app.record.session_brief import clock

FAQ_BRIEF_VERSION = 1
MAX_TEXT_CHARS = 600

_STATUS = {
    "written": "answered in writing during the session",
    "aloud": "answered aloud during the session",
    "here": "answered by the host after the session",
    "covered": "answered in the talk",
    "open": "not answered yet",
}


def _one_line(value: Any, limit: int = MAX_TEXT_CHARS) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit].rstrip() + "…" if len(text) > limit else text


def question_block(index: int, question: Mapping[str, Any]) -> str:
    """One question of the brief, `{source, t, answered_as, text, answer,
    reply, located}`: a desk row with its status as `answered_as`, never its
    author."""
    status = str(question.get("answered_as") or "open")
    source = "chat" if question.get("source") == "chat" else "Q&A"
    lines = [
        f"### q{index}",
        f"asked: {clock(question.get('t'))}, in the {source}",
        f"status: {_STATUS.get(status, _STATUS['open'])}",
        f"question: \"{_one_line(question.get('text'))}\"",
    ]
    if question.get("answer"):
        lines.append(f"written answer: \"{_one_line(question['answer'])}\"")
    if question.get("reply"):
        lines.append(f"host's answer: \"{_one_line(question['reply'])}\"")
    located = question.get("located")
    if isinstance(located, Mapping) and located.get("quote"):
        lines.append(f"answered in the talk at {clock(located.get('t'))}: \"{_one_line(located['quote'])}\"")
    elif status == "aloud":
        lines.append("the words of the spoken answer weren't found: point to the replay")
    return "\n".join(lines)


def build_faq_brief(
    *,
    title: Optional[str],
    internal: bool,
    questions: Sequence[Mapping[str, Any]],
) -> Dossier:
    """The FAQ's brief for one session's desk rows (`question_desk(...)
    ["questions"]`, as `question_block` takes them). Raises ValueError
    without a question: there is no FAQ to write."""
    if not questions:
        raise ValueError("A FAQ needs at least one question.")
    blocks: List[str] = [question_block(index, q) for index, q in enumerate(questions, start=1)]
    header = "\n".join(
        [
            "# FAQ brief",
            f"version: {FAQ_BRIEF_VERSION}",
            f"title: {_one_line(title, 200) or 'untitled'}",
            f"audience: {'an internal meeting, everyone is a team member' if internal else 'a public session'}",
            "times: minutes:seconds from the start of the session",
            f"questions: {len(questions)}",
        ]
    )
    return make_dossier([header, "## Questions", *blocks], record_version=f"faq-brief-{FAQ_BRIEF_VERSION}")
