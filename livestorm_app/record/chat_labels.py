"""The chat labels' contract: their classes, the chat they were made from, and the checks a set must pass.

A model labels each chat message once, in an analysis the user starts;
the labels are stored whole per session (`db.replace_chat_labels`,
docs/data-model.md) and read back by the record. This module holds what
both sides agree on, with no model, database or web framework:

- `LABEL_CLASSES` and `QUESTION_KINDS`: what a message can be labelled;
- `chat_hash`: which chat a set of labels describes, so an unchanged chat
  is never labelled again and labels for an older chat are never shown
  against a newer one;
- `check_labels`: a set is complete (one label per message, no other id)
  and every label is well formed, or it is refused whole.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

# Bump when the classes, the prompt or the schema change what a label means:
# stored labels of an older version are treated as missing.
LABEL_VERSION = 1

# One class per message.
LABEL_CLASSES = (
    "content",  # reacts to what is being said or shown
    "question",  # asks something; `question` says what about
    "tech",  # sound, video, connection, the platform, the format
    "logistics",  # replay, slides, timing, links, without asking
    "social",  # greetings, thanks, where people write from
    "laughter",
    "off_topic",
)
QUESTION_KINDS = ("content", "logistics")

LABEL_KEYS = ("id", "livestorm_id", "class", "question", "language")

_CHAT_ID = re.compile(r"^C[0-9]{4,}$")
_LANGUAGE = re.compile(r"^[a-z]{2,3}$")


def chat_hash(messages: Iterable[Mapping[str, Any]]) -> str:
    """The SHA-256 of the chat a set of labels is made from: each message's
    evidence id, Livestorm id and text, in id order. Rows are
    `record.evidence.audience_events` chat rows (`eid`, `livestorm_id`,
    `text`); anything else in them (the clock, the author) doesn't change
    the hash, since it doesn't change a label."""
    items = sorted(
        ([str(row.get("eid") or ""), row.get("livestorm_id"), str(row.get("text") or "")] for row in messages),
        key=lambda item: _id_order(item[0]),
    )
    data = json.dumps(items, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _id_order(label_id: str) -> tuple:
    """C9999 before C10000: by length, then by text."""
    return (len(label_id), label_id)


def check_labels(
    labels: Sequence[Any],
    *,
    expected_ids: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    """The labels, checked and ordered by evidence id. Raises ValueError
    (naming keys and ids, never a message's text) when one is malformed, an
    id repeats, or, given `expected_ids`, the set doesn't hold exactly one
    label for each of them.

    A label is `{id, livestorm_id, class, question, language}`: `id` a `C`
    evidence id; `livestorm_id` the message's own id or None; `class` one of
    `LABEL_CLASSES`; `question` one of `QUESTION_KINDS` when the class is
    "question", None otherwise; `language` a lowercase ISO 639 code or None.
    """
    checked: Dict[str, Dict[str, Any]] = {}
    for label in labels:
        if not isinstance(label, Mapping):
            raise ValueError("Each chat label must be a mapping.")
        if set(label) != set(LABEL_KEYS):
            raise ValueError(f"A chat label has exactly the keys {list(LABEL_KEYS)}.")
        label_id = label["id"]
        if not isinstance(label_id, str) or not _CHAT_ID.match(label_id):
            raise ValueError("A chat label's id is a C evidence id.")
        if label_id in checked:
            raise ValueError(f"Chat label {label_id} appears twice.")
        livestorm_id = label["livestorm_id"]
        if livestorm_id is not None and (not isinstance(livestorm_id, str) or not livestorm_id.strip()):
            raise ValueError(f"Chat label {label_id}: livestorm_id is a non-empty string or None.")
        label_class = label["class"]
        if label_class not in LABEL_CLASSES:
            raise ValueError(f"Chat label {label_id}: unknown class.")
        question = label["question"]
        if label_class == "question":
            if question not in QUESTION_KINDS:
                raise ValueError(f"Chat label {label_id}: a question needs its kind.")
        elif question is not None:
            raise ValueError(f"Chat label {label_id}: only a question has a kind.")
        language = label["language"]
        if language is not None and (not isinstance(language, str) or not _LANGUAGE.match(language)):
            raise ValueError(f"Chat label {label_id}: language is a lowercase ISO 639 code or None.")
        checked[label_id] = {
            "id": label_id,
            "livestorm_id": livestorm_id,
            "class": label_class,
            "question": question,
            "language": language,
        }
    if expected_ids is not None:
        expected = set(expected_ids)
        if set(checked) != expected:
            missing = len(expected - set(checked))
            extra = len(set(checked) - expected)
            raise ValueError(f"The labels must cover the chat exactly: {missing} missing, {extra} unknown.")
    return [checked[label_id] for label_id in sorted(checked, key=_id_order)]
