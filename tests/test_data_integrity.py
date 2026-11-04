"""Data integrity: results are saved atomically, completely, or not at all.

Real-Postgres tests use the `test_database` fixture (skipped without
TEST_DATABASE_URL). Everything else runs on fakes.
"""

from __future__ import annotations

import asyncio
import hashlib
import threading

import pytest

from livestorm_app import api_logic, db, llm_client, services, worker
from livestorm_app.db import ALL_ORGS
from livestorm_app.gladia import transcriber


def _row(session_id: str) -> dict:
    return db.fetch_cached_session("", session_id, organization_id=ALL_ORGS)


# ── Atomic bundle merges (real Postgres) ──────────────────────────────────


def test_concurrent_merges_into_one_bundle_lose_nothing(test_database):
    db.upsert_cached_session("key-a", "sess-a", organization_id="org-a", session_payload={"id": "sess-a"})
    keys = [f"lang-{i}" for i in range(20)]
    barrier = threading.Barrier(len(keys))
    errors: list = []

    def merge(key: str) -> None:
        try:
            barrier.wait()
            db.merge_cached_bundle_entry("sess-a", "analysis_bundle", key, f"text for {key}")
        except Exception as exc:  # surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=merge, args=(key,)) for key in keys]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert _row("sess-a")["analysis_bundle"] == {key: f"text for {key}" for key in keys}


def test_merge_keeps_other_entries_and_writes_legacy_text_only_when_asked(test_database):
    db.upsert_cached_session("key-a", "sess-a", analysis_bundle={"English": "EN"}, analysis_md="EN")
    db.merge_cached_bundle_entry("sess-a", "analysis_bundle", "French", "FR")
    row = _row("sess-a")
    assert row["analysis_bundle"] == {"English": "EN", "French": "FR"}
    assert row["analysis_md"] == "EN", "a French result must not replace the legacy English column"

    db.merge_cached_bundle_entry("sess-a", "analysis_bundle", "English", "EN2", analysis_md="EN2")
    row = _row("sess-a")
    assert row["analysis_bundle"] == {"English": "EN2", "French": "FR"}
    assert row["analysis_md"] == "EN2"


def test_merge_stores_nested_bundles_and_replaces_non_object_values(test_database):
    db.upsert_cached_session("key-a", "sess-a", content_repurpose_bundle=["not", "an", "object"])
    entry = {"summary": "S", "blog": "B", "email": "E", "social_media": "M"}
    db.merge_cached_bundle_entry("sess-a", "content_repurpose_bundle", "English", entry)
    assert _row("sess-a")["content_repurpose_bundle"] == {"English": entry}


def test_merge_on_a_missing_session_raises(test_database):
    with pytest.raises(RuntimeError, match="no longer cached"):
        db.merge_cached_bundle_entry("sess-missing", "smart_recap_bundle", "hype", "text")


def test_account_key_hash_is_first_writer_wins(test_database):
    db.upsert_cached_session("livestorm-key", "sess-a", session_payload={"id": "sess-a"})
    db.upsert_cached_session("llm-provider-key", "sess-a", smart_recap_bundle={"hype": "x"})
    assert _row("sess-a")["account_key_hash"] == hashlib.sha256(b"livestorm-key").hexdigest()


# ── Failures surface instead of being swallowed ───────────────────────────


def test_merge_rejects_unknown_columns():
    with pytest.raises(ValueError):
        db.merge_cached_bundle_entry("sess-a", "transcript_payload", "x", "y")
    with pytest.raises(ValueError):
        db.merge_cached_bundle_entry("sess-a", "analysis_bundle", "English", "y", session_payload="nope")


def test_upsert_raises_when_the_database_write_fails(monkeypatch):
    def broken_connection():
        raise ConnectionError("database unreachable")

    monkeypatch.setattr(db, "database_enabled", lambda: True)
    monkeypatch.setattr(db, "get_db_connection", broken_connection)
    with pytest.raises(RuntimeError, match="Could not save"):
        db.upsert_cached_session("key-a", "sess-a", transcript_payload={"text": "x"})
    with pytest.raises(ValueError):
        db.upsert_cached_session("", "sess-a", transcript_payload={"text": "x"})


# ── Runners save complete results only ────────────────────────────────────


@pytest.fixture
def runner_backend(monkeypatch):
    merges: list = []
    row = {
        "session_payload": {"id": "s1"},
        "chat_payload": {"data": []},
        "questions_payload": {"data": []},
        "transcript_payload": {"transcript": {"segments": [{"start": 0, "end": 2, "text": "Hello there everyone", "speaker": "A"}]}},
        "analysis_bundle": {"English": "Existing EN"},
        "smart_recap_bundle": {"professional": "Existing recap"},
        "content_repurpose_bundle": {},
    }
    monkeypatch.setattr(api_logic, "fetch_cached_session", lambda key, sid, *, organization_id: dict(row))
    monkeypatch.setattr(api_logic, "merge_cached_bundle_entry", lambda *args, **kwargs: merges.append((args, kwargs)))
    monkeypatch.setattr(api_logic, "get_llm_model", lambda role: "test-model")
    return merges


@pytest.mark.parametrize("empty_output", ["", "   "])
def test_empty_recap_is_an_error_not_a_silent_success(runner_backend, monkeypatch, empty_output):
    monkeypatch.setattr(api_logic, "analyze_with_openai", lambda **kwargs: empty_output)
    with pytest.raises(RuntimeError, match="empty smart recap"):
        api_logic.run_smart_recap("llm-key", "s1", "hype")
    assert runner_backend == []


@pytest.mark.parametrize(
    ("run", "label"),
    [
        (lambda: api_logic.run_overall_analysis("llm-key", "s1", "English"), "empty overall analysis"),
        (lambda: api_logic.run_deep_analysis("llm-key", "s1", "English"), "empty deep analysis"),
        (lambda: api_logic.run_smart_recap("llm-key", "s1", "hype"), "empty smart recap"),
    ],
)
def test_an_empty_model_answer_fails_the_job(runner_backend, monkeypatch, run, label):
    # Runs the real analyze_with_openai; only the provider call is faked. It
    # used to substitute a placeholder text, which was then saved as output.
    monkeypatch.setattr(services, "call_llm", lambda *args, **kwargs: "")
    with pytest.raises(RuntimeError, match=label):
        run()
    assert runner_backend == []


def test_recap_writes_only_its_own_tone(runner_backend, monkeypatch):
    monkeypatch.setattr(api_logic, "analyze_with_openai", lambda **kwargs: "  # Hype recap  ")
    api_logic.run_smart_recap("llm-key", "s1", "hype")
    assert runner_backend == [(("s1", "smart_recap_bundle", "hype", "# Hype recap"), {})]


def test_french_translation_does_not_touch_the_legacy_english_column(runner_backend, monkeypatch):
    monkeypatch.setattr(api_logic, "translate_markdown_with_openai", lambda **kwargs: "Analyse FR")
    api_logic.run_overall_analysis("llm-key", "s1", "French")
    assert runner_backend == [(("s1", "analysis_bundle", "French", "Analyse FR"), {})]


def test_incomplete_repurposing_bundle_is_not_saved(runner_backend, monkeypatch):
    monkeypatch.setattr(
        api_logic,
        "generate_content_repurpose_bundle_with_openai",
        lambda **kwargs: {"summary": "S", "blog": "B", "email": "", "social_media": "M"},
    )
    with pytest.raises(RuntimeError, match="missing: email"):
        api_logic.run_content_repurposing("llm-key", "s1", "English")
    assert runner_backend == []


# ── LLM truncation ────────────────────────────────────────────────────────


def _fake_provider(monkeypatch, responses: list):
    calls: list = []

    def fake_call(api_key, model, messages, temperature, max_tokens):
        calls.append(max_tokens)
        return responses[len(calls) - 1]

    monkeypatch.setattr(llm_client, "get_provider", lambda: "anthropic")
    monkeypatch.setattr(llm_client, "_call_anthropic", fake_call)
    return calls


def test_truncated_output_is_retried_with_a_larger_budget(monkeypatch):
    calls = _fake_provider(monkeypatch, [("partial", True), ("complete", False)])
    assert llm_client.call_llm("k", "m", [], max_tokens=2500) == "complete"
    assert calls == [2500, 5000]


def test_output_still_truncated_after_retry_raises(monkeypatch):
    calls = _fake_provider(monkeypatch, [("partial", True), ("still partial", True)])
    with pytest.raises(llm_client.LLMOutputTruncated):
        llm_client.call_llm("k", "m", [], max_tokens=12000)
    assert calls == [12000, llm_client.TRUNCATION_RETRY_MAX_TOKENS]


def test_complete_output_is_returned_without_retry(monkeypatch):
    calls = _fake_provider(monkeypatch, [("complete", False)])
    assert llm_client.call_llm("k", "m", [], max_tokens=900) == "complete"
    assert calls == [900]


# ── Transcription heartbeat and failure handling ─────────────────────────


def test_gladia_polling_reports_progress_while_status_is_unchanged(monkeypatch):
    statuses = iter(["queued"] + ["processing"] * 6 + ["done"])
    clock = {"now": 0.0}

    def fake_monotonic() -> float:
        clock["now"] += 25.0
        return clock["now"]

    monkeypatch.setattr(transcriber, "_json_request", lambda **kwargs: {"status": next(statuses)})
    monkeypatch.setattr(transcriber.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(transcriber.time, "monotonic", fake_monotonic)
    reports: list = []
    transcriber._poll_gladia_transcription("job-1", "gladia-key", on_progress=reports.append)
    processing_reports = [r for r in reports if r["gladia_status"] == "processing"]
    assert len(processing_reports) >= 3, "a long 'processing' phase must keep the job heartbeat fresh"


def test_transcription_is_marked_error_when_saving_fails(monkeypatch):
    statuses: list = []
    monkeypatch.setattr(worker, "update_transcript_job_status", lambda job_id, status, error=None: statuses.append((status, error)))
    monkeypatch.setattr(worker, "publish_progress_sync", lambda *a, **k: None)
    monkeypatch.setattr(worker, "clear_progress_sync", lambda *a, **k: None)
    monkeypatch.setattr("livestorm_app.transcript_client.fetch_session_transcript", lambda *a, **k: {"text": "x"})
    monkeypatch.setattr("livestorm_app.auth.livestorm_auth_for_job", lambda connection_id: "Bearer t")
    monkeypatch.setattr(worker, "claim_transcript_job", lambda job_id: {"connection_id": "conn-a"})
    monkeypatch.setenv("GLADIA_KEY", "gladia-key")

    def failing_upsert(*args, **kwargs):
        raise RuntimeError("Could not save the session to the database. Please try again.")

    monkeypatch.setattr(worker, "upsert_cached_session", failing_upsert)
    with pytest.raises(RuntimeError):
        asyncio.run(worker.run_transcription({}, "job-1", "s1"))
    assert statuses[-1][0] == "error"
    assert ("completed", None) not in statuses
