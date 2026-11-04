"""Backend performance changes keep their results identical.

Every optimisation here replaced slower code; each test pins the new code to
the old behaviour (the old algorithm is kept below as an oracle where it was
removed) and checks the saving actually happens. Real Postgres / Redis tests
need TEST_DATABASE_URL / TEST_REDIS_URL and skip otherwise.
"""

from __future__ import annotations

import concurrent.futures
import logging
import math
import random
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import pandas as pd
import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

import app as app_module
from livestorm_app.routes import spa as routes_spa
from livestorm_app import api_logic, db, queue
from livestorm_app.analytics import cross_source
from livestorm_app.analytics import transcript as transcript_analytics


# ── Entity context lookup ───────────────────────────────────────────────────


def _reference_entity_context(source: pd.DataFrame, entity_text: str, entity_start: Optional[float]) -> Dict[str, Any]:
    """The original O(mentions × sentences) scan, verbatim in behaviour."""
    if source.empty:
        return {}
    best_row, best_score = None, None
    entity_text_lower = entity_text.lower()
    for _, candidate in source.iterrows():
        text = str(candidate.get("sentence") or candidate.get("text") or "").strip()
        start = transcript_analytics._coerce_seconds(candidate.get("start_seconds"))
        end = transcript_analytics._coerce_seconds(candidate.get("end_seconds"))
        contains_entity = bool(text) and entity_text_lower in text.lower()
        contains_time = (
            entity_start is not None and start is not None and end is not None
            and float(start) <= float(entity_start) <= float(end)
        )
        if contains_entity and contains_time:
            return candidate.to_dict()
        distance = abs(float(start) - float(entity_start)) if (start is not None and entity_start is not None) else float("inf")
        score = (0 if contains_entity else 1, 0 if contains_time else 1, distance)
        if best_score is None or score < best_score:
            best_score, best_row = score, candidate
    return best_row.to_dict() if best_row is not None else {}


def _random_sentences(rng: random.Random, missing_starts: bool = False) -> pd.DataFrame:
    vocabulary = ["alpha", "beta", "Livestorm", "Paris", "quarter", "revenue", "Q3", "growth", ""]
    rows, t = [], 0.0
    for _ in range(rng.randint(0, 40)):
        start = None if missing_starts and rng.random() < 0.05 else round(t, 2)
        end = None if rng.random() < 0.05 else round(t + rng.uniform(0.5, 6.0), 2)
        words = " ".join(rng.choice(vocabulary) for _ in range(rng.randint(0, 8)))
        rows.append({"sentence": words, "start_seconds": start, "end_seconds": end, "speaker": rng.randint(0, 2)})
        t += rng.uniform(-1.0, 5.0)  # sentences may overlap or go back in time
    return pd.DataFrame(rows, columns=["sentence", "start_seconds", "end_seconds", "speaker"])


def _same(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    def norm(value):
        return None if value is None or (isinstance(value, float) and math.isnan(value)) else value
    return {k: norm(v) for k, v in a.items()} == {k: norm(v) for k, v in b.items()}


def test_entity_context_finder_matches_the_original_scan():
    rng = random.Random(7)
    checked = 0
    for _ in range(300):
        source = _random_sentences(rng)
        finder = transcript_analytics._EntityContextFinder(source)
        for _ in range(8):
            entity = rng.choice(["Livestorm", "paris", "Q3", "nowhere-to-be-found", "a"])
            start = None if rng.random() < 0.1 else rng.uniform(-5, 150)
            assert _same(finder.find(entity, start), _reference_entity_context(source, entity, start)), (entity, start)
            checked += 1
    assert checked == 2400


def test_sentence_without_a_start_time_never_wins_on_proximity():
    # Deliberate difference from the original scan: there, a sentence whose
    # start was missing scored a NaN distance, and NaN comparisons made the
    # first such sentence unbeatable whatever the times (order-dependent).
    # Gladia sentences always carry times, so real workspaces are unaffected.
    source = pd.DataFrame([
        {"sentence": "Paris intro", "start_seconds": None, "end_seconds": 5.0, "speaker": 0},
        {"sentence": "Paris pricing", "start_seconds": 98.0, "end_seconds": 99.0, "speaker": 1},
    ])
    assert transcript_analytics._EntityContextFinder(source).find("Paris", 100.0)["sentence"] == "Paris pricing"
    assert _reference_entity_context(source, "Paris", 100.0)["sentence"] == "Paris intro"


def test_cross_source_reuses_insights_it_is_given(monkeypatch):
    chat = pd.DataFrame({"created_at": [1, 2], "text_content": ["a", "b"], "author_id": ["p1", "p2"]})
    questions = pd.DataFrame({"asked_at": [1], "question": ["q"]})
    transcript = {"segments": [{"start": 0.0, "end": 5.0, "text": "hello there", "speaker": 0}]}
    insights = transcript_analytics.build_transcript_insights(transcript)
    expected = cross_source.build_cross_source_insights(chat, questions, transcript)

    def must_not_rebuild(*args, **kwargs):
        raise AssertionError("transcript insights were built a second time")

    monkeypatch.setattr(cross_source, "build_transcript_insights", must_not_rebuild)
    reused = cross_source.build_cross_source_insights(chat, questions, transcript, transcript_insights=insights)
    for key in expected:
        pd.testing.assert_frame_equal(reused[key], expected[key])


def test_a_workspace_read_builds_transcript_insights_once(monkeypatch):
    calls: list = []
    real = transcript_analytics.build_transcript_insights

    def counting(payload):
        calls.append(1)
        return real(payload)

    monkeypatch.setattr(cross_source, "build_transcript_insights", counting)
    monkeypatch.setattr(api_logic, "build_transcript_insights", counting)
    monkeypatch.setattr(api_logic, "_insights_cache", api_logic.OrderedDict())
    row = {
        "chat_payload": {"data": [{"id": "m1", "type": "chat_messages", "attributes": {"text_content": "hi", "created_at": 1, "author_id": "p1"}}]},
        "questions_payload": {"data": [{"id": "q1", "type": "questions", "attributes": {"question": "why?", "created_at": 2}}]},
        "transcript_payload": {"segments": [{"start": 0.0, "end": 5.0, "text": "hello there", "speaker": 0}]},
        "updated_at": datetime(2026, 11, 4, tzinfo=timezone.utc),
    }
    api_logic._serialize_cached_session("s1", row)
    assert len(calls) == 1


def test_polls_never_load_the_full_row_while_a_job_runs(monkeypatch):
    def full_row(*args, **kwargs):
        raise AssertionError("a poll loaded the full session row")

    monkeypatch.setattr(api_logic, "fetch_cached_session", full_row)
    monkeypatch.setattr(api_logic, "fetch_session_fields", lambda sid, fields, *, organization_id: {f: None for f in fields} | {"has_transcript": False})
    monkeypatch.setattr(api_logic, "get_transcript_job_for_session", lambda sid: {"job_id": "j1", "status": "running", "progress": None})
    monkeypatch.setattr(api_logic, "read_job_status_sync", lambda job_id: {"jobStatus": "running"})
    monkeypatch.setattr(api_logic, "read_progress", lambda *a, **k: None)
    assert api_logic.get_transcript_job_status_data("", "s1", organization_id="org-a")["jobStatus"] == "running"
    assert api_logic.get_deep_analysis_job_data("s1", "", "English", organization_id="org-a")["jobStatus"] == "running"


# ── Insights cache ──────────────────────────────────────────────────────────


@pytest.fixture
def insights_builds(monkeypatch):
    builds: list = []
    monkeypatch.setattr(api_logic, "build_transcript_insights", lambda payload: builds.append(payload) or {"n": len(builds)})
    monkeypatch.setattr(api_logic, "_insights_cache", api_logic.OrderedDict())
    return builds


def _row(updated_at, transcript=True):
    return {"transcript_payload": {"text": "x"} if transcript else None, "updated_at": updated_at}


def test_insights_are_built_once_per_session_version(insights_builds):
    t0 = datetime(2026, 11, 4, tzinfo=timezone.utc)
    first = api_logic._cached_transcript_insights("s1", _row(t0))
    assert api_logic._cached_transcript_insights("s1", _row(t0)) is first
    assert len(insights_builds) == 1
    api_logic._cached_transcript_insights("s1", _row(t0 + timedelta(seconds=1)))  # any write bumps updated_at
    assert len(insights_builds) == 2
    assert [key for key in api_logic._insights_cache if key[0] == "s1"] == [("s1", t0 + timedelta(seconds=1))], "older versions are dropped"


def test_insights_cache_is_bounded(insights_builds):
    t0 = datetime(2026, 11, 4, tzinfo=timezone.utc)
    for i in range(api_logic.TRANSCRIPT_INSIGHTS_CACHE_SIZE + 3):
        api_logic._cached_transcript_insights(f"s{i}", _row(t0))
    assert len(api_logic._insights_cache) == api_logic.TRANSCRIPT_INSIGHTS_CACHE_SIZE
    assert api_logic._cached_transcript_insights("s0", _row(None, transcript=False)) == {}


# ── Slimmer workspace ───────────────────────────────────────────────────────


def test_workspace_omits_raw_payloads_but_keeps_subtitles(monkeypatch):
    monkeypatch.setattr(api_logic, "_insights_cache", api_logic.OrderedDict())
    transcript = {
        "segments": [{"start": 0.0, "end": 4.0, "text": "hello world", "speaker": 0}],
        "subtitles": [{"format": "srt", "subtitles": "1\n00:00:00,000 --> 00:00:04,000\nhello world"}],
        "language": "en",
        "duration_seconds": 4.0,
        "result": {"huge": "raw gladia result"},
    }
    row = {
        "session_payload": {"data": {"attributes": {"name": "S"}}, "included": [{"type": "people", "id": "p1", "attributes": {}}]},
        "chat_payload": {"data": []}, "questions_payload": {"data": []}, "transcript_payload": transcript,
        "updated_at": datetime(2026, 11, 4, tzinfo=timezone.utc),
    }
    workspace = api_logic._serialize_cached_session("s1", row)
    assert workspace["payloads"] == {"transcript": {"subtitles": transcript["subtitles"], "language": "en", "durationSeconds": 4.0}}
    assert set(workspace["text"]) == {"transcriptDisplay"}
    assert "transcriptKeyMoments" not in workspace["tables"] and "transcriptPace" not in workspace["tables"]
    assert api_logic._serialize_cached_session("s2", {**row, "transcript_payload": None})["payloads"] == {"transcript": None}


def test_finished_ai_job_poll_can_return_only_the_outputs(monkeypatch):
    rows = {"s1": {"analysis_bundle": {"English": "# Overall"}, "analysis_md": "# Overall", "smart_recap_bundle": {"hype": "# Hype"}}}

    def fields(sid, wanted, *, organization_id):
        if organization_id != "org-a" or sid not in rows:
            return None
        return {field: rows[sid].get(field) for field in wanted}

    def full_row(*args, **kwargs):
        raise AssertionError("an outputs-only poll loaded the full session row")

    monkeypatch.setattr(api_logic, "fetch_session_fields", fields)
    monkeypatch.setattr(api_logic, "fetch_cached_session", full_row)
    response = api_logic.get_overall_analysis_job_data("s1", "", "English", organization_id="org-a", outputs_only=True)
    assert response == {
        "sessionId": "s1",
        "outputsOnly": True,
        "outputs": {
            "analysisBundle": {"English": "# Overall"},
            "deepAnalysisBundle": {},
            "contentRepurposeBundle": {},
            "smartRecapBundle": {"hype": "# Hype"},
        },
    }
    with pytest.raises(api_logic.SessionNotFoundError):
        api_logic.get_overall_analysis_job_data("s1", "", "English", organization_id="org-b", outputs_only=True)


def test_transcript_download_needs_a_visible_transcript(monkeypatch):
    rows = {"s1": {"transcript_payload": {"text": "full"}}, "s2": {"transcript_payload": None}}
    monkeypatch.setattr(api_logic, "fetch_session_fields", lambda sid, fields, *, organization_id: rows.get(sid))
    assert api_logic.get_transcript_download("s1", organization_id="org-a") == {"text": "full"}
    for missing in ("s2", "other-org"):
        with pytest.raises(api_logic.SessionNotFoundError):
            api_logic.get_transcript_download(missing, organization_id="org-a")


# ── Postgres: light reads ───────────────────────────────────────────────────


def _has_content_oracle(bundle: Any) -> bool:
    """The Python rule the card list used before the flags moved into SQL."""
    if not isinstance(bundle, dict):
        return False
    for value in bundle.values():
        if isinstance(value, str) and value.strip():
            return True
        if isinstance(value, dict) and any(str(inner or "").strip() for inner in value.values()):
            return True
    return False


BUNDLE_SHAPES = [
    None, {}, [], "text", 5,
    {"English": ""}, {"English": "   \n\t"}, {"English": "# Analysis"}, {"English": None}, {"English": 3},
    {"English": "", "French": "# Analyse"},
    {"English": {}}, {"English": {"summary": ""}}, {"English": {"summary": "  "}}, {"English": {"summary": "S"}},
    {"English": {"summary": None, "blog": 0}}, {"English": {"blog": 1}}, {"English": {"blog": False}},
    {"English": {"blog": True}}, {"English": {"blog": []}}, {"English": {"blog": ["x"]}},
    {"English": {"blog": {}}}, {"English": {"blog": {"a": 1}}}, {"English": {"blog": 0.0}}, {"English": {"blog": 0.5}},
    {"English": ["not", "an", "object"]},
]


def test_card_flags_computed_in_sql_match_the_python_rule(test_database):
    with db.get_db_connection() as conn:
        for i, bundle in enumerate(BUNDLE_SHAPES):
            conn.execute(
                """INSERT INTO session_cache (account_key_hash, session_id, organization_id, analysis_bundle,
                                              deep_analysis_bundle, smart_recap_bundle, content_repurpose_bundle)
                   VALUES ('h', %s, 'org-a', %s, %s, %s, %s)""",
                (f"s{i}", *(Jsonb(bundle) if bundle is not None else None for _ in range(4))),
            )
        conn.commit()
    rows = {row["session_id"]: row for row in db.list_workspace_sessions("org-a")}
    for i, bundle in enumerate(BUNDLE_SHAPES):
        expected = _has_content_oracle(bundle)
        for flag in ("has_overall_analysis", "has_deep_analysis", "has_smart_recap", "has_content_repurposing"):
            assert rows[f"s{i}"][flag] is expected, (bundle, flag)


def test_card_list_reads_only_what_a_card_shows(test_database):
    session_payload = {"data": {"id": "s1", "attributes": {"name": "Demo", "duration": 3600, "event_id": "e1"}},
                       "included": [{"type": "people", "id": f"p{i}"} for i in range(500)]}
    db.upsert_cached_session(
        "k", "s1", organization_id="org-a", session_payload=session_payload,
        event_payload={"data": {"attributes": {"title": "Webinar"}}}, transcript_payload={"text": "t"},
        analysis_bundle={"English": "# A"},
    )
    db.upsert_cached_session("k", "s2", organization_id="org-a", session_payload={"data": ["not", "an", "object"]})
    db.upsert_cached_session("k", "s3", organization_id="org-b", session_payload=session_payload)
    rows = {row["session_id"]: row for row in db.list_workspace_sessions("org-a")}
    assert set(rows) == {"s1", "s2"}
    assert rows["s1"]["session_attributes"] == session_payload["data"]["attributes"]
    assert rows["s1"]["event_title"] == "Webinar"
    assert rows["s1"]["has_transcript"] and rows["s1"]["has_overall_analysis"] and not rows["s1"]["has_deep_analysis"]
    assert rows["s2"]["session_attributes"] is None and rows["s2"]["event_title"] is None
    assert not {"session_payload", "event_payload", "analysis_bundle", "transcript_payload"} & set(rows["s1"])

    cards = {c["sessionId"]: c for c in api_logic.list_workspace_sessions_data("org-a")["sessions"]}
    assert cards["s1"]["eventTitle"] == "Webinar" and cards["s1"]["durationLabel"] and cards["s1"]["hasOverallAnalysis"]
    assert cards["s2"]["eventTitle"] == "Untitled event" and cards["s2"]["sessionName"] == "Untitled session"


def test_light_reads_follow_the_org_scope(test_database):
    db.upsert_cached_session("k", "s1", organization_id="org-a", analysis_bundle={"English": "# A"})
    assert db.fetch_session_fields("s1", ["analysis_bundle", "has_transcript"], organization_id="org-a") == {
        "analysis_bundle": {"English": "# A"}, "has_transcript": False,
    }
    assert db.fetch_session_fields("s1", ["has_transcript"], organization_id=db.ALL_ORGS) == {"has_transcript": False}
    assert db.fetch_session_fields("s1", ["has_transcript"], organization_id="org-b") is None
    assert db.fetch_session_fields("s1", ["has_transcript"], organization_id="") is None


def test_light_reads_only_accept_known_fields():
    with pytest.raises(ValueError):
        db.fetch_session_fields("s1", ["has_transcript", "1; DROP TABLE session_cache"], organization_id="org-a")


# ── Postgres: connection pool ───────────────────────────────────────────────


def test_requests_reuse_pooled_connections(test_database):
    # Counted on the server: each Postgres connection is its own backend process.
    backends = set()
    for _ in range(20):
        with db.get_db_connection() as conn:
            backends.add(conn.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"])
    assert len(backends) <= 2, f"{len(backends)} connections opened for 20 sequential queries"


def test_a_failed_transaction_does_not_poison_the_pool(test_database):
    with pytest.raises(psycopg.errors.UndefinedTable):
        with db.get_db_connection() as conn:
            conn.execute("SELECT * FROM no_such_table")
    with db.get_db_connection() as conn:
        assert conn.execute("SELECT 1 AS one").fetchone()["one"] == 1


# ── Settings cache ──────────────────────────────────────────────────────────


@pytest.fixture
def settings_db(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://settings-test@localhost/none")
    monkeypatch.setattr(db, "_settings_cache", {})
    state = {"reads": 0, "value": "anthropic", "fail": False}

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, *args):
            if state["fail"]:
                raise psycopg.OperationalError("connection lost")
            state["reads"] += 1

        def fetchone(self):
            return {"value": state["value"]}

    class Connection:
        def cursor(self):
            return Cursor()

    @contextmanager
    def fake_connection():
        yield Connection()

    monkeypatch.setattr(db, "get_db_connection", fake_connection)
    return state


def test_settings_are_read_once_per_minute(settings_db, monkeypatch):
    assert db.get_setting("llm_provider") == "anthropic"
    assert db.get_setting("llm_provider") == "anthropic"
    assert settings_db["reads"] == 1
    now = db.time.monotonic()
    monkeypatch.setattr(db.time, "monotonic", lambda: now + db.SETTINGS_CACHE_TTL_SECONDS + 1)
    settings_db["value"] = "openai"
    assert db.get_setting("llm_provider") == "openai"


def test_a_database_blip_keeps_the_last_known_setting(settings_db, monkeypatch):
    assert db.get_setting("llm_provider", "default") == "anthropic"
    now = db.time.monotonic()
    monkeypatch.setattr(db.time, "monotonic", lambda: now + db.SETTINGS_CACHE_TTL_SECONDS + 1)
    settings_db["fail"] = True
    assert db.get_setting("llm_provider", "default") == "anthropic", "a failed read must not switch the provider"


# ── Redis: one connection per process ───────────────────────────────────────


def test_queue_calls_share_one_redis_pool(test_redis, monkeypatch):
    created: list = []
    original = queue.create_pool
    monkeypatch.setattr(queue, "create_pool", lambda *a, **k: created.append(1) or original(*a, **k))
    queue.read_job_status_sync("warm-up")  # may (re)create the pool for this test's REDIS_URL
    created.clear()
    for i in range(10):
        assert queue.read_job_status_sync(f"job-{i}") == {"jobStatus": "not_found"}
    assert queue.enqueue_unique_job_sync("test_job", "kind:s1:English")["enqueued"] is True
    assert created == [], "status reads and enqueues must reuse the process's pool"


def test_a_redis_outage_fails_fast_instead_of_queueing_callers(monkeypatch):
    # conftest points REDIS_URL at a closed port. Callers used to wait in
    # line behind one 5-retry connection attempt each (5 s, 10 s, ... 20 s).
    monkeypatch.setattr(queue, "_bridge", queue._QueueBridge())
    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(6) as pool:
        results = list(pool.map(lambda i: queue.read_job_status_sync(f"job-{i}"), range(6)))
    assert results == [{"jobStatus": "unknown"}] * 6
    assert queue.enqueue_unique_job_sync("test_job", "kind:s1:English") is None
    assert time.monotonic() - started < 3, "callers must not queue behind connection retries"


def test_the_bridge_reconnects_after_the_cooldown(monkeypatch):
    bridge = queue._QueueBridge()
    monkeypatch.setattr(queue, "_bridge", bridge)
    attempts: list = []

    class FakePool:
        async def aclose(self):
            pass

    async def flaky_create_pool(settings):
        attempts.append(settings.conn_retries)
        if len(attempts) == 1:
            raise ConnectionRefusedError("redis down")
        return FakePool()

    async def ping(pool):
        return "ok"

    monkeypatch.setattr(queue, "create_pool", flaky_create_pool)
    with pytest.raises(ConnectionRefusedError):
        bridge.run(ping)
    with pytest.raises(ConnectionError):  # inside the cooldown: no new attempt
        bridge.run(ping)
    assert attempts == [0], "one attempt, no arq retries"
    later = time.monotonic() + queue.REDIS_DOWN_COOLDOWN_SECONDS + 1
    monkeypatch.setattr(queue.time, "monotonic", lambda: later)
    assert bridge.run(ping) == "ok"
    assert len(attempts) == 2
    bridge.close()


# ── HTTP: static assets and timing log ──────────────────────────────────────


def test_hashed_assets_are_cached_for_a_year(tmp_path):
    (tmp_path / "index-abc123.js").write_text("console.log(1)")
    asset_app = FastAPI()
    asset_app.mount("/assets", routes_spa.ImmutableStaticFiles(directory=tmp_path), name="assets")
    response = TestClient(asset_app).get("/assets/index-abc123.js")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_api_requests_are_timed_by_route_template(caplog):
    client = TestClient(app_module.app)
    with caplog.at_level(logging.INFO, logger="stormiq.timing"):
        client.get("/api/health")
        client.get("/api/sessions/sess-secret-id")
    lines = [record.getMessage() for record in caplog.records if record.name == "stormiq.timing"]
    assert len(lines) == 1, "health checks are not logged"
    assert "route=/api/sessions/{session_id} status=401" in lines[0]
    assert "sess-secret-id" not in lines[0]
    assert " ms=" in lines[0] and " bytes=" in lines[0]
