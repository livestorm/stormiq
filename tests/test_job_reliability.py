"""Job cost and reliability: dedupe, retries, timeouts, no secrets in the queue.

Real Redis tests need TEST_REDIS_URL, real Postgres tests TEST_DATABASE_URL
(both skip otherwise; CI provides both). Everything else uses fakes.
"""

from __future__ import annotations

import ast
import asyncio
import copy
import inspect
import json
import logging
import pickle
import re
import subprocess
import threading
import time
import urllib.error
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from arq.jobs import serialize_result
from urllib3._collections import HTTPHeaderDict

from livestorm_app.routes import models as routes_models
from livestorm_app.routes import sessions as routes_sessions
from livestorm_app import api_logic, auth, db, llm_client, people_sync, queue, worker
from livestorm_app.livestorm import client as livestorm_client
from livestorm_app.db import (
    ALL_ORGS,
    claim_transcript_job,
    create_transcript_job,
    ensure_database_schema,
    get_db_connection,
    update_transcript_job_status,
)
from livestorm_app.gladia import transcriber
import synthetic_session as synthetic


# ── Queue: deterministic ids on a real Redis ───────────────────────────────


async def _flaky_job(ctx):
    raise RuntimeError("model overloaded")


async def _good_job(ctx):
    return "ok"


def _run_worker_once(*functions, **settings) -> None:
    """Process everything queued, then stop (arq burst mode). `settings`
    are extra Worker options, such as the worker's `job_serializer`."""
    from arq.worker import Worker, func

    async def _run():
        w = Worker(
            functions=[func(f, name="test_job") for f in functions],
            redis_settings=queue.get_redis_settings(),
            burst=True,
            handle_signals=False,
            max_tries=1,
            poll_delay=0.01,
            **settings,
        )
        try:
            await w.main()
        finally:
            await w.close()

    asyncio.run(_run())


def test_duplicate_enqueue_joins_the_queued_job(test_redis):
    first = queue.enqueue_unique_job_sync("test_job", "overall_analysis:s1:English")
    second = queue.enqueue_unique_job_sync("test_job", "overall_analysis:s1:English")
    assert first == {"jobId": "overall_analysis:s1:English", "jobStatus": "pending", "enqueued": True}
    assert second == {"jobId": "overall_analysis:s1:English", "jobStatus": "pending", "enqueued": False}
    assert test_redis.zcard("arq:queue") == 1, "a double-click must not queue two paid jobs"


def test_running_job_is_joined_not_duplicated(test_redis):
    queue.enqueue_unique_job_sync("test_job", "deep_analysis:s1:English")
    test_redis.set("arq:in-progress:deep_analysis:s1:English", b"1")
    again = queue.enqueue_unique_job_sync("test_job", "deep_analysis:s1:English")
    assert again["enqueued"] is False and again["jobStatus"] == "running"


def test_failed_job_can_be_retried_by_the_user(test_redis):
    job_id = "smart_recap:s1:professional"
    queue.enqueue_unique_job_sync("test_job", job_id)
    _run_worker_once(_flaky_job)
    status = queue.read_job_status_sync(job_id)
    assert status["jobStatus"] == "error" and "overloaded" in status["error"]

    # arq keeps the failed result for an hour, which would block the same id;
    # an explicit retry clears it and queues the job again.
    retry = queue.enqueue_unique_job_sync("test_job", job_id)
    assert retry["enqueued"] is True and retry["jobStatus"] == "pending"
    _run_worker_once(_good_job)
    assert queue.read_job_status_sync(job_id) == {"jobStatus": "completed"}


# ── What a failed job leaves in Redis ──────────────────────────────────────

FAKE_SECRETS = (b"sk-test-SECRET", b"fake-oauth-token", b"fake-upstream-body", b"fake-prompt")


def _failed_request():
    return requests.Request(
        "POST",
        "https://provider.example.test/v1/messages",
        headers={"x-api-key": "sk-test-SECRET", "Authorization": "Bearer fake-oauth-token"},
        json={"prompt": "fake-prompt"},
    ).prepare()


def _http_failure():
    """A real requests error, as `raise_for_status` raises it: it holds the
    prepared request and the response."""
    request = _failed_request()
    response = requests.Response()
    response.status_code = 503
    response.reason = "Service Unavailable"
    response.url = request.url
    response.request = request
    response._content = b'{"error": "fake-upstream-body"}'
    return requests.HTTPError(
        "503 Server Error: Service Unavailable for url: https://provider.example.test/v1/messages",
        request=request,
        response=response,
    )


def _connection_failure():
    return requests.ConnectionError("Connection reset by peer", request=_failed_request())


@pytest.mark.parametrize("make_error", [_http_failure, _connection_failure], ids=["http", "connection"])
def test_a_failed_job_is_stored_as_its_message_only(make_error):
    from arq.jobs import deserialize_result, serialize_result

    error = make_error()
    result_args = ("run_overall_analysis_job", ("s1", "English"), {}, 1, 0, False, error, 0, 0, "ref", "arq:queue", "job-1")
    assert any(secret in serialize_result(*result_args) for secret in FAKE_SECRETS), (
        "the fixture must hold what arq's default pickle would store"
    )
    stored = serialize_result(*result_args, serializer=queue.job_result_serializer)
    assert [secret for secret in FAKE_SECRETS if secret in stored] == []
    # The web process reads it with arq's default pickle.loads.
    read = deserialize_result(stored)
    assert type(read.result) is queue.JobFailed and str(read.result) == str(error)
    assert read.success is False and read.args == ("s1", "English")


def test_a_stored_error_keeps_the_text_the_poll_showed():
    assert str(queue.storable_job_error(TimeoutError())) == "TimeoutError", "no message: the class name, as before"
    assert str(queue.storable_job_error(llm_client.LLMRefused("bio"))) == llm_client.REFUSED_MESSAGE
    assert str(queue.storable_job_error(RuntimeError("model overloaded"))) == "model overloaded"


def test_job_arguments_and_successful_results_are_pickled_as_before():
    job = {"t": 1, "f": "run_overall_analysis_job", "a": ("s1", "English"), "k": {}, "et": 0}
    done = {**job, "s": True, "r": {"status": "ok"}, "st": 0, "ft": 1, "q": "arq:queue", "id": "job-1"}
    for data in (job, done):
        assert queue.job_result_serializer(data) == pickle.dumps(data)


def test_the_worker_stores_results_through_the_message_only_serializer():
    from arq.worker import get_kwargs

    assert get_kwargs(worker.WorkerSettings)["job_serializer"] is queue.job_result_serializer


async def _failing_http_job(ctx):
    raise _http_failure()


def test_a_failed_jobs_redis_result_holds_its_message_and_the_poll_shows_it(test_redis):
    from arq.constants import result_key_prefix

    job_id = "overall_analysis:s1:English"
    queue.enqueue_unique_job_sync("test_job", job_id)
    _run_worker_once(_failing_http_job, job_serializer=worker.WorkerSettings.job_serializer)
    stored = test_redis.get(result_key_prefix + job_id)
    assert stored and [secret for secret in FAKE_SECRETS if secret in stored] == []
    assert queue.read_job_status_sync(job_id) == {"jobStatus": "error", "error": str(_http_failure())}


# ── AI job start ───────────────────────────────────────────────────────────


@pytest.fixture
def ai_rows(monkeypatch):
    rows: dict = {"s1": {"analysis_bundle": {}, "smart_recap_bundle": {}}}
    enqueued: list = []
    monkeypatch.setattr(api_logic, "fetch_cached_session", lambda key, sid, *, organization_id: rows.get(sid))
    monkeypatch.setattr(api_logic, "fetch_session_fields", lambda sid, fields, *, organization_id: rows.get(sid))
    monkeypatch.setattr(api_logic, "_serialize_cached_session", lambda sid, cached: {"sessionId": sid})

    def fake_enqueue(function_name, job_id, *args):
        enqueued.append((function_name, job_id, *args))
        return {"jobId": job_id, "jobStatus": "running", "enqueued": False}

    monkeypatch.setattr(api_logic, "enqueue_unique_job_sync", fake_enqueue)
    return rows, enqueued


def test_ai_job_id_is_fixed_per_session_and_dimension(ai_rows):
    _rows, enqueued = ai_rows
    response = api_logic.start_smart_recap("s1", "hype", organization_id="org-a")
    assert enqueued == [("run_smart_recap_job", "smart_recap:s1:hype", "s1", "hype")]
    assert response == {"jobId": "smart_recap:s1:hype", "jobKind": "smart_recap", "jobStatus": "running", "tone": "hype"}


@pytest.mark.parametrize(
    "start,value",
    [
        (api_logic.start_overall_analysis, "Klingon"),
        (api_logic.start_deep_analysis, ""),
        (api_logic.start_content_repurposing, "english"),
        (api_logic.start_smart_recap, "angry"),
    ],
)
def test_unknown_language_or_tone_starts_no_paid_job(ai_rows, start, value):
    _rows, enqueued = ai_rows
    with pytest.raises(ValueError):
        start("s1", value, organization_id="org-a")
    assert enqueued == []


def test_job_poll_ignores_a_client_supplied_job_id(ai_rows, monkeypatch):
    # Job ids are predictable; a caller must not be able to read the status
    # of another session's job by naming it while polling their own session.
    looked_up: list = []
    monkeypatch.setattr(api_logic, "read_job_status_sync", lambda job_id: looked_up.append(job_id) or {"jobStatus": "running"})
    monkeypatch.setattr(api_logic, "read_progress", lambda *a, **k: None)
    response = api_logic.get_deep_analysis_job_data(
        "s1", "deep_analysis:other-orgs-session:English", "English", organization_id="org-a",
    )
    assert looked_up == ["deep_analysis:s1:English"]
    assert response["jobId"] == "deep_analysis:s1:English"


@pytest.mark.parametrize(
    "model,payload",
    [
        (routes_models.AnalysisRequest, {"outputLanguage": "Klingon"}),
        (routes_models.SmartRecapRequest, {"tone": "angry"}),
    ],
)
def test_request_models_reject_unknown_language_and_tone(model, payload):
    with pytest.raises(Exception, match="validation error"):
        model.model_validate(payload)


# ── Cover images ───────────────────────────────────────────────────────────


def test_reading_a_workspace_never_starts_a_paid_cover(monkeypatch):
    row = {"smart_recap_bundle": {"professional": "# Recap"}, "has_cover_image": False, "transcript_payload": {}}
    started: list = []
    monkeypatch.setattr(api_logic, "fetch_cached_session", lambda key, sid, *, organization_id: row)
    monkeypatch.setattr(api_logic, "_serialize_cached_session", lambda sid, cached: {"sessionId": sid})
    monkeypatch.setattr(api_logic, "enqueue_unique_job_sync", lambda *a, **k: started.append(a))
    monkeypatch.setattr(api_logic, "get_transcript_job_for_session", lambda sid: None)

    for _ in range(3):
        api_logic.get_cached_workspace("s1", organization_id="org-a")
        api_logic.fetch_session_transcript_data("key", "s1", job_connection_id="c", read_scope="org-a")
    assert started == []


class _FakeArqPool:
    def __init__(self) -> None:
        self.calls: list = []

    async def enqueue_job(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        return object()


def _strict_fetch(row):
    # Same keyword-only signature as db.fetch_cached_session: a call without
    # an explicit scope raises TypeError, as it does in production.
    def fetch(api_key, session_id, *, organization_id):
        assert organization_id is ALL_ORGS
        return row
    return fetch


def test_professional_recap_queues_one_cover_with_a_fixed_id(monkeypatch):
    monkeypatch.setattr(worker, "fetch_cached_session", _strict_fetch({"has_cover_image": False}))
    pool = _FakeArqPool()
    asyncio.run(worker._enqueue_default_cover_image({"redis": pool}, "s1"))
    assert pool.calls == [("run_cover_image_job", ("s1",), {"_job_id": "cover_image:s1"})]


def test_existing_cover_is_not_regenerated(monkeypatch):
    monkeypatch.setattr(worker, "fetch_cached_session", _strict_fetch({"has_cover_image": True}))
    pool = _FakeArqPool()
    asyncio.run(worker._enqueue_default_cover_image({"redis": pool}, "s1"))
    assert pool.calls == []


# ── Transcript jobs: one active job per session (Postgres) ─────────────────


def test_second_transcript_job_for_a_session_attaches_to_the_first(test_database):
    first = create_transcript_job("s1")
    second = create_transcript_job("s1")
    other = create_transcript_job("s2")
    assert first["created"] is True and other["created"] is True
    assert second == {"job_id": first["job_id"], "status": "pending", "created": False}

    update_transcript_job_status(first["job_id"], "error", error="boom")
    retry = create_transcript_job("s1")
    assert retry["created"] is True and retry["job_id"] != first["job_id"]


def _stored_connection(job_id):
    with get_db_connection() as conn:
        return conn.execute("SELECT connection_id FROM transcript_jobs WHERE job_id = %s", (job_id,)).fetchone()["connection_id"]


def test_claim_hands_over_the_connection_once_and_clears_it(test_database):
    job = create_transcript_job("s1", connection_id="conn-a")
    assert _stored_connection(job["job_id"]) == "conn-a"
    assert claim_transcript_job(job["job_id"]) == {"connection_id": "conn-a"}
    assert _stored_connection(job["job_id"]) is None, "the credential reference must not outlive the claim"
    assert claim_transcript_job(job["job_id"]) is None, "a job can only be claimed once"


def test_closed_job_cannot_be_claimed_and_forgets_its_connection(test_database):
    job = create_transcript_job("s1", connection_id="conn-a")
    update_transcript_job_status(job["job_id"], "error", error="lost by the queue")
    assert _stored_connection(job["job_id"]) is None
    assert claim_transcript_job(job["job_id"]) is None


def test_concurrent_fetches_create_exactly_one_transcript_job(test_database):
    results: list = []
    barrier = threading.Barrier(8)

    def create():
        barrier.wait()
        results.append(create_transcript_job("s1"))

    threads = [threading.Thread(target=create) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(results) == 8
    assert sum(r["created"] for r in results) == 1
    assert len({r["job_id"] for r in results}) == 1


def test_schema_upgrade_closes_duplicate_active_jobs(test_database):
    now = datetime.now(timezone.utc)
    with get_db_connection() as conn:
        conn.execute("DROP INDEX idx_transcript_jobs_one_active_per_session")
        for job_id, session_id, age in (("old", "s1", 60), ("new", "s1", 1), ("solo", "s2", 60)):
            conn.execute(
                "INSERT INTO transcript_jobs (job_id, session_id, status, created_at, updated_at) VALUES (%s, %s, 'pending', %s, %s)",
                (job_id, session_id, now - timedelta(minutes=age), now),
            )
        conn.commit()
    ensure_database_schema()
    with get_db_connection() as conn:
        statuses = {row["job_id"]: row["status"] for row in conn.execute("SELECT job_id, status FROM transcript_jobs")}
    assert statuses == {"old": "error", "new": "pending", "solo": "pending"}
    assert create_transcript_job("s1")["created"] is False


# ── Transcript jobs: dead queue jobs don't block the session ───────────────


@pytest.fixture
def reconcile(monkeypatch):
    marked: list = []
    arq = {"status": {"jobStatus": "running"}}
    monkeypatch.setattr(api_logic, "read_job_status_sync", lambda job_id: arq["status"])
    monkeypatch.setattr(
        api_logic, "update_transcript_job_status", lambda job_id, status, error=None: marked.append((job_id, status)),
    )
    return arq, marked


def _job(status="running", minutes_ago=0):
    return {"job_id": "j1", "status": status, "updated_at": datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)}


@pytest.mark.parametrize(
    "arq_status,minutes_ago,closed",
    [
        ({"jobStatus": "error", "error": "max 1 retries exceeded"}, 0, True),
        ({"jobStatus": "not_found"}, 5, True),
        ({"jobStatus": "not_found"}, 0, False),  # just inserted, enqueue in progress
        ({"jobStatus": "unknown"}, 60, False),  # Redis down: leave it to the sweeper
        ({"jobStatus": "running"}, 60, False),
        ({"jobStatus": "pending"}, 60, False),
    ],
)
def test_reconcile_closes_only_dead_jobs(reconcile, arq_status, minutes_ago, closed):
    arq, marked = reconcile
    arq["status"] = arq_status
    result = api_logic._reconcile_transcript_job(_job(minutes_ago=minutes_ago))
    assert (result["status"] == "error") is closed
    assert (marked == [("j1", "error")]) is closed


def test_finished_job_is_not_checked_against_the_queue(reconcile):
    arq, marked = reconcile
    arq["status"] = {"jobStatus": "not_found"}
    assert api_logic._reconcile_transcript_job(_job(status="completed", minutes_ago=600))["status"] == "completed"
    assert marked == []


@pytest.fixture
def transcript_flow(monkeypatch, reconcile):
    arq, _marked = reconcile
    enqueued: list = []
    created: list = []
    monkeypatch.setattr(api_logic, "fetch_cached_session", lambda key, sid, *, organization_id: {"session_payload": {}})
    monkeypatch.setattr(api_logic, "upsert_cached_session", lambda *a, **k: None)
    monkeypatch.setattr(api_logic, "get_transcript_job_for_session", lambda sid: _job(minutes_ago=10))

    def fake_create(sid, connection_id=""):
        created.append((sid, connection_id))
        return {"job_id": "j2", "status": "pending", "created": True}

    monkeypatch.setattr(api_logic, "create_transcript_job", fake_create)
    monkeypatch.setattr(
        api_logic, "enqueue_unique_job_sync",
        lambda *args: enqueued.append(args) or {"jobId": args[1], "jobStatus": "pending", "enqueued": True},
    )
    return arq, enqueued, created


def test_force_refresh_attaches_to_the_active_transcription(transcript_flow):
    _arq, enqueued, created = transcript_flow
    response = api_logic.fetch_session_transcript_data(
        "key", "s1", force_refresh=True, job_connection_id="c", read_scope="org-a",
    )
    assert response == {"jobId": "j1", "jobStatus": "running"}
    assert enqueued == [] and created == []


def test_lost_transcription_is_replaced_by_a_new_job(transcript_flow):
    arq, enqueued, created = transcript_flow
    arq["status"] = {"jobStatus": "not_found"}
    response = api_logic.fetch_session_transcript_data("key", "s1", job_connection_id="conn-1", read_scope="org-a")
    assert response == {"jobId": "j2", "jobStatus": "pending"}
    assert created == [("s1", "conn-1")], "the connection is stored on the job row"
    assert enqueued == [("run_transcription", "transcription:j2", "j2", "s1")], "and never in the queue"


def test_web_process_cannot_transcribe_inline():
    # Transcription (Gladia, ffmpeg) only runs in the worker; the /fetch
    # route used to run it inside the web request.
    assert not hasattr(api_logic, "fetch_session_transcript")


def test_full_fetch_returns_the_base_workspace_and_the_transcript_job(monkeypatch):
    roster_sync = {"status": "pending", "started": True, "skipped": None}
    base_calls: list = []

    def base(*args, **kwargs):
        base_calls.append(kwargs)
        return {"sessionId": "s1", "base": True, "rosterSync": roster_sync}

    monkeypatch.setattr(api_logic, "fetch_session_base_data", base)
    monkeypatch.setattr(api_logic, "fetch_session_transcript_data", lambda *a, **k: {"jobId": "j1", "jobStatus": "pending"})
    response = api_logic.fetch_all_session_data("key", "s1", job_connection_id="c", read_scope="org-a", local_dev=True)
    assert response == {
        "sessionId": "s1", "base": True, "rosterSync": roster_sync, "transcriptJob": {"jobId": "j1", "jobStatus": "pending"},
    }
    # The base fetch gets what the people sync needs: the caller's connection and the local-dev flag.
    assert (base_calls[0]["job_connection_id"], base_calls[0]["local_dev"]) == ("c", True)

    # With the transcript already cached, the full workspace still says what the people sync did.
    monkeypatch.setattr(api_logic, "fetch_session_transcript_data", lambda *a, **k: {"sessionId": "s1", "full": True})
    cached = api_logic.fetch_all_session_data("key", "s1", job_connection_id="c", read_scope="org-a")
    assert cached == {"sessionId": "s1", "full": True, "rosterSync": roster_sync}


# ── Worker ─────────────────────────────────────────────────────────────────


def test_every_job_kind_has_its_own_timeout_and_no_retries():
    timeouts = {
        getattr(f, "name", getattr(f, "__name__", "")): getattr(f, "timeout_s", None)
        for f in worker.WorkerSettings.functions
    }
    assert timeouts["run_transcription"] == 120 * 60
    assert timeouts["run_cover_image_job"] == 5 * 60
    for name in ("run_overall_analysis_job", "run_faq_job", "run_decisions_job", "run_team_digest_job", "run_attendee_followup_job", "run_deep_analysis_job", "run_smart_recap_job", "run_content_repurposing_job"):
        assert timeouts[name] == 15 * 60
    # The chat labels' batches, then the understanding call and its 10-minute stream deadline.
    assert timeouts["run_session_analysis_job"] == 25 * 60
    assert timeouts["run_people_sync"] == 15 * 60
    # The org lock outlives the job, so it is never freed under a running sync.
    assert worker.PEOPLE_SYNC_LOCK_TTL_SECONDS > worker.PEOPLE_SYNC_JOB_TIMEOUT_SECONDS
    assert worker.WorkerSettings.max_tries == 1


@pytest.fixture
def transcription(monkeypatch):
    calls: dict = {"statuses": [], "progress": [], "auth": [], "transcribe": []}
    monkeypatch.setenv("GLADIA_KEY", "gladia-key")
    monkeypatch.setattr(worker, "claim_transcript_job", lambda job_id: {"connection_id": f"conn-for-{job_id}"})
    monkeypatch.setattr(worker, "update_transcript_job_status", lambda job_id, status, error=None: calls["statuses"].append((status, error)))
    monkeypatch.setattr(worker, "update_transcript_job_progress", lambda job_id, progress: calls["progress"].append(progress))
    monkeypatch.setattr(worker, "publish_progress_sync", lambda *a, **k: None)
    monkeypatch.setattr(worker, "clear_progress_sync", lambda *a, **k: None)
    calls["upserts"] = []
    monkeypatch.setattr(worker, "upsert_cached_session", lambda *a, **k: calls["upserts"].append(k))
    calls["field_reads"] = []
    monkeypatch.setattr(
        worker, "fetch_session_fields", lambda *a, **k: calls["field_reads"].append((a, k)) and None
    )
    monkeypatch.setattr(
        "livestorm_app.auth.livestorm_auth_for_job",
        lambda connection_id: calls["auth"].append(connection_id) or "Bearer fresh-token",
    )
    return calls


def test_worker_resolves_livestorm_credentials_itself(transcription, monkeypatch):
    def fake_transcribe(gladia_key, sid, livestorm_api_key, gladia_options, on_progress):
        transcription["transcribe"].append((gladia_key, livestorm_api_key))
        return {}

    monkeypatch.setattr("livestorm_app.transcript_client.fetch_session_transcript", fake_transcribe)
    asyncio.run(worker.run_transcription({}, "job-1", "s1"))
    assert transcription["auth"] == ["conn-for-job-1"], "the connection comes from the claimed row"
    assert transcription["transcribe"] == [("gladia-key", "Bearer fresh-token")]
    assert transcription["statuses"][-1] == ("completed", None)


def test_job_that_is_no_longer_pending_does_no_paid_work(transcription, monkeypatch):
    monkeypatch.setattr(worker, "claim_transcript_job", lambda job_id: None)
    monkeypatch.setattr(
        "livestorm_app.transcript_client.fetch_session_transcript",
        lambda *a, **k: transcription["transcribe"].append("called") or {},
    )
    assert asyncio.run(worker.run_transcription({}, "job-1", "s1"))["status"] == "skipped"
    assert transcription["transcribe"] == [] and transcription["statuses"] == []


def test_missing_gladia_key_fails_the_job_clearly(transcription, monkeypatch):
    monkeypatch.setenv("GLADIA_KEY", "")
    with pytest.raises(RuntimeError, match="GLADIA_KEY"):
        asyncio.run(worker.run_transcription({}, "job-1", "s1"))
    assert transcription["statuses"][-1][0] == "error"


def test_timed_out_transcription_is_marked_error_and_its_thread_stops(transcription, monkeypatch):
    thread_outcome: list = []

    def slow_transcribe(gladia_key, sid, livestorm_api_key, gladia_options, on_progress):
        time.sleep(0.3)  # still running when the job times out
        try:
            on_progress({"step": "transcribing", "message": "still going"})
            thread_outcome.append("kept going")
        except RuntimeError:
            thread_outcome.append("stopped")
        return {}

    monkeypatch.setattr("livestorm_app.transcript_client.fetch_session_transcript", slow_transcribe)

    async def run_with_short_timeout():
        await asyncio.wait_for(worker.run_transcription({}, "job-1", "s1"), timeout=0.05)

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(run_with_short_timeout())  # returns after the thread finishes
    status, error = transcription["statuses"][-1]
    assert status == "error" and "interrupted" in error
    assert thread_outcome == ["stopped"], "the orphaned thread must stop at its next progress report"


def test_transcriptions_wait_for_a_free_slot(transcription, monkeypatch):
    active = {"now": 0, "max": 0}

    def fake_transcribe(*args, **kwargs):
        active["now"] += 1
        active["max"] = max(active["max"], active["now"])
        time.sleep(0.15)
        active["now"] -= 1
        return {}

    monkeypatch.setattr("livestorm_app.transcript_client.fetch_session_transcript", fake_transcribe)
    monkeypatch.setattr(worker, "TRANSCRIPTION_SLOT_HEARTBEAT_SECONDS", 0.02)

    async def run_two():
        ctx = {"transcription_slots": asyncio.Semaphore(1)}
        await asyncio.gather(
            worker.run_transcription(ctx, "job-1", "s1"),
            worker.run_transcription(ctx, "job-2", "s2"),
        )

    asyncio.run(run_two())
    assert active["max"] == 1
    assert any(p.get("step") == "waiting" for p in transcription["progress"]), "a waiting job must keep its heartbeat"


def _capture_transcribe(monkeypatch, sent: list, payload=None):
    def fake_transcribe(gladia_key, sid, *, livestorm_api_key, gladia_options=None, on_progress=None):
        sent.append({"sid": sid, "gladia_options": gladia_options})
        return copy.deepcopy(payload) if payload is not None else {"stormiq": {"people_on_stage": None, "max_speakers": 20}}

    monkeypatch.setattr("livestorm_app.transcript_client.fetch_session_transcript", fake_transcribe)


def test_the_speaker_cap_comes_from_the_cached_people_and_the_job_args_stay_ids(transcription, monkeypatch):
    from synthetic_session import session_payload as synthetic_session_payload

    reads: list = []
    monkeypatch.setattr(
        worker,
        "fetch_session_fields",
        lambda session_id, fields, *, organization_id: reads.append((session_id, fields, organization_id))
        or {"session_payload": synthetic_session_payload()},
    )
    sent: list = []
    _capture_transcribe(monkeypatch, sent)

    assert asyncio.run(worker.run_transcription({}, "job-1", "s1"))["status"] == "completed"

    assert reads == [("s1", ["session_payload"], ALL_ORGS)], "one light read of the cached people"
    # The synthetic webinar has an organizer and a guest speaker: 2 + 2.
    assert sent == [{"sid": "s1", "gladia_options": {"diarization_config": {"min_speakers": 1, "max_speakers": 4}}}]
    assert transcription["upserts"][0]["transcript_payload"]["stormiq"]["people_on_stage"] == 2
    assert list(inspect.signature(worker.run_transcription).parameters) == ["ctx", "job_id", "session_id"]
    sent_text = json.dumps(sent)
    assert "conn-for-job-1" not in sent_text and "Bearer" not in sent_text and "gladia-key" not in sent_text


def test_the_speaker_cap_reads_the_stored_roster_when_there_is_one(transcription, monkeypatch):
    """Once a people sync saved a roster, the cap counts its people on stage,
    from the same source as the v1 record."""
    people = synthetic.person_rows("s1")
    reads: list = []
    monkeypatch.setattr(worker, "fetch_session_fields", lambda *a, **k: {"session_payload": {"included": []}})
    monkeypatch.setattr(
        worker, "fetch_session_roster",
        lambda session_id, organization_id: reads.append((session_id, organization_id))
        or {"roster": {"roster_version": 1, "source": "paginated", "complete": True, "record_count": len(people)}, "people": people},
    )
    sent: list = []
    _capture_transcribe(monkeypatch, sent)

    assert asyncio.run(worker.run_transcription({}, "job-1", "s1"))["status"] == "completed"

    assert reads == [("s1", ALL_ORGS)]
    # The payload lists nobody; the stored roster has an organizer and a guest speaker: 2 + 2.
    assert sent[0]["gladia_options"] == {"diarization_config": {"min_speakers": 1, "max_speakers": 4}}
    assert transcription["upserts"][0]["transcript_payload"]["stormiq"]["people_on_stage"] == 2


def _raise(*args, **kwargs):
    raise RuntimeError("boom")


@pytest.mark.parametrize("failure", ["no cached row", "read fails", "stored roster fails", "roster fails", "nobody on stage"])
def test_an_unknown_stage_keeps_the_default_speaker_cap(transcription, monkeypatch, failure):
    if failure == "read fails":
        monkeypatch.setattr(worker, "fetch_session_fields", _raise)
    elif failure == "stored roster fails":
        from synthetic_session import session_payload as synthetic_session_payload

        # The payload's people would give a cap of 4; a roster that can't be read gives no count at all.
        monkeypatch.setattr(worker, "fetch_session_fields", lambda *a, **k: {"session_payload": synthetic_session_payload()})
        monkeypatch.setattr(worker, "fetch_session_roster", _raise)
    elif failure == "roster fails":
        monkeypatch.setattr(worker, "fetch_session_fields", lambda *a, **k: {"session_payload": {"included": []}})
        monkeypatch.setattr("livestorm_app.record.inputs.count_people_on_stage", _raise)
    elif failure == "nobody on stage":
        everyone_team = [
            {"id": f"tm-{n}", "type": "people", "attributes": {"role": "team_member", "registrant_detail": {"attended": True}}}
            for n in range(4)
        ]
        monkeypatch.setattr(worker, "fetch_session_fields", lambda *a, **k: {"session_payload": {"included": everyone_team}})
    sent: list = []
    _capture_transcribe(monkeypatch, sent)

    assert asyncio.run(worker.run_transcription({}, "job-1", "s1"))["status"] == "completed"
    assert sent[0]["gladia_options"] == {"diarization_config": {"min_speakers": 1, "max_speakers": 20}}
    assert transcription["upserts"][0]["transcript_payload"]["stormiq"]["people_on_stage"] is None


def test_ai_job_releases_the_worker_slot_immediately(monkeypatch):
    cleared: list = []
    monkeypatch.setattr(worker, "publish_progress_sync", lambda *a, **k: None)
    monkeypatch.setattr(worker, "clear_progress_sync", lambda kind, sid: cleared.append(sid))

    def failing_runner():
        raise RuntimeError("model error")

    started = time.monotonic()
    asyncio.run(worker._run_ai_job("overall_analysis", "s1", lambda: {"ok": True}))
    assert time.monotonic() - started < 1.0
    with pytest.raises(RuntimeError):
        asyncio.run(worker._run_ai_job("overall_analysis", "s2", failing_runner))
    assert cleared == ["s1", "s2"]


# ── Livestorm credentials for jobs ─────────────────────────────────────────


def test_job_credentials_come_from_the_stored_connection(monkeypatch):
    monkeypatch.setattr(
        auth, "refresh_connection_if_needed",
        lambda cid: {"access_token": "refreshed", "token_type": "Bearer"} if cid == "conn-a" else None,
    )
    assert auth.livestorm_auth_for_job("conn-a") == "Bearer refreshed"
    with pytest.raises(RuntimeError, match="reconnect"):
        auth.livestorm_auth_for_job("conn-deleted")
    with pytest.raises(RuntimeError, match="no Livestorm credentials"):
        auth.livestorm_auth_for_job("")


def test_local_dev_credentials_are_refused_outside_local_dev(monkeypatch):
    monkeypatch.setenv("LS_API_KEY", "ls-key")
    monkeypatch.setattr(auth, "refresh_connection_if_needed", lambda cid: {"access_token": "t"})
    with pytest.raises(RuntimeError):
        auth.livestorm_auth_for_job("")
    with pytest.raises(RuntimeError):
        auth.livestorm_auth_for_job(auth.DEV_CONNECTION_ID)
    monkeypatch.setenv("STORMIQ_LOCAL_DEV", "1")
    assert auth.livestorm_auth_for_job("") == "ls-key"


def test_caller_hands_jobs_a_connection_id_not_a_token(monkeypatch):
    connection = {"connection_id": "conn-a", "access_token": "secret-token"}
    assert auth.Caller(kind="user", org_id="org-a", connection=connection).job_connection_id() == "conn-a"
    monkeypatch.setenv("LS_API_KEY", "ls-key")
    assert auth.Caller(kind="local_dev", org_id="", local_dev=True).job_connection_id() == ""


# ── Livestorm GET retries (real HTTP against a local server) ───────────────


@pytest.fixture
def livestorm_stub():
    script: list = []
    seen: list = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (http.server API)
            seen.append(self.headers.get("Cookie"))
            status = script.pop(0) if script else 200
            self.send_response(status)
            self.send_header("Set-Cookie", "tracker=user-a; Path=/")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1/sessions/s1", script, seen
    finally:
        server.shutdown()
        server.server_close()


def test_livestorm_get_retries_a_transient_server_error(livestorm_stub):
    url, script, seen = livestorm_stub
    script.extend([503])
    assert livestorm_client.livestorm_get(url, headers={}).status_code == 200
    assert len(seen) == 2


def test_livestorm_get_does_not_retry_a_client_error(livestorm_stub):
    url, script, seen = livestorm_stub
    script.extend([404])
    assert livestorm_client.livestorm_get(url, headers={}).status_code == 404
    assert len(seen) == 1


def test_livestorm_session_never_shares_cookies_between_users(livestorm_stub):
    url, _script, seen = livestorm_stub
    livestorm_client.livestorm_get(url, headers={})
    livestorm_client.livestorm_get(url, headers={})
    assert seen == [None, None]


def test_livestorm_retry_after_is_capped():
    retry = livestorm_client._livestorm_http.get_adapter("https://api.livestorm.co").max_retries
    response = type("FakeResponse", (), {"headers": HTTPHeaderDict({"Retry-After": "3600"})})()
    assert retry.get_retry_after(response) == livestorm_client.LIVESTORM_MAX_RETRY_AFTER_SECONDS


# ── Gladia ─────────────────────────────────────────────────────────────────


def test_every_gladia_call_has_a_timeout(monkeypatch):
    seen: list = []

    def fake_urlopen(request, timeout=None):
        seen.append(timeout)
        raise urllib.error.URLError("unreachable")

    monkeypatch.setattr(transcriber.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(transcriber.TransientHTTPError):
        transcriber._json_request(method="GET", url="https://api.gladia.io/v2/pre-recorded/x", api_key="k")
    assert seen == [transcriber.HTTP_TIMEOUT_SECONDS]


def test_gladia_client_errors_are_not_treated_as_transient(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(transcriber.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError) as excinfo:
        transcriber._json_request(method="GET", url="https://api.gladia.io/v2/pre-recorded/x", api_key="k")
    assert not isinstance(excinfo.value, transcriber.TransientHTTPError)


def test_gladia_polling_survives_brief_network_errors(monkeypatch):
    script = [transcriber.TransientHTTPError("blip"), transcriber.TransientHTTPError("blip"), {"status": "done", "result": {}}]

    def fake_json_request(**kwargs):
        step = script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    monkeypatch.setattr(transcriber, "_json_request", fake_json_request)
    monkeypatch.setattr(transcriber.time, "sleep", lambda seconds: None)
    assert transcriber._poll_gladia_transcription("job", "k")["status"] == "done"


def test_gladia_request_is_built_once_and_sent_as_recorded(monkeypatch, tmp_path):
    audio = tmp_path / "chunk.wav"
    audio.write_bytes(b"RIFF")
    built: list = []
    posted: list = []
    real_build = transcriber._build_gladia_request

    def counting_build(audio_url, gladia_options=None, *, metadata=None):
        built.append(audio_url)
        return real_build(audio_url, gladia_options, metadata=metadata)

    def fake_json_request(*, method, url, api_key, payload=None):
        posted.append((method, payload))
        return {"id": "job-1"}

    monkeypatch.setattr(transcriber, "_upload_audio_file", lambda path, key: {"audio_url": "https://gladia.example/a"})
    monkeypatch.setattr(transcriber, "_build_gladia_request", counting_build)
    monkeypatch.setattr(transcriber, "_json_request", fake_json_request)
    monkeypatch.setattr(transcriber, "_poll_gladia_transcription", lambda job_id, api_key, **kwargs: {"status": "done"})

    chunk = transcriber._transcribe_audio_file(
        audio,
        api_key="k",
        gladia_options={"diarization_config": {"max_speakers": 5}},
        metadata={"stormiq_session_id": "s1", "chunk": 1, "chunks": 1, "offset_s": 0.0},
    )

    assert built == ["https://gladia.example/a"]
    assert posted == [("POST", chunk["gladia_request"])]
    assert chunk["gladia_request"]["diarization_config"]["max_speakers"] == 5
    assert chunk["gladia_request"]["custom_metadata"]["stormiq_session_id"] == "s1"


# The real probe, kept before `gladia_transport` replaces it.
REAL_PROBE_MEDIA_INFO = transcriber._probe_media_info

# A synthetic recording: its URL is signed, so it must never leave the worker.
SIGNED_RECORDING_URL = "https://cdn.example.test/recordings/r.mp4?signature=signed-url-token"
GLADIA_SECRET = "gladia-secret-key"
LIVESTORM_SECRET = "Bearer livestorm-secret-token"


def _done_chunk(job_id: str) -> dict:
    return {
        "id": job_id,
        "status": "done",
        "file": {"audio_duration": 60.0},
        "result": {
            "metadata": {"audio_duration": 60.0, "billing_time": 60.0},
            "transcription": {
                "languages": ["en"],
                "full_transcript": "hello there",
                "utterances": [{"start": 1.0, "end": 2.0, "speaker": 0, "text": "hello there", "words": []}],
            },
        },
    }


@pytest.fixture
def gladia_transport(monkeypatch, tmp_path):
    """The whole transcriber with a patched transport: a two-chunk recording,
    and every Gladia POST recorded. Nothing leaves the process."""
    posted: list = []
    recording = {
        "id": "rec-1",
        "type": "recordings",
        "attributes": {
            "file_type": "video",
            "mime_type": "mp4",
            "file_size": 123456789,
            "file_name": "r.mp4",
            "url": SIGNED_RECORDING_URL,
            "event_id": "e1",
            "session_id": "s1",
        },
    }

    def fake_json_request(*, method, url, api_key, payload=None):
        assert method == "POST" and api_key == GLADIA_SECRET
        posted.append(copy.deepcopy(payload))
        return {"id": f"gladia-job-{len(posted)}"}

    monkeypatch.setattr(transcriber, "_fetch_livestorm_recordings", lambda session_id, livestorm_api_key=None: {"data": [recording]})
    monkeypatch.setattr(transcriber, "_download_recording", lambda rec, destination: destination.write_bytes(b"mp4"))
    monkeypatch.setattr(
        transcriber, "_probe_media_info", lambda path: {"duration_s": 8160.0, "creation_time": "2026-10-01T09:00:00.000000Z"}
    )
    monkeypatch.setattr(transcriber, "_extract_audio", lambda video, audio: audio.write_bytes(b"RIFF"))
    monkeypatch.setattr(transcriber, "_split_audio_file", lambda audio, chunk_dir: [(audio, 0.0), (audio, 8100.0)])
    monkeypatch.setattr(transcriber, "_upload_audio_file", lambda path, key: {"audio_url": f"https://gladia.example/{len(posted) + 1}"})
    monkeypatch.setattr(transcriber, "_json_request", fake_json_request)
    monkeypatch.setattr(transcriber, "_poll_gladia_transcription", lambda job_id, api_key, **kwargs: _done_chunk(job_id))
    monkeypatch.setenv("RENDER", "true")

    def run(gladia_options=None):
        return transcriber.transcribe_livestorm_session_data(
            "s1",
            output_path=tmp_path / "s1.transcript.json",
            gladia_options=gladia_options,
            gladia_api_key=GLADIA_SECRET,
            livestorm_api_key=LIVESTORM_SECRET,
        )

    return run, posted


def test_each_gladia_job_carries_ids_only_and_the_transcript_records_what_was_sent(gladia_transport):
    run, posted = gladia_transport
    transcript = run(transcriber.speaker_options(2))

    assert len(posted) == 2, "one POST per chunk, never retried"
    config = transcriber.gladia_config_fingerprint(posted[0])
    assert len(config) == 12 and int(config, 16) >= 0
    for chunk, (request, offset) in enumerate(zip(posted, (0.0, 8100.0), strict=True), start=1):
        assert request["custom_metadata"] == {
            "stormiq_session_id": "s1",
            "recording_id": "rec-1",
            "file_size": 123456789,
            "env": "render",
            "chunk": chunk,
            "chunks": 2,
            "offset_s": offset,
            "config": config,
        }
        assert request["diarization_config"]["max_speakers"] == 4
        metadata_text = json.dumps(request["custom_metadata"])
        assert len(metadata_text) <= transcriber.GLADIA_METADATA_MAX_CHARS
        for secret in (GLADIA_SECRET, "livestorm-secret-token", "signed-url-token", "Bearer", "https://"):
            assert secret not in metadata_text

    assert transcript["stormiq"] == {
        "pipeline_version": 2,
        "config": config,
        "max_speakers": 4,
        "people_on_stage": None,  # the worker sets it
        "gladia_jobs": [
            {"chunk": 1, "id": "gladia-job-1", "custom_metadata": posted[0]["custom_metadata"]},
            {"chunk": 2, "id": "gladia-job-2", "custom_metadata": posted[1]["custom_metadata"]},
        ],
        "media": {"duration_s": 8160.0, "creation_time": "2026-10-01T09:00:00.000000Z"},
    }
    assert transcript["request_payload"] == posted[0], "the stored request is the one sent"
    stored_text = json.dumps(transcript["stormiq"])
    for secret in (GLADIA_SECRET, "livestorm-secret-token", "signed-url-token"):
        assert secret not in stored_text


def test_without_options_the_cap_and_the_metadata_still_go_out(gladia_transport, monkeypatch):
    run, posted = gladia_transport
    monkeypatch.setenv("RENDER", "")
    transcript = run()
    assert [request["custom_metadata"]["env"] for request in posted] == ["local", "local"]
    assert transcript["stormiq"]["max_speakers"] == 20


def test_the_speaker_cap_reaches_every_gladia_request_through_the_whole_chain(transcription, gladia_transport, monkeypatch):
    """worker.run_transcription -> transcript_client.fetch_session_transcript ->
    the transcriber, with only the transport patched: every link must pass
    the options on, or the cap silently goes back to 20."""
    from synthetic_session import session_payload as synthetic_session_payload

    _run, posted = gladia_transport
    monkeypatch.setenv("GLADIA_KEY", GLADIA_SECRET)
    monkeypatch.setattr(worker, "fetch_session_fields", lambda *a, **k: {"session_payload": synthetic_session_payload()})

    assert asyncio.run(worker.run_transcription({}, "job-1", "s1"))["status"] == "completed"

    assert len(posted) == 2
    assert [request["diarization_config"]["max_speakers"] for request in posted] == [4, 4]
    stored = transcription["upserts"][0]["transcript_payload"]
    assert (stored["stormiq"]["people_on_stage"], stored["stormiq"]["max_speakers"]) == (2, 4)
    assert "output_path" not in stored


def test_the_transcript_client_passes_the_options_on(gladia_transport):
    from livestorm_app import transcript_client

    _run, posted = gladia_transport
    transcript = transcript_client.fetch_session_transcript(
        GLADIA_SECRET, "s1", livestorm_api_key=LIVESTORM_SECRET, gladia_options=transcriber.speaker_options(2)
    )
    assert [request["diarization_config"]["max_speakers"] for request in posted] == [4, 4]
    assert transcript["stormiq"]["max_speakers"] == 4


def test_the_config_fingerprint_follows_the_settings_not_the_file():
    def request(url, options=None, chunk=1):
        return transcriber._build_gladia_request(
            url, options, metadata={"stormiq_session_id": "s1", "chunk": chunk, "chunks": 2, "offset_s": 0.0}
        )

    first, second = request("https://gladia.example/1"), request("https://gladia.example/2", chunk=2)
    assert first["custom_metadata"]["config"] == second["custom_metadata"]["config"]
    assert first["custom_metadata"]["config"] == transcriber.gladia_config_fingerprint(second)
    capped = request("https://gladia.example/1", transcriber.speaker_options(3))
    assert capped["custom_metadata"]["config"] != first["custom_metadata"]["config"]
    reordered = dict(reversed(list(first.items())))
    assert transcriber.gladia_config_fingerprint(reordered) == first["custom_metadata"]["config"]


@pytest.mark.parametrize(
    "metadata",
    [
        {"api_key": "k"},
        {"connection_id": "c"},
        {"recording_url": SIGNED_RECORDING_URL},
        {"stormiq_session_id": {"nested": "x"}},
        {"chunk": True},
        {"stormiq_session_id": "x" * 2001},
        {"config": "set by the builder"},
    ],
)
def test_gladia_metadata_refuses_anything_but_ids_before_sending(metadata):
    with pytest.raises(ValueError):
        transcriber._build_gladia_request("https://gladia.example/a", metadata=metadata)


def test_gladia_metadata_drops_unknown_values_and_any_from_the_options():
    request = transcriber._build_gladia_request(
        "https://gladia.example/a",
        {"custom_metadata": {"anything": "else"}},
        metadata={"stormiq_session_id": "s1", "recording_id": None, "file_size": None},
    )
    assert request["custom_metadata"] == {"stormiq_session_id": "s1", "config": transcriber.gladia_config_fingerprint(request)}
    assert "custom_metadata" not in transcriber._build_gladia_request("https://gladia.example/a", {"custom_metadata": {"a": 1}})


@pytest.mark.parametrize(("raw", "size"), [(42, 42), (42.0, 42), ("42", 42), (" 42 ", 42), (None, None), (-1, None), (True, None), ("big", None)])
def test_the_recording_file_size_is_a_whole_number_of_bytes(raw, size):
    assert transcriber._file_size(raw) == size


FFMPEG_HEADER = """Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'r.mp4':
  Metadata:
    major_brand     : isom
    creation_time   : 2026-10-01T09:00:00.000000Z
  Duration: 01:30:00.50, start: 0.000000, bitrate: 900 kb/s
  Stream #0:0[0x1](und): Audio: aac
    Metadata:
      creation_time   : 2026-10-01T09:05:00.000000Z
At least one output file must be specified
"""


def test_the_media_probe_reads_the_container_with_a_timeout(monkeypatch, tmp_path):
    calls: list = []

    def fake_run(command, **kwargs):
        calls.append((command[0], kwargs.get("timeout")))
        return subprocess.CompletedProcess(command, 1, stdout="", stderr=FFMPEG_HEADER)

    monkeypatch.setattr(transcriber, "_ffprobe_executable", lambda: None)
    monkeypatch.setattr(transcriber, "_ffmpeg_executable", lambda: "ffmpeg")
    monkeypatch.setattr(transcriber.subprocess, "run", fake_run)
    info = transcriber._probe_media_info(tmp_path / "r.mp4")
    assert info == {"duration_s": 5400.5, "creation_time": "2026-10-01T09:00:00.000000Z"}, "the container's tag, not a stream's"
    assert calls == [("ffmpeg", transcriber.MEDIA_PROBE_TIMEOUT_SECONDS)]


def test_the_media_probe_never_takes_a_streams_creation_time(monkeypatch, tmp_path):
    stream_tag_only = """Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'r.mp4':
  Metadata:
    major_brand     : isom
  Duration: 00:01:00.00, start: 0.000000, bitrate: 900 kb/s
  Stream #0:0[0x1](und): Audio: aac
    Metadata:
      creation_time   : 2026-10-01T09:05:00.000000Z
At least one output file must be specified
"""
    monkeypatch.setattr(transcriber, "_ffprobe_executable", lambda: None)
    monkeypatch.setattr(transcriber, "_ffmpeg_executable", lambda: "ffmpeg")
    monkeypatch.setattr(
        transcriber.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1, stdout="", stderr=stream_tag_only),
    )
    assert transcriber._probe_media_info(tmp_path / "r.mp4") == {"duration_s": 60.0, "creation_time": None}


def test_the_media_probe_prefers_ffprobe(monkeypatch, tmp_path):
    calls: list = []

    def fake_run(command, **kwargs):
        calls.append((command[0], kwargs.get("timeout")))
        stdout = json.dumps({"format": {"duration": "61.250000", "tags": {"creation_time": "2026-10-01T09:00:00Z"}}})
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(transcriber, "_ffprobe_executable", lambda: "ffprobe")
    monkeypatch.setattr(transcriber.subprocess, "run", fake_run)
    assert transcriber._probe_media_info(tmp_path / "r.mp4") == {"duration_s": 61.25, "creation_time": "2026-10-01T09:00:00Z"}
    assert calls == [("ffprobe", transcriber.MEDIA_PROBE_TIMEOUT_SECONDS)]


@pytest.mark.parametrize(
    "outcome",
    [
        subprocess.TimeoutExpired("ffmpeg", 60),
        OSError("no such file"),
        RuntimeError("no ffmpeg"),
        subprocess.CompletedProcess(["ffmpeg"], 1, stdout="", stderr="r.mp4: Invalid data found"),
    ],
)
def test_a_failed_media_probe_never_fails_the_transcription(monkeypatch, tmp_path, outcome):
    def fake_run(command, **kwargs):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(transcriber, "_ffprobe_executable", lambda: None)
    monkeypatch.setattr(transcriber, "_ffmpeg_executable", lambda: "ffmpeg")
    monkeypatch.setattr(transcriber.subprocess, "run", fake_run)
    assert transcriber._probe_media_info(tmp_path / "r.mp4") is None


def test_an_unexpected_probe_error_is_caught_by_the_probe(monkeypatch, tmp_path):
    def broken_parse(stderr):
        raise ValueError("an unexpected header")

    monkeypatch.setattr(transcriber, "_ffprobe_executable", lambda: None)
    monkeypatch.setattr(transcriber, "_ffmpeg_executable", lambda: "ffmpeg")
    monkeypatch.setattr(
        transcriber.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1, stdout="", stderr=FFMPEG_HEADER),
    )
    monkeypatch.setattr(transcriber, "_parse_ffmpeg_duration", broken_parse)
    assert transcriber._probe_media_info(tmp_path / "r.mp4") is None


def test_a_probe_that_raises_still_gives_a_transcript(gladia_transport, monkeypatch):
    def no_ffmpeg():
        raise RuntimeError("FFmpeg is required but not available.")

    run, posted = gladia_transport
    monkeypatch.setattr(transcriber, "_probe_media_info", REAL_PROBE_MEDIA_INFO)
    monkeypatch.setattr(transcriber, "_ffmpeg_executable", no_ffmpeg)
    transcript = run(transcriber.speaker_options(2))
    assert len(posted) == 2
    assert transcript["stormiq"]["media"] is None


def test_gladia_polling_gives_up_after_repeated_errors(monkeypatch):
    calls = {"n": 0}

    def always_failing(**kwargs):
        calls["n"] += 1
        raise transcriber.TransientHTTPError("down")

    monkeypatch.setattr(transcriber, "_json_request", always_failing)
    monkeypatch.setattr(transcriber.time, "sleep", lambda seconds: None)
    with pytest.raises(transcriber.TransientHTTPError):
        transcriber._poll_gladia_transcription("job", "k")
    assert calls["n"] == transcriber.GLADIA_POLL_MAX_CONSECUTIVE_ERRORS


# ── People sync (people_sync.py, worker.run_people_sync) ───────────────────
#
# Synthetic people only. Livestorm is faked at `client.livestorm_get`, so the
# real pager, parser and roster write run; nothing reaches Livestorm.


class _LivestormAnswer:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.headers: dict = {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Client Error for url: https://api.livestorm.test/x", response=self)

    def json(self):
        return copy.deepcopy(self._payload)


UPSTREAM_BODY = "upstream-body-marker"


def _upstream_error(url, headers, params, status_code: int) -> requests.Response:
    """A real `requests.Response` error, as Livestorm's comes back: its request
    carries the Authorization header (the user's token) and its body is
    upstream text, so the `HTTPError` it raises carries both."""
    response = requests.Response()
    response.status_code = status_code
    response._content = json.dumps({"errors": [{"detail": UPSTREAM_BODY}]}).encode()
    response.request = requests.Request("GET", url, headers=headers, params=params).prepare()
    response.url = response.request.url
    return response


def _raw_person(index: int) -> dict:
    return {
        "id": f"p-sync-{index:03d}",
        "type": "people",
        "attributes": {
            "role": "participant",
            "first_name": f"Person{index}",
            "registrant_detail": {"attended": index % 2 == 0, "password_key": "synthetic-password-key"},
        },
    }


TEAM_SIZE_ATTRIBUTE = {
    "id": "attr-1",
    "type": "people_attributes",
    "attributes": {"slug": "team_size", "name": "Team size", "type": "unique_select", "user_id": "u-synthetic", "options": []},
}


class _FakeLivestorm:
    """`client.livestorm_get` for the people list (50 per page) and the people attributes."""

    def __init__(self) -> None:
        self.people = synthetic.people()
        self.fail_people_page = None  # a page answered with HTTP 500
        self.unreachable_page = None  # a page whose request never gets an answer
        self.loop_on_page = None  # a page whose next_page points back at page 0
        self.record_count_offset = 0  # Livestorm's count against the people it lists
        self.attributes_status = 200
        self.slow_s = 0.0
        self.calls: list = []
        self.errors: list = []  # the request of every failed call, token included

    def get(self, url, *, headers, params=None):
        self.calls.append(url.rsplit("/", 1)[-1])
        assert "Bearer" in headers.get("Authorization", ""), "a sync always sends the resolved token"
        time.sleep(self.slow_s)
        number = (params or {}).get("page[number]", 0)
        if url.endswith("/people_attributes"):
            if self.attributes_status != 200:
                return self._error(url, headers, params, self.attributes_status)
            return _LivestormAnswer({"data": [TEAM_SIZE_ATTRIBUTE], "meta": {"next_page": None, "record_count": 1}})
        if number == self.fail_people_page:
            return self._error(url, headers, params, 500)
        if number == self.unreachable_page:
            request = requests.Request("GET", url, headers=headers, params=params).prepare()
            self.errors.append(request)
            raise requests.ConnectionError("Livestorm could not be reached", request=request)
        size = (params or {}).get("page[size]", 50)
        chunk = self.people[number * size:(number + 1) * size]
        next_page = number + 1 if (number + 1) * size < len(self.people) else None
        if number == self.loop_on_page:
            next_page = 0
        record_count = len(self.people) + self.record_count_offset
        return _LivestormAnswer({"data": chunk, "meta": {"next_page": next_page, "record_count": record_count}})

    def _error(self, url, headers, params, status_code):
        response = _upstream_error(url, headers, params, status_code)
        self.errors.append(response.request)
        return response


@pytest.fixture
def people_env(monkeypatch):
    """A worker with fake Livestorm, credentials, queue and progress; real Postgres where the test asks for it."""
    env = SimpleNamespace(livestorm=_FakeLivestorm(), enqueued=[], auth=[], progress=[], cleared=[])
    monkeypatch.setattr(livestorm_client, "livestorm_get", env.livestorm.get)
    monkeypatch.setattr(
        people_sync, "enqueue_unique_job_sync",
        lambda *args: env.enqueued.append(args) or {"jobId": args[1], "jobStatus": "pending", "enqueued": True},
    )
    monkeypatch.setattr(
        people_sync, "fetch_oauth_connection",
        lambda cid: {"connection_id": cid, "organization_id": {"conn-a": "org-a", "conn-b": "org-b"}[cid]}
        if cid in ("conn-a", "conn-b") else None,
    )
    monkeypatch.setattr(people_sync, "livestorm_auth_for_job", lambda cid: env.auth.append(cid) or "Bearer fresh-token")
    monkeypatch.setattr(people_sync, "publish_progress_sync", lambda *a, **k: env.progress.append(a[2]))
    monkeypatch.setattr(worker, "publish_progress_sync", lambda *a, **k: env.progress.append(a[2]))
    monkeypatch.setattr(worker, "clear_progress_sync", lambda kind, key: env.cleared.append((kind, key)))
    return env


def _seed_session(session_id: str = "sess-a", org: str = "org-a", *, ended_s_ago: float = 86400) -> float:
    """A cached session that ended `ended_s_ago` seconds ago; returns its end (unix seconds)."""
    end = int(time.time() - ended_s_ago)
    payload = {"data": {"id": session_id, "type": "sessions", "attributes": {"started_at": end - 1800, "ended_at": end, "duration": 1800}}}
    db.upsert_cached_session("key-a", session_id, organization_id=org, session_payload=payload, event_payload=synthetic.event_payload())
    return end


def _request_sync(session_id: str = "sess-a", *, org: str = "org-a", connection: str = "conn-a") -> dict:
    return people_sync.request_people_sync(session_id, read_scope=org, caller_org_id=org, job_connection_id=connection)


def _run_job(sync_id: str, session_id: str = "sess-a", ctx=None):
    return asyncio.run(worker.run_people_sync({} if ctx is None else ctx, sync_id, session_id))


def _sync_row(sync_id: str) -> dict:
    with get_db_connection() as conn:
        return dict(conn.execute("SELECT * FROM livestorm_sync_jobs WHERE sync_id = %s", (sync_id,)).fetchone())


def _arq_result_blob(exc: BaseException) -> bytes:
    """What arq stores in Redis for a failed job (`Worker.run_job`: the
    exception as its result, pickled)."""
    return serialize_result("run_people_sync", ("sync-1", "sess-a"), {}, 1, 0, False, exc, 0, 0, "ref", "arq:queue", "job-1")


def test_a_people_sync_hands_over_its_connection_once_and_queues_ids_only(test_database, people_env, caplog):
    caplog.set_level(logging.DEBUG)
    _seed_session()
    started = _request_sync()
    sync_id = started["syncId"]
    assert started == {"sessionId": "sess-a", "status": "pending", "syncId": sync_id, "started": True, "skipped": None}
    assert people_env.enqueued == [("run_people_sync", f"livestorm_sync:{sync_id}", sync_id, "sess-a")]
    assert _sync_row(sync_id)["connection_id"] == "conn-a", "the connection waits on the row, not in the queue"

    result = _run_job(sync_id)

    # arq keeps results in Redis: counts only. One page of people, one of field labels.
    assert result == {"status": "completed", "calls": 2, "people": 10, "recordCount": 10}
    assert people_env.auth == ["conn-a"], "the token comes from the claimed row"
    row = _sync_row(sync_id)
    assert (row["status"], row["connection_id"], row["pages_fetched"], row["people_written"]) == ("completed", None, 2, 10)
    roster = db.fetch_roster_status("sess-a", "org-a")["roster"]
    assert (roster["source"], roster["complete"], roster["people_count"], roster["reason"]) == ("paginated", True, 10, "initial")
    assert roster["fields_source"] == "people_attributes" and roster["fields"][0]["name"] == "Team size"
    assert len(db.list_session_people("sess-a", "org-a")) == 10
    assert "queued" in people_env.progress and "people" in people_env.progress
    assert people_env.cleared == [("roster", "sess-a")], "the job clears its progress"

    # The claim handed the connection over once: running the job again does nothing.
    calls_before = len(people_env.livestorm.calls)
    assert _run_job(sync_id) == {"status": "skipped"}
    assert len(people_env.livestorm.calls) == calls_before
    shared = json.dumps([people_env.enqueued, result])
    assert "conn-a" not in shared and "Bearer" not in shared
    assert "conn-a" not in caplog.text and "fresh-token" not in caplog.text, "no connection id or token in the logs"


@pytest.mark.parametrize("failure", ["page fails", "unreachable", "list loops", "deadline", "labels fail"])
def test_a_failed_page_keeps_the_previous_roster_and_marks_the_sync_error(test_database, people_env, monkeypatch, caplog, failure):
    caplog.set_level(logging.DEBUG)
    _seed_session()
    people_env.livestorm.people = [_raw_person(index) for index in range(120)]  # three pages
    first = _request_sync()
    _run_job(first["syncId"])
    before = db.fetch_roster_status("sess-a", "org-a")["roster"]
    people_before = db.list_session_people("sess-a", "org-a")
    assert before["roster_version"] == 1 and len(people_before) == 120

    people_env.livestorm.people = [_raw_person(index) for index in range(130)]
    expected_calls = {"page fails": 2, "unreachable": 1, "list loops": 2, "deadline": 0, "labels fail": 4}[failure]
    if failure == "page fails":
        people_env.livestorm.fail_people_page = 1
    elif failure == "unreachable":
        people_env.livestorm.unreachable_page = 1
    elif failure == "list loops":
        people_env.livestorm.loop_on_page = 1
    elif failure == "deadline":
        monkeypatch.setattr(worker, "PEOPLE_SYNC_DEADLINE_MARGIN_SECONDS", worker.PEOPLE_SYNC_JOB_TIMEOUT_SECONDS + 60)
    else:
        people_env.livestorm.attributes_status = 500
        # The labels read the day before are stale, so they are read again.
        with get_db_connection() as conn:
            conn.execute("UPDATE org_people_attributes SET fetched_at = NOW() - INTERVAL '25 hours'")
            conn.commit()
    retry = _request_sync()
    assert retry["started"] is False and retry["skipped"] == "cooldown", "a sync completed a moment ago"
    with get_db_connection() as conn:
        conn.execute("UPDATE livestorm_sync_jobs SET updated_at = NOW() - INTERVAL '20 minutes'")
        conn.commit()
    second = _request_sync()
    assert second["started"] is True

    with pytest.raises(people_sync.PeopleSyncFailed) as failed:
        _run_job(second["syncId"])

    assert db.fetch_roster_status("sess-a", "org-a")["roster"] == before, "the previous roster and version stay"
    assert db.list_session_people("sess-a", "org-a") == people_before
    row = _sync_row(second["syncId"])
    assert (row["status"], row["connection_id"], row["pages_fetched"]) == ("error", None, expected_calls)
    assert "previous people list is kept" in row["error"]
    assert livestorm_client.API_BASE not in row["error"] and "Bearer" not in row["error"], "no upstream URL or token in the error"
    assert people_env.cleared == [("roster", "sess-a")] * 2, "each run clears its progress"

    # arq pickles a failed job's exception into Redis, and a status read
    # unpickles it in the web process: only our own message gets there, never
    # the request (the user's token) or the upstream body a requests error carries.
    if failure in ("page fails", "unreachable", "labels fail"):
        assert "fresh-token" in people_env.livestorm.errors[-1].headers["Authorization"], "the upstream error did carry the token"
    raised = failed.value
    assert str(raised) == row["error"] and raised.__cause__ is None
    for blob in (pickle.dumps(raised), _arq_result_blob(raised)):
        assert b"fresh-token" not in blob and UPSTREAM_BODY.encode() not in blob and b"api.livestorm" not in blob
    assert "conn-a" not in caplog.text and "fresh-token" not in caplog.text, "no connection id or token in the logs"


def test_the_first_roster_is_due_a_refresh_a_week_after_the_session_and_later_syncs_keep_it(test_database, people_env):
    end = _seed_session(ended_s_ago=86400)
    _run_job(_request_sync()["syncId"])
    due = db.fetch_roster_status("sess-a", "org-a")["roster"]["refresh_due_at"]
    assert due == datetime.fromtimestamp(end, tz=timezone.utc) + timedelta(days=7)

    with get_db_connection() as conn:
        conn.execute("UPDATE livestorm_sync_jobs SET updated_at = NOW() - INTERVAL '20 minutes'")
        conn.commit()
    _seed_session(ended_s_ago=3 * 86400)  # a later read moves nothing that was due
    second = _request_sync()
    _run_job(second["syncId"])
    roster = db.fetch_roster_status("sess-a", "org-a")["roster"]
    assert (roster["roster_version"], roster["reason"], roster["refresh_due_at"]) == (2, "manual", due)
    # The field labels read less than 24 hours ago are reused: one call, the people page.
    assert _sync_row(second["syncId"])["pages_fetched"] == 1

    _seed_session("sess-old", ended_s_ago=30 * 86400)
    _run_job(_request_sync("sess-old")["syncId"], "sess-old")
    assert db.fetch_roster_status("sess-old", "org-a")["roster"]["refresh_due_at"] is None, "this sync already is the refresh"


def test_refused_field_labels_fall_back_to_the_event_fields(test_database, people_env):
    _seed_session()
    people_env.livestorm.attributes_status = 403
    result = _run_job(_request_sync()["syncId"])
    assert result["status"] == "completed"
    roster = db.fetch_roster_status("sess-a", "org-a")["roster"]
    assert roster["fields_source"] == "event_fields" and roster["fields"][0]["name"] == "Team size"
    assert db.get_org_people_attributes("org-a")["status"] == "forbidden"


def test_a_count_that_disagrees_with_the_list_saves_an_incomplete_roster(test_database, people_env):
    _seed_session()
    people_env.livestorm.record_count_offset = 1  # Livestorm counts one more person than it lists
    result = _run_job(_request_sync()["syncId"])
    assert result == {"status": "completed", "calls": 2, "people": 10, "recordCount": 11}
    roster = db.fetch_roster_status("sess-a", "org-a")["roster"]
    assert (roster["source"], roster["complete"], roster["record_count"], roster["people_count"]) == ("paginated", False, 11, 10)


def test_the_worker_checks_the_daily_cap_again_before_any_call(test_database, people_env, monkeypatch):
    # The web side sums only the syncs that have ended, so syncs queued
    # together all pass it. The worker checks again under the org's lock,
    # where every earlier sync has recorded its calls.
    for session_id in ("sess-a", "sess-b"):
        _seed_session(session_id)
        _run_job(_request_sync(session_id)["syncId"], session_id)  # each session's first sync: never capped
    with get_db_connection() as conn:
        conn.execute("UPDATE livestorm_sync_jobs SET updated_at = NOW() - INTERVAL '20 minutes'")
        conn.commit()
    monkeypatch.setenv(people_sync.DAILY_CALLS_ENV, "1")
    first, second = _request_sync("sess-a"), _request_sync("sess-b")
    assert first["started"] and second["started"], "both pass the web side: no call counted yet"

    assert _run_job(first["syncId"], "sess-a")["calls"] == 1, "one page of people; the field labels are reused"
    calls_before, auth_before = len(people_env.livestorm.calls), list(people_env.auth)
    with pytest.raises(people_sync.PeopleSyncFailed, match="daily limit"):
        _run_job(second["syncId"], "sess-b")
    assert len(people_env.livestorm.calls) == calls_before, "no Livestorm call"
    assert people_env.auth == auth_before, "not even a token refresh"
    row = _sync_row(second["syncId"])
    assert (row["status"], row["connection_id"], row["pages_fetched"]) == ("error", None, None)
    assert "daily limit" in row["error"]
    assert db.fetch_roster_status("sess-b", "org-a")["roster"]["roster_version"] == 1, "the roster stays"

    # A session's first sync is never capped, in the worker either.
    monkeypatch.setenv(people_sync.DAILY_CALLS_ENV, "0")
    _seed_session("sess-c")
    assert _run_job(_request_sync("sess-c")["syncId"], "sess-c")["status"] == "completed"


def test_an_org_mismatch_makes_no_livestorm_call(monkeypatch):
    def no_call(*args, **kwargs):
        raise AssertionError("Livestorm was called for another org's session")

    monkeypatch.setattr(livestorm_client, "livestorm_get", no_call)
    monkeypatch.setattr(people_sync, "livestorm_auth_for_job", no_call)  # not even a token refresh
    monkeypatch.setattr(people_sync, "fetch_oauth_connection", lambda cid: {"connection_id": cid, "organization_id": "org-b"})
    claim = {"session_id": "s1", "reason": "initial", "connection_id": "conn-b", "credential_user_id": ""}
    for session_org in ("org-a", ""):
        with pytest.raises(RuntimeError, match="another Livestorm organization"):
            people_sync._resolve_sync_credentials(claim, session_org_id=session_org)

    # The same through the job: the row ends in error, with no call made.
    marked: list = []
    monkeypatch.setattr(worker, "claim_livestorm_sync", lambda sync_id: dict(claim))
    monkeypatch.setattr(people_sync, "session_organization", lambda sid: "org-a")
    monkeypatch.setattr(people_sync, "check_daily_cap", lambda sync_id, org: None)
    monkeypatch.setattr(people_sync, "update_livestorm_sync", lambda sync_id, **kw: marked.append(kw) or True)
    monkeypatch.setattr(worker, "publish_progress_sync", lambda *a, **k: None)
    monkeypatch.setattr(worker, "clear_progress_sync", lambda *a, **k: None)
    with pytest.raises(RuntimeError, match="another Livestorm organization"):
        _run_job("sync-1", "s1")
    assert marked[-1]["status"] == "error" and "another Livestorm organization" in marked[-1]["error"]


def test_a_sync_without_a_connection_uses_the_operator_key_in_local_dev_only(monkeypatch):
    monkeypatch.setattr(people_sync, "fetch_oauth_connection", lambda cid: pytest.fail("no connection to read"))
    monkeypatch.setenv("LS_API_KEY", "ls-key")
    claim = {"session_id": "s1", "reason": "initial", "connection_id": "", "credential_user_id": ""}
    with pytest.raises(RuntimeError, match="no Livestorm credentials"):
        people_sync._resolve_sync_credentials(claim, session_org_id="org-a")
    monkeypatch.setenv("STORMIQ_LOCAL_DEV", "1")
    assert people_sync._resolve_sync_credentials(claim, session_org_id="") == "ls-key"
    with pytest.raises(RuntimeError, match="not supported yet"):
        people_sync._resolve_sync_credentials({**claim, "credential_user_id": "u-1"}, session_org_id="org-a")


@pytest.fixture
def sync_start(monkeypatch):
    """`start_people_sync` with its row, queue and cap faked."""
    state = SimpleNamespace(created=[], enqueued=[], updates=[], used=0, arq={"jobStatus": "running"})

    def create(session_id, *, reason, connection_id=""):
        state.created.append((session_id, reason, connection_id))
        return {"sync_id": "sync-new", "status": "pending", "created": True}

    monkeypatch.setattr(people_sync, "create_livestorm_sync", create)
    monkeypatch.setattr(
        people_sync, "enqueue_unique_job_sync",
        lambda *args: state.enqueued.append(args) or {"jobId": args[1], "jobStatus": "pending", "enqueued": True},
    )
    monkeypatch.setattr(people_sync, "update_livestorm_sync", lambda sync_id, **kw: state.updates.append((sync_id, kw)) or True)
    monkeypatch.setattr(people_sync, "livestorm_sync_calls_since", lambda org, since: state.used)
    monkeypatch.setattr(people_sync, "read_job_status_sync", lambda job_id: state.arq)
    monkeypatch.delenv(people_sync.DAILY_CALLS_ENV, raising=False)
    return state


def _prior_sync(status: str, minutes_ago: float, reason: str = "initial") -> dict:
    return {"sync_id": "sync-old", "status": status, "reason": reason, "updated_at": datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)}


def _start(sync=None, *, roster=True, automatic=False, caller_org="org-a", session_org="org-a", connection="conn-a", local_dev=False):
    return people_sync.start_people_sync(
        "s1",
        reason="manual" if roster else "initial",
        caller_org_id=caller_org,
        session_org_id=session_org,
        job_connection_id=connection,
        roster_status={"session_id": "s1", "roster": {"roster_version": 1} if roster else None, "sync": sync},
        local_dev=local_dev,
        automatic=automatic,
    )


@pytest.mark.parametrize(
    ("caller_org", "session_org", "connection", "local_dev", "starts"),
    [
        ("org-a", "org-a", "conn-a", False, True),
        ("org-b", "org-a", "conn-admin", False, False),  # an admin reading another org's session
        ("", "", "conn-x", False, False),  # a session without an org is no one's
        ("org-b", "org-a", "conn-admin", True, False),  # local dev, but with a user's connection
        ("", "org-a", "", True, True),  # local dev with the operator's LS_API_KEY
    ],
)
def test_only_the_sessions_org_starts_a_sync(sync_start, caller_org, session_org, connection, local_dev, starts):
    result = _start(caller_org=caller_org, session_org=session_org, connection=connection, local_dev=local_dev)
    assert result["started"] is starts
    if not starts:
        assert result["skipped"] == "other_org"
        assert sync_start.created == [] and sync_start.enqueued == [], "nothing is created or enqueued for another org"


@pytest.mark.parametrize(
    ("prior", "automatic", "skipped"),
    [
        (_prior_sync("completed", 5), False, "cooldown"),
        (_prior_sync("completed", 5), True, "cooldown"),
        (_prior_sync("completed", 20), False, None),
        (_prior_sync("error", 30), True, "error_backoff"),
        (_prior_sync("error", 30), False, None),  # an explicit POST retries at once
        (_prior_sync("error", 120), True, None),
    ],
    ids=["cooldown", "cooldown-automatic", "after-cooldown", "backoff", "explicit-retry", "after-backoff"],
)
def test_the_cooldown_and_the_error_backoff_start_nothing(sync_start, prior, automatic, skipped):
    result = _start(prior, automatic=automatic)
    assert result["skipped"] == skipped
    assert result["started"] is (skipped is None)
    assert (sync_start.created == [] and sync_start.enqueued == []) is (skipped is not None)


def test_an_active_sync_is_joined_unless_its_queue_job_is_gone(sync_start):
    joined = _start(_prior_sync("running", 30))
    assert joined == {"status": "running", "syncId": "sync-old", "started": False, "skipped": None}
    assert sync_start.created == [] and sync_start.enqueued == []

    sync_start.arq = {"jobStatus": "not_found"}
    replaced = _start(_prior_sync("pending", 5))
    assert replaced["started"] is True
    assert sync_start.updates[0][0] == "sync-old" and sync_start.updates[0][1]["status"] == "error"
    assert sync_start.updates[0][1]["only_from"] == ("pending", "running"), "only a row still active is closed"


@pytest.mark.parametrize(
    ("arq_status", "minutes_ago", "closed"),
    [
        ({"jobStatus": "error", "error": "boom"}, 0, True),
        ({"jobStatus": "not_found"}, 5, True),
        ({"jobStatus": "not_found"}, 0, False),  # just inserted, enqueue in progress
        ({"jobStatus": "unknown"}, 60, False),  # Redis down: leave it to the sweeper
        ({"jobStatus": "running"}, 60, False),
        ({"jobStatus": "pending"}, 60, False),
    ],
)
def test_a_status_read_closes_only_dead_syncs(monkeypatch, arq_status, minutes_ago, closed):
    marked: list = []
    monkeypatch.setattr(people_sync, "read_job_status_sync", lambda job_id: arq_status)
    monkeypatch.setattr(people_sync, "update_livestorm_sync", lambda sync_id, **kw: marked.append(kw) or True)
    monkeypatch.setattr(people_sync, "read_progress", lambda *a: {"stage": "people"})
    monkeypatch.setattr(
        people_sync, "fetch_roster_status",
        lambda sid, scope: {"session_id": sid, "roster": None, "sync": _prior_sync("pending", minutes_ago)} if scope == "org-a" else None,
    )
    status = people_sync.read_roster_status("s1", organization_id="org-a")
    assert (status["status"] == "error") is closed
    assert (status["progress"] is None) is closed
    assert bool(marked) is closed
    with pytest.raises(people_sync.RosterSessionNotFound):
        people_sync.read_roster_status("s1", organization_id="org-b")


def test_the_daily_cap_stops_every_sync_but_a_sessions_first(sync_start, monkeypatch):
    sync_start.used = 1000
    capped = _start(_prior_sync("completed", 60))
    assert capped["skipped"] == "daily_cap" and (capped["limit"], capped["used"]) == (1000, 1000)
    assert sync_start.created == [] and sync_start.enqueued == []
    # A retry of a failed first sync is a second initial sync: it counts.
    assert _start(_prior_sync("error", 60), roster=False)["skipped"] == "daily_cap"
    # The session's first initial sync never counts, and is never capped.
    assert _start(None, roster=False)["started"] is True

    sync_start.used = 999
    assert _start(_prior_sync("completed", 60))["started"] is True
    monkeypatch.setenv(people_sync.DAILY_CALLS_ENV, "2000")
    sync_start.used = 1500
    assert _start(_prior_sync("completed", 60))["started"] is True


@pytest.mark.parametrize(("raw", "cap"), [(None, 1000), ("250", 250), (" 0 ", 0), ("lots", 0), ("-5", 0), ("1.5", 0)])
def test_the_daily_cap_reads_its_env_var_and_fails_closed(monkeypatch, raw, cap):
    if raw is None:
        monkeypatch.delenv(people_sync.DAILY_CALLS_ENV, raising=False)
    else:
        monkeypatch.setenv(people_sync.DAILY_CALLS_ENV, raw)
    assert people_sync.daily_calls_cap() == cap


def test_the_capped_route_answers_429_with_a_clear_error(sync_start, monkeypatch):
    sync_start.used = 1000
    monkeypatch.setattr(people_sync, "fetch_session_fields", lambda sid, fields, *, organization_id: {"organization_id": "org-a"})
    monkeypatch.setattr(
        people_sync, "fetch_roster_status", lambda sid, scope: {"session_id": sid, "roster": {"roster_version": 1}, "sync": None},
    )
    caller = auth.Caller(kind="user", org_id="org-a", connection={"connection_id": "conn-a", "access_token": "secret-token"})
    with pytest.raises(routes_sessions.HTTPException) as refused:
        routes_sessions.start_roster_sync("s1", caller=caller)
    assert refused.value.status_code == 429
    detail = refused.value.detail
    assert set(detail) == {"resource", "message", "details"} and detail["resource"] == "People sync"
    assert "daily limit" in detail["message"]
    assert detail["details"] == {"resource": "People sync", "dailyCallCap": 1000, "callsLast24h": 1000}
    assert "conn-a" not in json.dumps(detail) and "secret-token" not in json.dumps(detail)
    assert sync_start.created == [] and sync_start.enqueued == []


def test_the_daily_cap_counts_one_orgs_calls_of_the_last_24_hours(test_database):
    for session_id, org in (("sess-a", "org-a"), ("sess-b", "org-a"), ("sess-c", "org-b")):
        db.upsert_cached_session("key-a", session_id, organization_id=org)

    def sync(session_id, reason, calls, *, hours_ago=0):
        sync_id = db.create_livestorm_sync(session_id, reason=reason)["sync_id"]
        db.update_livestorm_sync(sync_id, status="completed", pages_fetched=calls)
        if hours_ago:
            with get_db_connection() as conn:
                conn.execute(
                    "UPDATE livestorm_sync_jobs SET updated_at = NOW() - make_interval(hours => %s) WHERE sync_id = %s",
                    (hours_ago, sync_id),
                )
                conn.commit()

    sync("sess-a", "initial", 40)  # the session's first initial sync: exempt
    sync("sess-a", "initial", 5)  # a second initial sync counts
    sync("sess-a", "manual", 7)
    sync("sess-b", "manual", 11, hours_ago=25)  # outside the window
    sync("sess-b", "manual", 3)
    sync("sess-c", "manual", 100)  # another org
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    assert db.livestorm_sync_calls_since("org-a", since) == 15
    assert db.livestorm_sync_calls_since("org-b", since) == 100
    assert db.livestorm_sync_calls_since("", since) == 0
    with pytest.raises(ValueError):
        db.livestorm_sync_calls_since(ALL_ORGS, since)


def test_the_sweeper_closes_stuck_syncs_and_clears_their_connection(test_database):
    for session_id in ("sess-a", "sess-b", "sess-c"):
        db.upsert_cached_session("key-a", session_id, organization_id="org-a")
    stuck = db.create_livestorm_sync("sess-a", reason="initial", connection_id="conn-a")["sync_id"]
    fresh = db.create_livestorm_sync("sess-b", reason="initial", connection_id="conn-a")["sync_id"]
    done = db.create_livestorm_sync("sess-c", reason="initial")["sync_id"]
    db.update_livestorm_sync(done, status="completed")
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE livestorm_sync_jobs SET updated_at = NOW() - INTERVAL '2 hours' WHERE sync_id IN (%s, %s)", (stuck, done),
        )
        conn.commit()

    asyncio.run(worker.sweep_stuck_jobs({}))

    stuck_row, fresh_row, done_row = _sync_row(stuck), _sync_row(fresh), _sync_row(done)
    assert (stuck_row["status"], stuck_row["connection_id"]) == ("error", None)
    assert "sweeper" in stuck_row["error"]
    assert (fresh_row["status"], fresh_row["connection_id"]) == ("pending", "conn-a"), "a fresh row is left alone"
    assert done_row["status"] == "completed"
    assert db.claim_livestorm_sync(stuck) is None, "a swept sync can't be claimed"
    assert db.create_livestorm_sync("sess-a", reason="initial")["created"] is True, "the session is free again"


def test_a_failed_enqueue_leaves_no_connection_id(test_database, people_env, monkeypatch):
    _seed_session()
    monkeypatch.setattr(people_sync, "enqueue_unique_job_sync", lambda *args: None)
    with pytest.raises(people_sync.PeopleSyncUnavailable):
        _request_sync()
    with get_db_connection() as conn:
        rows = [dict(row) for row in conn.execute("SELECT status, connection_id, error FROM livestorm_sync_jobs")]
    assert len(rows) == 1 and rows[0]["status"] == "error" and rows[0]["connection_id"] is None
    assert "queue" in rows[0]["error"]

    monkeypatch.setattr(
        people_sync, "enqueue_unique_job_sync",
        lambda *args: {"jobId": args[1], "jobStatus": "pending", "enqueued": True},
    )
    assert _request_sync()["started"] is True, "the closed row doesn't block a retry"


def test_a_failed_enqueue_answers_503_with_a_clear_error(sync_start, monkeypatch):
    monkeypatch.setattr(people_sync, "enqueue_unique_job_sync", lambda *args: None)
    monkeypatch.setattr(people_sync, "fetch_session_fields", lambda sid, fields, *, organization_id: {"organization_id": "org-a"})
    monkeypatch.setattr(people_sync, "fetch_roster_status", lambda sid, scope: {"session_id": sid, "roster": None, "sync": None})
    caller = auth.Caller(kind="user", org_id="org-a", connection={"connection_id": "conn-a", "access_token": "secret-token"})
    with pytest.raises(routes_sessions.HTTPException) as refused:
        routes_sessions.start_roster_sync("s1", caller=caller)
    assert refused.value.status_code == 503
    detail = refused.value.detail
    assert set(detail) == {"resource", "message", "details"} and detail["details"] == {"resource": "People sync"}
    assert "queue" in detail["message"]
    assert "conn-a" not in json.dumps(detail) and "secret-token" not in json.dumps(detail)
    assert sync_start.updates[-1][0] == "sync-new" and sync_start.updates[-1][1]["status"] == "error", "the row is closed at once"


def test_a_timed_out_sync_is_marked_error_and_its_thread_stops(people_env, monkeypatch):
    marked: list = []
    written: list = []
    monkeypatch.setattr(
        worker, "claim_livestorm_sync",
        lambda sync_id: {"session_id": "s1", "reason": "initial", "connection_id": "conn-a", "credential_user_id": ""},
    )
    monkeypatch.setattr(people_sync, "session_organization", lambda sid: "org-a")
    monkeypatch.setattr(people_sync, "check_daily_cap", lambda sync_id, org: None)
    monkeypatch.setattr(
        people_sync, "fetch_session_fields",
        lambda sid, fields, *, organization_id: {"session_payload": synthetic.session_payload(), "event_payload": None},
    )
    monkeypatch.setattr(people_sync, "update_livestorm_sync", lambda sync_id, **kw: marked.append(kw) or True)
    monkeypatch.setattr(people_sync, "replace_session_roster", lambda *a, **k: written.append(a))
    people_env.livestorm.people = [_raw_person(index) for index in range(120)]
    people_env.livestorm.slow_s = 0.2  # each page takes a while

    async def run_with_short_timeout():
        await asyncio.wait_for(worker.run_people_sync({}, "sync-1", "s1"), timeout=0.05)

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(run_with_short_timeout())  # returns after the thread stops
    assert marked and all(update["status"] == "error" for update in marked)
    assert "interrupted" in marked[0]["error"]
    assert written == [], "a cancelled sync never writes a roster"
    assert len(people_env.livestorm.calls) == 1, "the thread stops at its next page"
    assert people_env.cleared == [("roster", "s1")], "a cancelled job clears its progress too"


class _FakeLockRedis:
    """The two Redis calls the org lock makes: SET NX EX and the token-checked delete."""

    def __init__(self) -> None:
        self.values: dict = {}

    async def set(self, key, value, *, nx=False, ex=None):
        if nx and key in self.values:
            return None
        self.values[key] = value
        return True

    async def eval(self, script, numkeys, key, token):
        if self.values.get(key) == token:
            del self.values[key]
            return 1
        return 0


def test_a_held_org_lock_makes_the_next_sync_wait_not_fail(monkeypatch):
    active = {"org-a": 0, "org-b": 0}
    most = {"org-a": 0, "org-b": 0, "total": 0}
    touched: list = []
    progress: list = []
    orgs = {"s1": "org-a", "s2": "org-a", "s3": "org-b"}

    def fake_run(api_key, session_id, sync, *, deadline, should_stop):
        org = orgs[session_id]
        active[org] += 1
        most[org] = max(most[org], active[org])
        most["total"] = max(most["total"], sum(active.values()))
        time.sleep(0.15)
        active[org] -= 1
        return {"status": "completed", "calls": 1, "people": 1, "recordCount": 1}

    monkeypatch.setattr(
        worker, "claim_livestorm_sync",
        lambda sync_id: {"session_id": sync_id.replace("sync", "s"), "reason": "initial", "connection_id": "c", "credential_user_id": ""},
    )
    monkeypatch.setattr(people_sync, "session_organization", lambda sid: orgs[sid])
    monkeypatch.setattr(people_sync, "check_daily_cap", lambda sync_id, org: None)
    monkeypatch.setattr(people_sync, "_resolve_sync_credentials", lambda claim, *, session_org_id: "Bearer t")
    monkeypatch.setattr(people_sync, "run_people_sync", fake_run)
    monkeypatch.setattr(worker, "touch_livestorm_sync", lambda sync_id: touched.append(sync_id) or True)
    monkeypatch.setattr(worker, "publish_progress_sync", lambda kind, sid, stage, **k: progress.append((sid, stage)))
    monkeypatch.setattr(worker, "clear_progress_sync", lambda *a, **k: None)
    monkeypatch.setattr(worker, "PEOPLE_SYNC_LOCK_POLL_SECONDS", 0.01)
    monkeypatch.setattr(worker, "PEOPLE_SYNC_LOCK_HEARTBEAT_SECONDS", 0.03)

    async def run_three():
        ctx = {"redis": _FakeLockRedis()}
        # A lock that is never released fails the test instead of hanging it.
        runs = asyncio.gather(*(worker.run_people_sync(ctx, f"sync{n}", f"s{n}") for n in (1, 2, 3)))
        results = await asyncio.wait_for(runs, timeout=5)
        return ctx, results

    ctx, results = asyncio.run(run_three())
    assert [result["status"] for result in results] == ["completed"] * 3, "the waiting sync ran after the first"
    assert most["org-a"] == 1, "one sync per org at a time"
    assert most["total"] == 2, "another org's sync doesn't wait"
    assert touched and set(touched) <= {"sync1", "sync2"}, "the waiting sync keeps its row fresh for the sweeper"
    assert any(stage == "waiting" for _sid, stage in progress)
    assert ctx["redis"].values == {}, "every lock was released by its owner"


def test_the_org_lock_is_released_only_by_its_owner(test_redis):
    import redis.asyncio as redis_async

    async def scenario():
        client = redis_async.Redis.from_url(queue.get_redis_url())  # the test database, set by the fixture
        key = worker.people_sync_lock_key("org-a")
        try:
            async with worker._people_sync_org_lock({"redis": client}, "org-a", "sync-1", "s1"):
                ttl = await client.ttl(key)
                assert worker.PEOPLE_SYNC_JOB_TIMEOUT_SECONDS < ttl <= worker.PEOPLE_SYNC_LOCK_TTL_SECONDS
                # The lock expired and another sync took it: leaving must not free it.
                await client.set(key, "next-owner")
            assert await client.get(key) == b"next-owner"
            await client.delete(key)
            async with worker._people_sync_org_lock({"redis": client}, "org-a", "sync-2", "s2"):
                assert await client.exists(key)
            assert not await client.exists(key), "its owner releases it"
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.fixture
def lock_env(monkeypatch):
    """`worker.run_people_sync` with its claim, org, cap, credentials and sync
    faked. Job `syncN` claims session `sN`; s1 to s3 are org-a's."""
    env = SimpleNamespace(
        orgs={"s1": "org-a", "s2": "org-a", "s3": "org-a"}, claims={}, touch_ok=True, hold_s=0.3,
        marked=[], touched=[], resolved=[], ran=[], progress=[], cleared=[],
    )

    def claim(sync_id):
        return env.claims.get(sync_id) or {"session_id": sync_id.replace("sync", "s"), "reason": "manual", "connection_id": "c", "credential_user_id": ""}

    def fake_run(api_key, session_id, sync, *, deadline, should_stop):
        env.ran.append(session_id)
        time.sleep(env.hold_s)
        return {"status": "completed", "calls": 1, "people": 1, "recordCount": 1}

    monkeypatch.setattr(worker, "claim_livestorm_sync", claim)
    monkeypatch.setattr(people_sync, "session_organization", lambda sid: env.orgs[sid])
    monkeypatch.setattr(people_sync, "check_daily_cap", lambda sync_id, org: None)
    monkeypatch.setattr(
        people_sync, "_resolve_sync_credentials", lambda claim, *, session_org_id: env.resolved.append(claim["session_id"]) or "Bearer t",
    )
    monkeypatch.setattr(people_sync, "run_people_sync", fake_run)
    monkeypatch.setattr(people_sync, "update_livestorm_sync", lambda sync_id, **kw: env.marked.append((sync_id, kw)) or True)
    monkeypatch.setattr(worker, "touch_livestorm_sync", lambda sync_id: env.touched.append(sync_id) or env.touch_ok)
    monkeypatch.setattr(worker, "publish_progress_sync", lambda kind, sid, stage, **k: env.progress.append((sid, stage)))
    monkeypatch.setattr(worker, "clear_progress_sync", lambda kind, key: env.cleared.append(key))
    monkeypatch.setattr(worker, "PEOPLE_SYNC_LOCK_POLL_SECONDS", 0.01)
    monkeypatch.setattr(worker, "PEOPLE_SYNC_LOCK_HEARTBEAT_SECONDS", 0.03)
    return env


def _org_lock_held_elsewhere() -> _FakeLockRedis:
    redis = _FakeLockRedis()
    redis.values[worker.people_sync_lock_key("org-a")] = "another-sync"
    return redis


def test_only_one_sync_per_org_waits_and_the_next_fails_at_once(lock_env):
    # A waiting sync holds one of the worker's max_jobs slots, which every
    # org's jobs share: one org holds two at most (one running, one waiting).
    async def run_three():
        ctx = {"redis": _FakeLockRedis()}
        runs = asyncio.gather(*(worker.run_people_sync(ctx, f"sync{n}", f"s{n}") for n in (1, 2, 3)), return_exceptions=True)
        return ctx, await asyncio.wait_for(runs, timeout=5)

    ctx, results = asyncio.run(run_three())
    busy = [result for result in results if isinstance(result, BaseException)]
    assert len(busy) == 1 and isinstance(busy[0], people_sync.PeopleSyncFailed), results
    assert "already waiting" in str(busy[0])
    assert [result["status"] for result in results if isinstance(result, dict)] == ["completed"] * 2
    assert sorted(lock_env.ran) == sorted(lock_env.resolved) and len(lock_env.ran) == 2, "the refused sync made no call"
    refused = [(sync_id, update) for sync_id, update in lock_env.marked if update["status"] == "error"]
    assert len(refused) == 1 and "already waiting" in refused[0][1]["error"]
    assert sorted(lock_env.cleared) == ["s1", "s2", "s3"]
    assert ctx["redis"].values == {}, "the lock and the waiting place are both released"


def test_a_sync_cancelled_while_it_waits_for_the_org_lock_is_marked_error(lock_env):
    async def scenario():
        redis = _org_lock_held_elsewhere()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(worker.run_people_sync({"redis": redis}, "sync1", "s1"), timeout=0.1)
        return redis

    redis = asyncio.run(scenario())
    assert lock_env.ran == [] and lock_env.resolved == [], "no thread, no credentials"
    assert lock_env.marked[-1][1]["status"] == "error" and "interrupted" in lock_env.marked[-1][1]["error"]
    assert redis.values == {worker.people_sync_lock_key("org-a"): "another-sync"}, "its waiting place is freed, the other lock kept"
    assert lock_env.cleared == ["s1"]


def test_a_sync_swept_while_it_waits_stops_waiting(lock_env):
    lock_env.touch_ok = False  # the sweeper closed the row meanwhile

    async def scenario():
        redis = _org_lock_held_elsewhere()
        with pytest.raises(people_sync.PeopleSyncFailed, match="closed while it waited"):
            await asyncio.wait_for(worker.run_people_sync({"redis": redis}, "sync1", "s1"), timeout=5)
        return redis

    redis = asyncio.run(scenario())
    assert lock_env.touched == ["sync1"] and lock_env.ran == [] and lock_env.resolved == []
    assert redis.values == {worker.people_sync_lock_key("org-a"): "another-sync"}


def test_a_claim_for_another_session_makes_no_call(lock_env):
    lock_env.claims["sync1"] = {"session_id": "s2", "reason": "manual", "connection_id": "c", "credential_user_id": ""}
    with pytest.raises(people_sync.PeopleSyncFailed, match="another session"):
        asyncio.run(worker.run_people_sync({"redis": _FakeLockRedis()}, "sync1", "s1"))
    assert lock_env.resolved == [] and lock_env.ran == []
    assert lock_env.marked[-1] == ("sync1", {"status": "error", "error": "The people sync's row belongs to another session.", "pages_fetched": None})


WORKER_SIDE_SYNC_FUNCTIONS = {"run_people_sync", "_people_attributes"}


def _top_level_functions_naming(source: str, names: set) -> set:
    found = set()
    for statement in ast.parse(source).body:
        for node in ast.walk(statement):
            named = (
                (isinstance(node, ast.Name) and node.id in names)
                or (isinstance(node, ast.Attribute) and node.attr in names)
                or (isinstance(node, ast.alias) and node.name in names)
            )
            if named:
                found.add(statement.name if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)) else None)
    return found


def _code_naming(source: str, names: set) -> set:
    """The names in `names` that a module's code uses (a name, an attribute or
    an import; never a string or a comment)."""
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name) and node.id in names:
            found.add(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in names:
            found.add(node.attr)
        elif isinstance(node, ast.alias) and node.name in names:
            found.add(node.name)
    return found


def test_web_process_cannot_paginate_people_inline(sync_start, monkeypatch):
    # The paginated people list (about one call per 50 registrants) runs only
    # in the worker: outside the client, only people_sync's worker side names
    # the pagers, and no module but the worker calls that worker side
    # (the worker defines its own `run_people_sync`, the arq job).
    pagers = {"fetch_session_people", "fetch_people_attributes"}
    package = Path(people_sync.__file__).parent
    worker_only = {package / "livestorm" / "client.py", package / "people_sync.py", package / "worker.py"}
    checked = 0
    for path in package.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if path == package / "people_sync.py":
            assert _top_level_functions_naming(source, pagers) == WORKER_SIDE_SYNC_FUNCTIONS
        if path in worker_only:
            continue
        assert not pagers & set(re.findall(r"\w+", source)), f"{path.name} names a people pager"
        assert not _code_naming(source, WORKER_SIDE_SYNC_FUNCTIONS), f"{path.name} calls the people sync's worker side"
        checked += 1
    assert checked > 20, "the walk covers the web modules"
    # The check itself catches an inline call, as in an api_logic helper.
    inline = "def _inline(api_key, sync):\n    from livestorm_app import people_sync\n    return people_sync.run_people_sync(api_key, 's1', sync, deadline=0)\n"
    assert _code_naming(inline, WORKER_SIDE_SYNC_FUNCTIONS) == {"run_people_sync"}

    # And a POST /roster-sync with every Livestorm call trapped only queues the job.
    def no_call(*args, **kwargs):
        raise AssertionError("the web process called Livestorm")

    monkeypatch.setattr(livestorm_client, "livestorm_get", no_call)
    monkeypatch.setattr(people_sync, "fetch_session_fields", lambda sid, fields, *, organization_id: {"organization_id": "org-a"})
    monkeypatch.setattr(people_sync, "fetch_roster_status", lambda sid, scope: {"session_id": sid, "roster": None, "sync": None})
    caller = auth.Caller(kind="user", org_id="org-a", connection={"connection_id": "conn-a", "access_token": "t"})
    answer = routes_sessions.start_roster_sync("s1", caller=caller)
    assert answer["started"] is True
    assert sync_start.enqueued == [("run_people_sync", "livestorm_sync:sync-new", "sync-new", "s1")]


# ── The automatic start, after a session's base fetch ───────────────────────


def _sync_on_fetch(sync_start, monkeypatch, *, roster, sync=None, fresh=False, scope="org-a", **overrides):
    """`start_sync_on_fetch` for session s1 of org-a, with its roster status faked."""
    monkeypatch.setattr(
        people_sync, "fetch_roster_status",
        lambda sid, read_scope: {"session_id": sid, "roster": {"roster_version": 1} if roster else None, "sync": sync}
        if read_scope in ("org-a", ALL_ORGS) else None,
    )
    arguments = {
        "fresh": fresh, "read_scope": scope, "caller_org_id": "org-a", "session_org_id": "org-a", "job_connection_id": "conn-a",
        **overrides,
    }
    return people_sync.start_sync_on_fetch("s1", **arguments)


def test_a_session_without_a_roster_gets_its_first_sync_on_fetch(sync_start, monkeypatch):
    sync_start.used = 5000  # over the daily cap: a session's first initial sync is exempt
    result = _sync_on_fetch(sync_start, monkeypatch, roster=False)
    assert result == {"status": "pending", "started": True, "skipped": None}, "no sync id, connection id or person"
    assert sync_start.created == [("s1", "initial", "conn-a")], "the connection waits on the row"
    assert sync_start.enqueued == [("run_people_sync", "livestorm_sync:sync-new", "sync-new", "s1")], "ids only in the queue"


def test_a_cached_session_with_a_roster_starts_nothing_on_fetch(sync_start, monkeypatch):
    assert _sync_on_fetch(sync_start, monkeypatch, roster=True) == {"status": "none", "started": False, "skipped": None}
    done = _sync_on_fetch(sync_start, monkeypatch, roster=True, sync=_prior_sync("completed", 600))
    assert done == {"status": "completed", "started": False, "skipped": None}
    assert sync_start.created == [] and sync_start.enqueued == []


def test_a_fresh_fetch_reads_the_people_again(sync_start, monkeypatch):
    """The user refreshed the session: its people are read again too, as a
    manual sync, so the cooldown and the daily cap apply."""
    result = _sync_on_fetch(sync_start, monkeypatch, roster=True, sync=_prior_sync("completed", 600), fresh=True)
    assert result == {"status": "pending", "started": True, "skipped": None}
    assert sync_start.created == [("s1", "manual", "conn-a")]

    sync_start.created.clear()
    recent = _sync_on_fetch(sync_start, monkeypatch, roster=True, sync=_prior_sync("completed", 5), fresh=True)
    assert recent == {"status": "completed", "started": False, "skipped": "cooldown"}
    sync_start.used = 1000
    capped = _sync_on_fetch(sync_start, monkeypatch, roster=True, sync=_prior_sync("completed", 600), fresh=True)
    assert capped == {"status": "completed", "started": False, "skipped": "daily_cap"}, "the cap's numbers stay server-side"
    assert sync_start.created == []


@pytest.mark.parametrize(
    ("sync", "overrides", "skipped"),
    [
        (_prior_sync("error", 30), {}, "error_backoff"),  # every start on fetch is an automatic one
        (_prior_sync("running", 1), {}, None),  # joined, never duplicated
        # An admin of org-b reads every org, but their token is never used on org-a's session.
        (None, {"scope": ALL_ORGS, "caller_org_id": "org-b", "job_connection_id": "conn-admin"}, "other_org"),
    ],
    ids=["error-backoff", "active-sync", "admin-of-another-org"],
)
def test_the_guards_stand_in_front_of_the_start_on_fetch(sync_start, monkeypatch, sync, overrides, skipped):
    result = _sync_on_fetch(sync_start, monkeypatch, roster=False, sync=sync, **overrides)
    assert (result["started"], result["skipped"]) == (False, skipped)
    assert sync_start.created == [] and sync_start.enqueued == []


def test_a_start_on_fetch_outside_the_callers_scope_starts_nothing(sync_start, monkeypatch):
    with pytest.raises(people_sync.RosterSessionNotFound):
        _sync_on_fetch(sync_start, monkeypatch, roster=False, scope="org-b")
    assert sync_start.created == [] and sync_start.enqueued == []


def _base_fetch(monkeypatch, *, cached=True):
    """`fetch_session_base_data` for a session of org-a, with Livestorm, the
    cache and the classic serializer faked. Returns the fetch's keyword arguments."""
    row = {"session_id": "s1", "organization_id": "org-a", "session_payload": {}, "chat_payload": {}, "questions_payload": {}}
    monkeypatch.setattr(api_logic, "fetch_cached_session", lambda key, sid, *, organization_id: dict(row))
    monkeypatch.setattr(api_logic, "fetch_session_details", lambda key, sid: {"data": {"id": sid}})
    monkeypatch.setattr(api_logic, "fetch_chat_and_questions_bundle", lambda key, sid: {"chat_payload": {}, "questions_payload": {}})
    monkeypatch.setattr(api_logic, "_fetch_event_payload_safe", lambda key, event_id: None)
    monkeypatch.setattr(api_logic, "upsert_cached_session", lambda key, sid, **fields: None)
    monkeypatch.setattr(api_logic, "_serialize_cached_session", lambda sid, cached_row: {"sessionId": sid, "classic": True})
    return {"force_refresh": not cached, "job_connection_id": "conn-a", "read_scope": "org-a", "organization_id": "org-a"}


@pytest.mark.parametrize("cached", [True, False], ids=["cache-hit", "fresh-fetch"])
def test_a_base_fetch_starts_the_people_sync_and_says_so(sync_start, monkeypatch, cached):
    monkeypatch.setattr(people_sync, "fetch_roster_status", lambda sid, scope: {"session_id": sid, "roster": None, "sync": None})
    response = api_logic.fetch_session_base_data("Bearer t", "s1", **_base_fetch(monkeypatch, cached=cached))
    assert response == {"sessionId": "s1", "classic": True, "rosterSync": {"status": "pending", "started": True, "skipped": None}}
    assert sync_start.created == [("s1", "initial", "conn-a")]
    assert sync_start.enqueued == [("run_people_sync", "livestorm_sync:sync-new", "sync-new", "s1")]
    assert "conn-a" not in json.dumps(response) and "conn-a" not in json.dumps(sync_start.enqueued)


@pytest.mark.parametrize("failure", ["database", "queue", "row"])
def test_a_failed_start_never_fails_the_base_fetch(sync_start, monkeypatch, caplog, failure):
    """The session's data is saved and returned whatever happens to the
    people sync: Postgres or Redis failing here reads `unavailable`."""
    def broken(*args, **kwargs):
        raise RuntimeError("connection to postgresql://stormiq:pg-secret@db failed for conn-a")

    monkeypatch.setattr(people_sync, "fetch_roster_status", lambda sid, scope: {"session_id": sid, "roster": None, "sync": None})
    if failure == "database":
        monkeypatch.setattr(people_sync, "fetch_roster_status", broken)
    elif failure == "queue":
        monkeypatch.setattr(people_sync, "enqueue_unique_job_sync", lambda *args: None)  # Redis unreachable
    else:
        monkeypatch.setattr(people_sync, "create_livestorm_sync", broken)

    with caplog.at_level(logging.DEBUG):
        response = api_logic.fetch_session_base_data("Bearer t", "s1", **_base_fetch(monkeypatch))
    assert response == {"sessionId": "s1", "classic": True, "rosterSync": {"status": "unavailable", "started": False, "skipped": None}}
    assert "session_id=s1" in caplog.text
    assert "pg-secret" not in caplog.text and "conn-a" not in caplog.text, "the log names the error's class, never its message"
    if failure == "queue":
        # The row was created, then closed at once: its connection id never waits for the sweeper.
        assert sync_start.updates and sync_start.updates[0][1]["status"] == "error"


def test_the_fetch_routes_pass_the_callers_connection_and_local_dev_flag(monkeypatch):
    calls: dict = {}
    monkeypatch.setattr(routes_sessions, "fetch_session_base_data", lambda **kwargs: calls.setdefault("base", kwargs) and {})
    monkeypatch.setattr(routes_sessions, "fetch_all_session_data", lambda **kwargs: calls.setdefault("all", kwargs) and {})
    caller = auth.Caller(kind="user", org_id="org-a", connection={"connection_id": "conn-a", "access_token": "t"})
    request = routes_models.FetchSessionRequest()
    routes_sessions.fetch_session_base_workspace("s1", request, caller=caller)
    routes_sessions.fetch_session_workspace("s1", request, caller=caller)
    for name in ("base", "all"):
        assert (calls[name]["job_connection_id"], calls[name]["local_dev"]) == ("conn-a", False), name
        assert (calls[name]["read_scope"], calls[name]["organization_id"]) == ("org-a", "org-a")


def test_the_records_sync_state_is_the_stored_one_without_ids():
    now = datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc)
    assert people_sync.roster_live_state(None, None, now=now) == {
        "sync": {"status": "none", "active": False, "reason": None, "error": None, "updatedAt": None},
        "refreshDue": False,
    }
    sync = {
        "sync_id": "sync-1", "kind": "people", "reason": "manual", "status": "pending", "error": None,
        "pages_fetched": 3, "updated_at": now - timedelta(minutes=2),
    }
    roster = {"roster_version": 2, "refresh_due_at": now - timedelta(seconds=1)}
    state = people_sync.roster_live_state(roster, sync, now=now)
    assert state == {
        "sync": {"status": "pending", "active": True, "reason": "manual", "error": None, "updatedAt": "2026-03-09T11:58:00+00:00"},
        "refreshDue": True,
    }
    assert "sync-1" not in json.dumps(state)
    for status, active in (("running", True), ("completed", False), ("error", False), ("deferred", False)):
        assert people_sync.roster_live_state(roster, {**sync, "status": status}, now=now)["sync"]["active"] is active
    failed = people_sync.roster_live_state(roster, {**sync, "status": "error", "error": "Livestorm took too long."}, now=now)
    assert failed["sync"]["error"] == "Livestorm took too long."
    # Due exactly now is due; a refresh that is still ahead, or was never planned, is not.
    assert people_sync.roster_live_state({"refresh_due_at": now}, None, now=now)["refreshDue"] is True
    assert people_sync.roster_live_state({"refresh_due_at": now + timedelta(days=6)}, None, now=now)["refreshDue"] is False
    assert people_sync.roster_live_state({"refresh_due_at": None}, None, now=now)["refreshDue"] is False


def test_a_closed_sync_is_not_reopened_by_a_late_reconcile(test_database):
    db.upsert_cached_session("key-a", "sess-a", organization_id="org-a")
    sync_id = db.create_livestorm_sync("sess-a", reason="initial", connection_id="conn-a")["sync_id"]
    db.update_livestorm_sync(sync_id, status="completed", pages_fetched=3)
    assert db.update_livestorm_sync(sync_id, status="error", error="lost", only_from=("pending", "running")) is False
    assert _sync_row(sync_id)["status"] == "completed"
    assert db.touch_livestorm_sync(sync_id) is False, "only a running sync is kept fresh"


# ── "Add" chains the session analysis after the transcript ──


@pytest.mark.parametrize("marked", [True, False], ids=["added", "not-added"])
def test_the_transcript_job_queues_the_analysis_only_when_add_asked(monkeypatch, marked):
    queued = []

    async def fake_enqueue(pool, function_name, job_id, *args):
        queued.append((function_name, job_id, *args))
        return {"jobId": job_id, "jobStatus": "pending", "enqueued": True}

    monkeypatch.setattr(worker, "transcript_job_then_analyse", lambda job_id: marked)
    monkeypatch.setattr(worker, "enqueue_unique_job", fake_enqueue)
    asyncio.run(worker._chain_analysis_after({"redis": object()}, "job-1", "sess-1"))
    assert queued == ([("run_session_analysis_job", api_logic.session_analysis_job_id("sess-1"), "sess-1")] if marked else [])


def test_a_failed_chain_never_fails_the_transcript_job(monkeypatch):
    def broken(job_id):
        raise RuntimeError("database down")

    monkeypatch.setattr(worker, "transcript_job_then_analyse", broken)
    asyncio.run(worker._chain_analysis_after({"redis": object()}, "job-1", "sess-1"))
