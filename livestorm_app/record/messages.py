"""The chat's messages, by evidence id, as `GET /api/sessions/{id}/record/chat` serves them.

The record (`record/assemble.py`) carries no message text: a measure that
counts messages lists their `C` ids instead (`m.chat_themes` rows carry
`ids`), and the page asks this route for the text when someone opens a
count, so a label can be checked against what was written.

`chat_messages(session_payload, chat_payload)` reads the chat through
`evidence.audience_events`, the only source of `C` ids, so its ids are the
record's. Each message is `{id, t, text, team}`: `t` is seconds since the
session's start (None without a clock), and `team` is the message's own
team or guest-speaker flag. No author: no name, people id or `P-` ref.

Pure functions: no database, network or web framework.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from livestorm_app.record.clock import clock_base
from livestorm_app.record.evidence import CHAT, audience_events


def _seconds(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else round(number, 3)


def chat_messages(session_payload: Any, chat_payload: Any) -> Dict[str, List[Dict[str, Any]]]:
    """`{messages}`: every chat message in id order, team messages included
    (the page shows only the ids a measure lists)."""
    events = audience_events(chat_payload, None, clock_base(session_payload))
    if events.empty:
        return {"messages": []}
    chat = events[events["kind"] == CHAT]
    return {
        "messages": [
            {"id": str(row["eid"]), "t": _seconds(row["t"]), "text": str(row["text"] or ""), "team": bool(row["is_team"])}
            for row in chat.to_dict("records")
        ]
    }
