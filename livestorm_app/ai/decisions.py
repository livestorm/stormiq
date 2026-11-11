"""The Follow-up tab's "Decisions and commitments": one model call over the
session's dossier (`record.understanding.build_dossier`: the utterances by
id with speaker keys, never a name), which lists what was decided and what
someone promised to do, each with the utterance that says it and the words
quoted (prompt: `prompts/decisions.txt`).

A v1 job (`ai.jobs`, Sonnet 5.5 by default, the `dossier` layout), started
by a click on Follow-up, never by "Analyse session". It writes nothing and
checks nothing beyond the schema: the caller passes the answer through
`record.decisions.check_decisions`, which keeps only items whose quote is in
the utterance they cite, then words them (`record.decisions.decisions_markdown`).
One call, so nothing is marked for the cache.

Imports no `record/` code: the dossier comes in, an answer goes out.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from livestorm_app.ai.dossier import Dossier
from livestorm_app.ai.request import build_request
from livestorm_app.ai.schemas import json_schema
from livestorm_app.ai.usage import CallContext
from livestorm_app.config import DECISIONS_PROMPT_PATH

KIND = "decisions"

DecisionKind = Literal["decision", "commitment"]


class DecisionItem(BaseModel):
    kind: DecisionKind = Field(description="decision: something settled; commitment: someone will do something.")
    text: str = Field(description="What was decided or promised, in one plain sentence, in English.")
    owner: Optional[str] = Field(
        description="Who will do it: 'speaker' when the person speaking commits themselves, else the words of the utterance that say who, or null."
    )
    due: Optional[str] = Field(description="When, in the words of the utterance ('by Friday', 'next quarter'), or null.")
    utterance_id: str = Field(description="The id of the utterance that states it, such as U0042.")
    quote: str = Field(description="The words that state it, copied exactly from that utterance.")


@json_schema
class SessionDecisions(BaseModel):
    items: List[DecisionItem] = Field(description="The decisions and commitments, in the order they were said.")


def load_instructions() -> str:
    text = DECISIONS_PROMPT_PATH.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError("The decisions prompt is empty.")
    return text


def read_decisions(dossier: Dossier, *, api_key: str, ctx: CallContext) -> SessionDecisions:
    """The model's answer for one dossier. Raises the `llm_client` errors."""
    from livestorm_app import llm_client

    request = build_request(KIND, context=dossier, instructions=load_instructions())
    return llm_client.generate_json(KIND, SessionDecisions, request, api_key=api_key, ctx=ctx)
