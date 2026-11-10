"""Label a session's chat with a model (v1): one class per message, in batches.

`label_chat(messages, api_key=…, ctx=…)` takes the chat rows of
`record.evidence.audience_events` (`eid`, `livestorm_id`, `text`,
`is_team`) and returns every message's label (`record.chat_labels`), the
chat's hash and the model that made them. It writes nothing: the caller
stores the result (`db.replace_chat_labels`).

- **Batches.** `BATCH_SIZE` messages per call, in id order. Every call sends
  the same instructions (`prompts/chat_labels.txt`) first, cached across
  the batches of one run, and the same schema (`ChatLabelBatch`): a schema
  is part of the cached prefix, so the batches share it only if it is the
  same (docs/providers.md).
- **What is sent.** Each message's `C` id, whether the team wrote it, and
  its text on one line. Never an author, a name, an email or a people id.
- **Complete or nothing.** A batch's answer must label exactly that batch's
  ids (`check_labels`); otherwise the run raises `LLMInvalidOutput` and no
  label of it is returned. The caller keeps the earlier labels.
- **Laughter.** A message that is only laughter (`LAUGHTER`) is labelled
  laughter whatever the model said; the model can still label laughter on
  its own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence

from pydantic import BaseModel, Field

from livestorm_app.ai import jobs
from livestorm_app.ai.request import build_request
from livestorm_app.ai.schemas import json_schema
from livestorm_app.ai.usage import CallContext
from livestorm_app.config import CHAT_LABELS_PROMPT_PATH

KIND = "chat_labels"
BATCH_SIZE = 150
# A message longer than this is cut before it is sent: a label needs its gist.
MAX_TEXT_CHARS = 600

# Only laughter: "haha", "hihi", "hehe", "lol", "lmao", "mdr", "ptdr", "xD",
# laughing emoji, and runs of them.
_LAUGH = r"(?:(?:h[aeiu]){2,}h?|a?(?:ha){2,}|lol+|lmao+|mdr+|ptdr+|x+d+|\U0001F602|\U0001F923|\U0001F606|\U0001F639)"
LAUGHTER = re.compile(rf"^[\s!.]*{_LAUGH}(?:[\s!.]*{_LAUGH})*[\s!.]*$", re.IGNORECASE)
_WHITESPACE = re.compile(r"\s+")

LabelClass = Literal["content", "question", "tech", "logistics", "social", "laughter", "off_topic"]


class ChatLabelItem(BaseModel):
    id: str = Field(description="The message's id, copied exactly, such as C0042.")
    label: LabelClass = Field(description="The message's class.")
    question: Optional[Literal["content", "logistics"]] = Field(
        description="For the class question: what it is about. Null for every other class.",
    )
    language: Optional[str] = Field(
        pattern=r"^[a-z]{2,3}$",
        description="The message's language as a lowercase ISO 639 code, or null.",
    )


@json_schema
class ChatLabelBatch(BaseModel):
    labels: List[ChatLabelItem] = Field(description="One label per message of the batch, in any order.")


@dataclass(frozen=True)
class ChatLabels:
    labels: List[Dict[str, Any]]  # `check_labels` rows, in id order
    chat_hash: str
    model: str


def load_instructions() -> str:
    text = CHAT_LABELS_PROMPT_PATH.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError("The chat labels prompt is empty.")
    return text


def _id_order(message: Mapping[str, Any]) -> tuple:
    eid = str(message["eid"])
    return (len(eid), eid)


def _line(message: Mapping[str, Any]) -> str:
    text = _WHITESPACE.sub(" ", str(message.get("text") or "")).strip()
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS].rstrip() + "…"
    who = "team" if message.get("is_team") else "audience"
    return f"{message['eid']}\t{who}\t{text}"


def batches(messages: Sequence[Mapping[str, Any]], size: int = BATCH_SIZE) -> List[List[Mapping[str, Any]]]:
    """The messages in id order, cut into batches of at most `size`."""
    ordered = sorted(messages, key=_id_order)
    return [ordered[start:start + size] for start in range(0, len(ordered), size)]


def label_chat(
    messages: Sequence[Mapping[str, Any]],
    *,
    api_key: str,
    ctx: CallContext,
) -> ChatLabels:
    """Every message's label. Raises the `llm_client` errors (a refusal, an
    invalid or incomplete answer, a provider failure) and `jobs.NoModelForJob`;
    nothing is returned for a run that didn't label every message. An empty
    chat makes no call."""
    from livestorm_app import llm_client
    from livestorm_app.record.chat_labels import chat_hash, check_labels

    choice = jobs.resolve_model(KIND)
    groups = batches(messages)
    if not groups:
        return ChatLabels(labels=[], chat_hash=chat_hash(messages), model=choice.model_id)
    instructions = load_instructions()
    labelled: List[Dict[str, Any]] = []
    for number, group in enumerate(groups, start=1):
        request = build_request(
            KIND,
            instructions=instructions,
            payload="\n".join(_line(message) for message in group),
            shared_calls=len(groups),
            call_number=number,
        )
        answer = llm_client.generate_json(KIND, ChatLabelBatch, request, api_key=api_key, ctx=ctx)
        by_id = {str(message["eid"]): message for message in group}
        rows = []
        for item in answer.labels:
            message = by_id.get(item.id)
            label_class, question = item.label, item.question
            if message is not None and LAUGHTER.match(str(message.get("text") or "")):
                label_class, question = "laughter", None
            rows.append({
                "id": item.id,
                "livestorm_id": (message or {}).get("livestorm_id") or None,
                "class": label_class,
                "question": question if label_class == "question" else None,
                "language": item.language,
            })
        try:
            labelled.extend(check_labels(rows, expected_ids=by_id))
        except ValueError as exc:
            raise llm_client.LLMInvalidOutput(f"Chat labels, batch {number} of {len(groups)}: {exc}") from exc
    return ChatLabels(labels=check_labels(labelled), chat_hash=chat_hash(messages), model=choice.model_id)
