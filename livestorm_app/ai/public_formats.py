"""The v1.1 public formats on Follow-up (proposal §7): one model call over a
format's brief (`record/format_briefs.py`), written as Markdown for the
audience (prompt: `prompts/<kind>.txt`, listed in
`config.PUBLIC_FORMAT_PROMPT_PATHS`). The first is the attendee follow-up
("You asked, we answered"); the no-show email, the LinkedIn post and the clip
shortlist are meant to reuse this module with their own kind and prompt.

Each format is a v1 job (`ai.jobs`, the `dossier` layout), started by a click
on Follow-up. It checks nothing: the caller (`api_logic._write_public_format`)
redacts the brief before and checks the answer after, for a leak and with
`record.format_briefs.check_format`, before saving it. The French version is
a translation of the English one. One call, so nothing is marked for the
cache.

Imports no `record/` code: the brief comes in, the text goes out.
"""

from __future__ import annotations

from livestorm_app.ai.dossier import Dossier
from livestorm_app.ai.request import build_request
from livestorm_app.ai.usage import CallContext
from livestorm_app.config import PUBLIC_FORMAT_PROMPT_PATHS


def load_instructions(kind: str) -> str:
    path = PUBLIC_FORMAT_PROMPT_PATHS.get(kind)
    if path is None:
        raise RuntimeError(f"There is no public format {kind!r}.")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError(f"The {kind.replace('_', ' ')} prompt is empty.")
    return text


def write_format(kind: str, brief: Dossier, *, api_key: str, ctx: CallContext) -> str:
    """The model's Markdown for one format's brief. Raises the `llm_client` errors."""
    from livestorm_app import llm_client

    request = build_request(kind, context=brief, instructions=load_instructions(kind))
    return llm_client.generate_text(kind, request, api_key=api_key, ctx=ctx)
