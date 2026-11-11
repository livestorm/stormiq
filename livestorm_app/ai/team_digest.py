"""The Follow-up tab's "Team digest": one model call over the digest's brief
(`record.digest_brief.build_digest_brief`: the vitals, key moments,
chapters, open questions and decisions, never a name), written as about
100 words of Slack-friendly Markdown marked internal (prompt:
`prompts/team_digest.txt`).

A v1 job (`ai.jobs`, Haiku 5.5 by default, the `dossier` layout), started
by a click on Follow-up. It checks nothing: the caller passes the answer
through `record.digest_brief.check_digest` (the internal marker, every
number and time in the brief, the length) before saving it. The French
version is a translation of the English one (`api_logic.run_team_digest`).
One call, so nothing is marked for the cache.

Imports no `record/` code: the brief comes in, the text goes out.
"""

from __future__ import annotations

from livestorm_app.ai.dossier import Dossier
from livestorm_app.ai.request import build_request
from livestorm_app.ai.usage import CallContext
from livestorm_app.config import TEAM_DIGEST_PROMPT_PATH

KIND = "team_digest"


def load_instructions() -> str:
    text = TEAM_DIGEST_PROMPT_PATH.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError("The team digest prompt is empty.")
    return text


def write_team_digest(brief: Dossier, *, api_key: str, ctx: CallContext) -> str:
    """The model's Markdown for one brief. Raises the `llm_client` errors."""
    from livestorm_app import llm_client

    request = build_request(KIND, context=brief, instructions=load_instructions())
    return llm_client.generate_text(KIND, request, api_key=api_key, ctx=ctx)
