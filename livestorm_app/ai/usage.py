"""Usage logging for text-model calls, and the org budget for automatic jobs.

Every text-model call that gets an answer (HTTP 200) is recorded once by
`llm_client` (`call_llm`, `generate_text`, `generate_json`), through
`record_call`: one `llm_usage` row and one `llm_usage …` log line. A
truncated, refused or retried call is recorded too, and so is a JSON answer
that fails its schema. A call that fails in transport has no usage and is
only in the log. Surprise me's voice calls (speech, not text) don't go
through `llm_client`; a user starts them, so no budget applies.

A row holds ids, the model, token counts, the estimated cost, the time taken
and the stop reason. Never a prompt, an output, a name or a key.

A failed insert does not fail the job (an owner decision, and the one
exception to "writes raise"): the output was complete and paid for, so
`record_call` logs the line at ERROR, which keeps the data, and the job goes
on to save its output.

The daily budget (`ORG_DAILY_LLM_BUDGET_USD`) applies only to automatic text
jobs (`ai.jobs.automatic_kinds`; none exists yet). A job a user starts is
never held to it.
"""

from __future__ import annotations

import logging
import math
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, Iterator, List, Mapping, Optional

from livestorm_app import db
from livestorm_app.ai.models import catalog_model

if TYPE_CHECKING:
    from livestorm_app.llm_client import LLMResult

logger = logging.getLogger(__name__)

ORG_DAILY_LLM_BUDGET_ENV = "ORG_DAILY_LLM_BUDGET_USD"

# What a call's stop means for its output, stored as `llm_usage.outcome`. A JSON
# step whose complete answer doesn't match its schema, or a v1 call that
# answered nothing, is recorded as "invalid_output" (`INVALID_OUTPUT`).
OUTCOMES: Dict[str, str] = {
    "complete": "ok",
    "max_tokens": "truncated",
    "refusal": "refused",
    "other": "other_stop",
}
INVALID_OUTPUT = "invalid_output"


@dataclass(frozen=True)
class LLMUsage:
    """A call's token counts, the same for both providers."""

    input_tokens: int = 0  # uncached input
    output_tokens: int = 0  # the answer, thinking or reasoning included
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0  # every cache write, 1-hour ones included
    cache_write_1h_tokens: int = 0
    reasoning_tokens: Optional[int] = None  # kept for rows written by OpenAI calls before 2026-11-11
    raw: Dict[str, Any] = field(default_factory=dict)  # the provider's usage object, counts and tags only


@dataclass(frozen=True)
class CallContext:
    """Who a model call is for. Ids only: never content or credentials."""

    job_kind: str  # an `ai.jobs` kind, for example "overall_analysis" or "translation"
    session_id: str = ""
    organization_id: str = ""
    job_id: str = ""  # the deterministic arq id (`queue.ai_job_id`); never a connection id
    record_version: str = ""  # v1 jobs only


# ── Normalising the providers' usage objects ───────────────────────────────


def _count(value: Any) -> int:
    """A token count, or 0 for anything that isn't one. Usage parsing never raises:
    an odd usage object must not cost the call's output."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return 0
    return max(int(value), 0)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


# Short enum-like strings a usage object carries (service tier, iteration type).
_TAG_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


def tag_or_none(value: Any) -> Optional[str]:
    """`value` when it is a short enum-like tag (a refusal category, say), else None."""
    return value if isinstance(value, str) and _TAG_RE.match(value) else None


def sanitize_usage(value: Any, depth: int = 0) -> Any:
    """Keep numbers, short enum-like tags, and objects and lists of them; drop
    anything else, so a usage row can never hold free text."""
    if depth > 4:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value if _TAG_RE.match(value) else None
    if isinstance(value, Mapping):
        kept = {str(key): sanitize_usage(item, depth + 1) for key, item in value.items() if _TAG_RE.match(str(key))}
        return {key: item for key, item in kept.items() if item is not None}
    if isinstance(value, (list, tuple)):
        kept = [sanitize_usage(item, depth + 1) for item in value[:50]]
        return [item for item in kept if item is not None]
    return None


def anthropic_usage(usage: Any) -> LLMUsage:
    """Anthropic's `usage`: `input_tokens` is already the uncached remainder, and
    `output_tokens` includes thinking."""
    usage = _mapping(usage)
    creation = _mapping(usage.get("cache_creation"))
    return LLMUsage(
        input_tokens=_count(usage.get("input_tokens")),
        output_tokens=_count(usage.get("output_tokens")),
        cache_read_tokens=_count(usage.get("cache_read_input_tokens")),
        cache_write_tokens=_count(usage.get("cache_creation_input_tokens")),
        cache_write_1h_tokens=_count(creation.get("ephemeral_1h_input_tokens")),
        raw=sanitize_usage(usage) or {},
    )


def answered_by_fallback(usage: LLMUsage) -> bool:
    """Whether a fallback model answered after the requested one declined
    (Anthropic's `fallbacks`): the usage then lists a `fallback_message`
    attempt in `iterations`. The counts on the usage are the answering
    attempt's alone; the declined attempt's are in `raw` only."""
    iterations = usage.raw.get("iterations") if isinstance(usage.raw, Mapping) else None
    return any(
        isinstance(attempt, Mapping) and attempt.get("type") == "fallback_message"
        for attempt in (iterations if isinstance(iterations, list) else [])
    )


def estimate_cost_usd_micros(model_id: str, usage: LLMUsage) -> Optional[int]:
    """The call's estimated cost in millionths of a dollar, from the model's price in
    the catalog (`ai.models.PRICES_AS_OF`). None when the catalog has no price for it."""
    caps = catalog_model(model_id)
    if caps is None or caps.price is None:
        return None
    prompt_tokens = usage.input_tokens + usage.cache_read_tokens + usage.cache_write_tokens
    price = caps.price.for_prompt(prompt_tokens)
    cache_write_5m = max(usage.cache_write_tokens - usage.cache_write_1h_tokens, 0)
    # Tokens times dollars per million tokens is millionths of a dollar.
    micros = (
        usage.input_tokens * price.input
        + usage.output_tokens * price.output
        + usage.cache_read_tokens * price.cache_read
        + cache_write_5m * price.cache_write_5m
        + usage.cache_write_1h_tokens * price.cache_write_1h
    )
    return int(round(micros))


# ── Recording a call ───────────────────────────────────────────────────────


def _id_or_none(value: Any) -> Optional[str]:
    cleaned = str(value or "").strip()
    return cleaned or None


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _hash_or_none(value: Any) -> Optional[str]:
    """`value` when it is a SHA-256 in hex, else None: a hash column never holds text."""
    return value if isinstance(value, str) and _SHA256_RE.match(value) else None


def build_usage_row(
    ctx: Optional[CallContext],
    result: "LLMResult",
    *,
    purpose: str,
    provider: str,
    model_requested: str,
    max_tokens: int,
    outcome: Optional[str] = None,
    effort: Optional[str] = None,
    prompt_hash: Optional[str] = None,
    dossier_hash: Optional[str] = None,
) -> Dict[str, Any]:
    """The `llm_usage` row for one answered call (`db.insert_llm_usage`). `outcome`
    overrides the one read from the stop (`INVALID_OUTPUT`). A v1 call also
    gives the `effort` it sent and its request's two hashes
    (`ai.request.LLMRequest.prompt_hash`, `dossier_hash`); a value that isn't
    an effort tag or a hex hash is left out."""
    ctx = ctx or CallContext(job_kind="unknown")
    usage = result.usage
    priced_model = result.model_served if catalog_model(result.model_served) else model_requested
    return {
        "organization_id": _id_or_none(ctx.organization_id),
        "session_id": _id_or_none(ctx.session_id),
        "job_kind": _id_or_none(ctx.job_kind) or "unknown",
        "job_id": _id_or_none(ctx.job_id),
        "purpose": purpose,
        "provider": provider,
        "model_requested": str(model_requested or ""),
        "model_served": _id_or_none(result.model_served),
        "effort": tag_or_none(effort),
        "max_tokens": int(max_tokens),
        "streamed": bool(getattr(result, "streamed", False)),
        "stop_reason": str(result.stop_raw or ""),
        "refusal_category": _id_or_none(result.refusal_category),
        "outcome": outcome or OUTCOMES.get(result.stop, "other_stop"),
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read_tokens": usage.cache_read_tokens,
        "cache_write_tokens": usage.cache_write_tokens,
        "cache_write_1h_tokens": usage.cache_write_1h_tokens,
        "reasoning_tokens": usage.reasoning_tokens,
        "cost_usd_micros": estimate_cost_usd_micros(priced_model, usage),
        "duration_ms": int(result.duration_ms),
        "request_id": _id_or_none(result.request_id),
        "prompt_hash": _hash_or_none(prompt_hash),
        "dossier_hash": _hash_or_none(dossier_hash),
        "record_version": _id_or_none(ctx.record_version),
        "usage_raw": usage.raw or None,
    }


_LOG_FIELDS = (
    ("job", "job_kind"),
    ("purpose", "purpose"),
    ("org", "organization_id"),
    ("session", "session_id"),
    ("job_id", "job_id"),
    ("provider", "provider"),
    ("model", "model_requested"),
    ("served", "model_served"),
    ("effort", "effort"),
    ("max_tokens", "max_tokens"),
    ("stop", "stop_reason"),
    ("outcome", "outcome"),
    ("refusal", "refusal_category"),
    ("in", "input_tokens"),
    ("out", "output_tokens"),
    ("cache_read", "cache_read_tokens"),
    ("cache_write", "cache_write_tokens"),
    ("cache_write_1h", "cache_write_1h_tokens"),
    ("reasoning", "reasoning_tokens"),
    ("usd", "cost_usd_micros"),
    ("ms", "duration_ms"),
    ("request_id", "request_id"),
    ("prompt", "prompt_hash"),
    ("dossier", "dossier_hash"),
)


def _log_value(name: str, value: Any) -> str:
    if value is None or value == "":
        return "-"
    if name == "usd":
        return f"{value / 1_000_000:.6f}"
    return re.sub(r"\s+", "_", str(value))


def usage_log_line(row: Mapping[str, Any]) -> str:
    """One line with every field of the row but `usage_raw`: no prompt, output or key."""
    return "llm_usage " + " ".join(f"{name}={_log_value(name, row.get(column))}" for name, column in _LOG_FIELDS)


def _error_name(exc: BaseException) -> str:
    """The exception's class and SQL state: never its message, which could quote values."""
    sqlstate = getattr(exc, "sqlstate", None)
    return f"{type(exc).__name__}" + (f" sqlstate={sqlstate}" if sqlstate else "")


# ── What a run's calls were, for the output it saves ───────────────────────
#
# `collect_calls()` opens a run (the worker wraps each AI job in one); every
# answered call recorded inside it adds its ids and hashes (never content),
# and `run_provenance()` sums them up for `db.merge_cached_bundle_entry`,
# which keeps it beside the saved output in `output_versions`.

_RUN_CALLS: ContextVar[Optional[List[Dict[str, Any]]]] = ContextVar("llm_run_calls", default=None)


@contextmanager
def collect_calls() -> Iterator[List[Dict[str, Any]]]:
    """A run whose answered calls are collected (`run_provenance`)."""
    calls: List[Dict[str, Any]] = []
    token = _RUN_CALLS.set(calls)
    try:
        yield calls
    finally:
        _RUN_CALLS.reset(token)


def _note_call(row: Mapping[str, Any]) -> None:
    calls = _RUN_CALLS.get()
    if calls is None:
        return
    calls.append(
        {
            "job_kind": row.get("job_kind"),
            "job_id": row.get("job_id"),
            "model": row.get("model_served") or row.get("model_requested") or None,
            "prompt_hash": row.get("prompt_hash"),
            "dossier_hash": row.get("dossier_hash"),
            "record_version": row.get("record_version"),
        }
    )


def run_provenance() -> Dict[str, Any]:
    """What made the output this run saves: `{calls, job_id, job_kinds,
    models, prompt_hashes, dossier_hashes, record_versions}`, each list in
    the order first seen, without repeats or blanks. `calls` is 0 outside a
    run (`collect_calls`) or when no call was answered."""
    calls = _RUN_CALLS.get() or []

    def distinct(key: str) -> List[str]:
        seen: List[str] = []
        for call in calls:
            value = call.get(key)
            if isinstance(value, str) and value and value not in seen:
                seen.append(value)
        return seen

    job_ids = distinct("job_id")
    return {
        "calls": len(calls),
        "job_id": job_ids[0] if job_ids else None,
        "job_kinds": distinct("job_kind"),
        "models": distinct("model"),
        "prompt_hashes": distinct("prompt_hash"),
        "dossier_hashes": distinct("dossier_hash"),
        "record_versions": distinct("record_version"),
    }


def record_call(
    ctx: Optional[CallContext],
    result: "LLMResult",
    *,
    purpose: str,
    provider: str,
    model_requested: str,
    max_tokens: int,
    outcome: Optional[str] = None,
    effort: Optional[str] = None,
    prompt_hash: Optional[str] = None,
    dossier_hash: Optional[str] = None,
) -> None:
    """Log one answered call and save its `llm_usage` row. Never raises: the
    call's output is complete and paid for, so losing its usage row must not
    cost it. A failed insert is logged at ERROR with the whole line."""
    try:
        if ctx is None:
            logger.warning("llm_usage: a model call had no call context; it is recorded as job=unknown")
        row = build_usage_row(
            ctx, result, purpose=purpose, provider=provider, model_requested=model_requested, max_tokens=max_tokens,
            outcome=outcome, effort=effort, prompt_hash=prompt_hash, dossier_hash=dossier_hash,
        )
        line = usage_log_line(row)
    except Exception as exc:
        logger.error("llm_usage: could not build the usage row (%s); the output is kept", _error_name(exc))
        return
    logger.info(line)
    try:
        _note_call(row)
    except Exception as exc:
        logger.error("llm_usage: could not note the call for its output (%s)", _error_name(exc))
    try:
        db.insert_llm_usage(row)
    except Exception as exc:
        logger.error("llm_usage insert failed (%s); the output is kept. %s", _error_name(exc), line)


# ── The org budget for automatic jobs ──────────────────────────────────────


class OrgBudgetExceeded(RuntimeError):
    """An automatic job may not run: its org spent its daily budget, the session
    has no org to charge, or the budget setting is unreadable."""


def org_daily_budget_micros() -> Optional[int]:
    """`ORG_DAILY_LLM_BUDGET_USD` in millionths of a dollar; None when it is unset
    (no limit). An invalid value stops automatic jobs until it is fixed."""
    raw = (os.getenv(ORG_DAILY_LLM_BUDGET_ENV) or "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        value = float("nan")
    if not math.isfinite(value) or value < 0:
        raise OrgBudgetExceeded(
            f"{ORG_DAILY_LLM_BUDGET_ENV} is not a dollar amount, so automatic AI jobs are off until it is fixed."
        )
    return int(round(value * 1_000_000))


def check_org_budget(job_kind: str, organization_id: str, model: str, *, now: Optional[datetime] = None) -> None:
    """Raise OrgBudgetExceeded when an automatic job may not make a paid call to `model`.

    Checked in the worker before each send, the truncation retry included
    (`llm_client.call_llm`, `generate_text`, `generate_json`). A job a user
    starts returns at once, without reading anything. An automatic job needs
    an org to charge: a session
    without one never runs it, whatever the budget. With no budget set there
    is no limit. Otherwise the model must have a price in the catalog, so its
    spend is counted, and the org's estimated spend since midnight UTC (every
    job's; rows without a cost count 0) must be under the budget. A failed
    spend read raises, so nothing automatic runs blind.
    """
    from livestorm_app.ai.jobs import automatic_kinds

    if job_kind not in automatic_kinds():
        return
    org = str(organization_id or "").strip()
    if not org:
        raise OrgBudgetExceeded("This session has no organization to charge, so automatic AI jobs don't run on it.")
    budget = org_daily_budget_micros()
    if budget is None:
        return
    caps = catalog_model(model)
    if caps is None or caps.price is None:
        raise OrgBudgetExceeded(
            f"The model {str(model or '').strip() or '(none)'} has no price in the catalog, so its spend can't be "
            "held to the daily AI budget. Automatic AI jobs don't use it while a budget is set."
        )
    now = now or datetime.now(timezone.utc)
    since = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    if db.llm_spend_since(org, since) >= budget:
        raise OrgBudgetExceeded(
            "This organization reached its daily AI budget. Automatic AI jobs start again after midnight UTC."
        )
