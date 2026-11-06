"""Check with real, paid calls that the models StormIQ plans to use accept the parameters it plans to send.

It costs money, so it runs only with `--i-understand-this-costs`. Run it by
hand, never in CI. It reads the keys from the environment (`CLAUDE_API_KEY`,
`OPENAI_API_KEY`) and never from `.env`: set them explicitly. A provider
whose key is unset is skipped. It touches no database and writes nothing.

    CLAUDE_API_KEY=… OPENAI_API_KEY=… .venv/bin/python scripts/llm_smoke.py --i-understand-this-costs [--legacy] [--model ID ...]

What it calls, one small request per line of output:

- **Anthropic, every `offered` model of the catalog** (`ai/models.py`), through
  the same SDK client as the app (`llm_client._build_anthropic_client`: the
  key passed explicitly, the SDK's retries off, the same timeouts). One call
  per planned parameter, so a refusal names it: none (`plain`), each effort
  the v1 jobs use (`output_config.effort`), adaptive thinking, a JSON schema
  (`output_config.format`), an instruction after the data as a mid-conversation
  `system` message, the server-side fallback (`fallbacks: "default"`, beta
  `server-side-fallback-2026-07-01`) and a stream with a budget over 16,000
  tokens; each where the model's catalog row says it applies. Then one call
  per v1 job shape (`job=<kind>`): the job's budget (`ai.jobs.budget`, so it
  streams when the budget says so), its effort and adaptive thinking. No call
  sends `temperature`.
- **Three dossier calls** on `--dossier-model` (default `claude-haiku-5-5`): a
  synthetic record of about 3,000 tokens marked `cache_control`, then the same
  request again (its `cache_read_tokens` must be above 0: a 0 counts as a
  cache miss), then the same with a JSON schema added (does a schema change
  the cached prefix?).
- **With `--legacy`**, the classic outputs' Claude models with their
  `temperature`, on the API version the SDK sends (2023-06-01).
- **OpenAI, every catalog row**: plain (with the row's token parameter),
  `temperature`, a strict JSON schema, the other token parameter, and
  `reasoning_effort`.

What it prints, one line per call:

    anthropic claude-haiku-5-5 effort=low accepted stop=end_turn
    anthropic claude-haiku-5-5 dossier-repeat accepted stop=end_turn cache_read=2911
    openai gpt-4o-mini reasoning_effort=low rejected HTTP 400 invalid_request_error param=reasoning_effort

`accepted` or `rejected` (with the HTTP status, the API's error type and, for
OpenAI, the parameter it names), or `failed` with the error's class when there
was no HTTP answer. Then the stop reason, and `cache_read=` for the dossier
calls, with `(expected > 0)` after a repeat that read no cache. Never a
prompt, an answer, an error message, a header or a key.

The last line counts them: `calls=… not_accepted=… cache_misses=…`. The
script exits 1 when either count is above 0 (some rejections are expected:
read the lines), 2 without the flag, 0 otherwise.

Cost: about 60 calls (35 to Anthropic, 25 to OpenAI, 3 more with `--legacy`)
with prompts of a few dozen tokens and one-word answers (the 5.5 models may
think first), plus the three dossier calls of about 3,000 tokens on Haiku 5.5.
Well under one dollar in total.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, TextIO

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import anthropic  # noqa: E402
import requests  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from livestorm_app import llm_client  # noqa: E402
from livestorm_app.ai import jobs, models, schemas  # noqa: E402
from livestorm_app.config import OPENAI_CHAT_COMPLETIONS_URL  # noqa: E402

CONFIRM_FLAG = "--i-understand-this-costs"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
PROMPT = "Reply with the single word OK."
SMALL_BUDGET = 4_000  # room for a 5.5 model's thinking before a one-word answer
STREAM_BUDGET = 20_000  # over ai.jobs.STREAM_ABOVE_TOKENS
OPENAI_BUDGET = 256
OPENAI_TIMEOUT = (10, 120)
_TAG_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


class SmokeAnswer(BaseModel):
    """The JSON schema the format probes send."""

    word: str


SMOKE_SCHEMA = schemas.build_strict_schema(SmokeAnswer)


@dataclass(frozen=True)
class Probe:
    provider: str  # "anthropic" | "openai"
    model: str
    name: str
    params: Dict[str, Any]  # Anthropic: messages.create keyword arguments; OpenAI: the request body
    stream: bool = False
    beta: bool = False  # Anthropic's beta endpoint (the server-side fallback)
    cache: bool = False  # print cache_read_tokens
    expect_cache_read: bool = False  # the request repeats a cached prefix: cache_read_tokens must be > 0


@dataclass(frozen=True)
class Outcome:
    probe: Probe
    status: str  # "accepted" | "rejected" | "failed"
    detail: str = ""  # "HTTP 400 invalid_request_error", or the error's class
    stop: str = ""
    cache_read: Optional[int] = None

    @property
    def cache_missed(self) -> bool:
        """An accepted repeat of a cached prefix that read nothing from the cache."""
        return self.status == "accepted" and self.probe.expect_cache_read and not self.cache_read


@dataclass
class Clients:
    anthropic: Any = None
    openai_key: str = ""
    openai_post: Callable[..., Any] = field(default=requests.post)


# ── Which calls ────────────────────────────────────────────────────────────


def _user(text: str = PROMPT) -> List[Dict[str, Any]]:
    return [{"role": "user", "content": text}]


def _v1_specs() -> List[jobs.JobSpec]:
    return [spec for spec in jobs.JOB_SPECS if spec.group == "v1"]


def anthropic_probes(caps: models.ModelCaps) -> List[Probe]:
    """One probe per planned parameter the model's row allows, then one per v1 job shape."""
    base = {"model": caps.id, "max_tokens": SMALL_BUDGET, "messages": _user()}
    probes = [Probe("anthropic", caps.id, "plain", dict(base))]
    for effort in sorted({spec.effort for spec in _v1_specs()} & set(caps.effort_levels)):
        probes.append(Probe("anthropic", caps.id, f"effort={effort}", {**base, "output_config": {"effort": effort}}))
    if caps.adaptive_thinking:
        probes.append(Probe("anthropic", caps.id, "thinking=adaptive", {**base, "thinking": {"type": "adaptive"}}))
    if caps.structured_outputs:
        probes.append(Probe("anthropic", caps.id, "format=json_schema", {
            **base, "output_config": {"format": {"type": "json_schema", "schema": SMOKE_SCHEMA}},
        }))
    if caps.mid_conversation_system:
        probes.append(Probe("anthropic", caps.id, "system-after-data", {
            **base, "messages": [{"role": "user", "content": "Synthetic data: 3 questions."}, {"role": "system", "content": PROMPT}],
        }))
    if caps.server_fallback:
        probes.append(Probe("anthropic", caps.id, "fallbacks=default", {
            **base, "betas": [FALLBACK_BETA], "fallbacks": "default",
        }, beta=True))
    probes.append(Probe("anthropic", caps.id, f"stream max_tokens={min(STREAM_BUDGET, caps.max_output)}", {
        **base, "max_tokens": min(STREAM_BUDGET, caps.max_output),
    }, stream=True))
    for spec in _v1_specs():
        max_tokens, stream = jobs.budget(spec, caps)
        params: Dict[str, Any] = {**base, "max_tokens": max_tokens}
        if spec.effort in caps.effort_levels:
            params["output_config"] = {"effort": spec.effort}
        if caps.adaptive_thinking:
            params["thinking"] = {"type": "adaptive"}
        probes.append(Probe("anthropic", caps.id, f"job={spec.kind}", params, stream=stream))
    return probes


def legacy_probes(caps: models.ModelCaps) -> List[Probe]:
    """A classic output's call: its temperature, as `llm_client._anthropic_send` sends it."""
    params: Dict[str, Any] = {"model": caps.id, "max_tokens": 1_000, "messages": _user()}
    if caps.temperature_ok:
        params["extra_body"] = {"temperature": 0.2}
    return [Probe("anthropic", caps.id, "temperature=0.2" if caps.temperature_ok else "plain", params)]


def synthetic_dossier(lines: int = 120) -> str:
    """Made-up session lines, about 3,000 tokens: over every 5.5 model's shortest cacheable prefix."""
    return "\n".join(
        f"Line {index:03d}: in minute {index % 60}, the synthetic session covered topic {index % 17} "
        f"and the audience asked {index % 5} questions about part {index % 11}."
        for index in range(1, lines + 1)
    )


def dossier_probes(caps: models.ModelCaps) -> List[Probe]:
    """Write the cache, read it with the same request, then with a schema added."""
    dossier = {"role": "user", "content": [{"type": "text", "text": synthetic_dossier(), "cache_control": {"type": "ephemeral"}}]}
    instruction = {"role": "system", "content": PROMPT} if caps.mid_conversation_system else {"role": "user", "content": PROMPT}
    base = {"model": caps.id, "max_tokens": SMALL_BUDGET, "system": "You read a synthetic session record.", "messages": [dossier, instruction]}
    schema = {"output_config": {"format": {"type": "json_schema", "schema": SMOKE_SCHEMA}}}
    return [
        Probe("anthropic", caps.id, "dossier-write", dict(base), cache=True),
        Probe("anthropic", caps.id, "dossier-repeat", dict(base), cache=True, expect_cache_read=True),
        Probe("anthropic", caps.id, "dossier-with-schema", {**base, **schema}, cache=True),
    ]


def openai_probes(caps: models.ModelCaps) -> List[Probe]:
    other = "max_tokens" if caps.token_param == "max_completion_tokens" else "max_completion_tokens"
    base = {"model": caps.id, "messages": _user(), caps.token_param: OPENAI_BUDGET}
    return [
        Probe("openai", caps.id, "plain", dict(base)),
        Probe("openai", caps.id, "temperature=0.2", {**base, "temperature": 0.2}),
        Probe("openai", caps.id, "format=json_schema", {**base, "response_format": {
            "type": "json_schema", "json_schema": {"name": "SmokeAnswer", "strict": True, "schema": SMOKE_SCHEMA},
        }}),
        Probe("openai", caps.id, f"token_param={other}", {"model": caps.id, "messages": _user(), other: OPENAI_BUDGET}),
        Probe("openai", caps.id, "reasoning_effort=low", {**base, "reasoning_effort": "low"}),
    ]


def plan(*, legacy: bool, only: Sequence[str], dossier_model: str, anthropic_on: bool, openai_on: bool) -> List[Probe]:
    """Every call, in order. `only` keeps the named models (empty: all)."""
    def wanted(model_id: str) -> bool:
        return not only or model_id in only

    probes: List[Probe] = []
    if anthropic_on:
        for caps in models.catalog_models("anthropic", "offered"):
            if wanted(caps.id):
                probes.extend(anthropic_probes(caps))
        dossier_caps = models.catalog_model(dossier_model)
        if dossier_caps is not None and dossier_caps.provider == "anthropic" and wanted(dossier_caps.id):
            probes.extend(dossier_probes(dossier_caps))
        if legacy:
            for caps in models.catalog_models("anthropic", "legacy"):
                if wanted(caps.id):
                    probes.extend(legacy_probes(caps))
    if openai_on:
        for caps in models.catalog_models("openai", "legacy"):
            if wanted(caps.id):
                probes.extend(openai_probes(caps))
    return probes


# ── Making a call and reading only what may be printed ──────────────────────


def _tag(value: Any) -> str:
    return value if isinstance(value, str) and _TAG_RE.match(value) else ""


def _count(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _anthropic_outcome(probe: Probe, message: Any) -> Outcome:
    usage = getattr(message, "usage", None)
    return Outcome(
        probe,
        "accepted",
        stop=_tag(getattr(message, "stop_reason", None)),
        cache_read=_count(getattr(usage, "cache_read_input_tokens", None)) if probe.cache else None,
    )


def run_anthropic(probe: Probe, client: Any) -> Outcome:
    try:
        if probe.stream:
            with client.messages.stream(**probe.params) as stream:
                message = stream.get_final_message()
        elif probe.beta:
            message = client.beta.messages.create(**probe.params)
        else:
            message = client.messages.create(**probe.params)
    except anthropic.APIStatusError as exc:
        detail = f"HTTP {exc.status_code}" + (f" {_tag(exc.type)}" if _tag(exc.type) else "")
        return Outcome(probe, "rejected", detail)
    except Exception as exc:  # no HTTP answer: a timeout, a connection error, or the SDK refusing an argument
        return Outcome(probe, "failed", type(exc).__name__)
    return _anthropic_outcome(probe, message)


def run_openai(probe: Probe, key: str, post: Callable[..., Any]) -> Outcome:
    try:
        response = post(
            OPENAI_CHAT_COMPLETIONS_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=probe.params,
            timeout=OPENAI_TIMEOUT,
        )
        body = response.json()
    except Exception as exc:
        return Outcome(probe, "failed", type(exc).__name__)
    body = body if isinstance(body, Mapping) else {}
    if response.status_code >= 400:
        error = body.get("error") if isinstance(body.get("error"), Mapping) else {}
        parts = [f"HTTP {response.status_code}", _tag(error.get("type")) or _tag(error.get("code"))]
        if _tag(error.get("param")):
            parts.append(f"param={_tag(error.get('param'))}")
        return Outcome(probe, "rejected", " ".join(part for part in parts if part))
    choices = body.get("choices") if isinstance(body.get("choices"), list) else []
    choice = choices[0] if choices and isinstance(choices[0], Mapping) else {}
    return Outcome(probe, "accepted", stop=_tag(choice.get("finish_reason")))


def format_outcome(outcome: Outcome) -> str:
    """One line: provider, model, probe, result, stop reason, cache reads. Nothing else."""
    parts = [outcome.probe.provider, outcome.probe.model, outcome.probe.name, outcome.status]
    if outcome.detail:
        parts.append(outcome.detail)
    if outcome.status == "accepted":
        parts.append(f"stop={outcome.stop or '-'}")
    if outcome.probe.cache and outcome.status == "accepted":
        parts.append(f"cache_read={outcome.cache_read if outcome.cache_read is not None else '-'}")
    if outcome.cache_missed:
        parts.append("(expected > 0)")
    return " ".join(parts)


def run(probes: Iterable[Probe], clients: Clients, out: TextIO) -> List[Outcome]:
    outcomes = []
    for probe in probes:
        if probe.provider == "anthropic":
            outcome = run_anthropic(probe, clients.anthropic)
        else:
            outcome = run_openai(probe, clients.openai_key, clients.openai_post)
        outcomes.append(outcome)
        print(format_outcome(outcome), file=out, flush=True)
    return outcomes


# ── Entry point ────────────────────────────────────────────────────────────


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Paid parameter checks for the planned model calls (see the docstring).")
    parser.add_argument(CONFIRM_FLAG, dest="confirmed", action="store_true", help="required: these calls are billed")
    parser.add_argument("--legacy", action="store_true", help="also check the classic outputs' Claude models")
    parser.add_argument("--model", action="append", default=[], help="only this model id (repeatable)")
    parser.add_argument("--dossier-model", default="claude-haiku-5-5", help="the model for the three dossier calls")
    return parser.parse_args(list(argv))


def main(
    argv: Sequence[str],
    *,
    environ: Mapping[str, str] = os.environ,
    anthropic_factory: Callable[[str], Any] = llm_client._build_anthropic_client,
    openai_post: Callable[..., Any] = requests.post,
    out: TextIO = sys.stdout,
) -> int:
    args = parse_args(argv)
    if not args.confirmed:
        print(f"This makes paid calls to Anthropic and OpenAI. Run it again with {CONFIRM_FLAG}.", file=out)
        return 2
    claude_key = (environ.get("CLAUDE_API_KEY") or "").strip()
    openai_key = (environ.get("OPENAI_API_KEY") or "").strip()
    if not claude_key:
        print("anthropic skipped: CLAUDE_API_KEY is not set", file=out)
    if not openai_key:
        print("openai skipped: OPENAI_API_KEY is not set", file=out)
    probes = plan(
        legacy=args.legacy, only=args.model, dossier_model=args.dossier_model,
        anthropic_on=bool(claude_key), openai_on=bool(openai_key),
    )
    clients = Clients(
        anthropic=anthropic_factory(claude_key) if claude_key else None,
        openai_key=openai_key,
        openai_post=openai_post,
    )
    outcomes = run(probes, clients, out)
    rejected = sum(1 for outcome in outcomes if outcome.status != "accepted")
    missed = sum(1 for outcome in outcomes if outcome.cache_missed)
    print(f"calls={len(outcomes)} not_accepted={rejected} cache_misses={missed}", file=out)
    return 1 if rejected or missed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
