"""The LLM client for text generation: Anthropic's Messages API (Claude),
with the worker's CLAUDE_API_KEY. Every text job runs on Claude since the
owner's decision of 2026-11-11; the OpenAI text path is gone. Cover images
still use the OpenAI Images API, in livestorm_app/ai/cover_image.py.
See docs/providers.md.

The adapter returns one `LLMResult`: the text, why the model stopped, the
model that answered, the request id and the token usage. The classic
outputs go through `_anthropic_send`, which writes their bodies as they
always were; the v1 jobs through `_anthropic_call`, with a body written by
`ai/request.py`. Every answered call is recorded in
`llm_usage` (`ai.usage.record_call`).

Two ways in:

- `call_llm`, for the classic outputs: their own model, messages,
  `temperature` and `max_tokens`, as before. It returns the text.
- `generate_text(kind, request, …)` and `generate_json(kind, schema,
  request, …)`, for the v1 jobs: the model and budget come from the job
  registry (`ai/jobs.py`), the request is laid out for the job
  (`ai/request.py`: the dossier or the instructions first, the stable
  prefix cached when a run shares it), the model's catalog row decides the
  effort, the thinking and the fallback, no `temperature` is sent, and a
  JSON step sends its registered schema (`ai/schemas.py`) and gets back the
  validated answer.

Both check why the model stopped, in this order: a refusal raises
`LLMRefused` at once (never retried, its partial text dropped); an answer
cut off at the token limit is retried once if the job's spec says so
(`JobSpec.truncation_retry`), else `LLMOutputTruncated`; then a JSON answer
must match its schema (`LLMInvalidOutput`). Nothing is returned for the
caller to save on any of these errors.

Transport. Anthropic goes through the official SDK (`anthropic` 1.x, which
sends API version 2023-06-01): one client per process (`_anthropic_client`)
with the SDK's own retries off, and StormIQ's retry policy around it
(`_with_transport_retry`). A v1 call goes through the SDK's own methods
(`messages.create`, `messages.stream`; `client.beta.messages` when it asks
for the server-side fallback), and one whose budget is large streams, stopped
at a wall-clock deadline (`JobSpec.call_deadline_s`, `_StreamDeadline`); the
classic outputs never stream, and their request bodies are byte for byte the
ones the old `requests` transport sent (`_post_message`). A failed call raises
`LLMProviderError`, which names the HTTP status and error type only. See
docs/providers.md.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple, Type, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from livestorm_app.ai.models import ModelCaps, catalog_model
from livestorm_app.ai.request import AnthropicCall, JsonFormat, LLMRequest, anthropic_call
from livestorm_app.ai.usage import (
    INVALID_OUTPUT,
    CallContext,
    LLMUsage,
    answered_by_fallback,
    anthropic_usage,
    check_org_budget,
    record_call,
    tag_or_none,
)


logger = logging.getLogger(__name__)

SchemaModel = TypeVar("SchemaModel", bound=BaseModel)
Sent = TypeVar("Sent")


def get_provider() -> str:
    """The text provider: always "anthropic". The `llm_provider` setting and
    the LLM_PROVIDER variable are no longer read (an "openai" saved before
    2026-11-11 is ignored)."""
    return "anthropic"


def get_llm_key() -> str:
    """The Anthropic API key. Raises RuntimeError if CLAUDE_API_KEY is missing,
    so callers get a clear error at job start rather than at HTTP call time."""
    key = (os.getenv("CLAUDE_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("CLAUDE_API_KEY not configured on worker process.")
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
    streamed: bool = False  # the answer came as a stream (Anthropic only)

    @property
    def truncated(self) -> bool:
        return self.stop == "max_tokens"


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


class LLMProviderError(LLMError):
    """The Anthropic call failed without an answer: an HTTP error, a timeout,
    a lost connection or a stream stopped part-way. The message names the HTTP
    status, the API's error type or the error class, never a body, a URL or
    a key. Unlike the SDK's own errors, it survives arq's stored result
    (`queue._job_status` reads it back), so the poll shows the message."""


class LLMDeadlineExceeded(LLMProviderError):
    """A streamed answer ran past its job's wall-clock deadline
    (`JobSpec.call_deadline_s`), so the stream was closed. Its partial text
    is dropped."""


# Ceiling for the automatic retry after truncation. Stays within the output
# limits of every configured model (gpt-4o-mini caps at ~16k tokens), and
# under the size a call must stream at (`ai.jobs.STREAM_ABOVE_TOKENS`): the
# classic outputs never stream.
TRUNCATION_RETRY_MAX_TOKENS = 16000

Sender = Callable[..., LLMResult]


PROVIDER = "anthropic"


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
    """Call Claude for a classic output and return the generated text.

    `messages` may contain role="system" entries — the Anthropic adapter
    extracts them into the top-level `system` parameter automatically. A
    classic call never streams, so its request is the one the classic
    outputs always sent.

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
    def attempt(budget: int, call_purpose: str) -> LLMResult:
        if ctx is not None:
            check_org_budget(ctx.job_kind, ctx.organization_id, model)
        result = _anthropic_send(api_key, model, messages, temperature, budget)
        record_call(ctx, result, purpose=call_purpose, provider=PROVIDER, model_requested=model, max_tokens=budget)
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
    provider = PROVIDER
    choice = resolve_model(spec.kind, provider)
    caps: Optional[ModelCaps] = choice.caps
    if caps is None:
        raise LLMError(
            f"{choice.model_id} is not in the model catalog, so {spec.label} can't use it. Choose a listed model in Admin."
        )
    if structured and not caps.structured_outputs:
        raise LLMError(f"{caps.label} can't return structured output, so {spec.label} can't use it.")
    max_tokens, _ = budget(spec, caps)
    return spec, provider, caps, max_tokens


def _must_stream(max_tokens: int) -> bool:
    from livestorm_app.ai.jobs import STREAM_ABOVE_TOKENS

    return max_tokens > STREAM_ABOVE_TOKENS


def _generate(
    kind: str,
    request: LLMRequest,
    *,
    api_key: str,
    ctx: CallContext,
    purpose: str,
    schema: Optional[Type[BaseModel]],
) -> Any:
    from livestorm_app.ai.schemas import strict_json_schema

    spec, provider, caps, max_tokens = _job_model(kind, ctx, structured=schema is not None)
    if not isinstance(request, LLMRequest):
        raise TypeError(f"A {spec.kind} call takes a request built with ai.request.build_request, not {type(request).__name__}.")
    if request.spec.kind != spec.kind:
        raise ValueError(f"A {spec.kind} call was given a request built for {request.spec.kind}.")
    json_format = JsonFormat(schema.__name__, strict_json_schema(schema)) if schema is not None else None
    prompt_hash = request.prompt_hash(json_format)

    def attempt(budget: int, call_purpose: str):
        check_org_budget(ctx.job_kind, ctx.organization_id, caps.id)
        call = anthropic_call(request, caps, budget, json_format)
        effort: Optional[str] = call.effort
        stream = _must_stream(budget)
        result = _anthropic_call(api_key, call, stream=stream, deadline_s=spec.call_deadline_s if stream else None)
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
            effort=effort, prompt_hash=prompt_hash, dossier_hash=request.dossier_hash,
        )
        _note_cache_and_fallback(request, caps, result, provider)
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


def _note_cache_and_fallback(request: LLMRequest, caps: ModelCaps, result: LLMResult, provider: str) -> None:
    """Two things a v1 call's usage can show that nobody would otherwise see,
    logged at WARNING with ids and counts only.

    - A call that should have read its prefix from the prompt cache and read
      nothing. A cache miss is silent and costs the whole prefix again on
      every later call: the prefix is shorter than the model's minimum, the
      cache's lifetime passed between two calls, or its bytes changed.
    - A call answered by a fallback model after the requested one declined
      (`fallbacks: "default"`). Its usage row names the model that answered;
      the declined attempt's own usage is in the row's `usage_raw` only.
    """
    if request.expects_cache_read and result.usage.cache_read_tokens == 0:
        logger.warning(
            "LLM call %d of %d for %s read nothing from the prompt cache (model %s, minimum prefix %d tokens, request_id=%s)",
            request.call_number, request.shared_calls, request.kind, caps.id, caps.min_cache_tokens, result.request_id or "-",
        )
    if answered_by_fallback(result.usage):
        logger.warning(
            "LLM call for %s was declined by %s and answered by its fallback %s (request_id=%s)",
            request.kind, caps.id, tag_or_none(result.model_served) or "-", result.request_id or "-",
        )


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
    request: LLMRequest,
    *,
    api_key: str,
    ctx: CallContext,
    purpose: str = "main",
) -> str:
    """A v1 job's text answer. `request` is the call's content, laid out for
    the job (`ai.request.build_request(kind, …)`). The model and `max_tokens`
    come from the job (`ai.jobs.resolve_model`, `ai.jobs.budget`); the model's
    catalog row decides the rest (`ai.request.anthropic_call`): the job's
    effort and adaptive thinking where the model takes them, the server-side
    fallback where it accepts one, never a `temperature`. A budget above
    `ai.jobs.STREAM_ABOVE_TOKENS` streams, stopped at the job's
    `call_deadline_s`.

    Raises LLMRefused, LLMOutputTruncated (after the retry, when the job's
    spec has one), LLMInvalidOutput (empty, or an unexpected stop) or, on
    Anthropic, LLMProviderError (the call failed, or a stream ran past its
    deadline). Every answered call is recorded in `llm_usage`, with the
    effort sent, the request's `prompt_hash` and its dossier's hash.
    """
    return _generate(kind, request, api_key=api_key, ctx=ctx, purpose=purpose, schema=None)


def generate_json(
    kind: str,
    schema: Type[SchemaModel],
    request: LLMRequest,
    *,
    api_key: str,
    ctx: CallContext,
    purpose: str = "main",
) -> SchemaModel:
    """A v1 JSON step's answer, validated against `schema` (registered with
    `ai.schemas.json_schema`). `request` as for `generate_text`. The provider
    is sent the strict schema (Anthropic `output_config.format`, OpenAI strict
    `response_format`); the model must accept structured outputs
    (`ModelCaps.structured_outputs`).

    Checks, in order: refusal (LLMRefused), the token limit
    (LLMOutputTruncated), any other unexpected stop, then the answer against
    the full Pydantic model, bounds included (LLMInvalidOutput). A call
    whose answer fails the schema is recorded with the outcome
    "invalid_output".
    """
    return _generate(kind, request, api_key=api_key, ctx=ctx, purpose=purpose, schema=schema)


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


# The SDK client's timeouts: every request, and every wait between two chunks
# of a stream, gives up after 300 s; connecting, after 10 s. A non-streaming
# call sends nothing until the whole answer is generated, so 300 s must cover
# a 16k-token completion.
ANTHROPIC_TIMEOUT = anthropic.Timeout(300.0, connect=10.0)

_client_lock = threading.Lock()
_client: Optional[anthropic.Anthropic] = None


def _build_anthropic_client(api_key: str, *, http_client: Any = None) -> anthropic.Anthropic:
    """A new SDK client. The key and the base URL are passed explicitly, so the
    SDK never reads ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL, a profile or
    federation settings. It would still add ANTHROPIC_CUSTOM_HEADERS to every
    request, over the key and the API version it is given, so a process with
    that variable set gets no client (`_refuse_sdk_custom_headers`).
    `max_retries=0` turns the SDK's own retries off: they would retry
    timeouts, which can bill an answer twice (`_with_transport_retry` is the
    policy). `http_client` is for tests (an httpx2 mock transport)."""
    from livestorm_app.config import ANTHROPIC_BASE_URL

    _refuse_sdk_custom_headers()
    _keep_sdk_debug_log_off()
    return anthropic.Anthropic(
        api_key=api_key,
        base_url=ANTHROPIC_BASE_URL,
        max_retries=0,
        timeout=ANTHROPIC_TIMEOUT,
        http_client=http_client,
    )


def _anthropic_client(api_key: str) -> anthropic.Anthropic:
    """The process's SDK client, built on first use with the key the worker
    read from its own environment (`worker._read_llm_key_or_raise`). A
    different key builds a new one. The key lives only in the client: never
    in a log, a row or an error."""
    global _client
    with _client_lock:
        if _client is None or _client.api_key != api_key:
            _client = _build_anthropic_client(api_key)
        return _client


def _refuse_sdk_custom_headers() -> None:
    """The SDK reads ANTHROPIC_CUSTOM_HEADERS whatever it is given, and its
    headers win over the client's own: one stray line could send every call
    with another key or another API version (2026-10-21 refuses the classic
    outputs' `temperature`). No call is made while it is set. The error names
    the variable, never its value."""
    if (os.getenv("ANTHROPIC_CUSTOM_HEADERS") or "").strip():
        raise LLMError(
            "ANTHROPIC_CUSTOM_HEADERS is set on the worker, so no Anthropic call was made: "
            "the Anthropic SDK would add those headers to every request. Unset it."
        )


def _keep_sdk_debug_log_off() -> None:
    """The SDK logs each request's options, the prompt included, at DEBUG.
    Unless ANTHROPIC_LOG set its logger's level, keep it at INFO so a DEBUG
    root logger can't turn that on. ANTHROPIC_LOG must stay unset outside
    local debugging (docs/operations.md)."""
    sdk_logger = logging.getLogger("anthropic")
    if sdk_logger.level == logging.NOTSET:
        sdk_logger.setLevel(logging.INFO)
    if (os.getenv("ANTHROPIC_LOG") or "").strip():
        logger.warning("ANTHROPIC_LOG is set: the Anthropic SDK may log request details, prompts included. Unset it.")


def _anthropic_send(
    api_key: str,
    model: str,
    messages: List[Dict[str, str]],
    temperature: Optional[float],
    max_tokens: int,
) -> LLMResult:
    """One Messages API call of a classic output, through the SDK client. Text
    comes from the text blocks only (never a thinking or fallback block); a
    refusal keeps none of it. `temperature` None sends none.

    The body is written by `_post_message`, byte for byte as the old
    `requests` transport wrote it
    (tests/fixtures/anthropic-requests-before-sdk.json). A classic output
    never streams and never sends a schema, an effort or a fallback: those
    belong to the v1 jobs' calls (`_anthropic_call`). Transient failures are
    retried by `_with_transport_retry`; a failed call raises LLMProviderError.
    """
    system_parts = [m["content"] for m in messages if m["role"] == "system"]
    user_messages = [m for m in messages if m["role"] != "system"]

    # The fields, and their order, of the old `requests` transport.
    payload: Dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": user_messages,
    }
    # The API takes `temperature` only before version 2026-10-21 (the helper checks).
    if temperature is not None and anthropic_accepts_temperature(model):
        payload["temperature"] = temperature
    if system_parts:
        payload["system"] = "\n\n".join(system_parts)

    client = _anthropic_client(api_key)
    started = time.monotonic()
    try:
        message = _with_transport_retry(lambda: _post_message(client, payload))
    except LLMError:
        raise
    except anthropic.AnthropicError as exc:
        raise _provider_error(exc) from exc
    return _anthropic_result(_message_body(message), getattr(message, "_request_id", None), started, streamed=False)


def _anthropic_call(
    api_key: str,
    call: AnthropicCall,
    *,
    stream: bool = False,
    deadline_s: Optional[float] = None,
) -> LLMResult:
    """One Messages API call of a v1 job, written by `ai.request.anthropic_call`,
    through the SDK's own methods: `messages.create`, or `messages.stream`
    when `stream` (stopped after `deadline_s` seconds). A call that needs a
    beta (the server-side fallback) goes through `client.beta.messages` with
    its `betas`. The same client, timeouts, retry policy and errors as the
    classic outputs' calls (`_anthropic_send`)."""
    client = _anthropic_client(api_key)
    started = time.monotonic()
    try:
        if stream:
            message, request_id = _with_transport_retry(
                lambda: _stream_message(client, call.params, deadline_s, betas=call.betas)
            )
        else:
            message = _with_transport_retry(lambda: _create_message(client, call))
            request_id = getattr(message, "_request_id", None)
    except LLMError:
        raise
    except anthropic.AnthropicError as exc:
        raise _provider_error(exc) from exc
    return _anthropic_result(_message_body(message), request_id, started, streamed=stream)


def _create_message(client: anthropic.Anthropic, call: AnthropicCall) -> Any:
    """One non-streamed call of a v1 job, through the SDK."""
    if call.betas:
        return client.beta.messages.create(betas=list(call.betas), **call.params)
    return client.messages.create(**call.params)


def _post_message(client: anthropic.Anthropic, payload: Dict[str, Any]) -> Any:
    """One non-streamed Messages call through the SDK client, its body written
    here as `requests` wrote it (`json.dumps`: its separators, ASCII escapes,
    the payload's key order). `messages.create` would write compact UTF-8 in
    the SDK's own key order, so a classic output's request would no longer be
    the bytes it sent before. Same client, headers, timeouts and errors."""
    return client.post(
        "/v1/messages",
        cast_to=anthropic.types.Message,
        content=json.dumps(payload, allow_nan=False).encode("utf-8"),
        options={"headers": {"content-type": "application/json"}},
    )


def _open_stream(client: anthropic.Anthropic, params: Dict[str, Any], betas: Tuple[str, ...]) -> Any:
    """The SDK's stream manager for a call: `client.beta.messages` when the
    call needs betas, `client.messages` otherwise. The stream asks for a
    connection of its own (`Connection: close`), so `_StreamDeadline` can
    never shut one that another call reuses."""
    own_connection = {"connection": "close"}
    if betas:
        return client.beta.messages.stream(betas=list(betas), **params, extra_headers=own_connection)
    return client.messages.stream(**params, extra_headers=own_connection)


def _stream_message(
    client: anthropic.Anthropic, params: Dict[str, Any], deadline_s: Optional[float], *, betas: Tuple[str, ...] = ()
) -> Tuple[Any, Optional[str]]:
    """Stream one answer and return (the final message, its request id).

    A failure before the first event (an HTTP error when the stream opens, or
    an `error` event first) raises the SDK's error, for `_with_transport_retry`
    to judge. From the first event on, output may be billed: any failure, and
    the deadline, raise LLMProviderError, which is never retried, and the
    partial answer is dropped.

    `deadline_s` is wall-clock time from the call's start. Checking it at each
    event is not enough: the SDK drops the server's `ping` events before this
    loop sees them, so a model that thinks for minutes without sending text
    yields nothing to check, and each ping restarts the 300 s read timeout.
    A timer (`_StreamDeadline`) therefore shuts the connection at the
    deadline, and the read in progress fails at once. The stream asks for a
    connection of its own (`_open_stream`), so the timer can never shut one
    that another call reuses.
    """
    started = time.monotonic()
    with _open_stream(client, params, betas) as stream:
        deadline = _StreamDeadline(stream, deadline_s - (time.monotonic() - started)) if deadline_s is not None else None
        seen_event = False
        try:
            for _event in stream:
                seen_event = True
                if deadline_s is not None and time.monotonic() - started > deadline_s:
                    raise _deadline_exceeded(params, deadline_s, stream)
            if deadline is not None and deadline.finish():
                raise _deadline_exceeded(params, deadline_s, stream)
            message = stream.get_final_message()
        except LLMError:
            raise
        except Exception as exc:
            if deadline is not None and deadline.finish():  # the timer shut the connection
                raise _deadline_exceeded(params, deadline_s, stream) from exc
            if isinstance(exc, anthropic.AnthropicError):
                if not seen_event:
                    raise
                logger.warning(
                    "Anthropic stream for %s failed after its first event (%s); not retried (request_id=%s)",
                    params.get("model"), type(exc).__name__, stream.request_id,
                )
            else:  # the connection broke while reading (httpx2's own errors)
                logger.warning(
                    "Anthropic stream for %s broke (%s); not retried (request_id=%s)",
                    params.get("model"), type(exc).__name__, stream.request_id,
                )
            raise _provider_error(exc) from exc
        finally:
            if deadline is not None:
                deadline.finish()
        return message, stream.request_id


def _deadline_exceeded(params: Dict[str, Any], deadline_s: float, stream: Any) -> LLMDeadlineExceeded:
    logger.warning(
        "Anthropic stream for %s passed its %.0f s deadline; closed, not retried (request_id=%s)",
        params.get("model"), deadline_s, stream.request_id,
    )
    return LLMDeadlineExceeded(f"The model's answer took longer than {deadline_s:.0f} s, so it was stopped. Nothing was saved.")


class _StreamDeadline:
    """Stops a stream when its deadline passes, whatever the stream is doing:
    a timer thread shuts the connection's socket and closes the stream, and
    the read blocked in `_stream_message` fails at once (closing the response
    alone would leave that read waiting for its 300 s timeout).
    `finish()` stops the timer and says whether it fired; after it, the timer
    touches nothing."""

    def __init__(self, stream: Any, seconds: float):
        self._stream = stream
        self._lock = threading.Lock()
        self._finished = False
        self._fired = False
        self._timer = threading.Timer(max(seconds, 0.0), self._fire)
        self._timer.daemon = True
        self._timer.start()

    def _fire(self) -> None:
        with self._lock:
            if self._finished:
                return
            self._fired = True
        try:
            _shut_socket(self._stream)
            self._stream.close()
        except Exception as exc:  # the read still ends, at the latest at its read timeout
            logger.warning("Could not close an Anthropic stream at its deadline (%s)", type(exc).__name__)

    def finish(self) -> bool:
        with self._lock:
            self._finished = True
        self._timer.cancel()
        return self._fired


def _shut_socket(stream: Any) -> None:
    """Shut the socket under a stream (httpx2's `network_stream` response
    extension), which wakes a read blocked on it in another thread. Nothing
    to shut on a mock transport, or once the stream closed its connection."""
    response = getattr(stream, "response", None)
    network_stream = (getattr(response, "extensions", None) or {}).get("network_stream")
    sock = network_stream.get_extra_info("socket") if network_stream is not None else None
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:  # already closed: the stream ended on its own
            pass


def _message_body(message: Any) -> Dict[str, Any]:
    """The SDK's message as plain data, as the API sent it. Read as data, not
    through the SDK's types, so an odd field can't cost a paid answer."""
    try:
        body = message.to_dict(warnings=False)
    except Exception:
        logger.warning("Could not read the Anthropic message (%s)", type(message).__name__)
        body = {}
    return body if isinstance(body, dict) else {}


def _provider_error(exc: BaseException) -> LLMProviderError:
    """A failed Anthropic call as an error the job can raise and arq can store.
    It names the HTTP status and the API's error type (a short tag), or the
    error's class: never the API's message, a body, a URL or a key."""
    if isinstance(exc, anthropic.APITimeoutError):
        reason = "no answer in time"
    elif isinstance(exc, anthropic.APIStatusError):
        kind = tag_or_none(exc.type)
        if exc.status_code >= 400:
            reason = f"HTTP {exc.status_code}" + (f" {kind}" if kind else "")
        else:  # an `error` event in the stream
            reason = f"error while answering: {kind or 'no type given'}"
    elif isinstance(exc, anthropic.APIConnectionError):
        reason = "connection error"
    else:
        reason = f"the stream broke: {type(exc).__name__}"
    return LLMProviderError(f"The Anthropic call failed ({reason}). Nothing was saved. Please try again.")


def _anthropic_result(body: Dict[str, Any], request_id: Optional[str], started: float, *, streamed: bool) -> LLMResult:
    """One answered Messages call (its JSON body) as an LLMResult."""
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
        request_id=str(request_id) if request_id else None,
        usage=anthropic_usage(body.get("usage")),
        duration_ms=_elapsed_ms(started),
        streamed=streamed,
    )


# ── Retries ────────────────────────────────────────────────────────────────────

# Rate limits, overload (Anthropic 529) and transient server errors are worth
# retrying: the provider did not produce (or bill) an answer.
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504, 529})
LLM_MAX_ATTEMPTS = 4
LLM_BACKOFF_BASE_SECONDS = 2.0
LLM_BACKOFF_MAX_SECONDS = 30.0


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


def _anthropic_retryable(exc: anthropic.APIStatusError) -> bool:
    """An HTTP error worth retrying: RETRYABLE_STATUS_CODES (the
    SDK raises 529 as OverloadedError, which is not an InternalServerError),
    or an `overloaded_error` event that opened a stream."""
    return exc.status_code in RETRYABLE_STATUS_CODES or exc.type == "overloaded_error"


def _with_transport_retry(send: Callable[[], Sent], max_attempts: int = LLM_MAX_ATTEMPTS) -> Sent:
    """Call Anthropic through the SDK (`send`), retrying transient failures.

    Retried: RETRYABLE_STATUS_CODES and connection errors. These almost
    always mean no answer was produced, but not always: a gateway 502/504
    or a connection reset after the request was sent can hide an answer
    that was generated and billed, and the retry then pays once more. That
    rare double charge is accepted over failing the whole job. Not retried:
    a read timeout, where the model is most likely still generating (and
    billing) the answer; and any other 4xx, which would fail again.

    The SDK raises every timeout, a connect timeout included, as
    APITimeoutError, a subclass of APIConnectionError: it is caught first and
    never retried, so a connect timeout is no longer retried (requests
    counted it as a connection error). A stream is retried only when it fails
    before its first event: `_stream_message` raises anything later as
    LLMProviderError, which this never catches.
    """
    for attempt in range(max_attempts):
        last_attempt = attempt == max_attempts - 1
        try:
            return send()
        except anthropic.APITimeoutError:
            raise
        except anthropic.APIStatusError as exc:
            if last_attempt or not _anthropic_retryable(exc):
                raise
            wait = _retry_delay(attempt, exc.response.headers.get("retry-after"))
            logger.warning(
                "Anthropic HTTP %d (%s) on attempt %d/%d — retrying in %.1fs",
                exc.status_code, tag_or_none(exc.type) or "-", attempt + 1, max_attempts, wait,
            )
        except anthropic.APIConnectionError as exc:
            if last_attempt:
                raise
            wait = _retry_delay(attempt)
            logger.warning(
                "Anthropic connection error on attempt %d/%d (%s) — retrying in %.1fs",
                attempt + 1, max_attempts, type(exc.__cause__ or exc).__name__, wait,
            )
        time.sleep(wait)
    raise AssertionError("unreachable: the last attempt returns or raises")
