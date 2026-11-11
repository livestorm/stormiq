"""Who spoke up: the Report's list of the audience members who wrote, as
`GET /api/sessions/{id}/record/voices` serves it.

Each person who wrote in the chat or asked in the Q&A, the most active
first: their short name (only through `roster.display_people`, which fails
closed), their company, how many chat messages and questions they wrote,
and one message that shows what they said. Read on demand, like the
Transcript's room view; the record itself never carries a name or a text.

- **Who counts.** The audience: a message flagged as the team's, or a
  question from a team member, is left out, except on an internal session,
  where everyone is counted (as the measures do).
- **Telling people apart.** By the author's `P-` ref; a chat cached before
  the people links has none, so Livestorm's author id tells them apart and
  they show unnamed. Never matched by name.
- **The message shown** is their longest without a link (a GIF is a link),
  else their longest: the one most likely to say something.

Pure functions: no database, network or web framework.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from livestorm_app.record.evidence import CHAT, QUESTION
from livestorm_app.record.roster import INTERNAL_PROFILE, PUBLIC_PROFILE, display_people
from livestorm_app.record.transcript_view import short_name

VOICES_VERSION = 1

# The people listed one by one; the rest are summed in `others`.
VOICES_LIMIT = 8

_ALL_PEOPLE = 1_000_000


def _people(inputs: Any) -> Dict[str, Dict[str, Optional[str]]]:
    """`{ref: {name, company}}` for the people the page may name."""
    profile = INTERNAL_PROFILE if inputs.roster.meta.internal else PUBLIC_PROFILE
    people: Dict[str, Dict[str, Optional[str]]] = {}
    for person in display_people(inputs.roster, profile=profile, limit=_ALL_PEOPLE):
        name = short_name(person.get("name"))
        if not name:
            continue
        company = person.get("company")
        company = company.strip() if isinstance(company, str) else ""
        people[person["ref"]] = {"name": name, "company": company or None}
    return people


def _seconds(value: Any) -> Optional[float]:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return seconds if math.isfinite(seconds) else None


def _sample(texts: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Their longest message without a link, else their longest."""
    if not texts:
        return None
    plain = [m for m in texts if not m["link"]] or texts
    best = max(plain, key=lambda m: (len(m["text"].split()), len(m["text"]), -m["order"]))
    return {"id": best["eid"], "t": best["t"], "text": best["text"]}


def _sums(entries: List[Dict[str, Any]]) -> Dict[str, int]:
    return {
        "people": len(entries),
        "messages": sum(e["messages"] for e in entries),
        "questions": sum(e["questions"] for e in entries),
    }


def chat_voices(inputs: Any, *, limit: int = VOICES_LIMIT) -> Dict[str, Any]:
    """`{version, internal, total, people, others}`. `total` and `others`
    are `{people, messages, questions}`; `people` is the most active first,
    `[{name, company, messages, questions, share, sample}]`: `share` is the
    person's part of everything the audience wrote (messages and
    questions), 0 to 1; `name` is None for a person the page may not name;
    `sample` is `{id, t, text}` or None."""
    internal = bool(inputs.roster.meta.internal)
    events = inputs.events
    by_author: Dict[str, Dict[str, Any]] = {}
    if events is not None and not events.empty:
        columns = ["eid", "kind", "t", "is_team", "author_ref", "author_id", "text", "has_link"]
        rows = events[columns].itertuples(index=False, name=None)
        for order, (eid, kind, t, is_team, ref, author_id, text, link) in enumerate(rows):
            if kind not in (CHAT, QUESTION) or (bool(is_team) and not internal):
                continue
            ref = ref if isinstance(ref, str) and ref else None
            key = ref or (f"id:{author_id}" if isinstance(author_id, str) and author_id else None)
            if key is None:
                continue
            entry = by_author.setdefault(key, {"ref": ref, "messages": 0, "questions": 0, "texts": []})
            entry["messages" if kind == CHAT else "questions"] += 1
            body = str(text or "").strip()
            if body:
                entry["texts"].append({"eid": str(eid), "t": _seconds(t), "text": body, "link": bool(link), "order": order})

    ranked = sorted(
        by_author.values(), key=lambda e: (-(e["messages"] + e["questions"]), -e["questions"], e["ref"] or "~")
    )
    written = sum(e["messages"] + e["questions"] for e in ranked)
    people = _people(inputs) if ranked else {}
    shown: List[Dict[str, Any]] = []
    for entry in ranked[:limit]:
        person = people.get(entry["ref"]) if entry["ref"] else None
        shown.append(
            {
                "name": person["name"] if person else None,
                "company": person["company"] if person else None,
                "messages": entry["messages"],
                "questions": entry["questions"],
                "share": round((entry["messages"] + entry["questions"]) / written, 4),
                "sample": _sample(entry["texts"]),
            }
        )
    return {
        "version": VOICES_VERSION,
        "internal": internal,
        "total": _sums(ranked),
        "people": shown,
        "others": _sums(ranked[limit:]),
    }
