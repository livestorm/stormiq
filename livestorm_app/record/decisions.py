"""The Follow-up tab's "Decisions and commitments": the model's answer
(`ai.decisions.SessionDecisions`) checked against the dossier it was read
from, then worded as Markdown by code.

- **What code keeps** (`check_decisions`). An item's utterance must be in the
  dossier and its quote in that utterance (or the one after it), word for
  word (case, punctuation and spacing aside). An owner is kept when it is
  the speaker ("speaker": the person saying it commits themselves) or when
  its words are in the utterance; a due date when its words are. Anything
  else is dropped (an item) or left blank (an owner, a date), never shown.
  Repeats are kept once.
- **Names.** The model sees speaker keys only (`S0`); the speaker's name is
  the one the host saved on the Transcript tab, else "Speaker n"
  (`transcript_view.speaker_name`), added here, after the call.
- **Times.** On the session clock when the alignment allows minutes (the
  dossier's own rule), else on the recording's clock, and the text says so.

Pure functions: no database, network or web framework.
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence

from livestorm_app.record.transcript_view import speaker_name
from livestorm_app.record.understanding import _plain, _verbatim

DECISIONS_VERSION = 1
MAX_TEXT_CHARS = 240
MAX_QUOTE_CHARS = 300
MAX_OWNER_CHARS = 80
SPEAKER_OWNER = "speaker"
NONE_STATED = "No decisions or commitments were stated on stage."

KIND_TITLES = {"decision": "Decisions", "commitment": "Commitments"}


def _one_line(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text[: limit - 1].rstrip() + "…" if len(text) > limit else text


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def clock_text(seconds: Optional[float]) -> str:
    """"4:05", "1:02:09" from seconds; "" when unknown."""
    if seconds is None:
        return ""
    total = max(0, int(round(seconds)))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _speaker_key(utterance: Mapping[str, Any]) -> Optional[str]:
    speaker = str(utterance.get("speaker") or "")
    key = speaker[1:] if speaker.startswith("S") else ""
    return key if key and key != "?" else None


def check_decisions(items: Sequence[Mapping[str, Any]], utterances: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """The items the dossier supports, in the order said: `{kind, text,
    owner, ownerIsSpeaker, due, utterance, speaker, t, r, quote}`. `speaker`
    is the utterance's speaker key without its `S` ("0"), or None."""
    order = {str(u["id"]): index for index, u in enumerate(utterances)}
    kept: List[Dict[str, Any]] = []
    seen = set()
    for item in items:
        kind = item.get("kind")
        uid = str(item.get("utterance_id") or "")
        if kind not in KIND_TITLES or uid not in order:
            continue
        index = order[uid]
        texts = [str(utterances[index]["text"])]
        if index + 1 < len(utterances):
            texts.append(str(utterances[index + 1]["text"]))
        quote = _one_line(item.get("quote"), MAX_QUOTE_CHARS)
        text = _one_line(item.get("text"), MAX_TEXT_CHARS)
        if not text or not _verbatim(quote, texts):
            continue
        key = (kind, _plain(text))
        if key in seen:
            continue
        seen.add(key)
        owner_raw = _one_line(item.get("owner"), MAX_OWNER_CHARS)
        owner_is_speaker = owner_raw.casefold() == SPEAKER_OWNER
        due_raw = _one_line(item.get("due"), MAX_OWNER_CHARS)
        utterance = utterances[index]
        kept.append(
            {
                "kind": kind,
                "text": text,
                "owner": None if owner_is_speaker or not _verbatim(owner_raw, texts) else owner_raw,
                "ownerIsSpeaker": owner_is_speaker,
                "due": due_raw if _verbatim(due_raw, texts) else None,
                "utterance": uid,
                "speaker": _speaker_key(utterance),
                "t": _finite(utterance.get("t")),
                "r": _finite(utterance.get("r")),
                "quote": quote,
            }
        )
    kept.sort(key=lambda entry: order[entry["utterance"]])
    return kept


def _speaker_order(utterances: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
    """Each speaker key ("0") with its order of first appearance, from 1."""
    found: Dict[str, int] = {}
    for u in utterances:
        key = _speaker_key(u)
        if key is not None and key not in found:
            found[key] = len(found) + 1
    return found


def decisions_markdown(
    items: Sequence[Mapping[str, Any]],
    utterances: Sequence[Mapping[str, Any]],
    saved_names: Mapping[str, Any],
    *,
    on_clock: bool,
) -> str:
    """The checked items as Markdown: a section per kind that has items,
    each a bullet with what, who, when, and the words said with their
    speaker and time. A session with none says so."""
    if not items:
        return NONE_STATED
    order = _speaker_order(utterances)
    saved = {str(key): value for key, value in (saved_names or {}).items()}

    def name(key: Optional[str]) -> str:
        return speaker_name(key, saved, order[key]) if key in order else "A speaker"

    sections: List[str] = []
    if not on_clock:
        sections.append("_Times are on the recording's clock._")
    for kind, title in KIND_TITLES.items():
        bullets = []
        for item in items:
            if item["kind"] != kind:
                continue
            said_by = name(item.get("speaker"))
            parts = [f"**{item['text'].rstrip('.')}.**"]
            owner = said_by if item.get("ownerIsSpeaker") else item.get("owner")
            if owner:
                parts.append(f"Owner: {owner}.")
            if item.get("due"):
                parts.append(f"Due: {item['due']}.")
            stamp = clock_text(item.get("t") if on_clock else item.get("r"))
            parts.append(f"{said_by}{f' at {stamp}' if stamp else ''}: *“{item['quote']}”*")
            bullets.append(f"- {' '.join(parts)}")
        if bullets:
            sections.append("\n".join([f"## {title}", "", *bullets]))
    return "\n\n".join(sections)


_BULLET = re.compile(r"^- \*\*(?P<text>.+?)\*\*(?P<rest>.*)$")
_DUE = re.compile(r"(?:^| )Due: (?P<due>.+?)\.(?= |$)")


def decision_lines(markdown: str) -> List[Dict[str, Any]]:
    """What `decisions_markdown` listed, without who: `[{kind, text, due}]`
    in its order, `kind` "decision" or "commitment", `due` None when none
    was said. The owner, the speaker and the quote are left out, for the
    team digest's brief (`record.digest_brief`), which never names anyone."""
    kinds = {f"## {title}": kind for kind, title in KIND_TITLES.items()}
    kind: Optional[str] = None
    found: List[Dict[str, Any]] = []
    for line in str(markdown or "").splitlines():
        line = line.strip()
        if line in kinds:
            kind = kinds[line]
            continue
        match = _BULLET.match(line)
        if kind is None or not match:
            continue
        # The due date sits before the speaker's words ("…: *“quote”*").
        rest = match.group("rest").split(": *“", 1)[0]
        due = _DUE.search(rest)
        found.append({"kind": kind, "text": match.group("text").strip(), "due": due.group("due").strip() if due else None})
    return found
