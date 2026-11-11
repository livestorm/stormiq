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

Rows follow the claude-api reference (2026-10), prices included
(`PRICES_AS_OF`). StormIQ calls Claude for every text job since the owner's
decision of 2026-11-11: the OpenAI text models are gone (OpenAI is used for
cover images only, `ai/cover_image.py`).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Dict, Literal, Optional, Tuple

Provider = Literal["anthropic"]
Status = Literal["offered", "legacy", "hidden"]

PROVIDERS: Tuple[str, ...] = ("anthropic",)

ALL_EFFORTS = ("low", "medium", "high", "xhigh", "max")

# The date the prices below were checked. A usage row's cost is estimated with
# the prices in force when it was written (`ai.usage.estimate_cost_usd_micros`).
PRICES_AS_OF = "2026-10-06"


@dataclass(frozen=True)
class Price:
    """US dollars per million tokens."""

    input: float  # uncached input
    output: float  # thinking included
    cache_write_5m: float
    cache_write_1h: float
    cache_read: float
    # A second rate card for long prompts (Claude Haiku 5.5 above 100K input tokens).
    long_prompt_above: int = 0
    long_prompt: Optional["Price"] = None

    def for_prompt(self, prompt_tokens: int) -> "Price":
        """The rate card for a prompt of `prompt_tokens` input tokens, cached ones included."""
        if self.long_prompt is not None and prompt_tokens > self.long_prompt_above:
            return self.long_prompt
        return self


def _anthropic_price(input: float, output: float, *, cache_read: Optional[float] = None) -> Price:
    """Cache writes cost 1.25x the input price (5 minutes) or 2x (1 hour); reads 0.1x
    unless the model's card says otherwise."""
    return Price(
        input=input,
        output=output,
        cache_write_5m=input * 1.25,
        cache_write_1h=input * 2,
        cache_read=input * 0.1 if cache_read is None else cache_read,
    )


# Claude Haiku 5.5 charges more for a prompt above 100K tokens.
_HAIKU_5_5_PRICE = replace(
    _anthropic_price(0.10, 0.50), long_prompt_above=100_000, long_prompt=_anthropic_price(0.50, 2.50),
)


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
    price: Optional[Price] = None  # None: unknown, so usage rows carry no cost


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
    price: Price,
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
        price=price,
    )


CATALOG: Tuple[ModelCaps, ...] = (
    # Claude 5.5: think by default (Opus always), no sampling parameters.
    _claude("claude-haiku-5-5", "Claude Haiku 5.5", "offered", thinks=True,
            mid_conversation_system=True, min_cache_tokens=512,  # no server-side fallback
            price=_HAIKU_5_5_PRICE),
    _claude("claude-sonnet-5-5", "Claude Sonnet 5.5", "offered", thinks=True,
            mid_conversation_system=True, server_fallback=True, min_cache_tokens=512,
            price=_anthropic_price(2.00, 10.00, cache_read=0.20)),
    _claude("claude-opus-5-5", "Claude Opus 5.5", "offered", thinks=True,
            mid_conversation_system=True, server_fallback=True, min_cache_tokens=512,
            price=_anthropic_price(4.00, 20.00, cache_read=0.20)),
    # The classic outputs' models. They answer directly unless asked to think.
    _claude("claude-haiku-4-5-20251001", "Claude Haiku 4.5", "legacy", max_output=64_000, thinks=False,
            adaptive_thinking=False, effort_levels=(), temperature_ok=True,
            mid_conversation_system=False, min_cache_tokens=4096, price=_anthropic_price(1.00, 5.00)),
    _claude("claude-sonnet-4-6", "Claude Sonnet 4.6", "legacy", thinks=False,
            effort_levels=("low", "medium", "high", "max"), temperature_ok=True,
            mid_conversation_system=False, min_cache_tokens=1024, price=_anthropic_price(3.00, 15.00)),
    _claude("claude-opus-4-8", "Claude Opus 4.8", "legacy", thinks=False,
            mid_conversation_system=True, min_cache_tokens=1024, price=_anthropic_price(5.00, 25.00)),
    # Admin offered Opus 4.7 until Opus 4.8 replaced it; a saved setting may name it.
    _claude("claude-opus-4-7", "Claude Opus 4.7", "hidden", thinks=False,
            mid_conversation_system=False, min_cache_tokens=2048, price=_anthropic_price(5.00, 25.00)),
)

_BY_ID: Dict[str, ModelCaps] = {model.id: model for model in CATALOG}


def catalog_model(model_id: str) -> Optional[ModelCaps]:
    """The catalog row for `model_id`, or None for an id the catalog doesn't know."""
    return _BY_ID.get(str(model_id or "").strip())


def catalog_models(provider: str, status: Status) -> Tuple[ModelCaps, ...]:
    """The rows of one provider with one status, in catalog order."""
    return tuple(model for model in CATALOG if model.provider == provider and model.status == status)
