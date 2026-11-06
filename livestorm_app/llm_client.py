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
request id and the token usage. Every answered call is recorded in
`llm_usage` (`ai.usage.record_call`).

Two ways in:

- `call_llm`, for the classic outputs: their own model, messages,
  `temperature` and `max_tokens`, as before. It returns the text.
- `generate_text(kind, …)` and `generate_json(kind, schema, …)`, for the
  v1 jobs: the model and budget come from the job registry (`ai/jobs.py`),
  no `temperature` is sent, and a JSON step sends its registered schema
  (`ai/schemas.py`) and gets back the validated answer.

Both check why the model stopped, in this order: a refusal raises
`LLMRefused` at once (never retried, its partial text dropped); an answer
cut off at the token limit is retried once if the job's spec says so
(`JobSpec.truncation_retry`), else `LLMOutputTruncated`; then a JSON answer
must match its schema (`LLMInvalidOutput`). Nothing is returned for the
caller to save on any of these errors.
"""

from __future__ import annotations

import logging
import os
import random
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Literal, Optional, Type, TypeVar

import requests
from pydantic import BaseModel, ValidationError

from livestorm_app.ai.models import ModelCaps, catalog_model
from livestorm_app.ai.usage import (
    INVALID_OUTPUT,
    CallContext,
    LLMUsage,
    anthropic_usage,
    check_org_budget,
    openai_usage,
    record_call,
    tag_or_none,
)


logger = logging.getLogger(__name__)

SchemaModel = TypeVar("SchemaModel", bound=BaseModel)


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
# (any other stop: `call_llm` uses its text as before; `generate_text` and
# `generate_json` refuse it).
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


@dataclass(frozen=True)
class JsonFormat:
    """A strict JSON schema the answer must follow (`ai.schemas.strict_json_schema`)."""

    name: str  # the schema's registered name; OpenAI requires one
    schema: Dict[str, Any]


class LLMError(RuntimeError):
    """A model call ended without an answer StormIQ can save."""


class LLMOutputTruncated(LLMError):
    """The model stopped at its token limit, so the output is incomplete."""


REFUSED_MESSAGE = "The model declined this session. Nothing was saved."


class LLMRefused(LLMError):
    """The model declined to answer. StormIQ never asks again, and keeps none
    of a partial answer. Its message is what the user sees, and it is the same
    whatever the category: the category stays on `category` and in the call's
    `llm_usage` row (`refusal_category`), never in the message.

    arq pickles a failed job's exception and the web process reads it back
    for the poll (`queue._job_status`), so it is rebuilt from its category
    (`__reduce__`), not from its message."""

    def __init__(self, category: Optional[str] = None):
        self.category = tag_or_none(category)
        super().__init__(REFUSED_MESSAGE)

    def __reduce__(self):
        return (type(self), (self.category,))


class LLMInvalidOutput(LLMError):
    """The answer can't be used: it doesn't match the job's schema, it is
    empty, or the model stopped for a reason StormIQ doesn't expect."""


# Ceiling for the automatic retry after truncation. Stays within the output
# limits of every configured model (gpt-4o-mini caps at ~16k tokens), and
# under the size a call must stream at (`ai.jobs.STREAM_ABOVE_TOKENS`):
# nothing streams until the SDK transport.
TRUNCATION_RETRY_MAX_TOKENS = 16000

Sender = Callable[..., LLMResult]


def _sender(provider: str) -> Sender:
    return _anthropic_send if provider == "anthropic" else _openai_send


def _active_provider() -> str:
    # Anything but "anthropic" goes to OpenAI, as it always has.
    return "anthropic" if get_provider() == "anthropic" else "openai"


def retry_budget(max_tokens: int, model: str) -> int:
    """The `max_tokens` of the one retry after a cut-off answer: double, capped
    at `TRUNCATION_RETRY_MAX_TOKENS` and at the model's own limit. No larger
    than `max_tokens` means there is no retry."""
    caps = catalog_model(model)
    larger = min(max_tokens * 2, TRUNCATION_RETRY_MAX_TOKENS)
    return min(larger, caps.max_output) if caps is not None else larger


def _job_retries_truncation(ctx: Optional[CallContext]) -> bool:
    """`JobSpec.truncation_retry` for the call's job. A call without a context,
    or of a kind the registry doesn't know, retries as it always has."""
    from livestorm_app.ai.jobs import job_spec

    if ctx is None:
        return True
    try:
        return job_spec(ctx.job_kind).truncation_retry
    except ValueError:
        return True


def _truncated_error(max_tokens: int) -> LLMOutputTruncated:
    return LLMOutputTruncated(
        f"The AI answer was cut off at its length limit ({max_tokens} tokens), so it was not saved. Please try again."
    )


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
    """Call the active LLM provider for a classic output and return the generated text.

    `messages` may contain role="system" entries — the Anthropic adapter
    extracts them into the top-level `system` parameter automatically.

    A refusal raises LLMRefused at once: no retry, and nothing of a partial
    answer is returned. If the model stops at `max_tokens`, the call is
    retried once with a larger budget (`retry_budget`) when the job's spec
    allows it (`JobSpec.truncation_retry`; every classic output does); if the
    answer is still cut off, LLMOutputTruncated is raised so a partial result
    is never saved as if it were complete. Any other stop returns its text,
    as before; callers fail the job on an empty one.

    `ctx` says which job and session the call is for. Every answered call,
    the retry included, gets one `llm_usage` row (`purpose` names the call:
    "main", "truncation_retry"…). Before each send, the retry included, an
    automatic job is checked against its org's daily budget
    (`ai.usage.check_org_budget`); a job a user started never is.
    """
    provider = _active_provider()
    send = _sender(provider)

    def attempt(budget: int, call_purpose: str) -> LLMResult:
        if ctx is not None:
            check_org_budget(ctx.job_kind, ctx.organization_id, model)
        result = send(api_key, model, messages, temperature, budget)
        record_call(ctx, result, purpose=call_purpose, provider=provider, model_requested=model, max_tokens=budget)
        if result.stop == "refusal":
            raise LLMRefused(result.refusal_category)
        return result

    result = attempt(max_tokens, purpose)
    if not result.truncated:
        return result.text
    larger_budget = retry_budget(max_tokens, model) if _job_retries_truncation(ctx) else max_tokens
    if larger_budget > max_tokens:
        logger.warning("LLM output hit max_tokens=%d; retrying once with %d", max_tokens, larger_budget)
        result = attempt(larger_budget, "truncation_retry")
        if not result.truncated:
            return result.text
    raise _truncated_error(max(larger_budget, max_tokens))


# ── v1 jobs: text and JSON through the job registry ───────────────────────


def _job_model(kind: str, ctx: CallContext, *, structured: bool):
    """The job's spec, provider, model row and budget, checked before any paid call."""
    from livestorm_app.ai.jobs import budget, job_spec, resolve_model

    spec = job_spec(kind)
    if ctx is None or ctx.job_kind != spec.kind:
        raise ValueError(f"A {spec.kind} call needs a CallContext for {spec.kind}")
    provider = _active_provider()
    choice = resolve_model(spec.kind, provider)
    caps: Optional[ModelCaps] = choice.caps
    if caps is None:
        raise LLMError(
            f"{choice.model_id} is not in the model catalog, so {spec.label} can't use it. Choose a listed model in Admin."
        )
    if structured and not caps.structured_outputs:
        raise LLMError(f"{caps.label} can't return structured output, so {spec.label} can't use it.")
    max_tokens, stream = budget(spec, caps)
    if stream:
        raise LLMError(
            f"{spec.label} needs {max_tokens} output tokens, which must stream; "
            "streaming comes with the Anthropic SDK transport."
        )
    return spec, provider, caps, max_tokens


def _generate(
    kind: str,
    messages: List[Dict[str, str]],
    *,
    api_key: str,
    ctx: CallContext,
    purpose: str,
    schema: Optional[Type[BaseModel]],
) -> Any:
    from livestorm_app.ai.schemas import strict_json_schema

    spec, provider, caps, max_tokens = _job_model(kind, ctx, structured=schema is not None)
    json_format = JsonFormat(schema.__name__, strict_json_schema(schema)) if schema is not None else None
    send = _sender(provider)

    def attempt(budget: int, call_purpose: str):
        check_org_budget(ctx.job_kind, ctx.organization_id, caps.id)
        result = send(api_key, caps.id, messages, None, budget, json_format=json_format)
        value: Any = None
        problem: Optional[str] = None
        if result.stop == "complete":
            try:
                value, problem = _read_answer(result.text, schema)
            except Exception as exc:  # the call is paid for: its usage row is still written
                value, problem = None, f"could not be read ({type(exc).__name__})"
        record_call(
            ctx, result, purpose=call_purpose, provider=provider, model_requested=caps.id, max_tokens=budget,
            outcome=INVALID_OUTPUT if problem else None,
        )
        if result.stop == "refusal":
            raise LLMRefused(result.refusal_category)
        return result, value, problem

    result, value, problem = attempt(max_tokens, purpose)
    used = max_tokens
    if result.truncated and spec.truncation_retry:
        larger_budget = retry_budget(max_tokens, caps.id)
        if larger_budget > max_tokens:
            logger.warning("LLM output for %s hit max_tokens=%d; retrying once with %d", kind, max_tokens, larger_budget)
            result, value, problem = attempt(larger_budget, "truncation_retry")
            used = larger_budget
    if result.truncated:
        raise _truncated_error(used)
    if result.stop != "complete":
        raise LLMInvalidOutput(
            f"The model stopped unexpectedly ({tag_or_none(result.stop_raw) or 'no reason given'}) during {spec.label}. "
            "Nothing was saved."
        )
    if problem:
        logger.warning("LLM answer for %s was not usable: %s", kind, problem)
        raise LLMInvalidOutput(f"The model's answer for {spec.label} {problem}. Nothing was saved.")
    return value


def _read_answer(text: str, schema: Optional[Type[BaseModel]]):
    """(value, problem): the usable answer, or why it isn't. The problem names
    no content: never the answer's text or its values."""
    if schema is None:
        return (text, None) if text.strip() else (None, "was empty")
    try:
        return schema.model_validate_json(text), None
    except ValidationError as exc:
        kinds = sorted({str(error.get("type") or "") for error in exc.errors(include_input=False, include_url=False)})
        count = exc.error_count()
        return None, f"did not match its format ({count} failed check{'' if count == 1 else 's'}: {', '.join(kinds)})"


def generate_text(
    kind: str,
    messages: List[Dict[str, str]],
    *,
    api_key: str,
    ctx: CallContext,
    purpose: str = "main",
) -> str:
    """A v1 job's text answer. The model and `max_tokens` come from the job
    (`ai.jobs.resolve_model`, `ai.jobs.budget`); no `temperature` is sent.

    Raises LLMRefused, LLMOutputTruncated (after the retry, when the job's
    spec has one) or LLMInvalidOutput (empty, or an unexpected stop). Every
    answered call is recorded in `llm_usage`.
    """
    return _generate(kind, messages, api_key=api_key, ctx=ctx, purpose=purpose, schema=None)


def generate_json(
    kind: str,
    schema: Type[SchemaModel],
    messages: List[Dict[str, str]],
    *,
    api_key: str,
    ctx: CallContext,
    purpose: str = "main",
) -> SchemaModel:
    """A v1 JSON step's answer, validated against `schema` (registered with
    `ai.schemas.json_schema`). The provider is sent the strict schema
    (Anthropic `output_config.format`, OpenAI strict `response_format`); the
    model must accept structured outputs (`ModelCaps.structured_outputs`).

    Checks, in order: refusal (LLMRefused), the token limit
    (LLMOutputTruncated), any other unexpected stop, then the answer against
    the full Pydantic model, bounds included (LLMInvalidOutput). A call
    whose answer fails the schema is recorded with the outcome
    "invalid_output".
    """
    return _generate(kind, messages, api_key=api_key, ctx=ctx, purpose=purpose, schema=schema)


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
    temperature: Optional[float],
    max_tokens: int,
    *,
    json_format: Optional[JsonFormat] = None,
) -> LLMResult:
    """One Messages API call. Text comes from the text blocks only (never a
    thinking or fallback block); a refusal keeps none of it.

    `temperature` None sends none. `json_format` asks for an answer that
    follows its schema (`output_config.format`, type `json_schema`).
    """
    from livestorm_app.config import ANTHROPIC_API_VERSION, ANTHROPIC_MESSAGES_URL

    system_parts = [m["content"] for m in messages if m["role"] == "system"]
    user_messages = [m for m in messages if m["role"] != "system"]

    payload: Dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": user_messages,
    }
    if temperature is not None and anthropic_accepts_temperature(model, ANTHROPIC_API_VERSION):
        payload["temperature"] = temperature
    if system_parts:
        payload["system"] = "\n\n".join(system_parts)
    if json_format is not None:
        payload["output_config"] = {"format": {"type": "json_schema", "schema": json_format.schema}}

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
    stop = _ANTHROPIC_STOPS.get(stop_raw, "other")
    return LLMResult(
        # A refusal (before or during the answer) keeps none of its partial text.
        text="" if stop == "refusal" else text.strip(),
        stop=stop,
        stop_raw=stop_raw,
        # Branch on stop_reason only: stop_details can be null on a refusal.
        refusal_category=tag_or_none(stop_details.get("category")),
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
    temperature: Optional[float],
    max_tokens: int,
    *,
    json_format: Optional[JsonFormat] = None,
) -> LLMResult:
    """One Chat Completions call. A `message.refusal` is a refusal whatever the
    finish reason, and so is `content_filter`; a refusal keeps no text, and the
    refusal's own wording is never kept.

    `temperature` None sends none. `json_format` asks for a strict JSON schema
    answer (`response_format`, type `json_schema`, `strict: true`).
    """
    from livestorm_app.config import OPENAI_CHAT_COMPLETIONS_URL

    token_param = "max_completion_tokens" if str(model or "").startswith("gpt-5") else "max_tokens"
    payload: Dict[str, Any] = {"model": model}
    if temperature is not None:
        payload["temperature"] = temperature
    payload["messages"] = messages
    payload[token_param] = max_tokens
    if json_format is not None:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": json_format.name, "strict": True, "schema": json_format.schema},
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
        text="" if stop == "refusal" else str(message.get("content") or "").strip(),
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
