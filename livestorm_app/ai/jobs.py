"""The job registry: one spec per AI job kind, and the model each one uses.

A job's model is the only thing an admin may change. Its effort and token
budget are code, reviewed against the eval. The model is resolved per job
(`resolve_model`) from, in order:

1. the per-job setting `llm_model.anthropic.<kind>`;
2. for the classic outputs only, the older key that names no provider:
   `llm_default_model` (three jobs) or `llm_smart_recap_model` (Smart Recap);
3. the job's default.

Every job runs on Claude (the owner's decision, 2026-11-11). A saved id that
isn't a Claude model (an OpenAI id saved before then) is skipped, with a
WARNING, for the next source. A saved Claude id that is no longer offered is
still used, with a WARNING: swapping it silently would change classic
outputs. Admin refuses such ids (`check_model_setting`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Literal, Optional, Tuple

from livestorm_app.ai.models import ALL_EFFORTS, PROVIDERS, ModelCaps, catalog_model, catalog_models

logger = logging.getLogger(__name__)

Effort = Literal["low", "medium", "high", "xhigh", "max"]
Group = Literal["v1", "legacy"]
Source = Literal["setting", "legacy_setting", "default"]
# How a job's request is laid out (`ai/request.py`): the session dossier
# first and the job's instructions after it; the instructions first and the
# call's own data after them; or the classic outputs' own messages.
Layout = Literal["dossier", "instructions", "legacy"]
CacheTtl = Literal["5m", "1h"]

LEGACY_DEFAULT_SETTING = "llm_default_model"
LEGACY_RECAP_SETTING = "llm_smart_recap_model"
PER_JOB_SETTING_PREFIX = "llm_model."
_PROVIDER_NAMES = {"anthropic": "Anthropic"}

# Room for thinking on top of the visible answer, per effort, for models
# that think by default. A guess until llm_usage holds enough rows per job to calibrate it.
THINKING_HEADROOM: Dict[str, int] = {"low": 4_000, "medium": 16_000, "high": 32_000, "xhigh": 48_000, "max": 64_000}
# Above this max_tokens, `budget` marks a call to stream so it can't hit the
# HTTP timeout (`llm_client._stream_message`, Anthropic only). The classic
# outputs never stream.
STREAM_ABOVE_TOKENS = 16_000
# A streamed call's wall-clock limit unless its spec sets one (`JobSpec.call_deadline_s`).
DEFAULT_CALL_DEADLINE_S = 600


@dataclass(frozen=True)
class JobSpec:
    kind: str  # the AI job kind; matches queue.ai_job_id kinds where one exists
    label: str
    group: Group  # "v1": the v1 Report's jobs; "legacy": the classic outputs
    default_anthropic: str
    effort: Effort  # sent only to models that accept it
    answer_tokens: int  # the cap for the visible answer
    legacy_setting: Optional[str] = None  # the older setting that also picks this job's model
    # Runs without a user asking (on first open, after a fetch). Only these
    # jobs are held to the org's daily budget (`ai.usage.check_org_budget`).
    automatic: bool = False
    # An answer cut off at max_tokens is asked for once more with double the
    # budget (`llm_client.retry_budget`). Off: the job fails at once.
    truncation_retry: bool = False
    # A streamed call is stopped, and the job fails, once this many seconds have
    # passed since it started, whether or not the stream is sending anything
    # (`llm_client._StreamDeadline` shuts the connection).
    call_deadline_s: int = DEFAULT_CALL_DEADLINE_S
    layout: Layout = "legacy"
    # The request's stable prefix (the dossier, or the instructions) may be
    # cached. A request marks it only when its run makes at least two calls
    # over that prefix: with one call, a cache write is a pure surcharge.
    cache: bool = False
    cache_ttl: CacheTtl = "5m"
    # Ask Anthropic to answer a declined request with its recommended
    # fallback model, on the models that accept it (`ModelCaps.server_fallback`).
    fallbacks: bool = True

    def default_model(self, provider: str) -> Optional[str]:
        return self.default_anthropic if provider == "anthropic" else None

    def setting_key(self, provider: str) -> str:
        return f"{PER_JOB_SETTING_PREFIX}{provider}.{self.kind}"


JOB_SPECS: Tuple[JobSpec, ...] = (
    # v1 jobs.
    # A cut-off answer fails them at once: their budgets already hold thinking headroom.
    # Layouts: the understanding passes share one dossier, so it comes first
    # and is cached; the other jobs send their instructions first, and the
    # chat labels cache them across their batches.
    JobSpec("profile", "Session profile", "v1", "claude-haiku-5-5", "low", 2_000, layout="instructions"),
    JobSpec("chat_labels", "Chat labels", "v1", "claude-haiku-5-5", "low", 8_000, layout="instructions", cache=True),
    JobSpec("understanding", "Session understanding", "v1", "claude-sonnet-5-5", "medium", 32_000,
            layout="dossier", cache=True),
    JobSpec("verify_meaning", "Meaning check", "v1", "claude-haiku-5-5", "low", 2_000, layout="instructions"),
    # The classic outputs. Their callers still pass today's max_tokens, and a
    # cut-off answer is retried once, as before.
    # The Follow-up's "Session analysis" (`ai/session_analysis.py`): a v1 job since
    # November 2026, over the session's brief.
    JobSpec("overall_analysis", "Session analysis", "v1", "claude-sonnet-5-5", "medium", 6_000, layout="dossier"),
    # The Follow-up's "FAQ" (`ai/faq.py`): the audience's questions and their
    # answers, from the question desk (`record/faq_brief.py`).
    JobSpec("faq", "FAQ", "v1", "claude-haiku-5-5", "low", 4_000, layout="dossier"),
    # The Follow-up's "Decisions and commitments" (`ai/decisions.py`): what was
    # decided or promised on stage, from the understanding's dossier.
    JobSpec("decisions", "Decisions and commitments", "v1", "claude-sonnet-5-5", "medium", 8_000, layout="dossier"),
    # The Follow-up's "Team digest" (`ai/team_digest.py`): about 100 words for
    # the team's channel, from the digest's brief (`record/digest_brief.py`).
    JobSpec("team_digest", "Team digest", "v1", "claude-haiku-5-5", "low", 1_000, layout="dossier"),
    # The Follow-up's "Attendee follow-up" (`ai/public_formats.py`): the email
    # the host sends attendees, from its brief (`record/format_briefs.py`).
    JobSpec("attendee_followup", "Attendee follow-up", "v1", "claude-sonnet-5-5", "medium", 6_000, layout="dossier"),
    # Surprise me's storyboard (`ai/surprise_story.py`): a headline, a number or a
    # room's line per beat of the saved take, on the Play click. Haiku: a short
    # pick from given words, checked word for word in code, with the player's
    # own scenes as the fallback, while the user waits for the voice.
    JobSpec("surprise_storyboard", "Surprise me storyboard", "v1", "claude-haiku-5-5", "low", 3_000, layout="instructions"),
    JobSpec("deep_analysis", "Deep Analysis", "legacy", "claude-haiku-4-5-20251001",
            "medium", 5_000, LEGACY_DEFAULT_SETTING, truncation_retry=True),
    JobSpec("smart_recap", "Smart Recap", "legacy", "claude-sonnet-4-6",
            "low", 900, LEGACY_RECAP_SETTING, truncation_retry=True),
    JobSpec("content_repurposing", "Content Repurposing", "legacy", "claude-haiku-4-5-20251001",
            "medium", 12_000, LEGACY_DEFAULT_SETTING, truncation_retry=True),
    # French versions of the analyses (6,000 or 8,000 tokens) and of the content bundle (12,000).
    JobSpec("translation", "Translation", "legacy", "claude-haiku-4-5-20251001",
            "low", 12_000, LEGACY_DEFAULT_SETTING, truncation_retry=True),
)

_SPECS: Dict[str, JobSpec] = {spec.kind: spec for spec in JOB_SPECS}


class NoModelForJob(RuntimeError):
    """The active provider has no model for this job yet."""


class InvalidModelSetting(ValueError):
    """Admin tried to save a model id a job can't use. The message says why."""


def job_spec(kind: str) -> JobSpec:
    spec = _SPECS.get(str(kind or "").strip())
    if spec is None:
        raise ValueError(f"Unknown AI job kind: {kind!r}")
    return spec


def automatic_kinds() -> frozenset:
    """The job kinds that run without a user asking. None does yet."""
    return frozenset(spec.kind for spec in JOB_SPECS if spec.automatic)


def allowed_models(provider: str, kind: str) -> Tuple[ModelCaps, ...]:
    """The models Admin may choose for a job on a provider: offered models for
    v1 jobs, the classic outputs' models for legacy jobs."""
    spec = job_spec(kind)
    return catalog_models(provider, "offered" if spec.group == "v1" else "legacy")


def budget(spec: JobSpec, caps: ModelCaps) -> Tuple[int, bool]:
    """(max_tokens, stream) for a job on a model: the answer, plus thinking
    headroom when the model thinks by default, capped at the model's limit."""
    max_tokens = spec.answer_tokens + (THINKING_HEADROOM[spec.effort] if caps.thinks else 0)
    max_tokens = min(max_tokens, caps.max_output)
    return max_tokens, max_tokens > STREAM_ABOVE_TOKENS


def effort_for(spec: JobSpec, caps: ModelCaps) -> Optional[str]:
    """The effort a job sends to a model: its own when the model accepts
    that level, else the highest level the model accepts below it, else the
    model's lowest. None for a model that takes no effort (it must never be
    sent one)."""
    levels = caps.effort_levels
    if not levels:
        return None
    if spec.effort in levels:
        return spec.effort
    wanted = ALL_EFFORTS.index(spec.effort)
    below = [level for level in levels if ALL_EFFORTS.index(level) < wanted]
    return below[-1] if below else levels[0]


# ── Which model a job uses ─────────────────────────────────────────────────


@dataclass(frozen=True)
class ModelChoice:
    kind: str
    provider: str
    model_id: str
    source: Source  # where the id came from
    caps: Optional[ModelCaps]  # None: a saved id the catalog doesn't know


def _read_setting(key: str) -> str:
    try:
        from livestorm_app.db import get_setting

        return str(get_setting(key) or "").strip()
    except Exception:
        logger.exception("Could not read setting %s; using the next source", key)
        return ""


def _saved_choice(spec: JobSpec, provider: str, key: str, saved: str, source: Source) -> Optional[ModelChoice]:
    """The saved id as a choice, or None when it belongs to another provider
    (an OpenAI id saved before every job ran on Claude)."""
    caps = catalog_model(saved)
    if caps is None and not saved.startswith("claude-"):
        logger.warning("Setting %s names %s, which is not a Claude model: %s uses the next source", key, saved, spec.kind)
        return None
    if caps is not None and caps.provider != provider:
        logger.warning(
            "Setting %s names %s, an %s model, but the provider is %s: %s uses the next source",
            key, saved, caps.provider, provider, spec.kind,
        )
        return None
    if saved not in {model.id for model in allowed_models(provider, spec.kind)}:
        logger.warning(
            "Setting %s names %s, which is not offered for %s; still using it. Choose an offered model in Admin",
            key, saved, spec.kind,
        )
    return ModelChoice(spec.kind, provider, saved, source, caps)


def resolve_model(kind: str, provider: Optional[str] = None) -> ModelChoice:
    """The model a job uses on `provider` (default: the active provider)."""
    spec = job_spec(kind)
    if provider is None:
        from livestorm_app.llm_client import get_provider

        provider = get_provider()
    provider = "anthropic"  # the only text provider (`llm_client.get_provider`)
    sources = [(spec.setting_key(provider), "setting")]
    if spec.legacy_setting:
        sources.append((spec.legacy_setting, "legacy_setting"))
    for key, source in sources:
        saved = _read_setting(key)
        choice = _saved_choice(spec, provider, key, saved, source) if saved else None
        if choice is not None:
            return choice
    default = spec.default_model(provider)
    if not default:
        raise NoModelForJob(f"No {_PROVIDER_NAMES[provider]} model is set up for {spec.label} yet.")
    return ModelChoice(spec.kind, provider, default, "default", catalog_model(default))


# ── What Admin may save ────────────────────────────────────────────────────


def _per_job_key(key: str) -> Optional[Tuple[str, JobSpec]]:
    if not key.startswith(PER_JOB_SETTING_PREFIX):
        return None
    provider, _, kind = key[len(PER_JOB_SETTING_PREFIX):].partition(".")
    spec = _SPECS.get(kind)
    if provider not in PROVIDERS or spec is None:
        return None
    return provider, spec


def is_model_setting(key: str) -> bool:
    """Whether `key` is a model setting: a legacy key or `llm_model.<provider>.<kind>`."""
    return key in (LEGACY_DEFAULT_SETTING, LEGACY_RECAP_SETTING) or _per_job_key(key) is not None


def check_model_setting(key: str, value: str) -> None:
    """Raise InvalidModelSetting unless `value` may be saved under model setting `key`.

    A per-job key takes a model offered for that job on its provider. A legacy
    key takes a model of either provider that every job it covers can use: it
    is applied only while that model's provider is active.
    """
    value = str(value or "").strip()
    shown = value if len(value) <= 80 else value[:77] + "..."
    per_job = _per_job_key(key)
    if per_job is not None:
        provider, spec = per_job
        allowed = [model.id for model in allowed_models(provider, spec.kind)]
        if not allowed:
            raise InvalidModelSetting(f"No {_PROVIDER_NAMES[provider]} model is offered for {spec.label} yet.")
        if value not in allowed:
            raise InvalidModelSetting(
                f"{shown!r} is not an {_PROVIDER_NAMES[provider]} model offered for {spec.label}. "
                f"Choose one of: {', '.join(allowed)}."
            )
        return
    specs = [spec for spec in JOB_SPECS if spec.legacy_setting == key]
    if not specs:
        raise InvalidModelSetting(f"{key} is not a model setting.")
    allowed = [
        model.id
        for provider in PROVIDERS
        for model in allowed_models(provider, specs[0].kind)
        if all(model in allowed_models(provider, spec.kind) for spec in specs[1:])
    ]
    if value not in allowed:
        caps = catalog_model(value)
        jobs = ", ".join(spec.label for spec in specs)
        reason = f"{caps.label} is not offered for {jobs}" if caps else f"Unknown model id {shown!r}"
        raise InvalidModelSetting(f"{reason}. Choose one of: {', '.join(allowed)}.")
