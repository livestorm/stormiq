"""Provider-agnostic LLM client for text generation.

Active provider is selected by the `llm_provider` admin setting, else the
LLM_PROVIDER environment variable:
  "anthropic" (default) — Anthropic Messages API, reads CLAUDE_API_KEY
  "openai"              — OpenAI Chat Completions API, reads OPENAI_API_KEY

Cover image generation always uses the OpenAI Images API regardless of
the provider; that lives in livestorm_app/ai/cover_image.py.

Switching providers needs the matching API key on the worker; no code
changes. See docs/providers.md.

Each provider adapter (`_anthropic_send`, `_openai_send`) returns one
`LLMResult`: the text, why the model stopped, the model that answered, the
request id and the token usage. `call_llm` records every answered call in
`llm_usage` (`ai.usage.record_call`) and returns the text.
"""

from __future__ import annotations

import logging
import os
import random
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Literal, Optional

import requests

from livestorm_app.ai.models import catalog_model
from livestorm_app.ai.usage import (
    CallContext,
    LLMUsage,
    anthropic_usage,
    check_org_budget,
    openai_usage,
    record_call,
)


logger = logging.getLogger(__name__)


def get_provider() -> str:
    """Return the active LLM provider.

    DB setting 'llm_provider' takes precedence; falls back to the
    LLM_PROVIDER env var, then to "anthropic".
    """
    try:
        from livestorm_app.db import get_setting
        db_val = get_setting("llm_provider")
        if db_val and db_val.strip().lower() in {"anthropic", "openai"}:
            return db_val.strip().lower()
    except Exception:
        pass
    return (os.getenv("LLM_PROVIDER") or "anthropic").strip().lower()


def get_llm_key() -> str:
    """Return the API key for the active LLM provider.

    Raises RuntimeError if the required env var is missing so callers
    get a clear error message at job start rather than at HTTP call time.
    """
    provider = get_provider()
    if provider == "anthropic":
        key = (os.getenv("CLAUDE_API_KEY") or "").strip()
        if not key:
            raise RuntimeError("CLAUDE_API_KEY not configured on worker process.")
        return key
    key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY not configured on worker process.")
    return key


def get_llm_model(kind: str) -> str:
    """The model id an AI job kind uses on the active provider, for example
    "overall_analysis" or "translation" (`ai.jobs.resolve_model`)."""
    from livestorm_app.ai.jobs import resolve_model

    return resolve_model(kind).model_id


# Why the model stopped, the same for both providers: "complete" (it finished),
# "max_tokens" (cut off at the token limit), "refusal" (it declined), "other"
# (any other stop; its text is used as before).
Stop = Literal["complete", "max_tokens", "refusal", "other"]


@dataclass(frozen=True)
class LLMResult:
    """One answered model call (HTTP 200)."""

    text: str  # the answer's text, stripped (Anthropic: the text blocks only)
    stop: Stop
    stop_raw: str  # the provider's own value: stop_reason or finish_reason
    refusal_category: Optional[str]  # Anthropic's stop_details.category; often None
    model_served: str  # the model that answered, as the provider reports it
    request_id: Optional[str]
    usage: LLMUsage
    duration_ms: int  # transport retries included

    @property
    def truncated(self) -> bool:
        return self.stop == "max_tokens"


class LLMError(RuntimeError):
    """A model call ended without an answer StormIQ can save."""


class LLMOutputTruncated(LLMError):
    """The model stopped at its token limit, so the output is incomplete."""


# Ceiling for the automatic retry after truncation. Stays within the output
# limits of every configured model (gpt-4o-mini caps at ~16k tokens).
TRUNCATION_RETRY_MAX_TOKENS = 16000

Sender = Callable[[str, str, List[Dict[str, str]], float, int], LLMResult]


def call_llm(
    api_key: str,
    model: str,
    messages: List[Dict[str, str]],
    temperature: float = 0.2,
    max_tokens: int = 2500,
    *,
    ctx: Optional[CallContext] = None,
    purpose: str = "main",
) -> str:
    """Call the active LLM provider and return the generated text.

    `messages` may contain role="system" entries — the Anthropic adapter
    extracts them into the top-level `system` parameter automatically.

    If the model stops at `max_tokens`, the call is retried once with a
    larger budget; if the answer is still cut off, LLMOutputTruncated is
    raised so a partial result is never saved as if it were complete.

    `ctx` says which job and session the call is for. Every answered call,
    the retry included, gets one `llm_usage` row (`purpose` names the call:
    "main", "truncation_retry"…). Before each send, the retry included, an
    automatic job is checked against its org's daily budget
    (`ai.usage.check_org_budget`); a job a user started never is.
    """
    provider = "anthropic" if get_provider() == "anthropic" else "openai"
    send: Sender = _anthropic_send if provider == "anthropic" else _openai_send

    def attempt(budget: int, call_purpose: str) -> LLMResult:
        if ctx is not None:
            check_org_budget(ctx.job_kind, ctx.organization_id, model)
        result = send(api_key, model, messages, temperature, budget)
        record_call(ctx, result, purpose=call_purpose, provider=provider, model_requested=model, max_tokens=budget)
        return result

    result = attempt(max_tokens, purpose)
    if not result.truncated:
        return result.text
    larger_budget = min(max_tokens * 2, TRUNCATION_RETRY_MAX_TOKENS)
    if larger_budget > max_tokens:
        logger.warning("LLM output hit max_tokens=%d; retrying once with %d", max_tokens, larger_budget)
        result = attempt(larger_budget, "truncation_retry")
        if not result.truncated:
            return result.text
    raise LLMOutputTruncated(
        f"The AI answer was cut off at its length limit ({max(larger_budget, max_tokens)} tokens), "
        "so it was not saved. Please try again."
    )


def _header(resp: Any, name: str) -> Optional[str]:
    headers = getattr(resp, "headers", None)
    value = headers.get(name) if headers is not None else None
    return str(value) if value else None


def _elapsed_ms(started: float) -> int:
    return int(round((time.monotonic() - started) * 1000))


# ── Anthropic ─────────────────────────────────────────────────────────────────

# Claude 4.7 and later models reject a non-default `temperature` with a 400
# (Opus 4.7/4.8, Opus 5/5.5, Sonnet 5/5.5, Haiku 5.5, Fable...). Models up to
# 4.6 (Haiku 4.5, Sonnet 4.6, Opus 4.6 and older) accept it, but only on API
# versions before 2026-10-21: from that version on, every model rejects it.
# A catalog model answers from its row (`ai.models.ModelCaps.temperature_ok`).
# An id outside the catalog (a setting saved before the catalog existed) keeps
# the rule by version below, and unknown or newer ids get no temperature:
# leaving it out is always accepted, sending it is not.
_CLAUDE_VERSION_RE = re.compile(r"^claude-(?:opus|sonnet|haiku)-(\d+)(?:-(\d{1,2}))?(?:-\d{8})?$")
_LEGACY_CLAUDE_RE = re.compile(r"^claude-\d")  # claude-3-5-sonnet-..., claude-3-haiku-...
_LAST_CLAUDE_VERSION_WITH_TEMPERATURE = (4, 6)
_FIRST_API_VERSION_WITHOUT_TEMPERATURE = "2026-10-21"


def anthropic_accepts_temperature(model: str, api_version: str = "") -> bool:
    """Whether the Anthropic model accepts a non-default `temperature` on
    `api_version` (default: the version this app sends, config.ANTHROPIC_API_VERSION)."""
    if not api_version:
        from livestorm_app.config import ANTHROPIC_API_VERSION

        api_version = ANTHROPIC_API_VERSION
    if str(api_version).strip() >= _FIRST_API_VERSION_WITHOUT_TEMPERATURE:  # ISO dates compare as strings
        return False
    caps = catalog_model(model)
    if caps is not None:
        return caps.provider == "anthropic" and caps.temperature_ok
    return claude_version_accepts_temperature(model)


def claude_version_accepts_temperature(model: str) -> bool:
    """The rule by version, for Anthropic ids outside the catalog."""
    name = str(model or "").strip().lower()
    if _LEGACY_CLAUDE_RE.match(name):
        return True
    match = _CLAUDE_VERSION_RE.match(name)
    if not match:
        return False
    version = (int(match.group(1)), int(match.group(2) or 0))
    return version <= _LAST_CLAUDE_VERSION_WITH_TEMPERATURE


_ANTHROPIC_STOPS: Dict[str, Stop] = {
    "end_turn": "complete",
    "stop_sequence": "complete",
    "max_tokens": "max_tokens",
    "refusal": "refusal",
}


def _anthropic_send(
    api_key: str,
    model: str,
    messages: List[Dict[str, str]],
    temperature: float,
    max_tokens: int,
) -> LLMResult:
    """One Messages API call. Text comes from the text blocks only."""
    from livestorm_app.config import ANTHROPIC_API_VERSION, ANTHROPIC_MESSAGES_URL

    system_parts = [m["content"] for m in messages if m["role"] == "system"]
    user_messages = [m for m in messages if m["role"] != "system"]

    payload: Dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": user_messages,
    }
    if anthropic_accepts_temperature(model, ANTHROPIC_API_VERSION):
        payload["temperature"] = temperature
    if system_parts:
        payload["system"] = "\n\n".join(system_parts)

    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_API_VERSION,
        "content-type": "application/json",
    }

    started = time.monotonic()
    resp = _post_with_retry(ANTHROPIC_MESSAGES_URL, headers, payload, provider="anthropic")
    body = resp.json()
    stop_raw = str(body.get("stop_reason") or "")
    stop_details = body.get("stop_details") if isinstance(body.get("stop_details"), dict) else {}
    text = "".join(
        str(block.get("text") or "")
        for block in body.get("content", [])
        if isinstance(block, dict) and block.get("type") == "text"
    )
    return LLMResult(
        text=text.strip(),
        stop=_ANTHROPIC_STOPS.get(stop_raw, "other"),
        stop_raw=stop_raw,
        refusal_category=str(stop_details.get("category") or "") or None,
        model_served=str(body.get("model") or ""),
        request_id=_header(resp, "request-id"),
        usage=anthropic_usage(body.get("usage")),
        duration_ms=_elapsed_ms(started),
    )


# ── OpenAI ────────────────────────────────────────────────────────────────────

_OPENAI_STOPS: Dict[str, Stop] = {
    "stop": "complete",
    "length": "max_tokens",
    "content_filter": "refusal",
}


def _openai_send(
    api_key: str,
    model: str,
    messages: List[Dict[str, str]],
    temperature: float,
    max_tokens: int,
) -> LLMResult:
    """One Chat Completions call. A `message.refusal` is a refusal whatever the finish reason."""
    from livestorm_app.config import OPENAI_CHAT_COMPLETIONS_URL

    token_param = "max_completion_tokens" if str(model or "").startswith("gpt-5") else "max_tokens"
    payload: Dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "messages": messages,
        token_param: max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    started = time.monotonic()
    resp = _post_with_retry(OPENAI_CHAT_COMPLETIONS_URL, headers, payload, provider="openai")
    body = resp.json()
    choices = body.get("choices") or []
    choice = choices[0] if choices and isinstance(choices[0], dict) else {}
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    stop_raw = str(choice.get("finish_reason") or "")
    stop: Stop = "refusal" if message.get("refusal") else _OPENAI_STOPS.get(stop_raw, "other")
    return LLMResult(
        text=str(message.get("content") or "").strip(),
        stop=stop,
        stop_raw=stop_raw,
        refusal_category=None,
        model_served=str(body.get("model") or ""),
        request_id=_header(resp, "x-request-id"),
        usage=openai_usage(body.get("usage")),
        duration_ms=_elapsed_ms(started),
    )


# ── Shared retry helper ────────────────────────────────────────────────────────

# Rate limits, overload (Anthropic 529) and transient server errors are worth
# retrying: the provider did not produce (or bill) an answer.
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504, 529})
LLM_MAX_ATTEMPTS = 4
LLM_BACKOFF_BASE_SECONDS = 2.0
LLM_BACKOFF_MAX_SECONDS = 30.0
# (connect, read). Non-streaming calls send nothing until the whole answer is
# generated, so the read timeout must cover a 16k-token completion.
LLM_REQUEST_TIMEOUT = (10, 300)


def _retry_delay(attempt: int, retry_after: str | None = None) -> float:
    """Seconds to wait before retry `attempt` (0-based).

    Honours a numeric `Retry-After` header (capped), otherwise exponential
    backoff with jitter so parallel jobs don't retry in lockstep.
    """
    try:
        hinted = float(retry_after) if retry_after else 0.0
    except (TypeError, ValueError):
        hinted = 0.0
    if hinted > 0:
        return min(hinted, LLM_BACKOFF_MAX_SECONDS)
    backoff = LLM_BACKOFF_BASE_SECONDS * (2 ** attempt)
    return min(backoff + random.uniform(0, backoff / 2), LLM_BACKOFF_MAX_SECONDS)


def _post_with_retry(
    url: str,
    headers: Dict[str, str],
    payload: Dict[str, Any],
    provider: str,
    max_attempts: int = LLM_MAX_ATTEMPTS,
) -> requests.Response:
    """POST to the provider, retrying transient failures.

    Retried: RETRYABLE_STATUS_CODES and connection errors. These almost
    always mean no answer was produced, but not always: a gateway 502/504
    or a connection reset after the request was sent can hide an answer
    that was generated and billed, and the retry then pays once more. That
    rare double charge is accepted over failing the whole job. Not retried:
    a read timeout, where the model is most likely still generating (and
    billing) the answer; and any other 4xx, which would fail again.
    """
    for attempt in range(max_attempts):
        last_attempt = attempt == max_attempts - 1
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=LLM_REQUEST_TIMEOUT)
        except requests.ConnectionError as exc:
            if last_attempt:
                raise
            wait = _retry_delay(attempt)
            logger.warning(
                "%s connection error on attempt %d/%d (%s) — retrying in %.1fs",
                provider.capitalize(), attempt + 1, max_attempts, type(exc).__name__, wait,
            )
            time.sleep(wait)
            continue
        if resp.status_code in RETRYABLE_STATUS_CODES and not last_attempt:
            wait = _retry_delay(attempt, resp.headers.get("Retry-After"))
            logger.warning(
                "%s HTTP %d on attempt %d/%d — retrying in %.1fs",
                provider.capitalize(), resp.status_code, attempt + 1, max_attempts, wait,
            )
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp
    raise AssertionError("unreachable: the last attempt returns or raises")
