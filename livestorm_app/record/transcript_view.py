"""The Transcript tab's room view, as `GET /api/sessions/{id}/record/transcript` serves it.

What was said and what the room wrote, as text, by id: the transcript's
utterances (`U` ids, from `transcript.parse_transcript`) and the audience's
chat messages, questions and written answers (`C`, `Q`, `A` ids, from
`evidence.audience_events`). The record carries none of this text; this view
is read on demand, when someone opens the tab.

- **The clock.** Speech is placed on the session clock (`t = r + offset`)
  only when `alignment.alignment_allows(alignment, SECONDS)`: reading a line
  of speech next to a chat line is a claim to the second. Otherwise every
  speech line's `t` is None and `clock.interleave` is False, and the page
  shows speech and the room apart. The audience is always on the session
  clock (Livestorm's own).
- **Speakers** are keyed as Gladia keys them; their names are the ones the
  user saved (`transcript_speaker_names`), else "Speaker 1", "Speaker 2"…
- **Authors** are shown shortened, "Priya S.", and only through
  `roster.display_people`, which fails closed: an author who doesn't
  resolve to a person gets no name. An internal session names its people
  too (the owner's decision, November 2026). Such a line says
  "Team" (the message's own team or guest-speaker flag) or "Attendee".
  Never an email address, a people id or a `P-` ref.
- **Unclear words**: each utterance lists the character spans of its words
  below `transcript_health.LOW_CONFIDENCE`, found in its text in order; a
  word that can't be found in the text is left out, never guessed.
- **Chapters** come from the understanding job (`understanding.chapter_list`,
  only while read from the session as it is now): a title and kind each,
  starting at a speech line's id (`first`), with that line's `r` and `t`
  (`t` only when speech is on the session clock). A chapter whose first
  line isn't shown is left out.
- **Questions** say whether they were answered in writing (an answer row
  replies to them). Nothing says a question is "open": an answer given out
  loud leaves no trace in the data.
- **Questions asked in the chat**: a chat message the session analysis
  labelled a question (`class` "question", about the content or about
  logistics) says so (`asks`), only while the stored labels describe this
  chat (`inputs.chat_labels`) and, as on the question desk, never for the
  team's own messages except on an internal session.

Pure functions: no database, network or web framework.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import pandas as pd

from livestorm_app.record.alignment import SECONDS, alignment_allows
from livestorm_app.record.evidence import ANSWER, CHAT, QUESTION
from livestorm_app.record.roster import INTERNAL_PROFILE, PUBLIC_PROFILE, display_people
from livestorm_app.record.transcript_health import LOW_CONFIDENCE
from livestorm_app.speakers import speaker_name  # re-exported: `record.moments` and the tests read it here

VIEW_VERSION = 1

TEAM = "Team"
ATTENDEE = "Attendee"

# Whatever the session's size, a name list this long covers it.
_ALL_PEOPLE = 1_000_000


def _seconds(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    number = round(number, 2)
    return int(number) if float(number).is_integer() else number


def short_name(name: Any) -> Optional[str]:
    """"Priya S." from "Priya Sharma": the first word and the last word's
    initial. One word stays as it is; no name is None."""
    if not isinstance(name, str):
        return None
    words = name.split()
    if not words:
        return None
    if len(words) == 1:
        return words[0]
    return f"{words[0]} {words[-1][0].upper()}."


def unclear_spans(text: str, words: Sequence[Tuple[str, float]]) -> List[List[int]]:
    """`[start, end]` character spans of the words below `LOW_CONFIDENCE`,
    found in `text` in order (case-insensitive). A word not found from where
    the last one ended is skipped, and the search goes on from there."""
    spans: List[List[int]] = []
    folded = text.casefold()
    cursor = 0
    for word, confidence in words:
        token = str(word or "").strip()
        if not token:
            continue
        at = folded.find(token.casefold(), cursor)
        if at < 0:
            continue
        end = at + len(token)
        cursor = end
        if isinstance(confidence, float) and math.isfinite(confidence) and confidence < LOW_CONFIDENCE:
            spans.append([at, end])
    return spans


def _words_by_utterance(words: pd.DataFrame) -> Dict[str, List[Tuple[str, float]]]:
    grouped: Dict[str, List[Tuple[str, float]]] = {}
    if words is None or words.empty:
        return grouped
    for uid, word, confidence in zip(words["uid"], words["word"], words["confidence"], strict=True):
        if isinstance(uid, str):
            grouped.setdefault(uid, []).append((str(word or ""), float(confidence)))
    return grouped


def _speech(inputs: Any, saved: Mapping[str, Any], offset: Optional[float]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    transcript = inputs.transcript
    if transcript is None or transcript.utterances.empty:
        return [], []
    words = _words_by_utterance(transcript.words)
    utterances = transcript.utterances.sort_values(["r_start", "order"], na_position="last", kind="stable")
    speakers: Dict[Optional[str], Dict[str, Any]] = {}
    lines: List[Dict[str, Any]] = []
    for row in utterances.to_dict("records"):
        text = str(row["text"] or "").strip()
        if not text:
            continue
        key = row["speaker_key"] if isinstance(row["speaker_key"], str) else None
        if key not in speakers:
            speakers[key] = {"key": key, "name": speaker_name(key, saved, len(speakers) + 1), "named": key in saved}
        r = _seconds(row["r_start"])
        lines.append(
            {
                "id": row["uid"],
                "r": r,
                "rEnd": _seconds(row["r_end"]),
                "t": _seconds(r + offset) if r is not None and offset is not None else None,
                "speaker": key,
                "text": text,
                "unclear": unclear_spans(text, words.get(row["uid"], [])),
            }
        )
    return lines, list(speakers.values())


def _names(inputs: Any) -> Dict[str, str]:
    """Short names by `P-` ref, through `display_people` (fails closed)."""
    profile = INTERNAL_PROFILE if inputs.roster.meta.internal else PUBLIC_PROFILE
    names: Dict[str, str] = {}
    for person in display_people(inputs.roster, profile=profile, limit=_ALL_PEOPLE):
        name = short_name(person.get("name"))
        if name:
            names[person["ref"]] = name
    return names


def _room(inputs: Any) -> List[Dict[str, Any]]:
    events = inputs.events
    if events is None or events.empty:
        return []
    names = _names(inputs)
    answered = {reply for reply in events.loc[events["kind"] == ANSWER, "reply_to"] if isinstance(reply, str)}
    labels = inputs.chat_labels or {}
    internal = bool(inputs.roster.meta.internal)
    lines: List[Dict[str, Any]] = []
    for row in events[events["kind"].isin([CHAT, QUESTION, ANSWER])].to_dict("records"):
        ref = row["author_ref"] if isinstance(row["author_ref"], str) else None
        team = bool(row["is_team"])
        author = names.get(ref) if ref else None
        line = {
            "id": str(row["eid"]),
            "kind": row["kind"],
            "t": _seconds(row["t"]),
            "text": str(row["text"] or ""),
            "author": author or (TEAM if team else ATTENDEE),
            "team": team,
        }
        asks = (labels.get(line["id"]) or {}).get("class") == "question"
        if row["kind"] == CHAT and asks and (internal or not team):
            line["asks"] = True
        if row["kind"] == QUESTION:
            line["answered"] = str(row["eid"]) in answered
        if row["kind"] == ANSWER:
            line["replyTo"] = row["reply_to"] if isinstance(row["reply_to"], str) else None
        lines.append(line)
    lines.sort(key=lambda line: (line["t"] is None, line["t"] if line["t"] is not None else 0, line["id"]))
    return lines


def _chapters(speech: Sequence[Mapping[str, Any]], chapters: Sequence[Mapping[str, Any]], state: str) -> Dict[str, Any]:
    lines = {line["id"]: line for line in speech}
    items = [
        {"title": c["title"], "kind": c["kind"], "first": c["first"], "r": lines[c["first"]]["r"], "t": lines[c["first"]]["t"]}
        for c in chapters
        if c.get("first") in lines
    ]
    return {"state": str(state or "none"), "items": items}


def transcript_view(
    inputs: Any,
    speaker_names: Optional[Mapping[str, Any]] = None,
    chapters: Sequence[Mapping[str, Any]] = (),
    chapters_state: str = "none",
) -> Dict[str, Any]:
    """`{version, clock, speakers, speech, room, chapters}` for one session's
    inputs (`inputs.RecordInputs`), the speaker names the user saved and
    the chapters (`understanding.chapter_list`)."""
    saved = {str(key): value for key, value in (speaker_names or {}).items()}
    interleave = inputs.transcript is not None and alignment_allows(inputs.alignment, SECONDS)
    offset = inputs.alignment.offset_s if interleave else None
    speech, speakers = _speech(inputs, saved, offset)
    return {
        "version": VIEW_VERSION,
        "clock": {"interleave": bool(interleave and speech), "offsetS": _seconds(offset) if speech else None},
        "speakers": speakers,
        "speech": speech,
        "room": _room(inputs),
        "chapters": _chapters(speech, chapters, chapters_state),
    }
