"""Data integrity: results are saved atomically, completely, or not at all.

Real-Postgres tests use the `test_database` fixture (skipped without
TEST_DATABASE_URL). Everything else runs on fakes.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import importlib.util
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests
from pydantic import BaseModel

from livestorm_app import api_logic, db, llm_client, worker
from livestorm_app.ai import generation, schemas
from livestorm_app.ai.usage import CallContext, LLMUsage
from livestorm_app.db import ALL_ORGS
from livestorm_app.gladia import transcriber
from livestorm_app.livestorm import client as livestorm_client
from livestorm_app.livestorm import payloads as livestorm_payloads


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
    monkeypatch.setattr(generation, "call_llm", lambda *args, **kwargs: "")
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


def test_an_empty_transcript_fails_content_repurposing(runner_backend, monkeypatch):
    # Runs the real generator; only the transcript text is empty. It used to
    # return "Transcript is empty." four times, which passed the
    # completeness check and was saved as content.
    def must_not_call(*args, **kwargs):
        raise AssertionError("no model call for an empty transcript")

    monkeypatch.setattr(api_logic, "build_transcript_plain_text", lambda payload: "  ")
    monkeypatch.setattr(generation, "call_llm", must_not_call)
    with pytest.raises(RuntimeError, match="transcript is empty"):
        api_logic.run_content_repurposing("llm-key", "s1", "English")
    assert runner_backend == []


def test_a_placeholder_bundle_from_the_model_fails_the_job(runner_backend, monkeypatch):
    # The model answering with the empty-transcript placeholder is not content.
    placeholder = '{"summary":"Transcript is empty.","blog":"Transcript is empty.","email":"Transcript is empty.","social_media":"Transcript is empty."}'
    monkeypatch.setattr(generation, "call_llm", lambda *args, **kwargs: placeholder)
    with pytest.raises(RuntimeError, match="missing: summary, blog, email, social_media"):
        api_logic.run_content_repurposing("llm-key", "s1", "English")
    assert runner_backend == []


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

    def fake_send(api_key, model, messages, temperature, max_tokens):
        calls.append(max_tokens)
        text, truncated = responses[len(calls) - 1]
        return llm_client.LLMResult(
            text=text, stop="max_tokens" if truncated else "complete", stop_raw="max_tokens" if truncated else "end_turn",
            refusal_category=None, model_served=model, request_id=None, usage=LLMUsage(), duration_ms=1,
        )

    monkeypatch.setattr(llm_client, "get_provider", lambda: "anthropic")
    monkeypatch.setattr(llm_client, "_anthropic_send", fake_send)
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


# ── Model usage rows (real Postgres) ──────────────────────────────────────

SECRET_KEY = "sk-test-SECRET"
_TRANSCRIPT = {"text": "Welcome everyone. Today we walk through the roadmap."}


def _answer(text: str, stop: str = "end_turn") -> dict:
    return {
        "model": "claude-sonnet-4-6",
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop,
        "usage": {"input_tokens": 2000, "output_tokens": 400, "service_tier": "standard", "note": "free text with spaces"},
    }


def _provider_http(monkeypatch, answers: list) -> list:
    """Fake Anthropic at the HTTP boundary, so the real adapter builds the request."""
    sent: list = []

    def fake_post(url, **kwargs):
        sent.append(kwargs["headers"])
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps(answers.pop(0)).encode()
        response.headers["request-id"] = f"req_{len(sent)}"
        return response

    monkeypatch.setattr(llm_client, "get_provider", lambda: "anthropic")
    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    return sent


def _usage_rows(session_id: str) -> list:
    with db.get_db_connection() as conn:
        rows = conn.execute("SELECT * FROM llm_usage WHERE session_id = %s ORDER BY id", (session_id,)).fetchall()
    return [dict(row) for row in rows]


def test_each_model_call_saves_one_usage_row(test_database, monkeypatch, caplog):
    db.upsert_cached_session("key-a", "sess-u", organization_id="org-a", transcript_payload=_TRANSCRIPT)
    sent = _provider_http(monkeypatch, [_answer("# Partial", "max_tokens"), _answer("# Hype recap")])
    with caplog.at_level(logging.INFO):
        api_logic.run_smart_recap(SECRET_KEY, "sess-u", "hype")

    assert _row("sess-u")["smart_recap_bundle"] == {"hype": "# Hype recap"}
    rows = _usage_rows("sess-u")
    assert [(row["purpose"], row["outcome"], row["max_tokens"]) for row in rows] == [
        ("main", "truncated", 900), ("truncation_retry", "ok", 1800),
    ]
    assert [row["request_id"] for row in rows] == ["req_1", "req_2"]
    for row in rows:
        assert (row["job_kind"], row["job_id"], row["organization_id"]) == ("smart_recap", "smart_recap:sess-u:hype", "org-a")
        assert (row["provider"], row["model_requested"], row["model_served"]) == ("anthropic", "claude-sonnet-4-6", "claude-sonnet-4-6")
        # Sonnet 4.6: $3 in and $15 out per million tokens.
        assert (row["input_tokens"], row["output_tokens"], row["cost_usd_micros"]) == (2000, 400, 2000 * 3 + 400 * 15)
        # The provider's usage object, its free-text "note" dropped.
        assert row["usage_raw"] == {"input_tokens": 2000, "output_tokens": 400, "service_tier": "standard"}
        assert row["created_at"] is not None and row["duration_ms"] >= 0
    # The key went to the provider, and nowhere else.
    assert [headers["x-api-key"] for headers in sent] == [SECRET_KEY, SECRET_KEY]
    assert SECRET_KEY not in caplog.text and SECRET_KEY not in repr(rows)
    assert "Hype recap" not in repr(rows), "a usage row never holds the output"


def test_a_failed_usage_insert_keeps_the_output(test_database, monkeypatch, caplog):
    # Owner decision: the output is complete and paid for, so the job saves it and logs the row
    # (docs/data-model.md#caching-rules).
    db.upsert_cached_session("key-a", "sess-u", organization_id="org-a", transcript_payload=_TRANSCRIPT)
    with db.get_db_connection() as conn:
        conn.execute("DROP TABLE llm_usage")
        conn.commit()
    _provider_http(monkeypatch, [_answer("# Hype recap")])
    api_logic.run_smart_recap(SECRET_KEY, "sess-u", "hype")

    assert _row("sess-u")["smart_recap_bundle"] == {"hype": "# Hype recap"}
    errors = [record.getMessage() for record in caplog.records if record.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert "llm_usage insert failed (UndefinedTable sqlstate=42P01)" in errors[0]
    for part in ("job=smart_recap", "org=org-a", "session=sess-u", "in=2000", "out=400", "usd=0.012000"):
        assert part in errors[0], part
    assert SECRET_KEY not in caplog.text


def test_usage_rows_outlive_a_deleted_session(test_database, monkeypatch):
    # Usage rows hold no content, so deleting the session keeps them.
    db.upsert_cached_session("key-a", "sess-u", organization_id="org-a", transcript_payload=_TRANSCRIPT)
    _provider_http(monkeypatch, [_answer("# Hype recap")])
    api_logic.run_smart_recap("llm-key", "sess-u", "hype")
    assert db.admin_delete_session("sess-u")
    assert _row("sess-u") is None
    assert len(_usage_rows("sess-u")) == 1
    db.ensure_database_schema()  # idempotent with the table and its indexes
    assert len(_usage_rows("sess-u")) == 1


def _refusal(category) -> dict:
    # A refusal mid-answer: partial text, which must never be saved.
    return {
        "model": "claude-haiku-4-5-20251001",
        "content": [{"type": "text", "text": "# Partial recap"}],
        "stop_reason": "refusal",
        "stop_details": {"type": "refusal", "category": category} if category else None,
        "usage": {"input_tokens": 2000, "output_tokens": 15},
    }


@pytest.mark.parametrize(
    ("run", "column", "dimension", "category"),
    [
        (api_logic.run_smart_recap, "smart_recap_bundle", "hype", "bio"),
        (api_logic.run_content_repurposing, "content_repurpose_bundle", "English", None),
    ],
)
def test_a_refusal_saves_nothing_but_its_usage_row(test_database, monkeypatch, run, column, dimension, category):
    db.upsert_cached_session("key-a", "sess-u", organization_id="org-a", transcript_payload=_TRANSCRIPT)
    sent = _provider_http(monkeypatch, [_refusal(category)] * 3)
    with pytest.raises(llm_client.LLMRefused) as refused:
        run(SECRET_KEY, "sess-u", dimension)
    assert str(refused.value) == "The model declined this session. Nothing was saved.", "no category in the message"
    assert not _row("sess-u")[column], "no bundle entry"
    assert len(sent) == 1, "one paid call: a refusal is never asked again"
    rows = _usage_rows("sess-u")
    assert [(row["purpose"], row["outcome"], row["stop_reason"], row["refusal_category"]) for row in rows] == [
        ("main", "refused", "refusal", category),
    ]
    assert rows[0]["organization_id"] == "org-a" and rows[0]["output_tokens"] == 15
    assert "Partial" not in repr(rows)


def test_an_invalid_structured_answer_is_recorded_and_not_returned(test_database, monkeypatch):
    class Verdict(BaseModel):
        holds: bool

    monkeypatch.setattr(schemas, "SCHEMAS", dict(schemas.SCHEMAS))
    monkeypatch.setattr(schemas, "_STRICT", dict(schemas._STRICT))
    schemas.json_schema(Verdict)
    answer = {**_answer('{"holds": "maybe"}'), "model": "claude-haiku-5-5"}
    _provider_http(monkeypatch, [answer])
    ctx = CallContext("verify_meaning", "sess-v", "org-a", "verify_meaning:sess-v:_")
    with pytest.raises(llm_client.LLMInvalidOutput, match="did not match its format"):
        llm_client.generate_json("verify_meaning", Verdict, [{"role": "user", "content": "Check"}], api_key=SECRET_KEY, ctx=ctx)
    rows = _usage_rows("sess-v")
    assert [(row["job_kind"], row["outcome"], row["stop_reason"], row["model_requested"]) for row in rows] == [
        ("verify_meaning", "invalid_output", "end_turn", "claude-haiku-5-5"),
    ]
    assert "maybe" not in repr(rows)


def test_a_usage_row_takes_known_columns_only(test_database):
    with pytest.raises(ValueError, match="Unknown llm_usage columns"):
        db.insert_llm_usage({"job_kind": "x", "prompt": "never stored"})


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


# ── Registrant join links are never stored ─────────────────────────────────
#
# Synthetic payloads only. A person's `registrant_detail` holds a personal
# join link (`password_key`, `connection_link`) that StormIQ doesn't need.

JOIN_LINK_KEYS = ("password_key", "connection_link")
OLD_UPDATED_AT = datetime(2026, 1, 2, 3, 4, 5, 678901, tzinfo=timezone.utc)


def _person_with_join_link(person_id: str) -> dict:
    return {
        "id": person_id,
        "type": "people",
        "attributes": {
            "first_name": "Test",
            "registrant_detail": {
                "attended": True,
                "fields": [{"id": "job_title", "value": "Tester"}],
                "password_key": f"pk-{person_id}",
                "connection_link": f"https://example.test/join/{person_id}?key=pk-{person_id}",
            },
        },
    }


def _base_payloads() -> dict:
    return {
        "session_payload": {
            "data": {"id": "s1", "type": "sessions", "attributes": {"event_id": "e1", "status": "past"}},
            "included": [_person_with_join_link("p1"), _person_with_join_link("p2")],
        },
        "chat_payload": {
            # As Livestorm sends it with include=people: the author is the
            # people link; `author_id` is another id, not a people id.
            "data": [{
                "id": "m1", "type": "chat_messages",
                "attributes": {"text": "Hi", "author_id": "c-0001", "created_at": 1700000000},
                "relationships": {"people": {"data": {"type": "people", "id": "p1"}}},
            }],
            "included": [_person_with_join_link("p1")],
            "meta": {"next_page": None},
        },
        "questions_payload": {
            "data": [{"id": "q1", "type": "questions", "attributes": {"question": "Why?", "created_at": 1700000100}}],
            "included": [_person_with_join_link("p2")],
            "meta": {"next_page": None},
        },
    }


def _has_join_link(value) -> bool:
    text = json.dumps(value)
    return any(key in text for key in JOIN_LINK_KEYS)


def _whole_row(session_id: str) -> dict:
    with db.get_db_connection() as conn:
        return dict(conn.execute("SELECT * FROM session_cache WHERE session_id = %s", (session_id,)).fetchone())


def _set_updated_at(session_id: str, value: datetime) -> None:
    with db.get_db_connection() as conn:
        conn.execute("UPDATE session_cache SET updated_at = %s WHERE session_id = %s", (value, session_id))


def test_a_base_fetch_never_saves_a_join_link(monkeypatch):
    upstream = _base_payloads()
    responses = {
        "/sessions/s1": upstream["session_payload"],
        "/sessions/s1/chat_messages": upstream["chat_payload"],
        "/sessions/s1/questions": upstream["questions_payload"],
        "/events/e1": {"data": {"id": "e1", "type": "events", "attributes": {"title": "Event"}}},
    }

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return copy.deepcopy(self.payload)

    def fake_get(url, *, headers, params=None):
        return FakeResponse(responses[url.split("/v1", 1)[1]])

    saved: dict = {}
    monkeypatch.setattr(livestorm_client, "livestorm_get", fake_get)
    monkeypatch.setattr(api_logic, "upsert_cached_session", lambda key, sid, **fields: saved.update(fields))
    monkeypatch.setattr(api_logic, "fetch_cached_session", lambda key, sid, *, organization_id: dict(saved))
    monkeypatch.setattr(api_logic, "_serialize_cached_session", lambda sid, cached: {"sessionId": sid})

    api_logic.fetch_session_base_data("Bearer t", "s1", force_refresh=True, read_scope="org-a", organization_id="org-a")

    for column in db.BASE_PAYLOAD_COLUMNS:
        assert _has_join_link(upstream[column]), f"the {column} fixture must hold a join link"
        assert not _has_join_link(saved[column]), column
    for column in ("session_payload", "questions_payload"):
        detail = saved[column]["included"][0]["attributes"]["registrant_detail"]
        assert detail == {"attended": True, "fields": [{"id": "job_title", "value": "Tester"}]}, column
    # The chat keeps each message's people link, not the people records.
    assert "included" not in saved["chat_payload"]
    assert saved["chat_payload"]["data"] == upstream["chat_payload"]["data"]
    assert saved["chat_payload"]["data"][0]["relationships"] == {"people": {"data": {"type": "people", "id": "p1"}}}


def test_rewriting_base_payloads_changes_nothing_else(test_database):
    db.upsert_cached_session(
        "key-a",
        "sess-a",
        organization_id="org-a",
        created_by_email="owner@example.test",
        transcript_payload={"text": "x"},
        analysis_bundle={"English": "EN"},
        cover_image_bytes=b"png-bytes",
        cover_image_mime="image/png",
        **_base_payloads(),
    )
    _set_updated_at("sess-a", OLD_UPDATED_AT)
    before = _whole_row("sess-a")
    stripped = {column: livestorm_payloads.strip_payload_join_links(before[column]) for column in db.BASE_PAYLOAD_COLUMNS}

    assert db.rewrite_base_payloads("sess-a", expected_updated_at=before["updated_at"], **stripped) is True

    after = _whole_row("sess-a")
    assert after["updated_at"] == OLD_UPDATED_AT, "a rewrite must not reorder the library"
    for column in db.BASE_PAYLOAD_COLUMNS:
        assert after[column] == stripped[column]
        assert not _has_join_link(after[column])
    others = set(before) - set(db.BASE_PAYLOAD_COLUMNS)
    assert {key: after[key] for key in others} == {key: before[key] for key in others}


def test_a_rewrite_never_overwrites_a_newer_save(test_database):
    db.upsert_cached_session("key-a", "sess-a", **_base_payloads())
    read = db.scan_base_payloads("", 10)[0]
    newer = {"data": {"id": "s1", "type": "sessions", "attributes": {"status": "past", "name": "refetched"}}}
    db.upsert_cached_session("key-a", "sess-a", session_payload=newer)

    stale = livestorm_payloads.strip_payload_join_links(read["session_payload"])
    assert db.rewrite_base_payloads("sess-a", expected_updated_at=read["updated_at"], session_payload=stale) is False
    assert _row("sess-a")["session_payload"] == newer
    assert db.rewrite_base_payloads("sess-missing", expected_updated_at=read["updated_at"], session_payload=stale) is False


def test_scanning_base_payloads_walks_every_row_in_batches(test_database):
    for session_id in ("sess-c", "sess-a", "sess-b"):
        db.upsert_cached_session("key-a", session_id, organization_id=f"org-{session_id}", **_base_payloads())
    first = db.scan_base_payloads("", 2)
    second = db.scan_base_payloads(first[-1]["session_id"], 2)
    assert [row["session_id"] for row in first + second] == ["sess-a", "sess-b", "sess-c"]
    assert db.scan_base_payloads("sess-c", 2) == []
    assert set(first[0]) == {"session_id", "updated_at", *db.BASE_PAYLOAD_COLUMNS}


@pytest.mark.parametrize(
    "payloads",
    [{}, {"transcript_payload": {}}, {"session_payload": None}, {"chat_payload": ["not", "an", "object"]}],
    ids=["nothing", "other-column", "null", "not-an-object"],
)
def test_rewrite_base_payloads_rejects_anything_but_base_payload_objects(payloads):
    with pytest.raises(ValueError):
        db.rewrite_base_payloads("sess-a", expected_updated_at=OLD_UPDATED_AT, **payloads)


def test_rewrite_base_payloads_raises_when_the_database_write_fails(monkeypatch):
    def broken_connection():
        raise ConnectionError("database unreachable")

    monkeypatch.setattr(db, "database_enabled", lambda: True)
    monkeypatch.setattr(db, "get_db_connection", broken_connection)
    with pytest.raises(RuntimeError, match="Could not rewrite"):
        db.rewrite_base_payloads("sess-a", expected_updated_at=OLD_UPDATED_AT, session_payload={"data": {}})


def _join_link_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "strip_registrant_join_links.py"
    spec = importlib.util.spec_from_file_location("strip_registrant_join_links", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_join_link_backfill_writes_only_when_applied(test_database):
    script = _join_link_script()
    clean = {column: livestorm_payloads.strip_payload_join_links(value) for column, value in _base_payloads().items()}
    db.upsert_cached_session("key-a", "sess-links", organization_id="org-a", **_base_payloads())
    db.upsert_cached_session("key-a", "sess-clean", organization_id="org-a", **clean)
    for session_id in ("sess-links", "sess-clean"):
        _set_updated_at(session_id, OLD_UPDATED_AT)
    clean_before = _whole_row("sess-clean")

    lines: list = []
    totals = script.run(apply=False, batch_size=1, out=lines.append)
    assert totals == {"scanned": 2, "to_clean": 1, "cleaned": 0, "changed_since_read": 0}
    assert lines == ["would clean sess-links: session_payload=2 chat_payload=1 questions_payload=1"]
    assert _has_join_link(_whole_row("sess-links")["session_payload"]), "a dry run writes nothing"

    lines.clear()
    totals = script.run(apply=True, batch_size=1, out=lines.append)
    assert totals == {"scanned": 2, "to_clean": 1, "cleaned": 1, "changed_since_read": 0}
    assert lines == ["cleaned sess-links: session_payload=2 chat_payload=1 questions_payload=1"]
    cleaned = _whole_row("sess-links")
    assert {column: cleaned[column] for column in db.BASE_PAYLOAD_COLUMNS} == clean
    assert cleaned["updated_at"] == OLD_UPDATED_AT
    assert _whole_row("sess-clean") == clean_before

    assert script.run(apply=True, out=lines.append)["to_clean"] == 0, "a second run finds nothing to do"


def test_the_join_link_backfill_skips_a_row_saved_since_it_was_read(test_database, monkeypatch, capsys):
    script = _join_link_script()
    db.upsert_cached_session("key-a", "sess-links", organization_id="org-a", **_base_payloads())
    _set_updated_at("sess-links", OLD_UPDATED_AT)
    newer_saves = iter(datetime(2026, 2, 1, 0, 0, second, tzinfo=timezone.utc) for second in range(10))

    def saved_in_between(session_id, **kwargs):
        # A fetch or an AI result is saved between the scan and the rewrite.
        _set_updated_at(session_id, next(newer_saves))
        return db.rewrite_base_payloads(session_id, **kwargs)

    monkeypatch.setattr(script, "rewrite_base_payloads", saved_in_between)

    lines: list = []
    totals = script.run(apply=True, out=lines.append)
    assert totals == {"scanned": 1, "to_clean": 1, "cleaned": 0, "changed_since_read": 1}
    assert lines == ["skipped sess-links: saved again since it was read; run the script again"]
    assert _has_join_link(_whole_row("sess-links")["session_payload"]), "a skipped row is left for the next run"

    monkeypatch.setattr("sys.argv", ["strip_registrant_join_links.py", "--apply"])
    assert script.main() == 2, "a skipped row makes the script exit non-zero"
    assert capsys.readouterr().out.splitlines()[-1] == (
        "Applied: scanned 1 session(s); 1 held join links; cleaned 0; skipped 1 saved again since read."
    )


def test_the_join_link_backfill_refuses_a_remote_database(monkeypatch):
    script = _join_link_script()
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@db.example.test:5432/app")
    with pytest.raises(SystemExit) as refused:
        script._require_database(allow_remote=False)
    assert "db.example.test" not in str(refused.value.code), "the refusal must not print the URL"
    script._require_database(allow_remote=True)

    monkeypatch.setenv("DATABASE_URL", "")
    with pytest.raises(SystemExit):
        script._require_database(allow_remote=True)
