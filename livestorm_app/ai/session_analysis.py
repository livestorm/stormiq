"""The Follow-up tab's "Session analysis": one model call over the session's
brief (`record.session_brief.build_brief`), written as Markdown with four
sections (prompt: `prompts/session_analysis.txt`).

The job kind stays `overall_analysis` (its queue, its bundle and its PDF are
the classic overall analysis's), but since November 2026 it is a v1 job:
the brief goes first (the `dossier` layout), the instructions follow, and
the model is the job's (`ai.jobs`, Sonnet 5.5 by default). The French
version is still a translation of the English one (`api_logic`).
"""

from __future__ import annotations

from livestorm_app.ai.dossier import Dossier
from livestorm_app.ai.request import build_request
from livestorm_app.ai.usage import CallContext
from livestorm_app.config import SESSION_ANALYSIS_PROMPT_PATH

KIND = "overall_analysis"


def load_instructions(output_language: str) -> str:
    """The prompt, with the language the analysis is written in."""
    text = SESSION_ANALYSIS_PROMPT_PATH.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError("The session analysis prompt is empty.")
    return text.replace("{output_language}", str(output_language or "English"))


def write_analysis(brief: Dossier, *, output_language: str, api_key: str, ctx: CallContext) -> str:
    """The model's Markdown for one brief. Raises the `llm_client` errors."""
    from livestorm_app import llm_client

    request = build_request(KIND, context=brief, instructions=load_instructions(output_language))
    return llm_client.generate_text(KIND, request, api_key=api_key, ctx=ctx)
