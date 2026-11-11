"""The saved form of the Follow-up's "Decisions and commitments", and how it
is read.

`run_decisions` saves one entry per language in `decisions_bundle`: the
checked items (`record.decisions.check_decisions`), never a name:

    {"format": "decisions/2", "clock": "session" | "recording",
     "speakers": {"S0": 1, "S2": 2},          # order of first appearance
     "items": [{"kind": "decision" | "commitment", "text": "...",
                "owner": {"speaker": "S0"} | {"words": "..."} | None,
                "due": "..." | None, "quote": "...", "utterance": "U0042",
                "speaker": "S0" | None, "t": 245.0 | None, "r": 260.1 | None}]}

Names are resolved when the entry is read (`resolve_entry`,
`entry_markdown`), from the speaker names saved on the Transcript tab and the
same rule as every page (`speakers.speaker_name`), so renaming a speaker
updates the list without a model call. An entry saved before November 2026
is the Markdown the job wrote then, names included, and is served as it is.

A French entry is the English one with its `text` and `due` translated
(`translation_source`, `apply_translation`); owners, quotes, ids and times
are copied, so names stay references in French too.

Pure functions, outside `record/`: the workspace's outputs read them, and a
classic read never loads the v1 record.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional

from livestorm_app.speakers import speaker_name

FORMAT = "decisions/2"
SESSION_CLOCK = "session"
RECORDING_CLOCK = "recording"
KINDS = ("decision", "commitment")

WORDS: Dict[str, Dict[str, str]] = {
    "English": {
        "decision": "Decisions",
        "commitment": "Commitments",
        "owner": "Owner: {}.",
        "due": "Due: {}.",
        "said": "{} at {}",
        "recording": "_Times are on the recording's clock._",
        "none": "No decisions or commitments were stated on stage.",
        "someone": "A speaker",
    },
    "French": {
        "decision": "Décisions",
        "commitment": "Engagements",
        "owner": "Responsable : {}.",
        "due": "Échéance : {}.",
        "said": "{} à {}",
        "recording": "_Les heures sont celles de l'enregistrement._",
        "none": "Aucune décision ni aucun engagement n'a été annoncé sur scène.",
        "someone": "Un intervenant",
    },
}


def is_entry(value: Any) -> bool:
    """Whether a bundle value is a saved entry (not the Markdown of before)."""
    return isinstance(value, Mapping) and value.get("format") == FORMAT and isinstance(value.get("items"), list)


def has_body(value: Any) -> bool:
    """Whether a bundle value holds a list: an entry (even one with no item,
    which says so) or non-empty Markdown."""
    return is_entry(value) or (isinstance(value, str) and bool(value.strip()))


def clock_text(seconds: Optional[float]) -> str:
    """"4:05", "1:02:09" from seconds; "" when unknown."""
    if seconds is None:
        return ""
    total = max(0, int(round(seconds)))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _words(language: str) -> Dict[str, str]:
    return WORDS.get(str(language or "").strip(), WORDS["English"])


def _names(saved_names: Any) -> Dict[str, Any]:
    return {str(key): value for key, value in saved_names.items()} if isinstance(saved_names, Mapping) else {}


def _speaker(entry: Mapping[str, Any], ref: Any, saved: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """`{speaker, name}` for a ref ("S0") the entry knows, else None. The
    saved names are keyed without the `S`, as the Transcript tab saves them."""
    ref = str(ref or "")
    order = (entry.get("speakers") or {}).get(ref)
    if not ref.startswith("S") or not isinstance(order, int):
        return None
    return {"speaker": ref, "name": speaker_name(ref[1:], saved, order)}


def resolve_entry(entry: Mapping[str, Any], saved_names: Any) -> Dict[str, Any]:
    """The entry with names, for the cards: `{clock, items}`, each item
    `{kind, text, due, quote, utterance, at, owner, saidBy}`. `at` is the
    time on the entry's clock (seconds or None); `owner` is `{speaker, name}`
    for a speaker, `{speaker: None, name: words}` for words of the talk, or
    None; `saidBy` is `{speaker, name}` or None."""
    saved = _names(saved_names)
    clock = entry.get("clock") if entry.get("clock") in (SESSION_CLOCK, RECORDING_CLOCK) else RECORDING_CLOCK
    items = []
    for item in entry.get("items") or []:
        if not isinstance(item, Mapping) or item.get("kind") not in KINDS:
            continue
        said_by = _speaker(entry, item.get("speaker"), saved)
        raw_owner = item.get("owner") if isinstance(item.get("owner"), Mapping) else {}
        if raw_owner.get("speaker"):
            owner = _speaker(entry, raw_owner["speaker"], saved)
        elif str(raw_owner.get("words") or "").strip():
            owner = {"speaker": None, "name": str(raw_owner["words"]).strip()}
        else:
            owner = None
        items.append(
            {
                "kind": item["kind"],
                "text": str(item.get("text") or ""),
                "due": item.get("due") or None,
                "quote": str(item.get("quote") or ""),
                "utterance": item.get("utterance"),
                "at": item.get("t") if clock == SESSION_CLOCK else item.get("r"),
                "owner": owner,
                "saidBy": said_by,
            }
        )
    return {"clock": clock, "items": items}


def entry_markdown(entry: Mapping[str, Any], saved_names: Any, language: str = "English") -> str:
    """The entry as Markdown, names resolved now: a section per kind that
    has items, each a bullet with what, who, when, and the words said with
    their speaker and time. A list with no item says so."""
    words = _words(language)
    resolved = resolve_entry(entry, saved_names)
    if not resolved["items"]:
        return words["none"]
    sections: List[str] = []
    if resolved["clock"] != SESSION_CLOCK:
        sections.append(words["recording"])
    for kind in KINDS:
        bullets = []
        for item in resolved["items"]:
            if item["kind"] != kind:
                continue
            said_by = item["saidBy"]["name"] if item["saidBy"] else words["someone"]
            parts = [f"**{item['text'].rstrip('.')}.**"]
            if item["owner"]:
                parts.append(words["owner"].format(item["owner"]["name"]))
            if item["due"]:
                parts.append(words["due"].format(item["due"]))
            stamp = clock_text(item["at"])
            parts.append(f"{words['said'].format(said_by, stamp) if stamp else said_by}: *“{item['quote']}”*")
            bullets.append(f"- {' '.join(parts)}")
        if bullets:
            sections.append("\n".join([f"## {words[kind]}", "", *bullets]))
    return "\n\n".join(sections)


def bundle_markdown(raw_bundle: Any, saved_names: Any) -> Dict[str, str]:
    """Each language's list as Markdown: an entry worded now with the saved
    names, an older Markdown entry as it was saved."""
    out: Dict[str, str] = {}
    if not isinstance(raw_bundle, Mapping):
        return out
    for language, value in raw_bundle.items():
        key = str(language or "").strip()
        if not key:
            continue
        if is_entry(value):
            out[key] = entry_markdown(value, saved_names, key)
        elif isinstance(value, str) and value.strip():
            out[key] = value.strip()
    return out


def entry_lines(entry: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """What the entry lists, without who: `[{kind, text, due}]` in its order,
    each text ending with a full stop as the Markdown writes it, for the team
    digest's brief, which never names anyone."""
    return [
        {"kind": item["kind"], "text": str(item.get("text") or "").strip().rstrip(".") + ".", "due": item.get("due") or None}
        for item in entry.get("items") or []
        if isinstance(item, Mapping) and item.get("kind") in KINDS
    ]


# ── Translation: only the text and the due words ──

_TAG = re.compile(r"^\s*\[\[([TD])(\d+)\]\]\s*(.*?)\s*$")


class TranslationMismatch(ValueError):
    """A translation that doesn't give back every line it was sent."""


def translation_source(entry: Mapping[str, Any]) -> str:
    """The lines a translation gets: each item's text (`[[T1]]`) and due
    words (`[[D1]]`), one per line. Never an owner, a quote or a name."""
    lines = []
    for index, item in enumerate(entry.get("items") or [], start=1):
        lines.append(f"[[T{index}]] {' '.join(str(item.get('text') or '').split())}")
        if item.get("due"):
            lines.append(f"[[D{index}]] {' '.join(str(item['due']).split())}")
    return "\n".join(lines)


def apply_translation(entry: Mapping[str, Any], translated: str) -> Dict[str, Any]:
    """The entry with the translated text and due words; owners, quotes, ids
    and times copied. Raises `TranslationMismatch` unless every line sent
    came back once, not empty, and nothing else did."""
    expected = {match.group(1) + match.group(2) for match in map(_TAG.match, translation_source(entry).splitlines()) if match}
    found: Dict[str, str] = {}
    for line in str(translated or "").splitlines():
        match = _TAG.match(line)
        if not match:
            continue
        tag = match.group(1) + match.group(2)
        if tag in found or tag not in expected or not match.group(3):
            raise TranslationMismatch("a line was repeated, empty or not one we sent")
        found[tag] = match.group(3)
    if set(found) != expected:
        raise TranslationMismatch("a line is missing")
    items = []
    for index, item in enumerate(entry.get("items") or [], start=1):
        copied = dict(item)
        copied["text"] = found[f"T{index}"]
        if item.get("due"):
            copied["due"] = found[f"D{index}"]
        items.append(copied)
    return {**dict(entry), "items": items}
