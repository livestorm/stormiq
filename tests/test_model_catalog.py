"""The model catalog (ai/models.py), the job registry and which model each
job uses (ai/jobs.py), and what Admin may save as a model setting.

Pure: settings are faked at `livestorm_app.db.get_setting`; no database,
Redis or provider is reached.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

from livestorm_app import api_logic, llm_client
from livestorm_app.ai import jobs, models

ROOT = Path(__file__).resolve().parents[1]
CLASSIC_KINDS = {"deep_analysis", "smart_recap", "content_repurposing", "translation"}
# The Session analysis keeps the kind `overall_analysis` (its queue, bundle and PDF) but is a v1 job.
V1_KINDS = {"profile", "chat_labels", "understanding", "verify_meaning", "overall_analysis", "faq", "decisions", "team_digest"}

# Every Claude id Admin offered or the config defaulted to before the catalog existed.
IDS_IN_USE_BEFORE_THE_CATALOG = ["claude-haiku-4-5-20251001", "claude-sonnet-4-6", "claude-opus-4-8"]
# The OpenAI text models offered until every text job moved to Claude (2026-11-11).
RETIRED_OPENAI_IDS = ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1", "gpt-5.4-mini"]
CLAUDE_5_5 = ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"]


# ── The catalog ────────────────────────────────────────────────────────────


def test_catalog_ids_are_unique_and_rows_are_well_formed():
    ids = [model.id for model in models.CATALOG]
    assert len(ids) == len(set(ids))
    for model in models.CATALOG:
        assert model.provider in models.PROVIDERS
        assert model.status in ("offered", "legacy", "hidden")
        assert set(model.effort_levels) <= set(models.ALL_EFFORTS)
        assert model.max_output > 0 and model.min_cache_tokens > 0
        assert models.catalog_model(model.id) is model


@pytest.mark.parametrize("model_id", IDS_IN_USE_BEFORE_THE_CATALOG)
def test_every_id_in_use_before_the_catalog_stays_usable_for_the_classic_outputs(model_id):
    caps = models.catalog_model(model_id)
    assert caps is not None and caps.provider == "anthropic" and caps.status == "legacy"
    jobs.check_model_setting("llm_default_model", model_id)
    jobs.check_model_setting("llm_smart_recap_model", model_id)


def test_every_text_model_is_claude():
    assert models.PROVIDERS == ("anthropic",)
    assert {model.provider for model in models.CATALOG} == {"anthropic"}


@pytest.mark.parametrize("model_id", RETIRED_OPENAI_IDS)
def test_the_openai_text_models_are_gone_and_refused(model_id):
    assert models.catalog_model(model_id) is None
    for key in ("llm_default_model", "llm_smart_recap_model", "llm_model.anthropic.smart_recap"):
        with pytest.raises(jobs.InvalidModelSetting):
            jobs.check_model_setting(key, model_id)


@pytest.mark.parametrize("model_id", CLAUDE_5_5)
def test_claude_5_5_models_are_offered_for_v1_jobs_with_their_parameters(model_id):
    caps = models.catalog_model(model_id)
    assert caps.provider == "anthropic" and caps.status == "offered"
    assert caps.thinks and caps.adaptive_thinking
    assert not caps.temperature_ok
    assert caps.effort_levels == models.ALL_EFFORTS
    assert caps.mid_conversation_system and caps.structured_outputs
    assert caps.max_output == 128_000 and caps.min_cache_tokens == 512
    # Haiku 5.5 has no server-side fallback.
    assert caps.server_fallback is (model_id != "claude-haiku-5-5")


def test_haiku_4_5_takes_no_effort_and_classic_claude_models_answer_directly():
    haiku = models.catalog_model("claude-haiku-4-5-20251001")
    assert haiku.effort_levels == () and haiku.max_output == 64_000 and not haiku.adaptive_thinking
    assert "xhigh" not in models.catalog_model("claude-sonnet-4-6").effort_levels
    for model in models.catalog_models("anthropic", "legacy"):
        assert not model.thinks, "the classic outputs' budgets leave no room for thinking"


def test_catalog_temperature_agrees_with_the_rule_by_version():
    # Keeps the catalog and the fallback rule for ids outside it from drifting apart.
    for model in models.CATALOG:
        assert model.temperature_ok is llm_client.claude_version_accepts_temperature(model.id), model.id


def test_temperature_comes_from_the_catalog_and_the_api_version():
    for model in models.CATALOG:
        assert llm_client.anthropic_accepts_temperature(model.id, "2023-06-01") is model.temperature_ok, model.id
        assert not llm_client.anthropic_accepts_temperature(model.id, "2026-10-21")
    # Ids outside the catalog keep the rule by version.
    assert llm_client.anthropic_accepts_temperature("claude-opus-4-6", "2023-06-01")
    assert not llm_client.anthropic_accepts_temperature("claude-fable-5-1", "2023-06-01")
    assert not llm_client.anthropic_accepts_temperature("some-future-model", "2023-06-01")


def _vue_string_list(source: str, name: str) -> list:
    block = re.search(rf"const {name} = \[(.*?)\];", source, re.S)
    assert block, f"{name} not found in AdminView.vue"
    return re.findall(r'value: "([^"]+)"', block.group(1))


def test_admin_view_offers_only_ids_the_server_accepts():
    # AdminView keeps its own list until it reads it from the server. Every
    # text job runs on Claude, so it offers Claude models only and no provider.
    source = (ROOT / "frontend/src/views/AdminView.vue").read_text(encoding="utf-8")
    offered = _vue_string_list(source, "ANTHROPIC_TEXT_MODELS")
    assert len(offered) == 3 and all(model_id.startswith("claude-") for model_id in offered)
    assert "OPENAI_TEXT_MODELS" not in source and "handleProviderChange" not in source
    for model_id in offered:
        jobs.check_model_setting("llm_default_model", model_id)
        jobs.check_model_setting("llm_smart_recap_model", model_id)


# ── The job registry ───────────────────────────────────────────────────────


def test_registry_holds_the_v1_jobs_and_the_classic_outputs():
    kinds = {spec.kind for spec in jobs.JOB_SPECS}
    assert kinds == V1_KINDS | CLASSIC_KINDS
    assert {spec.kind for spec in jobs.JOB_SPECS if spec.group == "legacy"} == CLASSIC_KINDS
    # The classic AI job kinds the queue knows are registry kinds.
    assert set(api_logic._ARQ_JOB_FUNCTION_BY_KIND) <= kinds


def test_every_default_is_a_catalog_model_offered_for_its_job():
    for spec in jobs.JOB_SPECS:
        default = spec.default_model("anthropic")
        assert default in [m.id for m in jobs.allowed_models("anthropic", spec.kind)], spec.kind
        caps = models.catalog_model(default)
        assert not caps.effort_levels or spec.effort in caps.effort_levels
        assert spec.default_model("openai") is None, spec.kind


def test_classic_outputs_keep_their_defaults_and_settings():
    expected = {
        "deep_analysis": ("claude-haiku-4-5-20251001", "llm_default_model"),
        "content_repurposing": ("claude-haiku-4-5-20251001", "llm_default_model"),
        "translation": ("claude-haiku-4-5-20251001", "llm_default_model"),
        "smart_recap": ("claude-sonnet-4-6", "llm_smart_recap_model"),
    }
    for kind, (anthropic, legacy_key) in expected.items():
        spec = jobs.job_spec(kind)
        assert (spec.default_anthropic, spec.legacy_setting) == (anthropic, legacy_key)
    assert all(jobs.job_spec(kind).legacy_setting is None for kind in V1_KINDS)


def test_the_session_analysis_is_a_v1_job_on_sonnet(settings):
    """The Follow-up's Session analysis (kind `overall_analysis`) reads the
    session's brief on Sonnet 5.5; the classic `llm_default_model` setting
    no longer picks its model."""
    spec = jobs.job_spec("overall_analysis")
    assert (spec.group, spec.default_anthropic, spec.layout) == ("v1", "claude-sonnet-5-5", "dossier")
    settings["llm_default_model"] = "claude-haiku-4-5-20251001"
    assert jobs.resolve_model("overall_analysis", "anthropic").model_id == "claude-sonnet-5-5"


@pytest.mark.parametrize(
    ("kind", "max_tokens", "stream"),
    [("profile", 6_000, False), ("chat_labels", 12_000, False), ("understanding", 48_000, True), ("verify_meaning", 6_000, False)],
)
def test_v1_budgets_add_thinking_headroom(kind, max_tokens, stream):
    spec = jobs.job_spec(kind)
    assert jobs.budget(spec, models.catalog_model(spec.default_anthropic)) == (max_tokens, stream)


def test_budget_is_the_answer_for_models_that_answer_directly_and_is_capped():
    recap = jobs.job_spec("smart_recap")
    assert jobs.budget(recap, models.catalog_model("claude-sonnet-4-6")) == (900, False)
    content = jobs.job_spec("content_repurposing")
    assert jobs.budget(content, models.catalog_model("claude-haiku-4-5-20251001")) == (12_000, False)
    huge = jobs.JobSpec("huge", "Huge", "v1", "claude-haiku-5-5", "max", 100_000)
    assert jobs.budget(huge, models.catalog_model("claude-haiku-5-5")) == (128_000, True)
    assert jobs.budget(huge, models.catalog_model("claude-haiku-4-5-20251001")) == (64_000, True)


def test_unknown_kind_is_an_error():
    with pytest.raises(ValueError, match="Unknown AI job kind"):
        jobs.job_spec("nope")


# ── Which model a job uses ─────────────────────────────────────────────────


@pytest.fixture
def settings(monkeypatch):
    saved: dict = {}
    monkeypatch.setattr("livestorm_app.db.get_setting", lambda key, default=None: saved.get(key, default))
    return saved


def test_resolve_order_is_per_job_setting_then_legacy_setting_then_default(settings):
    choice = jobs.resolve_model("deep_analysis", "anthropic")
    assert (choice.model_id, choice.source) == ("claude-haiku-4-5-20251001", "default")
    assert choice.caps is models.catalog_model("claude-haiku-4-5-20251001")

    settings["llm_default_model"] = "claude-sonnet-4-6"
    choice = jobs.resolve_model("deep_analysis", "anthropic")
    assert (choice.model_id, choice.source) == ("claude-sonnet-4-6", "legacy_setting")
    # The recap reads its own legacy key.
    assert jobs.resolve_model("smart_recap", "anthropic").source == "default"

    settings["llm_model.anthropic.deep_analysis"] = "claude-opus-4-8"
    choice = jobs.resolve_model("deep_analysis", "anthropic")
    assert (choice.model_id, choice.source) == ("claude-opus-4-8", "setting")
    # Another job still follows the legacy key.
    assert jobs.resolve_model("translation", "anthropic").model_id == "claude-sonnet-4-6"


def test_a_saved_openai_legacy_setting_is_skipped_for_the_jobs_claude_default(settings, caplog):
    # Saved before every text job ran on Claude: never sent, the job's default is used.
    settings["llm_default_model"] = "gpt-4o"
    with caplog.at_level(logging.WARNING, logger="livestorm_app.ai.jobs"):
        choice = jobs.resolve_model("deep_analysis")
    assert (choice.provider, choice.model_id, choice.source) == ("anthropic", "claude-haiku-4-5-20251001", "default")
    assert "gpt-4o, which is not a Claude model" in caplog.text
    settings["llm_smart_recap_model"] = "gpt-5.4-mini"
    assert (jobs.resolve_model("smart_recap").model_id, jobs.resolve_model("smart_recap").source) == ("claude-sonnet-4-6", "default")


@pytest.mark.parametrize(
    ("saved", "status"),
    [("claude-opus-4-7", "hidden"), ("claude-sonnet-4-5", None), ("claude-sonnet-5-5", "offered")],
)
def test_a_saved_id_that_is_not_offered_is_still_used_with_a_warning(settings, caplog, saved, status):
    # Swapping it silently would change classic outputs.
    settings["llm_smart_recap_model"] = saved
    with caplog.at_level(logging.WARNING, logger="livestorm_app.ai.jobs"):
        choice = jobs.resolve_model("smart_recap", "anthropic")
    assert (choice.model_id, choice.source) == (saved, "legacy_setting")
    # A hidden or offered row keeps its parameters; an unknown id has none.
    if status is None:
        assert choice.caps is None
    else:
        assert choice.caps is not None and choice.caps.status == status
    assert "not offered for smart_recap; still using it" in caplog.text


def test_an_offered_saved_id_logs_nothing(settings, caplog):
    settings["llm_smart_recap_model"] = "claude-opus-4-8"
    with caplog.at_level(logging.WARNING, logger="livestorm_app.ai.jobs"):
        assert jobs.resolve_model("smart_recap", "anthropic").model_id == "claude-opus-4-8"
    assert caplog.text == ""


def test_v1_jobs_never_read_the_legacy_keys(settings):
    # The legacy keys belong to the classic outputs; a v1 job uses its own key or its default.
    settings["llm_default_model"] = "claude-opus-4-8"
    settings["llm_smart_recap_model"] = "claude-opus-4-8"
    for kind in V1_KINDS:
        choice = jobs.resolve_model(kind, "anthropic")
        assert choice.source == "default" and choice.model_id == jobs.job_spec(kind).default_anthropic, kind


def test_every_job_resolves_to_claude_whatever_provider_was_saved(settings, monkeypatch):
    # The `llm_provider` setting and LLM_PROVIDER are no longer read, and a
    # per-job OpenAI key is not a model setting any more.
    settings["llm_provider"] = "openai"
    settings["llm_model.openai.smart_recap"] = "gpt-5.4-mini"
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    assert llm_client.get_provider() == "anthropic"
    for provider in (None, "anthropic", "openai", "azure"):
        choice = jobs.resolve_model("smart_recap", provider)
        assert (choice.provider, choice.model_id, choice.source) == ("anthropic", "claude-sonnet-4-6", "default")
        assert jobs.resolve_model("understanding", provider).model_id == "claude-sonnet-5-5"
    assert llm_client.get_llm_model("smart_recap") == "claude-sonnet-4-6"


def test_an_unreadable_setting_falls_back_to_the_default(monkeypatch):
    def broken(key, default=None):
        raise RuntimeError("database down")

    monkeypatch.setattr("livestorm_app.db.get_setting", broken)
    assert jobs.resolve_model("deep_analysis", "anthropic").model_id == "claude-haiku-4-5-20251001"


GENERATED_BUNDLE = {"summary": "S", "blog": "B", "email": "E", "social_media": "M"}


@pytest.mark.parametrize(
    ("run", "dimension", "generator", "kind"),
    [
        (api_logic.run_overall_analysis, "French", "translate_markdown_with_openai", "translation"),
        (api_logic.run_deep_analysis, "English", "analyze_with_openai", "deep_analysis"),
        (api_logic.run_deep_analysis, "French", "translate_markdown_with_openai", "translation"),
        (api_logic.run_smart_recap, "hype", "analyze_with_openai", "smart_recap"),
        (api_logic.run_content_repurposing, "English", "generate_content_repurpose_bundle_with_openai", "content_repurposing"),
        (api_logic.run_content_repurposing, "French", "translate_content_repurpose_bundle_with_openai", "translation"),
    ],
)
def test_each_classic_runner_asks_for_its_own_job_kind(monkeypatch, run, dimension, generator, kind):
    row = {
        "session_payload": {"id": "s1"},
        "chat_payload": {"data": []},
        "questions_payload": {"data": []},
        "transcript_payload": {"transcript": {"segments": [{"start": 0, "end": 2, "text": "Hello there", "speaker": "A"}]}},
        "analysis_bundle": {"English": "EN"},
        "deep_analysis_bundle": {"English": "EN"},
        "content_repurpose_bundle": {"English": GENERATED_BUNDLE},
    }
    asked: list = []
    used: list = []
    monkeypatch.setattr(api_logic, "fetch_cached_session", lambda key, sid, *, organization_id: dict(row))
    monkeypatch.setattr(api_logic, "merge_cached_bundle_entry", lambda *args, **kwargs: None)
    monkeypatch.setattr(api_logic, "get_llm_model", lambda job_kind: asked.append(job_kind) or f"model-for-{job_kind}")
    output = dict(GENERATED_BUNDLE) if "bundle" in generator else "# Output"
    monkeypatch.setattr(api_logic, generator, lambda **kwargs: used.append(kwargs["model"]) or output)
    run("llm-key", "s1", dimension)
    assert asked == [kind] and used == [f"model-for-{kind}"]
