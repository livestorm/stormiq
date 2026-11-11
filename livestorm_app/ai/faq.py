"""The Follow-up tab's "FAQ": one model call over the FAQ's brief
(`record.faq_brief.build_faq_brief`: the question desk's questions and
their answers, never who asked), written as Markdown (prompt:
`prompts/faq.txt`).

A v1 job (`ai.jobs`, Haiku 5.5 by default): the brief goes first (the
`dossier` layout), the instructions follow. The French version is a
translation of the English one (`api_logic.run_faq`), as for the Session
analysis.
"""

from __future__ import annotations

from livestorm_app.ai.dossier import Dossier
from livestorm_app.ai.request import build_request
from livestorm_app.ai.usage import CallContext
from livestorm_app.config import FAQ_PROMPT_PATH

KIND = "faq"


def load_instructions(output_language: str) -> str:
    """The prompt, with the language the FAQ is written in."""
    text = FAQ_PROMPT_PATH.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError("The FAQ prompt is empty.")
    return text.replace("{output_language}", str(output_language or "English"))


def write_faq(brief: Dossier, *, output_language: str, api_key: str, ctx: CallContext) -> str:
    """The model's Markdown for one brief. Raises the `llm_client` errors."""
    from livestorm_app import llm_client

    request = build_request(KIND, context=brief, instructions=load_instructions(output_language))
    return llm_client.generate_text(KIND, request, api_key=api_key, ctx=ctx)
