"""Surprise me's director: what the video shows for each clause of the
timed script (`ai/surprise_timing.py`), as a shot list (prompt:
`prompts/surprise_director.txt`).

- **When.** In the show job only (`api_logic.run_surprise_show`), after the
  timing; cached by `shots_key` (the timeline's key and
  `DIRECTOR_VERSION`) in `surprise_shotlists`, so a replay costs nothing.
- **The call** (`direct_shots`): a v1 job (`ai.jobs`, kind
  `surprise_director`, Sonnet 5.5, the `instructions` layout), one call:
  the instructions, then the payload (`director_payload`): the clauses (the
  script, already checked against the take: every figure the session's, no
  attendee named), the vitals, the audience chat per minute and the room's
  redacted lines by id. Never a name or an email.
- **The checks** (`check_shots`, pure): one shot per clause, by id; the
  on-screen words at most `MAX_WORDS`, copied in a row from that clause;
  a number only one the clause writes; a chat line only one of our ids,
  its text copied from our list, never the model's; a minute only one of
  the Pulse's; a split's sides copied from the clause; at most
  `MAX_IMAGES` images, each prompt free of names (`image_prompt_ok`);
  emoji without letters or digits. A shot that fails becomes kinetic type
  with the clause's own words (`fallback_words`).
- **The fallback** (`fallback_shots`): when the call fails, there is no
  Claude key or the answer is unusable, a shot list from rules (a
  percentage fills a ring, a number counts up, the rest is kinetic type),
  so the show never fails on a bad director answer.

No database, no web framework.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, Iterable, List, Literal, Mapping, Optional, Sequence, Set

from pydantic import BaseModel, Field

from livestorm_app.ai.request import build_request
from livestorm_app.ai.schemas import json_schema
from livestorm_app.ai.usage import CallContext
from livestorm_app.config import SURPRISE_DIRECTOR_PROMPT_PATH

KIND = "surprise_director"

# Bump when the shots' shape, the checks or the prompt's contract change.
DIRECTOR_VERSION = 1

TYPES = ("kinetic", "count", "ring", "pulse", "emoji", "chat", "image", "split", "stamp")
EMPHASES = ("setup", "build", "punchline", "aside", "beat")
TRANSITIONS = ("cut", "whip", "zoom", "flash", "shake", "drop", "blur")
MAX_WORDS = 6
MAX_SIDE_WORDS = 4
MAX_IMAGES = 3
MAX_EMOJI = 3
MAX_PROMPT_CHARS = 400

ShotType = Literal["kinetic", "count", "ring", "pulse", "emoji", "chat", "image", "split", "stamp"]
Emphasis = Literal["setup", "build", "punchline", "aside", "beat"]
Transition = Literal["cut", "whip", "zoom", "flash", "shake", "drop", "blur"]


class DirectorShot(BaseModel):
    id: int = Field(description="The clause's id.")
    type: ShotType = Field(description="The shot type.")
    words: str = Field(description="Up to 6 words copied exactly and in a row from the clause.")
    accent: str = Field(description="One word from words to colour, or an empty string.")
    emphasis: Emphasis = Field(description="setup, build, punchline, aside or beat.")
    transition: Transition = Field(description="How the shot arrives.")
    number: Optional[str] = Field(description="For count or ring: a figure exactly as the clause writes it; else null.")
    minute: Optional[int] = Field(description="For pulse: a minute from the chat data to mark; else null.")
    chat_id: Optional[str] = Field(description="For chat: the id of one of the room's lines, such as R2; else null.")
    emoji: List[str] = Field(description="For emoji: 1 to 3 emoji; else an empty list.")
    left: Optional[str] = Field(description="For split: 1 to 4 words copied from the clause; else null.")
    right: Optional[str] = Field(description="For split: 1 to 4 words copied from the clause; else null.")
    image_prompt: Optional[str] = Field(description="For image: a concrete visual gag, objects only, no names, text, people or logos; else null.")


@json_schema
class SurpriseDirection(BaseModel):
    shots: List[DirectorShot] = Field(description="One shot per clause, in the clauses' order.")


# ── The key ──


def shots_key(timeline_key: str) -> str:
    """sha256 of `DIRECTOR_VERSION` and the timeline's key."""
    return hashlib.sha256(f"{DIRECTOR_VERSION}\n{timeline_key}".encode("utf-8")).hexdigest()


# ── The payload ──


def director_payload(
    shots: Sequence[Mapping[str, Any]],
    vitals: Sequence[str],
    pulse: Sequence[Mapping[str, Any]],
    room: Sequence[Mapping[str, Any]],
) -> str:
    """What the model reads after its instructions: the clauses (`{id, line,
    tone, dur, text}`, a held pause marked), the vitals, the chat per minute
    (`{m, chat}`) and the room's lines (`{id, kind, text}`)."""
    lines = ["# Clauses (id | script line | voice tone | seconds on screen | clause)"]
    for index, shot in enumerate(shots):
        held = "  [held pause before this line]" if index and shot.get("k") == 0 and shot.get("tone") == "punchline" else ""
        lines.append(
            f"{shot['id']} | line {shot.get('line')} | {shot.get('tone') or '-'} | {float(shot.get('dur') or 0):.1f}s | "
            f"{' '.join(str(shot.get('text') or '').split())}{held}"
        )
    lines += ["", "# What StormIQ measured", *(list(vitals) or ["- none measured"])]
    lines += ["", "# Audience chat messages per minute (minute: count)"]
    lines.append(", ".join(f"{int(row['m'])}:{int(row.get('chat') or 0)}" for row in pulse) or "- no chat")
    lines += ["", "# The room's lines (id, kind: text)"]
    if room:
        for row in room:
            kind = "question" if row.get("kind") == "question" else "chat"
            lines.append(f"{row['id']} ({kind}): {' '.join(str(row.get('text') or '').split())}")
    else:
        lines.append("- none")
    return "\n".join(lines)


# ── The checks ──

_EDGE = "\"'“”‘’«»()[]{}.,!?;:…*_-–—"
_NUMBER = re.compile(r"^\$?\d[\d,]*(?:[.:]\d+)?%?$")
_ALNUM = re.compile(r"[A-Za-z0-9]")
_CAPITAL_WORD = re.compile(r"\b[A-ZÀ-Þ][\w'’-]{2,}\b")
_CONTACT = re.compile(r"@|https?://|www\.|\.com\b", re.IGNORECASE)
# Capitalised words that are only sentence starts, never a name or a brand.
_COMMON = frozenset({
    "the", "this", "that", "and", "but", "they", "then", "nobody", "everyone", "what", "okay", "when", "where", "which",
    "who", "why", "how", "yes", "not", "now", "one", "two", "three", "here", "there", "some", "all", "you", "your",
    "our", "his", "her", "its", "for", "with", "from", "into", "after", "before", "just", "well", "also", "still",
    "only", "even", "meanwhile", "somewhere", "someone", "something", "anyway", "honestly", "turns", "said", "says",
    "chat", "minute", "minutes", "session", "question", "questions", "people", "audience", "room", "hmm", "wait",
})


def _tokens(text: str) -> List[str]:
    out = []
    for raw in str(text or "").split():
        word = raw.strip(_EDGE).casefold()
        if word:
            out.append(word)
    return out


def verbatim(words: str, clause: str, limit: int = MAX_WORDS) -> bool:
    """Whether `words` is 1 to `limit` words of the clause, in a row (case and
    the punctuation around words aside)."""
    have, want = _tokens(clause), _tokens(words)
    if not want or len(want) > limit:
        return False
    return any(have[i:i + len(want)] == want for i in range(len(have) - len(want) + 1))


def fallback_words(clause: str) -> str:
    """The clause's own words: all of them when short, else its last six."""
    words = str(clause or "").split()
    return " ".join(words if len(words) <= MAX_WORDS else words[-MAX_WORDS:])


def number_in(number: Optional[str], clause: str) -> Optional[str]:
    """`number` as the clause writes it, when it is one of the clause's
    figures; else None."""
    wanted = str(number or "").strip().strip(_EDGE.replace("%", "").replace(":", "")).rstrip(".,")
    if not wanted or not _NUMBER.match(wanted):
        return None
    for raw in str(clause or "").split():
        word = raw.strip(_EDGE.replace("%", "").replace(":", "").replace("$", "")).rstrip(".,:")
        if word == wanted and _NUMBER.match(word):
            return word
    return None


def banned_words(texts: Iterable[str], names: Iterable[str] = ()) -> Set[str]:
    """Words an image prompt may not hold, lower case: every capitalised word
    in `texts` (the take, the room's lines: names, companies, products) that
    isn't a common sentence start, and every word of `names`."""
    out: Set[str] = set()
    for text in texts:
        for word in _CAPITAL_WORD.findall(str(text or "")):
            folded = word.casefold().strip("'’-")
            if folded not in _COMMON:
                out.add(folded)
    for name in names:
        for word in str(name or "").split():
            folded = word.strip(_EDGE).casefold()
            if len(folded) >= 3:
                out.add(folded)
    return out


def image_prompt_ok(prompt: Optional[str], banned: Set[str]) -> bool:
    """An image prompt is sent only when it is short, holds no contact
    detail and none of the `banned` words (names, companies, products)."""
    text = " ".join(str(prompt or "").split())
    if not text or len(text) > MAX_PROMPT_CHARS or _CONTACT.search(text):
        return False
    return not any(token in banned for token in _tokens(text))


def _kinetic(shot: Dict[str, Any], clause: str) -> Dict[str, Any]:
    shot.update({"type": "kinetic", "number": None, "minute": None, "chat": None, "left": None, "right": None,
                 "image_prompt": None, "emoji": []})
    if not verbatim(shot.get("words") or "", clause):
        shot["words"] = fallback_words(clause)
    return shot


def check_shot(
    item: Mapping[str, Any],
    clause: str,
    *,
    room: Mapping[str, Mapping[str, Any]],
    minutes: Set[int],
    banned: Set[str],
    images: List[int],
    used_chat: Set[str],
) -> Dict[str, Any]:
    """One checked shot, `{type, words, accent, emphasis, transition, number,
    minute, chat: {id, kind, text} | None, emoji, left, right, image_prompt,
    fixed}`; what fails falls back to kinetic type."""
    shot: Dict[str, Any] = {
        "type": item.get("type") if item.get("type") in TYPES else "kinetic",
        "words": " ".join(str(item.get("words") or "").split()),
        "accent": " ".join(str(item.get("accent") or "").split()),
        "emphasis": item.get("emphasis") if item.get("emphasis") in EMPHASES else "build",
        "transition": item.get("transition") if item.get("transition") in TRANSITIONS else "cut",
        "number": None,
        "minute": None,
        "chat": None,
        "emoji": [],
        "left": None,
        "right": None,
        "image_prompt": None,
    }
    fixed = item.get("type") not in TYPES
    if not verbatim(shot["words"], clause):
        shot["words"] = fallback_words(clause)
        fixed = True
    if shot["accent"] and not verbatim(shot["accent"], shot["words"], 1):
        shot["accent"] = ""
    kind = shot["type"]
    if kind in ("count", "ring"):
        number = number_in(item.get("number"), clause)
        if not number or (kind == "ring" and not number.endswith("%")) or (kind == "count" and ":" in number and not re.match(r"^\d+:\d\d$", number)):
            return {**_kinetic(shot, clause), "fixed": True}
        shot["number"] = number
    elif kind == "chat":
        chat_id = str(item.get("chat_id") or "").strip()
        row = room.get(chat_id)
        if row is None or chat_id in used_chat:
            return {**_kinetic(shot, clause), "fixed": True}
        used_chat.add(chat_id)
        shot["chat"] = {"id": chat_id, "kind": "question" if row.get("kind") == "question" else "chat",
                        "text": " ".join(str(row.get("text") or "").split())}
    elif kind == "pulse":
        if not minutes:
            return {**_kinetic(shot, clause), "fixed": True}
        minute = item.get("minute")
        shot["minute"] = minute if isinstance(minute, int) and not isinstance(minute, bool) and minute in minutes else None
    elif kind == "split":
        left, right = str(item.get("left") or ""), str(item.get("right") or "")
        if not (verbatim(left, clause, MAX_SIDE_WORDS) and verbatim(right, clause, MAX_SIDE_WORDS)):
            return {**_kinetic(shot, clause), "fixed": True}
        shot["left"], shot["right"] = " ".join(left.split()), " ".join(right.split())
    elif kind == "image":
        prompt = " ".join(str(item.get("image_prompt") or "").split())
        if images[0] >= MAX_IMAGES or not image_prompt_ok(prompt, banned):
            return {**_kinetic(shot, clause), "fixed": True}
        images[0] += 1
        shot["image_prompt"] = prompt
    elif kind == "emoji":
        emoji = [str(e) for e in item.get("emoji") or [] if str(e).strip() and not _ALNUM.search(str(e)) and len(str(e)) <= 8]
        if not emoji:
            return {**_kinetic(shot, clause), "fixed": True}
        shot["emoji"] = emoji[:MAX_EMOJI]
    shot["fixed"] = fixed
    return shot


def check_shots(
    timeline_shots: Sequence[Mapping[str, Any]],
    answer: Mapping[str, Any],
    *,
    room: Sequence[Mapping[str, Any]],
    minutes: Iterable[int],
    banned: Set[str],
) -> List[Dict[str, Any]]:
    """One checked shot per clause, by id (the first given for an id counts);
    a clause the model skipped gets the rules' shot."""
    by_room = {str(row["id"]): row for row in room if isinstance(row, Mapping) and row.get("id")}
    given: Dict[int, Mapping[str, Any]] = {}
    for item in (answer or {}).get("shots") or []:
        if isinstance(item, Mapping) and isinstance(item.get("id"), int) and not isinstance(item.get("id"), bool):
            given.setdefault(item["id"], item)
    minute_set = {int(m) for m in minutes}
    images = [0]
    used_chat: Set[str] = set()
    out = []
    for shot in timeline_shots:
        item = given.get(int(shot["id"]))
        clause = str(shot.get("text") or "")
        if item is None:
            checked = {**rule_shot(shot, minute_set), "fixed": True}
        else:
            checked = check_shot(item, clause, room=by_room, minutes=minute_set, banned=banned, images=images, used_chat=used_chat)
        out.append({"id": int(shot["id"]), **checked})
    return out


# ── The fallback ──

_PERCENT = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?%")
_COUNT = re.compile(r"(?<![\w.:])\d[\d,]*(?:\.\d+)?(?![\w%:])")
_ROOM_WORDS = re.compile(r"\b(chat|room|messages?|typing|typed|wrote|silence|quiet)\b", re.IGNORECASE)
_TRANSITION_CYCLE = ("cut", "whip", "blur", "cut", "drop")


def rule_shot(shot: Mapping[str, Any], minutes: Set[int]) -> Dict[str, Any]:
    """The rules' shot for one clause: a percentage fills a ring, a number
    counts up, a clause about the chat draws the Pulse, a punchline line
    is stamped, the rest is kinetic type."""
    clause = str(shot.get("text") or "")
    out: Dict[str, Any] = {
        "type": "kinetic", "words": fallback_words(clause), "accent": "",
        "emphasis": "punchline" if shot.get("tone") == "punchline" and shot.get("k") == 0 else "build",
        "transition": _TRANSITION_CYCLE[int(shot.get("id") or 0) % len(_TRANSITION_CYCLE)],
        "number": None, "minute": None, "chat": None, "emoji": [], "left": None, "right": None, "image_prompt": None,
    }
    percent = _PERCENT.search(clause)
    count = _COUNT.search(clause)
    if percent:
        out.update(type="ring", number=percent.group(0))
    elif count and len(count.group(0).replace(",", "")) <= 6:
        out.update(type="count", number=count.group(0))
    elif minutes and _ROOM_WORDS.search(clause):
        out.update(type="pulse")
    elif out["emphasis"] == "punchline":
        out.update(type="stamp", transition="shake")
    return out


def fallback_shots(timeline_shots: Sequence[Mapping[str, Any]], minutes: Iterable[int]) -> List[Dict[str, Any]]:
    minute_set = {int(m) for m in minutes}
    return [{"id": int(shot["id"]), **rule_shot(shot, minute_set), "fixed": False} for shot in timeline_shots]


# ── The call ──


def load_instructions() -> str:
    text = SURPRISE_DIRECTOR_PROMPT_PATH.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError("The director's prompt is empty.")
    return text


def direct_shots(payload: str, *, api_key: str, ctx: CallContext) -> SurpriseDirection:
    """The model's shot list for one payload. Raises the `llm_client` errors."""
    from livestorm_app import llm_client

    request = build_request(KIND, instructions=load_instructions(), payload=payload)
    return llm_client.generate_json(KIND, SurpriseDirection, request, api_key=api_key, ctx=ctx)
