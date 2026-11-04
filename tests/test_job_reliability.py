"""Job cost and reliability: dedupe, retries, timeouts, no secrets in the queue.

Real Redis tests need TEST_REDIS_URL, real Postgres tests TEST_DATABASE_URL
(both skip otherwise; CI provides both). Everything else uses fakes.
"""

from __future__ import annotations

import asyncio
import threading
import time
import urllib.error
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests
from urllib3._collections import HTTPHeaderDict

import app as app_module
from livestorm_app import api_logic, auth, llm_client, queue, services, worker
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


def _run_worker_once(*functions) -> None:
    """Process everything queued, then stop (arq burst mode)."""
    from arq.worker import Worker, func

    async def _run():
        w = Worker(
            functions=[func(f, name="test_job") for f in functions],
            redis_settings=queue.get_redis_settings(),
            burst=True,
            handle_signals=False,
            max_tries=1,
            poll_delay=0.01,
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
        (app_module.AnalysisRequest, {"outputLanguage": "Klingon"}),
        (app_module.SmartRecapRequest, {"tone": "angry"}),
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
    monkeypatch.setattr(worker, "upsert_cached_session", lambda *a, **k: None)
    monkeypatch.setattr(
        "livestorm_app.auth.livestorm_auth_for_job",
        lambda connection_id: calls["auth"].append(connection_id) or "Bearer fresh-token",
    )
    return calls


def test_worker_resolves_livestorm_credentials_itself(transcription, monkeypatch):
    def fake_transcribe(gladia_key, sid, livestorm_api_key, on_progress):
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

    def slow_transcribe(gladia_key, sid, livestorm_api_key, on_progress):
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
    assert services.livestorm_get(url, headers={}).status_code == 200
    assert len(seen) == 2


def test_livestorm_get_does_not_retry_a_client_error(livestorm_stub):
    url, script, seen = livestorm_stub
    script.extend([404])
    assert services.livestorm_get(url, headers={}).status_code == 404
    assert len(seen) == 1


def test_livestorm_session_never_shares_cookies_between_users(livestorm_stub):
    url, _script, seen = livestorm_stub
    services.livestorm_get(url, headers={})
    services.livestorm_get(url, headers={})
    assert seen == [None, None]


def test_livestorm_retry_after_is_capped():
    retry = services._livestorm_http.get_adapter("https://api.livestorm.co").max_retries
    response = type("FakeResponse", (), {"headers": HTTPHeaderDict({"Retry-After": "3600"})})()
    assert retry.get_retry_after(response) == services.LIVESTORM_MAX_RETRY_AFTER_SECONDS


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
