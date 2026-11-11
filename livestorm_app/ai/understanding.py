"""The understanding job (v1, plan 1e): one model call over a session's
dossier.

`read_session(dossier, api_key=…, ctx=…)` sends the dossier
(`record.understanding.build_dossier`) with the instructions in
`prompts/understanding.txt` and returns the model's answer as
`SessionUnderstanding`: chapters, located answers, prompts to the room,
figures said on stage and groups of repeated questions. It writes nothing,
and checks nothing beyond the schema: the caller passes the answer through
`record.understanding.check_understanding`, which drops whatever the
dossier doesn't support, before storing it (`db.replace_understanding`).

- **One call per run**, on the job's own model (`jobs.resolve_model`:
  Sonnet 5.5 by default, medium effort, the `dossier` layout). A single
  call, so nothing is marked for the cache (`shared_calls=1`).
- **What is sent.** The dossier only: utterances by id with speaker keys,
  and the audience's questions by id. No name, email, company or people id
  (docs/security.md).
- **Failures raise** (`llm_client`'s refusal, cut-off and invalid-answer
  errors, `jobs.NoModelForJob`); nothing is returned for a failed call, and
  a paid call is never retried here.

Imports no `record/` code: the dossier comes in, an answer goes out.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from livestorm_app.ai.dossier import Dossier
from livestorm_app.ai.request import build_request
from livestorm_app.ai.schemas import json_schema
from livestorm_app.ai.usage import CallContext
from livestorm_app.config import UNDERSTANDING_PROMPT_PATH

KIND = "understanding"

ChapterKind = Literal["opening", "content", "demo", "qa", "close", "other"]


class ChapterItem(BaseModel):
    title: str = Field(description="What the part is about, at most 8 words, in the session's language.")
    kind: ChapterKind = Field(description="The part's kind.")
    first_utterance: str = Field(description="The id of the part's first utterance, such as U0012.")
    last_utterance: str = Field(description="The id of the part's last utterance.")


class AnswerItem(BaseModel):
    question_id: str = Field(description="The question's id, such as Q004 or C0042.")
    utterance_ids: List[str] = Field(description="The ids of the utterances holding the answer, in order, at most 6.")
    quote: str = Field(description="The words that answer it, copied exactly from those utterances.")


class PromptItem(BaseModel):
    utterance_id: str = Field(description="The id of the utterance with the prompt.")
    quote: str = Field(description="The words of the prompt, copied exactly.")


class FigureItem(BaseModel):
    utterance_id: str = Field(description="The id of the utterance stating the figure.")
    span: str = Field(description="The words that state it, copied exactly.")
    value: str = Field(description="The number as said, with digits.")
    unit: Optional[str] = Field(description="The unit as said, or null.")
    metric: Optional[str] = Field(description="What the number measures, in the speaker's words, or null.")


class GroupItem(BaseModel):
    label: str = Field(description="The shared question, at most 10 words.")
    question_ids: List[str] = Field(description="Two or more question ids asking the same thing.")


@json_schema
class SessionUnderstanding(BaseModel):
    chapters: List[ChapterItem] = Field(description="The session's parts, in order.")
    answers: List[AnswerItem] = Field(description="Passages answering open or aloud questions.")
    prompts: List[PromptItem] = Field(description="Times a speaker asked the audience to respond.")
    figures: List[FigureItem] = Field(description="Numbers stated as facts.")
    groups: List[GroupItem] = Field(description="Questions that ask the same thing.")


def load_instructions() -> str:
    text = UNDERSTANDING_PROMPT_PATH.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError("The understanding prompt is empty.")
    return text


def read_session(dossier: Dossier, *, api_key: str, ctx: CallContext) -> SessionUnderstanding:
    """The model's answer for one dossier. Raises the `llm_client` errors."""
    from livestorm_app import llm_client

    request = build_request(KIND, context=dossier, instructions=load_instructions())
    return llm_client.generate_json(KIND, SessionUnderstanding, request, api_key=api_key, ctx=ctx)
