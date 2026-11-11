"""Model call results and usage logging: one `LLMResult` per answered call, its
`llm_usage` row and log line, the cost estimate, and the org budget for
automatic jobs.

Pure tests on synthetic provider responses: no provider is called (Anthropic
is a fake SDK client, `tests/fake_anthropic.py`) and no database is touched
(the row writer is replaced). The real-Postgres cases are
in test_data_integrity.py and test_db_scope.py.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from datetime import datetime, timezone

import pytest

from livestorm_app import api_logic, db, llm_client
from livestorm_app.ai import generation, jobs, usage
from livestorm_app.ai.models import CATALOG
from livestorm_app.ai.usage import CallContext, LLMUsage
from livestorm_app.queue import ai_job_id

from fake_anthropic import scripted_providers

SECRET = "sk-test-SECRET"
# Free text a provider might one day put in its usage object; it must never be stored.
FREE_TEXT = "free text with spaces"


class FakeResponse:
    def __init__(self, body: dict, headers: dict | None = None):
        self._body = body
        self.headers = headers or {}

    def json(self):
        return self._body


def anthropic_body(text="# Output", stop="end_turn", *, model="claude-haiku-4-5-20251001", usage_obj=None, **extra):
    body = {
        "id": "msg_test",
        "type": "message",
        "model": model,
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop,
        "stop_details": None,
        "usage": usage_obj or {"input_tokens": 1200, "output_tokens": 300, "service_tier": "standard", "note": FREE_TEXT},
    }
    body.update(extra)
    return body


@pytest.fixture
def http(monkeypatch):
    """Replace Anthropic: each call takes the next scripted response,
    through a fake SDK client."""
    script: list = []
    sent: list = []
    scripted_providers(monkeypatch, script, sent)
    return script, sent


@pytest.fixture
def rows(monkeypatch):
    """Collect the usage rows instead of writing them."""
    saved: list = []
    monkeypatch.setattr(db, "insert_llm_usage", saved.append)
    return saved


@pytest.fixture
def anthropic(monkeypatch):
    monkeypatch.setattr(llm_client, "get_provider", lambda: "anthropic")


# ── One result type ────────────────────────────────────────────────────────


def test_an_anthropic_answer_becomes_one_result(http):
    script, _ = http
    script.append(FakeResponse(
        anthropic_body(
            content=[{"type": "thinking", "thinking": ""}, {"type": "text", "text": "  Hello  "}],
            usage_obj={
                "input_tokens": 50, "output_tokens": 70, "cache_read_input_tokens": 1000,
                "cache_creation_input_tokens": 400,
                "cache_creation": {"ephemeral_5m_input_tokens": 300, "ephemeral_1h_input_tokens": 100},
                "iterations": [{"type": "message", "input_tokens": 50, "output_tokens": 70}],
                "service_tier": "standard",
                "note": FREE_TEXT,
            },
        ),
        headers={"request-id": "req_test_1"},
    ))
    result = llm_client._anthropic_send("key", "claude-haiku-4-5-20251001", [{"role": "user", "content": "Hi"}], 0.2, 100)
    assert (result.text, result.stop, result.stop_raw, result.truncated) == ("Hello", "complete", "end_turn", False)
    assert (result.model_served, result.request_id, result.refusal_category) == ("claude-haiku-4-5-20251001", "req_test_1", None)
    assert result.usage.input_tokens == 50 and result.usage.output_tokens == 70
    assert (result.usage.cache_read_tokens, result.usage.cache_write_tokens, result.usage.cache_write_1h_tokens) == (1000, 400, 100)
    assert result.usage.reasoning_tokens is None
    assert result.usage.raw["iterations"] == [{"type": "message", "input_tokens": 50, "output_tokens": 70}]
    assert "note" not in result.usage.raw and FREE_TEXT not in json.dumps(result.usage.raw)
    assert result.duration_ms >= 0


@pytest.mark.parametrize(
    ("stop_reason", "stop_details", "stop", "category"),
    [
        ("end_turn", None, "complete", None),
        ("stop_sequence", None, "complete", None),
        ("max_tokens", None, "max_tokens", None),
        ("refusal", {"type": "refusal", "category": "cyber", "explanation": "declined"}, "refusal", "cyber"),
        ("refusal", None, "refusal", None),
        ("pause_turn", None, "other", None),
        (None, None, "other", None),
    ],
)
def test_anthropic_stop_reasons(http, stop_reason, stop_details, stop, category):
    script, _ = http
    script.append(FakeResponse(anthropic_body(text="", stop=stop_reason, stop_details=stop_details)))
    result = llm_client._anthropic_send("key", "claude-haiku-4-5-20251001", [], 0.2, 100)
    assert (result.stop, result.refusal_category) == (stop, category)


def test_usage_parsing_never_raises(http):
    assert usage.anthropic_usage(None) == LLMUsage()
    odd = usage.anthropic_usage({"input_tokens": "12", "output_tokens": -3, "cache_creation": "bad", "cache_read_input_tokens": True})
    assert (odd.input_tokens, odd.output_tokens, odd.cache_read_tokens, odd.cache_write_1h_tokens) == (0, 0, 0, 0)
    script, _ = http
    script.append(FakeResponse({"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn", "usage": "not an object"}))
    assert llm_client._anthropic_send("key", "m", [], 0.2, 100).text == "ok"


def test_usage_raw_keeps_counts_and_tags_only():
    raw = usage.sanitize_usage({
        "input_tokens": 5,
        "service_tier": "standard",
        "note": "Free text the provider might one day send, with spaces and words.",
        "iterations": [{"type": "fallback_message", "model": "claude-sonnet-5-5", "output_tokens": 9}],
        "nested": {"deep": {"deeper": {"deepest": {"too_deep": {"x": 1}}}}},
    })
    assert raw["input_tokens"] == 5 and raw["service_tier"] == "standard"
    assert "note" not in raw
    assert raw["iterations"] == [{"type": "fallback_message", "model": "claude-sonnet-5-5", "output_tokens": 9}]
    assert "too_deep" not in json.dumps(raw)


# ── Cost ───────────────────────────────────────────────────────────────────


def test_every_catalog_model_is_claude_and_has_a_price():
    for model in CATALOG:
        assert model.provider == "anthropic", model.id
        assert model.price is not None, model.id
        assert model.price.cache_write_5m == pytest.approx(model.price.input * 1.25), model.id
        assert model.price.cache_write_1h == pytest.approx(model.price.input * 2), model.id


@pytest.mark.parametrize(
    ("model", "tokens", "micros"),
    [
        # $1 in, $5 out per million: 1,200 × 1 + 300 × 5.
        ("claude-haiku-4-5-20251001", {"input_tokens": 1200, "output_tokens": 300}, 2700),
        ("claude-haiku-4-5-20251001", {"input_tokens": 1_000_000, "output_tokens": 1_000_000}, 6_000_000),
        # Reads at $0.20; 5-minute writes at $2.50 and 1-hour writes at $4.00.
        ("claude-sonnet-5-5", {"cache_read_tokens": 10_000, "cache_write_tokens": 4000, "cache_write_1h_tokens": 1000}, 2000 + 7500 + 4000),
        # Haiku 5.5's second rate card above 100K prompt tokens.
        ("claude-haiku-5-5", {"input_tokens": 50_000, "output_tokens": 1000}, 5000 + 500),
        ("claude-haiku-5-5", {"input_tokens": 150_000, "output_tokens": 1000}, 75_000 + 2500),
        ("claude-haiku-5-5", {"input_tokens": 60_000, "cache_read_tokens": 60_000}, 30_000 + 3000),
        ("some-unknown-model", {"input_tokens": 1000}, None),
    ],
)
def test_cost_estimate(model, tokens, micros):
    assert usage.estimate_cost_usd_micros(model, LLMUsage(**tokens)) == micros


# ── Recording every answered call ──────────────────────────────────────────


# A classic output's call (the Deep analysis: a legacy job that retries a cut-off answer once).
CTX = CallContext("deep_analysis", "sess-1", "org-a", "deep_analysis:sess-1:English")


def test_call_llm_records_every_answered_call(http, rows, anthropic):
    script, _ = http
    script.extend([
        FakeResponse(anthropic_body("partial", "max_tokens"), headers={"request-id": "req_1"}),
        FakeResponse(anthropic_body("complete"), headers={"request-id": "req_2"}),
    ])
    assert llm_client.call_llm("key", "claude-haiku-4-5-20251001", [], max_tokens=2500, ctx=CTX) == "complete"
    assert [row["purpose"] for row in rows] == ["main", "truncation_retry"]
    assert [row["outcome"] for row in rows] == ["truncated", "ok"]
    assert [row["max_tokens"] for row in rows] == [2500, 5000]
    assert [row["request_id"] for row in rows] == ["req_1", "req_2"]
    row = rows[1]
    assert (row["job_kind"], row["session_id"], row["organization_id"], row["job_id"]) == (
        "deep_analysis", "sess-1", "org-a", "deep_analysis:sess-1:English",
    )
    assert (row["provider"], row["model_requested"], row["model_served"]) == ("anthropic", "claude-haiku-4-5-20251001", "claude-haiku-4-5-20251001")
    assert (row["input_tokens"], row["output_tokens"], row["cost_usd_micros"]) == (1200, 300, 2700)
    assert (row["streamed"], row["effort"], row["prompt_hash"], row["dossier_hash"], row["record_version"]) == (False, None, None, None, None)
    # The provider's usage object, its free-text "note" dropped.
    assert row["usage_raw"] == {"input_tokens": 1200, "output_tokens": 300, "service_tier": "standard"}
    assert set(row) == set(db._LLM_USAGE_COLUMNS)


def test_a_v1_calls_row_holds_its_effort_and_hashes_and_never_text():
    result = llm_client.LLMResult(
        text="answer", stop="complete", stop_raw="end_turn", refusal_category=None, model_served="claude-sonnet-5-5",
        request_id="req_1", usage=LLMUsage(input_tokens=10, output_tokens=5), duration_ms=12, streamed=True,
    )
    prompt_hash, dossier_hash = "a" * 64, "0123456789abcdef" * 4

    def row(**values):
        return usage.build_usage_row(
            CallContext("understanding", "sess-1", "org-a", record_version="6"), result, purpose="main",
            provider="anthropic", model_requested="claude-sonnet-5-5", max_tokens=48_000, **values,
        )

    kept = row(effort="medium", prompt_hash=prompt_hash, dossier_hash=dossier_hash)
    assert (kept["effort"], kept["prompt_hash"], kept["dossier_hash"], kept["record_version"]) == ("medium", prompt_hash, dossier_hash, "6")
    line = usage.usage_log_line(kept)
    assert "effort=medium" in line and f"prompt={prompt_hash}" in line and f"dossier={dossier_hash}" in line
    # A hash column holds a SHA-256 in hex or nothing: never a prompt or a dossier's text.
    odd = row(effort="very high effort please", prompt_hash="You read sessions.", dossier_hash=dossier_hash.upper())
    assert (odd["effort"], odd["prompt_hash"], odd["dossier_hash"]) == (None, None, None)
    assert "effort=-" in usage.usage_log_line(odd) and "prompt=-" in usage.usage_log_line(odd)
    assert set(kept) == set(db._LLM_USAGE_COLUMNS)


@pytest.mark.parametrize(
    ("raw", "fell_back"),
    [
        ({"iterations": [{"type": "message"}, {"type": "fallback_message", "model": "claude-sonnet-5"}]}, True),
        ({"iterations": [{"type": "message"}]}, False),
        ({"iterations": "fallback_message"}, False),
        ({}, False),
    ],
)
def test_a_fallback_answer_shows_in_the_usages_attempts(raw, fell_back):
    assert usage.answered_by_fallback(LLMUsage(raw=raw)) is fell_back
    assert usage.answered_by_fallback(usage.anthropic_usage({"input_tokens": 1, **raw})) is fell_back


def test_a_truncated_answer_after_the_retry_is_still_recorded(http, rows, anthropic):
    script, _ = http
    script.extend([FakeResponse(anthropic_body("partial", "max_tokens"))] * 2)
    with pytest.raises(llm_client.LLMOutputTruncated) as excinfo:
        llm_client.call_llm(SECRET, "claude-haiku-4-5-20251001", [], max_tokens=12000, ctx=CTX)
    assert [(row["purpose"], row["outcome"]) for row in rows] == [("main", "truncated"), ("truncation_retry", "truncated")]
    assert issubclass(llm_client.LLMOutputTruncated, llm_client.LLMError)
    assert SECRET not in str(excinfo.value) and SECRET not in repr(excinfo.value)
    assert SECRET not in repr(rows)


@pytest.mark.parametrize(
    ("served", "micros"),
    [
        # Sonnet 4.6 answered: $3 in and $15 out per million tokens, not Haiku 4.5's $1 and $5.
        ("claude-sonnet-4-6", 1200 * 3 + 300 * 15),
        # A served id outside the catalog is priced as the model asked for.
        ("claude-not-in-the-catalog", 1200 * 1 + 300 * 5),
    ],
)
def test_the_cost_uses_the_model_that_answered(http, rows, anthropic, served, micros):
    script, _ = http
    script.append(FakeResponse(anthropic_body("complete", model=served)))
    llm_client.call_llm("key", "claude-haiku-4-5-20251001", [], ctx=CTX)
    assert [(row["model_requested"], row["model_served"], row["cost_usd_micros"]) for row in rows] == [
        ("claude-haiku-4-5-20251001", served, micros),
    ]


def test_a_refusal_is_recorded_and_raised(http, rows, anthropic):
    # A refused classic output says so (it used to read as "empty"); test_llm_responses.py has the rest.
    script, _ = http
    script.append(FakeResponse(anthropic_body("", "refusal", stop_details={"type": "refusal", "category": "bio"})))
    with pytest.raises(llm_client.LLMRefused) as refused:
        llm_client.call_llm("key", "claude-haiku-4-5-20251001", [], ctx=CTX)
    assert str(refused.value) == "The model declined this session. Nothing was saved."
    assert [(row["outcome"], row["stop_reason"], row["refusal_category"]) for row in rows] == [("refused", "refusal", "bio")]


def test_a_call_to_an_unpriced_model_is_recorded_without_a_cost(http, rows, anthropic):
    script, _ = http
    script.append(FakeResponse(anthropic_body(model="claude-not-in-catalog")))
    assert llm_client.call_llm("key", "claude-not-in-catalog", [], ctx=CTX) == "# Output"
    assert [(row["provider"], row["cost_usd_micros"], row["input_tokens"]) for row in rows] == [("anthropic", None, 1200)]


def _row_ids(row: dict) -> tuple:
    return (row["job_kind"], row["organization_id"], row["session_id"], row["job_id"])


def _ctx_ids(ctx: CallContext) -> tuple:
    return (ctx.job_kind, ctx.organization_id, ctx.session_id, ctx.job_id)


def test_each_content_bundle_attempt_is_its_own_row(http, rows, anthropic):
    ctx = CallContext("content_repurposing", "sess-1", "org-a", "content_repurposing:sess-1:English")
    script, _ = http
    script.extend([FakeResponse(anthropic_body('{"summary": "S"}'))] * 3)
    generation.generate_content_repurpose_bundle_with_openai(
        api_key="key", model="claude-haiku-4-5-20251001", output_language="English", transcript_text="Hello", ctx=ctx,
    )
    assert [row["purpose"] for row in rows] == ["main", "completeness_retry", "language_retry"]
    assert [_row_ids(row) for row in rows] == [_ctx_ids(ctx)] * 3


def test_a_markdown_translation_carries_its_call_context(http, rows, anthropic):
    ctx = CallContext("translation", "sess-1", "org-a", "overall_analysis:sess-1:French")
    script, _ = http
    script.append(FakeResponse(anthropic_body("# Rapport")))
    assert generation.translate_markdown_with_openai(
        api_key="key", model="claude-haiku-4-5-20251001", source_markdown="# Report",
        source_language="English", target_language="French", ctx=ctx,
    ) == "# Rapport"
    assert [(row["purpose"], _row_ids(row)) for row in rows] == [("main", _ctx_ids(ctx))]


def test_each_bundle_translation_attempt_is_its_own_row(http, rows, anthropic):
    ctx = CallContext("translation", "sess-1", "org-a", "content_repurposing:sess-1:French")
    script, _ = http
    script.extend([FakeResponse(anthropic_body('{"summary": "S"}'))] * 3)
    generation.translate_content_repurpose_bundle_with_openai(
        api_key="key", model="claude-haiku-4-5-20251001",
        source_bundle={"summary": "S", "blog": "B", "email": "E", "social_media": "M"},
        source_language="English", target_language="French", ctx=ctx,
    )
    assert [row["purpose"] for row in rows] == ["main", "completeness_retry", "language_retry"]
    assert [_row_ids(row) for row in rows] == [_ctx_ids(ctx)] * 3


def test_the_log_line_carries_the_row_and_no_content(http, rows, anthropic, caplog):
    script, _ = http
    script.append(FakeResponse(anthropic_body("A confidential summary of the session"), headers={"request-id": "req_1"}))
    with caplog.at_level(logging.INFO, logger="livestorm_app.ai.usage"):
        llm_client.call_llm(SECRET, "claude-haiku-4-5-20251001", [{"role": "user", "content": "Prompt text"}], ctx=CTX)
    lines = [record.getMessage() for record in caplog.records if record.getMessage().startswith("llm_usage ")]
    assert len(lines) == 1
    line = lines[0]
    for part in ("job=deep_analysis", "purpose=main", "org=org-a", "session=sess-1", "model=claude-haiku-4-5-20251001",
                 "stop=end_turn", "outcome=ok", "in=1200", "out=300", "usd=0.002700", "request_id=req_1", "refusal=-"):
        assert part in line, part
    for leaked in ("confidential", "Prompt text", SECRET):
        assert leaked not in caplog.text


def test_a_failed_insert_is_logged_and_the_answer_returned(http, anthropic, monkeypatch, caplog):
    class UndefinedTable(Exception):
        sqlstate = "42P01"

    def broken(row):
        raise UndefinedTable(f"relation llm_usage does not exist; key {SECRET}")

    monkeypatch.setattr(db, "insert_llm_usage", broken)
    script, _ = http
    script.append(FakeResponse(anthropic_body("complete")))
    with caplog.at_level(logging.INFO):
        assert llm_client.call_llm(SECRET, "claude-haiku-4-5-20251001", [], ctx=CTX) == "complete"
    errors = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert len(errors) == 1
    message = errors[0].getMessage()
    assert "llm_usage insert failed (UndefinedTable sqlstate=42P01)" in message
    assert "llm_usage job=deep_analysis" in message and "in=1200" in message
    assert SECRET not in caplog.text, "the exception's message is never logged"


def test_a_broken_row_never_costs_the_answer(http, anthropic, monkeypatch, caplog):
    def broken(*args, **kwargs):
        raise TypeError("bug")

    monkeypatch.setattr(usage, "build_usage_row", broken)
    script, _ = http
    script.append(FakeResponse(anthropic_body("complete")))
    assert llm_client.call_llm("key", "claude-haiku-4-5-20251001", [], ctx=CTX) == "complete"
    assert "could not build the usage row (TypeError)" in caplog.text


def test_a_call_without_context_is_recorded_as_unknown(http, rows, anthropic):
    script, _ = http
    script.append(FakeResponse(anthropic_body("complete")))
    llm_client.call_llm("key", "claude-haiku-4-5-20251001", [])
    assert [(row["job_kind"], row["organization_id"], row["session_id"]) for row in rows] == [("unknown", None, None)]


# ── Each classic runner says who its calls are for ─────────────────────────


GENERATED_BUNDLE = {"summary": "S", "blog": "B", "email": "E", "social_media": "M"}


@pytest.mark.parametrize(
    ("run", "dimension", "generator", "kind", "flow"),
    [
        (api_logic.run_overall_analysis, "French", "translate_markdown_with_openai", "translation", "overall_analysis"),
        (api_logic.run_deep_analysis, "English", "analyze_with_openai", "deep_analysis", "deep_analysis"),
        (api_logic.run_deep_analysis, "French", "translate_markdown_with_openai", "translation", "deep_analysis"),
        (api_logic.run_smart_recap, "hype", "analyze_with_openai", "smart_recap", "smart_recap"),
        (api_logic.run_content_repurposing, "English", "generate_content_repurpose_bundle_with_openai", "content_repurposing", "content_repurposing"),
        (api_logic.run_content_repurposing, "French", "translate_content_repurpose_bundle_with_openai", "translation", "content_repurposing"),
    ],
)
def test_each_classic_runner_passes_its_call_context(monkeypatch, run, dimension, generator, kind, flow):
    row = {
        "organization_id": "org-a",
        "session_payload": {"id": "s1"},
        "chat_payload": {"data": []},
        "questions_payload": {"data": []},
        "transcript_payload": {"transcript": {"segments": [{"start": 0, "end": 2, "text": "Hello there", "speaker": "A"}]}},
        "analysis_bundle": {"English": "EN"},
        "deep_analysis_bundle": {"English": "EN"},
        "content_repurpose_bundle": {"English": GENERATED_BUNDLE},
    }
    contexts: list = []
    monkeypatch.setattr(api_logic, "fetch_cached_session", lambda key, sid, *, organization_id: dict(row))
    monkeypatch.setattr(api_logic, "merge_cached_bundle_entry", lambda *args, **kwargs: None)
    monkeypatch.setattr(api_logic, "get_llm_model", lambda job_kind: "test-model")
    output = dict(GENERATED_BUNDLE) if "bundle" in generator else "# Output"
    monkeypatch.setattr(api_logic, generator, lambda **kwargs: contexts.append(kwargs["ctx"]) or output)
    run("llm-key", "s1", dimension)
    assert contexts == [CallContext(kind, "s1", "org-a", ai_job_id(flow, "s1", dimension))]


# ── The org budget for automatic jobs ──────────────────────────────────────

PRICED = "claude-haiku-5-5"
UNPRICED = "claude-not-in-catalog"


@pytest.fixture
def spend(monkeypatch):
    """The org's spend since midnight UTC, and the reads made."""
    state = {"micros": 0, "reads": []}

    def fake_spend(org, since):
        state["reads"].append((org, since))
        return state["micros"]

    monkeypatch.setattr(db, "llm_spend_since", fake_spend)
    return state


@pytest.fixture
def automatic(monkeypatch):
    """Pretend `chat_labels` runs without a user asking."""
    monkeypatch.setattr(jobs, "automatic_kinds", lambda: frozenset({"chat_labels"}))


def test_no_job_runs_automatically_yet():
    assert jobs.automatic_kinds() == frozenset()


def test_the_budget_never_holds_a_job_a_user_started(monkeypatch, spend):
    monkeypatch.setenv(usage.ORG_DAILY_LLM_BUDGET_ENV, "0")
    for spec in jobs.JOB_SPECS:
        for model in (PRICED, UNPRICED):
            usage.check_org_budget(spec.kind, "org-a", model)
            usage.check_org_budget(spec.kind, "", model)
    usage.check_org_budget("unknown", "", UNPRICED)
    assert spend["reads"] == []


def test_an_automatic_job_needs_an_org(automatic, monkeypatch, spend):
    monkeypatch.delenv(usage.ORG_DAILY_LLM_BUDGET_ENV, raising=False)
    for blank in ("", "   ", None):
        with pytest.raises(usage.OrgBudgetExceeded, match="no organization"):
            usage.check_org_budget("chat_labels", blank, PRICED)
    assert spend["reads"] == []


def test_no_budget_set_means_no_limit(automatic, monkeypatch, spend):
    monkeypatch.delenv(usage.ORG_DAILY_LLM_BUDGET_ENV, raising=False)
    spend["micros"] = 10**12
    usage.check_org_budget("chat_labels", "org-a", PRICED)
    usage.check_org_budget("chat_labels", "org-a", UNPRICED)
    monkeypatch.setenv(usage.ORG_DAILY_LLM_BUDGET_ENV, "  ")
    usage.check_org_budget("chat_labels", "org-a", PRICED)
    assert spend["reads"] == []


def test_the_budget_counts_spend_since_midnight_utc(automatic, monkeypatch, spend):
    monkeypatch.setenv(usage.ORG_DAILY_LLM_BUDGET_ENV, "1.50")
    now = datetime(2026, 11, 6, 15, 30, tzinfo=timezone.utc)
    spend["micros"] = 1_499_999
    usage.check_org_budget("chat_labels", "org-a", PRICED, now=now)
    spend["micros"] = 1_500_000
    with pytest.raises(usage.OrgBudgetExceeded, match="daily AI budget"):
        usage.check_org_budget("chat_labels", " org-a ", PRICED, now=now)
    assert spend["reads"] == [("org-a", datetime(2026, 11, 6, tzinfo=timezone.utc))] * 2


@pytest.mark.parametrize("model", [UNPRICED, "some-unknown-model", ""])
def test_a_budget_refuses_a_model_without_a_price(automatic, monkeypatch, spend, model):
    # Its rows would carry no cost and count 0, so the budget could never stop it.
    monkeypatch.setenv(usage.ORG_DAILY_LLM_BUDGET_ENV, "5")
    with pytest.raises(usage.OrgBudgetExceeded, match="no price"):
        usage.check_org_budget("chat_labels", "org-a", model)
    assert spend["reads"] == []


@pytest.mark.parametrize("value", ["abc", "-1", "nan", "inf"])
def test_an_invalid_budget_stops_automatic_jobs(automatic, monkeypatch, spend, value):
    monkeypatch.setenv(usage.ORG_DAILY_LLM_BUDGET_ENV, value)
    with pytest.raises(usage.OrgBudgetExceeded, match="not a dollar amount") as refused:
        usage.check_org_budget("chat_labels", "org-a", PRICED)
    assert value not in str(refused.value)
    assert spend["reads"] == []


def test_a_failed_spend_read_stops_automatic_jobs(automatic, monkeypatch):
    def broken(org, since):
        raise RuntimeError("database down")

    monkeypatch.setenv(usage.ORG_DAILY_LLM_BUDGET_ENV, "5")
    monkeypatch.setattr(db, "llm_spend_since", broken)
    with pytest.raises(RuntimeError, match="database down"):
        usage.check_org_budget("chat_labels", "org-a", PRICED)


AUTOMATIC_CTX = CallContext("chat_labels", "sess-1", "org-a", "chat_labels:sess-1:_")


def test_the_budget_is_checked_before_the_paid_call(automatic, monkeypatch, spend, http, rows, anthropic):
    monkeypatch.setenv(usage.ORG_DAILY_LLM_BUDGET_ENV, "1")
    spend["micros"] = 2_000_000
    _, sent = http
    with pytest.raises(usage.OrgBudgetExceeded, match="daily AI budget"):
        llm_client.call_llm("key", PRICED, [], ctx=AUTOMATIC_CTX)
    assert sent == [] and rows == []


def test_an_unpriced_model_never_makes_an_automatic_call_under_a_budget(automatic, monkeypatch, spend, http, rows, anthropic):
    monkeypatch.setenv(usage.ORG_DAILY_LLM_BUDGET_ENV, "100")
    _, sent = http
    with pytest.raises(usage.OrgBudgetExceeded, match="no price"):
        llm_client.call_llm("key", UNPRICED, [], ctx=AUTOMATIC_CTX)
    assert sent == [] and rows == []


def test_the_truncation_retry_is_checked_again(automatic, monkeypatch, http, rows, anthropic):
    # The main call is under budget; its own cost takes the org over, so the retry is never sent.
    # (The v1 jobs don't retry a cut-off answer; this one is given a retry to test the check.)
    monkeypatch.setitem(jobs._SPECS, "chat_labels", dataclasses.replace(jobs.job_spec("chat_labels"), truncation_retry=True))
    monkeypatch.setattr(db, "llm_spend_since", lambda org, since: sum(row["cost_usd_micros"] or 0 for row in rows))
    monkeypatch.setenv(usage.ORG_DAILY_LLM_BUDGET_ENV, "0.002")  # 2,000 micros; one call costs 2,700
    script, sent = http
    script.extend([FakeResponse(anthropic_body("partial", "max_tokens")), FakeResponse(anthropic_body("complete"))])
    with pytest.raises(usage.OrgBudgetExceeded, match="daily AI budget"):
        llm_client.call_llm("key", "claude-haiku-4-5-20251001", [], ctx=AUTOMATIC_CTX)
    assert len(sent) == 1
    assert [(row["purpose"], row["outcome"], row["cost_usd_micros"]) for row in rows] == [("main", "truncated", 2700)]
