"""Surprise me's storyboard: what the player shows for each beat while the
voice reads it. Not the sentence written out: one short headline lifted
from it, a big number it says, or one of the room's own lines popping up
as a chat bubble, each with an effect (prompt: `prompts/surprise_storyboard.txt`).

- **When.** Made only from the "Make the show" click, in the show job,
  after the script and before the clips (`api_logic.run_surprise_show`); cached by `story_key` (the beats'
  text and `STORYBOARD_VERSION`) in `surprise_storyboards`, so a replay
  costs nothing.
- **The call** (`write_storyboard`): a v1 job (`ai.jobs`, kind
  `surprise_storyboard`, Haiku 5.5, the `instructions` layout), one call:
  the instructions first, then the payload (`storyboard_payload`): the
  beats (the saved take's sentences, already checked: every figure the
  session's, no attendee named), the room's lines by id (`R1`…, redacted:
  `record.surprise_brief.room_lines`) and the vitals. Never a name or an
  email: the room's lines were redacted before they got here.
- **The check** (`check_storyboard`, pure): a headline is at most
  `MAX_HEADLINE_WORDS` words in a row of the beat itself (case and the
  punctuation around words aside); a number is one the beat says; a quote
  is one of the room's ids, used once, and its text, kind and time are
  copied from our list, never from the model; an effect outside `EFFECTS`
  becomes `DEFAULT_EFFECT`, and an effect without what it needs (`count`
  without a number, `chat` without a quote) falls back to the headline's.
  A beat whose scene fails is None: the player draws its own scene for it
  (`frontend/src/followup/surpriseScenes.js`), so the show never fails.

No database, no web framework.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence

from pydantic import BaseModel, Field

from livestorm_app.ai.request import build_request
from livestorm_app.ai.schemas import json_schema
from livestorm_app.ai.usage import CallContext
from livestorm_app.config import SURPRISE_STORYBOARD_PROMPT_PATH

KIND = "surprise_storyboard"

# Bump when the scenes' shape, the check or the prompt's contract change:
# every storyboard key changes with it, and the next Play makes a new one.
STORYBOARD_VERSION = 1

# What a scene does on screen (`SurprisePlayer.vue`):
#   slam   the headline slams in, big
#   stamp  the headline is stamped on, at an angle
#   pop    the headline's words pop in one by one
#   count  a number the beat says counts up, big (a ring for a percentage)
#   chat   one of the room's lines flies in as a chat bubble
#   pulse  the audience chat's curve draws in to its peak, the headline under it
#   flash  a flash of colour behind the headline: a punchline
EFFECTS = ("slam", "stamp", "pop", "count", "chat", "pulse", "flash")
DEFAULT_EFFECT = "pop"
MAX_HEADLINE_WORDS = 6
MAX_HEADLINE_CHARS = 64

Effect = Literal["slam", "stamp", "pop", "count", "chat", "pulse", "flash"]


class StoryScene(BaseModel):
    beat: int = Field(description="The beat this scene is for: 1 for B1, 2 for B2, and so on.")
    effect: Effect = Field(description="How the scene plays: slam, stamp, pop, count, chat, pulse or flash.")
    headline: str = Field(
        description="At most 6 words copied in a row, exactly, from this beat's sentence; empty only for a count or a chat scene."
    )
    number: Optional[str] = Field(description="For a count scene: a number exactly as this beat writes it, such as 142 or 56%; else null.")
    quote_id: Optional[str] = Field(description="For a chat scene: the id of one line from the room, such as R2; else null.")


@json_schema
class SurpriseStoryboard(BaseModel):
    scenes: List[StoryScene] = Field(description="One scene per beat, in the beats' order.")


# ── The key ──


def story_key(beat_texts: Sequence[str]) -> str:
    """The storyboard's cache key: sha256 of `STORYBOARD_VERSION` and the
    beats' text, in order."""
    parts = [str(STORYBOARD_VERSION), *[str(text) for text in beat_texts]]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


# ── The payload ──


def _clock(seconds: Any) -> str:
    try:
        total = max(0, int(round(float(seconds))))
    except (TypeError, ValueError):
        return "?"
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def storyboard_payload(beats: Sequence[Mapping[str, Any]], room: Sequence[Mapping[str, Any]], vitals: Sequence[str]) -> str:
    """What the model reads after its instructions: the beats (`{kind,
    text}`, numbered B1…), the room's lines by id and the vitals."""
    lines = ["# The beats (the take, one sentence each, in order)"]
    for number, beat in enumerate(beats, start=1):
        lines.append(f"B{number} [{beat.get('kind') or 'line'}] {' '.join(str(beat.get('text') or '').split())}")
    lines += ["", "# From the room (quote one only by its id; never copy its words into a headline)"]
    if room:
        for row in room:
            kind = "question" if row.get("kind") == "question" else "chat"
            lines.append(f"{row['id']} {_clock(row.get('t'))} {kind}: \"{row.get('text')}\"")
    else:
        lines.append("- none")
    lines += ["", "# What StormIQ measured", *(list(vitals) or ["- none measured"])]
    return "\n".join(lines)


# ── The check ──

_EDGE = "\"'“”‘’«»()[]{}.,!?;:…*_-–—"
_NUMBER = re.compile(r"^\d[\d,]*(?:\.\d+)?%?$")


def _tokens(text: str) -> List[str]:
    """Words, lower case, without the punctuation around them ("23:00",
    "1,200" and "56%" stay whole)."""
    out = []
    for raw in str(text or "").split():
        word = raw.strip(_EDGE).casefold()
        if word:
            out.append(word)
    return out


def _in_a_row(words: List[str], beat: List[str]) -> bool:
    size = len(words)
    return any(beat[start:start + size] == words for start in range(len(beat) - size + 1))


def headline_ok(headline: str, beat_text: str) -> bool:
    """Whether `headline` is 1 to `MAX_HEADLINE_WORDS` words of the beat, in
    a row, as the beat writes them (case and surrounding punctuation aside)."""
    words = _tokens(headline)
    text = " ".join(str(headline or "").split())
    return bool(words) and len(words) <= MAX_HEADLINE_WORDS and len(text) <= MAX_HEADLINE_CHARS and _in_a_row(words, _tokens(beat_text))


def number_ok(number: Optional[str], beat_text: str) -> Optional[str]:
    """The number as the beat writes it, when `number` is one of the beat's
    numbers (a count or a percentage, not a clock time); else None."""
    wanted = str(number or "").strip().strip(_EDGE.replace("%", "")).casefold()
    if not wanted or not _NUMBER.match(wanted):
        return None
    for raw in str(beat_text or "").split():
        word = raw.strip(_EDGE.replace("%", "")).rstrip(".,")
        if word.casefold() == wanted and _NUMBER.match(word):
            return word
    return None


def _scene(
    item: Mapping[str, Any], beat_text: str, room: Mapping[str, Mapping[str, Any]], used: set
) -> Optional[Dict[str, Any]]:
    effect = item.get("effect") if item.get("effect") in EFFECTS else DEFAULT_EFFECT
    raw_headline = " ".join(str(item.get("headline") or "").split())
    headline = raw_headline.strip(" ,;:") if headline_ok(raw_headline, beat_text) else ""
    number = number_ok(item.get("number"), beat_text)
    quote_id = str(item.get("quote_id") or "").strip()
    quote = None
    if quote_id in room and quote_id not in used:
        row = room[quote_id]
        quote = {"id": quote_id, "kind": row.get("kind"), "t": row.get("t"), "text": row.get("text")}
    if effect == "count" and not number:
        effect = DEFAULT_EFFECT
    if effect == "chat" and not quote:
        effect = DEFAULT_EFFECT
    if effect not in ("count", "chat") and not headline:
        return None
    if effect != "chat":
        quote = None
    if effect != "count":
        number = None
    if quote:
        used.add(quote_id)
    return {"effect": effect, "headline": headline, "number": number, "quote": quote}


def check_storyboard(
    beat_texts: Sequence[str], answer: Mapping[str, Any], room: Sequence[Mapping[str, Any]]
) -> List[Optional[Dict[str, Any]]]:
    """One scene per beat, `{effect, headline, number, quote}` (`quote`:
    `{id, kind, t, text}` from `room`), or None where the model's scene
    fails the check or is missing. The first scene given for a beat counts."""
    by_id = {str(row["id"]): row for row in room if isinstance(row, Mapping) and row.get("id")}
    given: Dict[int, Mapping[str, Any]] = {}
    for item in (answer or {}).get("scenes") or []:
        if isinstance(item, Mapping) and isinstance(item.get("beat"), int) and not isinstance(item.get("beat"), bool):
            given.setdefault(item["beat"], item)
    used: set = set()
    scenes: List[Optional[Dict[str, Any]]] = []
    for number, text in enumerate(beat_texts, start=1):
        item = given.get(number)
        scenes.append(_scene(item, text, by_id, used) if item is not None else None)
    return scenes


# ── The call ──


def load_instructions() -> str:
    text = SURPRISE_STORYBOARD_PROMPT_PATH.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError("The storyboard prompt is empty.")
    return text


def write_storyboard(payload: str, *, api_key: str, ctx: CallContext) -> SurpriseStoryboard:
    """The model's storyboard for one payload. Raises the `llm_client` errors."""
    from livestorm_app import llm_client

    request = build_request(KIND, instructions=load_instructions(), payload=payload)
    return llm_client.generate_json(KIND, SurpriseStoryboard, request, api_key=api_key, ctx=ctx)
