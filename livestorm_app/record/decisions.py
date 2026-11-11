"""The Follow-up tab's "Decisions and commitments": the model's answer
(`ai.decisions.SessionDecisions`) checked against the dossier it was read
from, then saved as items (`saved_entry`, the shape in
`ai.decisions_entry`), worded when read.

- **What code keeps** (`check_decisions`). An item's utterance must be in the
  dossier and its quote in that utterance (or the one after it), word for
  word (case, punctuation and spacing aside). An owner is kept when it is
  the speaker ("speaker": the person saying it commits themselves) or when
  its words are in the utterance; a due date when its words are. Anything
  else is dropped (an item) or left blank (an owner, a date), never shown.
  Repeats are kept once.
- **Names.** The model sees speaker keys only (`S0`), and the saved entry
  keeps them as refs: the speaker's name, the one the host saved on the
  Transcript tab, else "Speaker n", is added when the list is read
  (`ai.decisions_entry`), so a rename shows without a new call.
- **Times.** On the session clock when the alignment allows minutes (the
  dossier's own rule), else on the recording's clock, and the entry says
  which.

Pure functions: no database, network or web framework.
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence

from livestorm_app.ai.decisions_entry import FORMAT as ENTRY_FORMAT
from livestorm_app.ai.decisions_entry import KINDS, RECORDING_CLOCK, SESSION_CLOCK, entry_lines, is_entry
from livestorm_app.record.understanding import _plain, _verbatim

DECISIONS_VERSION = 2  # 2: saved as items with speaker refs (`ai.decisions_entry.FORMAT`)
MAX_TEXT_CHARS = 240
MAX_QUOTE_CHARS = 300
MAX_OWNER_CHARS = 80
SPEAKER_OWNER = "speaker"

# The headings of the Markdown saved before November 2026 (`decision_lines`).
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
        if kind not in KINDS or uid not in order:
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
    """Each speaker ref ("S0") with its order of first appearance, from 1."""
    found: Dict[str, int] = {}
    for u in utterances:
        key = _speaker_key(u)
        if key is not None and f"S{key}" not in found:
            found[f"S{key}"] = len(found) + 1
    return found


def saved_entry(items: Sequence[Mapping[str, Any]], utterances: Sequence[Mapping[str, Any]], *, on_clock: bool) -> Dict[str, Any]:
    """The checked items in the form saved in `decisions_bundle`
    (`ai.decisions_entry`): speakers and owners as refs ("S0"), never a
    name, with each speaker's order of first appearance for "Speaker n"."""
    saved_items = []
    for item in items:
        speaker = f"S{item['speaker']}" if item.get("speaker") is not None else None
        if item.get("ownerIsSpeaker"):
            owner: Optional[Dict[str, str]] = {"speaker": speaker} if speaker else None
        elif item.get("owner"):
            owner = {"words": str(item["owner"])}
        else:
            owner = None
        saved_items.append(
            {
                "kind": item["kind"],
                "text": item["text"],
                "owner": owner,
                "due": item.get("due"),
                "quote": item["quote"],
                "utterance": item["utterance"],
                "speaker": speaker,
                "t": item.get("t"),
                "r": item.get("r"),
            }
        )
    return {
        "format": ENTRY_FORMAT,
        "clock": SESSION_CLOCK if on_clock else RECORDING_CLOCK,
        "speakers": _speaker_order(utterances),
        "items": saved_items,
    }


_BULLET = re.compile(r"^- \*\*(?P<text>.+?)\*\*(?P<rest>.*)$")
_DUE = re.compile(r"(?:^| )Due: (?P<due>.+?)\.(?= |$)")


def decision_lines(saved: Any) -> List[Dict[str, Any]]:
    """What a saved list holds, without who: `[{kind, text, due}]` in its
    order, `kind` "decision" or "commitment", `due` None when none was said.
    The owner, the speaker and the quote are left out, for the team
    digest's brief (`record.digest_brief`), which never names anyone. An
    entry is read as saved (`ai.decisions_entry.entry_lines`); the Markdown
    saved before November 2026 is parsed."""
    if is_entry(saved):
        return entry_lines(saved)
    markdown = saved if isinstance(saved, str) else ""
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
