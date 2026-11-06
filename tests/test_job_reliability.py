"""Job cost and reliability: dedupe, retries, timeouts, no secrets in the queue.

Real Redis tests need TEST_REDIS_URL, real Postgres tests TEST_DATABASE_URL
(both skip otherwise; CI provides both). Everything else uses fakes.
"""

from __future__ import annotations

import asyncio
import copy
import inspect
import json
import pickle
import subprocess
import threading
import time
import urllib.error
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests
from urllib3._collections import HTTPHeaderDict

from livestorm_app.routes import models as routes_models
from livestorm_app import api_logic, auth, llm_client, queue, worker
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
    monkeypatch.setattr(api_logic, "fetch_session_base_data", lambda *a, **k: {"sessionId": "s1", "base": True})
    monkeypatch.setattr(api_logic, "fetch_session_transcript_data", lambda *a, **k: {"jobId": "j1", "jobStatus": "pending"})
    response = api_logic.fetch_all_session_data("key", "s1", job_connection_id="c", read_scope="org-a")
    assert response == {"sessionId": "s1", "base": True, "transcriptJob": {"jobId": "j1", "jobStatus": "pending"}}


# ── Worker ─────────────────────────────────────────────────────────────────


def test_every_job_kind_has_its_own_timeout_and_no_retries():
    timeouts = {
        getattr(f, "name", getattr(f, "__name__", "")): getattr(f, "timeout_s", None)
        for f in worker.WorkerSettings.functions
    }
    assert timeouts["run_transcription"] == 120 * 60
    assert timeouts["run_cover_image_job"] == 5 * 60
    for name in ("run_overall_analysis_job", "run_deep_analysis_job", "run_smart_recap_job", "run_content_repurposing_job"):
        assert timeouts[name] == 15 * 60
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


def _raise(*args, **kwargs):
    raise RuntimeError("boom")


@pytest.mark.parametrize("failure", ["no cached row", "read fails", "roster fails", "nobody on stage"])
def test_an_unknown_stage_keeps_the_default_speaker_cap(transcription, monkeypatch, failure):
    if failure == "read fails":
        monkeypatch.setattr(worker, "fetch_session_fields", _raise)
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


# ── LLM retries ────────────────────────────────────────────────────────────


def _response(status: int, retry_after: str | None = None) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response._content = b'{"ok": true}'
    if retry_after is not None:
        response.headers["Retry-After"] = retry_after
    return response


@pytest.fixture
def llm_http(monkeypatch):
    script: list = []
    sleeps: list = []

    def fake_post(*args, **kwargs):
        step = script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    monkeypatch.setattr(llm_client.time, "sleep", sleeps.append)
    return script, sleeps


def _post():
    return llm_client._post_with_retry("https://llm.example/v1", {}, {}, provider="anthropic")


def test_llm_retries_overload_and_server_errors(llm_http):
    script, sleeps = llm_http
    script.extend([_response(529), _response(503), requests.ConnectionError("reset"), _response(200)])
    assert _post().status_code == 200
    assert len(sleeps) == 3


def test_llm_honours_retry_after_with_a_cap(llm_http):
    script, sleeps = llm_http
    script.extend([_response(429, "7"), _response(429, "3600"), _response(200)])
    _post()
    assert sleeps == [7.0, llm_client.LLM_BACKOFF_MAX_SECONDS]


@pytest.mark.parametrize("failure", [_response(400), requests.ReadTimeout("slow answer")], ids=["400", "read-timeout"])
def test_llm_does_not_repeat_calls_that_would_fail_again_or_double_bill(llm_http, failure):
    script, sleeps = llm_http
    script.extend([failure, _response(200)])
    with pytest.raises((requests.HTTPError, requests.ReadTimeout)):
        _post()
    assert sleeps == [] and len(script) == 1


def test_llm_gives_up_after_the_last_attempt(llm_http):
    script, _sleeps = llm_http
    script.extend([_response(503)] * llm_client.LLM_MAX_ATTEMPTS)
    with pytest.raises(requests.HTTPError):
        _post()
    assert script == []


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
