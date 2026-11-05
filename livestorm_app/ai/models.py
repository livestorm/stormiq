"""The model catalog: every text model id StormIQ knows, with what each accepts.

This is the one list of text model ids on the server. Admin saves only ids
from it (`ai.jobs.check_model_setting`), and request code reads a model's
parameters from its row instead of guessing from the id. The default model
of each AI job is in `ai/jobs.py`; a test checks every default is a row here.

Status says where a model may be chosen:

- `offered`: v1 jobs (`ai.jobs` specs in the `v1` group). From Claude 5,
  only the 5.5 models have rows, on purpose: Opus 5, Sonnet 5 and Fable are
  refused until a job needs them.
- `legacy`: the classic outputs (overall and deep analysis, Smart Recap,
  content repurposing, translation). These are the ids Admin and the config
  defaults could produce before this catalog existed.
- `hidden`: known, so its parameters are right, but never offered. A saved
  setting may still name it, and it is still used (`ai.jobs.resolve_model`).

Anthropic rows follow the claude-api reference (2026-10). OpenAI rows are
unverified until a live check; their temperature and token parameter match
what today's adapter sends. `gpt-4o*` and `gpt-4.1*` shut down on 2027-01-14.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Literal, Optional, Tuple

Provider = Literal["anthropic", "openai"]
Status = Literal["offered", "legacy", "hidden"]

PROVIDERS: Tuple[str, ...] = ("anthropic", "openai")

ALL_EFFORTS = ("low", "medium", "high", "xhigh", "max")


@dataclass(frozen=True)
class ModelCaps:
    id: str
    provider: Provider
    label: str
    status: Status
    max_output: int  # the largest max_tokens the model accepts
    thinks: bool  # thinks without being asked, into max_tokens: budgets need headroom
    adaptive_thinking: bool  # accepts thinking {"type": "adaptive"}
    effort_levels: Tuple[str, ...]  # () means never send an effort
    temperature_ok: bool  # accepts a non-default temperature (Anthropic: only before API 2026-10-21)
    mid_conversation_system: bool  # accepts role "system" after the first user message
    server_fallback: bool  # accepts fallbacks "default" (Anthropic only)
    min_cache_tokens: int  # shortest prefix that caches
    structured_outputs: bool  # accepts a JSON schema for the answer
    token_param: str = "max_tokens"  # OpenAI's newer models take max_completion_tokens


def _claude(
    id: str,
    label: str,
    status: Status,
    *,
    max_output: int = 128_000,
    thinks: bool,
    effort_levels: Tuple[str, ...] = ALL_EFFORTS,
    temperature_ok: bool = False,
    mid_conversation_system: bool,
    server_fallback: bool = False,
    min_cache_tokens: int,
    adaptive_thinking: bool = True,
) -> ModelCaps:
    return ModelCaps(
        id=id,
        provider="anthropic",
        label=label,
        status=status,
        max_output=max_output,
        thinks=thinks,
        adaptive_thinking=adaptive_thinking,
        effort_levels=effort_levels,
        temperature_ok=temperature_ok,
        mid_conversation_system=mid_conversation_system,
        server_fallback=server_fallback,
        min_cache_tokens=min_cache_tokens,
        structured_outputs=True,
    )


def _openai(id: str, label: str, *, max_output: int, thinks: bool = False, token_param: str = "max_tokens") -> ModelCaps:
    return ModelCaps(
        id=id,
        provider="openai",
        label=label,
        status="legacy",
        max_output=max_output,
        thinks=thinks,
        adaptive_thinking=False,
        effort_levels=(),
        temperature_ok=True,  # the adapter always sends it today; unverified for gpt-5.4-mini
        mid_conversation_system=True,
        server_fallback=False,
        min_cache_tokens=1024,  # automatic prefix caching
        structured_outputs=True,
        token_param=token_param,
    )


CATALOG: Tuple[ModelCaps, ...] = (
    # Claude 5.5: think by default (Opus always), no sampling parameters.
    _claude("claude-haiku-5-5", "Claude Haiku 5.5", "offered", thinks=True,
            mid_conversation_system=True, min_cache_tokens=512),  # no server-side fallback
    _claude("claude-sonnet-5-5", "Claude Sonnet 5.5", "offered", thinks=True,
            mid_conversation_system=True, server_fallback=True, min_cache_tokens=512),
    _claude("claude-opus-5-5", "Claude Opus 5.5", "offered", thinks=True,
            mid_conversation_system=True, server_fallback=True, min_cache_tokens=512),
    # The classic outputs' models. They answer directly unless asked to think.
    _claude("claude-haiku-4-5-20251001", "Claude Haiku 4.5", "legacy", max_output=64_000, thinks=False,
            adaptive_thinking=False, effort_levels=(), temperature_ok=True,
            mid_conversation_system=False, min_cache_tokens=4096),
    _claude("claude-sonnet-4-6", "Claude Sonnet 4.6", "legacy", thinks=False,
            effort_levels=("low", "medium", "high", "max"), temperature_ok=True,
            mid_conversation_system=False, min_cache_tokens=1024),
    _claude("claude-opus-4-8", "Claude Opus 4.8", "legacy", thinks=False,
            mid_conversation_system=True, min_cache_tokens=1024),
    # Admin offered Opus 4.7 until Opus 4.8 replaced it; a saved setting may name it.
    _claude("claude-opus-4-7", "Claude Opus 4.7", "hidden", thinks=False,
            mid_conversation_system=False, min_cache_tokens=2048),
    _openai("gpt-4o-mini", "GPT-4o mini", max_output=16_384),
    _openai("gpt-4o", "GPT-4o", max_output=16_384),
    _openai("gpt-4.1-mini", "GPT-4.1 mini", max_output=32_768),
    _openai("gpt-4.1", "GPT-4.1", max_output=32_768),
    # The OpenAI Smart Recap default. A reasoning model; its limits are unverified.
    _openai("gpt-5.4-mini", "GPT-5.4 mini", max_output=16_384, thinks=True, token_param="max_completion_tokens"),
)

_BY_ID: Dict[str, ModelCaps] = {model.id: model for model in CATALOG}


def catalog_model(model_id: str) -> Optional[ModelCaps]:
    """The catalog row for `model_id`, or None for an id the catalog doesn't know."""
    return _BY_ID.get(str(model_id or "").strip())


def catalog_models(provider: str, status: Status) -> Tuple[ModelCaps, ...]:
    """The rows of one provider with one status, in catalog order."""
    return tuple(model for model in CATALOG if model.provider == provider and model.status == status)
