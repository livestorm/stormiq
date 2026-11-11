"""Org scoping against a real Postgres.

Exercises the actual SQL behind `fetch_cached_session`, `fetch_cover_image`,
`list_workspace_sessions`, the upsert's first-writer-wins org stamp, the
per-org model spend (`llm_spend_since`), and the stored people
(`fetch_roster_status`, `list_session_people`, `get_org_people_attributes`).
Skipped unless TEST_DATABASE_URL points at a local database (see conftest).
"""

from datetime import datetime, timedelta, timezone

import pytest
import synthetic_session as synthetic

from livestorm_app import db
from livestorm_app.db import ALL_ORGS


def _seed() -> None:
    db.upsert_cached_session("key-a", "sess-a", session_payload={"name": "A"}, organization_id="org-a", cover_image_bytes=b"png-a")
    db.upsert_cached_session("key-b", "sess-b", session_payload={"name": "B"}, organization_id="org-b")


def test_fetch_is_scoped_by_org(test_database):
    _seed()
    assert db.fetch_cached_session("", "sess-a", organization_id="org-a")["session_payload"] == {"name": "A"}
    assert db.fetch_cached_session("", "sess-a", organization_id="org-b") is None
    assert db.fetch_cached_session("", "sess-a", organization_id="") is None
    assert db.fetch_cached_session("", "sess-a", organization_id=ALL_ORGS)["organization_id"] == "org-a"


def test_cover_image_is_scoped_by_org(test_database):
    _seed()
    assert db.fetch_cover_image("sess-a", organization_id="org-a")["bytes"] == b"png-a"
    assert db.fetch_cover_image("sess-a", organization_id="org-b") is None
    assert db.fetch_cover_image("sess-a", organization_id=ALL_ORGS)["bytes"] == b"png-a"


def test_workspace_list_is_scoped_by_org(test_database):
    _seed()
    assert [row["session_id"] for row in db.list_workspace_sessions("org-a")] == ["sess-a"]
    assert db.list_workspace_sessions("") == []


def test_organization_id_is_first_writer_wins(test_database):
    _seed()
    db.upsert_cached_session("key-b", "sess-a", organization_id="org-b", session_payload={"name": "A2"})
    assert db.fetch_cached_session("", "sess-a", organization_id=ALL_ORGS)["organization_id"] == "org-a"
    assert db.fetch_cached_session("", "sess-a", organization_id="org-b") is None


def test_legacy_row_without_org_is_claimed_by_the_first_org_to_stamp_it(test_database):
    db.upsert_cached_session("key-x", "sess-legacy", session_payload={"name": "L"})
    assert db.fetch_cached_session("", "sess-legacy", organization_id="org-a") is None
    db.upsert_cached_session("key-a", "sess-legacy", organization_id="org-a")
    assert db.fetch_cached_session("", "sess-legacy", organization_id="org-a")["session_payload"] == {"name": "L"}


# ── Model spend (llm_usage) ───────────────────────────────────────────────

_SINCE = datetime(2000, 1, 1, tzinfo=timezone.utc)


def _usage(org, cost, session="sess-a") -> None:
    db.insert_llm_usage({
        "organization_id": org, "session_id": session, "job_kind": "overall_analysis", "purpose": "main",
        "provider": "anthropic", "model_requested": "claude-haiku-4-5-20251001", "stop_reason": "end_turn",
        "outcome": "ok", "cost_usd_micros": cost,
    })


def test_usage_rows_carry_their_org_and_spend_is_per_org(test_database):
    _usage("org-a", 100)
    _usage("org-a", 200)
    _usage("org-a", None)  # a model without a price counts 0
    _usage("org-b", 1000, session="sess-b")
    _usage(None, 5000, session="sess-legacy")
    with db.get_db_connection() as conn:
        orgs = [row["organization_id"] for row in conn.execute("SELECT organization_id FROM llm_usage ORDER BY id").fetchall()]
    assert orgs == ["org-a", "org-a", "org-a", "org-b", None]
    assert db.llm_spend_since("org-a", _SINCE) == 300
    assert db.llm_spend_since("org-b", _SINCE) == 1000
    assert db.llm_spend_since("org-c", _SINCE) == 0

    with db.get_db_connection() as conn:
        conn.execute("UPDATE llm_usage SET created_at = NOW() - INTERVAL '2 days' WHERE cost_usd_micros = 200")
        conn.commit()
    assert db.llm_spend_since("org-a", datetime.now(timezone.utc) - timedelta(days=1)) == 100


def test_spend_never_sums_every_org(test_database):
    _usage("org-a", 100)
    for blank in ("", "   ", None):
        with pytest.raises(db.ScopeMatchesNothing):
            db.llm_spend_since(blank, _SINCE)
    with pytest.raises(ValueError, match="never ALL_ORGS"):
        db.llm_spend_since(ALL_ORGS, _SINCE)
    with pytest.raises(ValueError, match="timezone-aware"):
        db.llm_spend_since("org-a", datetime(2000, 1, 1))


# ── Stored people (session_rosters, session_people, livestorm_sync_jobs) ────


def _seed_people() -> None:
    _seed()
    db.upsert_cached_session("key-x", "sess-legacy", session_payload={"name": "L"})
    for session_id in ("sess-a", "sess-b"):
        rows = synthetic.person_rows(session_id)
        db.replace_session_roster(
            session_id, rows, source="paginated", complete=True, record_count=len(rows), reason="initial", refresh_due_at=None,
        )
    db.replace_session_roster(
        "sess-legacy", synthetic.person_rows("sess-legacy")[:2],
        source="session_include", complete=None, record_count=None, reason="backfill", refresh_due_at=None,
    )
    db.create_livestorm_sync("sess-a", reason="initial", connection_id="conn-of-org-a")


def test_roster_status_is_scoped_by_org(test_database):
    _seed_people()
    status = db.fetch_roster_status("sess-a", "org-a")
    assert status["session_id"] == "sess-a"
    assert status["roster"]["people_count"] == 10 and status["roster"]["source"] == "paginated"
    assert status["sync"]["status"] == "pending" and status["sync"]["reason"] == "initial"
    assert "connection_id" not in status["sync"] and "conn-of-org-a" not in repr(status)

    assert db.fetch_roster_status("sess-a", "org-b") is None
    assert db.fetch_roster_status("sess-b", "org-a") is None
    assert db.fetch_roster_status("sess-legacy", "org-a") is None, "a row without an org is not any org's"
    assert db.fetch_roster_status("sess-legacy", ALL_ORGS)["roster"]["source"] == "session_include"
    assert db.fetch_roster_status("sess-b", ALL_ORGS)["sync"] is None
    assert db.fetch_roster_status("sess-missing", ALL_ORGS) is None

    db.upsert_cached_session("key-a", "sess-new", organization_id="org-a")
    assert db.fetch_roster_status("sess-new", "org-a") == {"session_id": "sess-new", "roster": None, "sync": None}


def test_stored_people_are_scoped_by_org(test_database):
    _seed_people()
    assert len(db.list_session_people("sess-a", "org-a")) == 10
    assert db.list_session_people("sess-a", "org-b") == []
    assert db.list_session_people("sess-b", "org-a") == []
    assert db.list_session_people("sess-legacy", "org-a") == []
    assert len(db.list_session_people("sess-legacy", ALL_ORGS)) == 2
    assert db.list_session_people("sess-missing", ALL_ORGS) == []


def test_a_stored_roster_is_read_whole_in_scope(test_database):
    _seed_people()
    stored = db.fetch_session_roster("sess-a", "org-a")
    assert set(stored) == {"roster", "people"}
    assert stored["roster"] == db.fetch_roster_status("sess-a", "org-a")["roster"]
    assert stored["people"] == db.list_session_people("sess-a", "org-a") and len(stored["people"]) == 10
    assert "conn-of-org-a" not in repr(stored), "the roster never carries a sync's connection id"

    assert db.fetch_session_roster("sess-a", "org-b") is None
    assert db.fetch_session_roster("sess-b", "org-a") is None
    assert db.fetch_session_roster("sess-legacy", "org-a") is None, "a row without an org is not any org's"
    assert len(db.fetch_session_roster("sess-legacy", ALL_ORGS)["people"]) == 2
    assert db.fetch_session_roster("sess-missing", ALL_ORGS) is None

    db.upsert_cached_session("key-a", "sess-new", organization_id="org-a")
    assert db.fetch_session_roster("sess-new", "org-a") is None, "a cached session without a roster has none"


def test_a_blank_scope_never_queries_the_people_tables(monkeypatch):
    def no_query():
        raise AssertionError("a blank scope must not reach the database")

    monkeypatch.setattr(db, "database_enabled", lambda: True)
    monkeypatch.setattr(db, "get_db_connection", no_query)
    for blank in ("", "   ", None):
        assert db.fetch_roster_status("sess-a", blank) is None
        assert db.list_session_people("sess-a", blank) == []
        assert db.fetch_session_roster("sess-a", blank) is None
        assert db.get_org_people_attributes(blank) is None
        assert db.fetch_chat_labels("sess-a", blank) is None
        assert db.fetch_chat_labels_stamp("sess-a", blank) is None
        assert db.fetch_question_replies("sess-a", blank) == {}
        assert db.fetch_understanding("sess-a", blank) is None
        assert db.save_question_reply(
            "sess-a", organization_id=blank, source="qa", livestorm_id="q-1", evidence_id="Q001",
            answered=True, reply=None, updated_by=None,
        ) is False
        assert db.save_located_decision(
            "sess-a", organization_id=blank, source="chat", livestorm_id="msg-9", evidence_id="C0009",
            decision="confirmed", updated_by=None,
        ) is False


def _reply(session_id, organization_id, **fields):
    values = {"source": "qa", "livestorm_id": "q-1", "evidence_id": "Q001", "answered": True, "reply": "See the docs.", "updated_by": "user-a"}
    return db.save_question_reply(session_id, organization_id=organization_id, **{**values, **fields})


def test_question_replies_are_read_and_written_only_in_scope(test_database):
    _seed()
    db.upsert_cached_session("key-x", "sess-legacy", session_payload={"name": "L"})
    assert _reply("sess-a", "org-a") is True
    assert _reply("sess-a", "org-b", reply="outsider") is False, "another org writes nothing"
    assert _reply("sess-legacy", "org-a") is False, "a row without an org is not any org's"
    assert _reply("sess-missing", ALL_ORGS) is False, "no session, no row"
    saved = db.fetch_question_replies("sess-a", "org-a")
    assert list(saved) == ["qa:q-1"]
    assert (saved["qa:q-1"]["answered"], saved["qa:q-1"]["reply"], saved["qa:q-1"]["evidence_id"]) == (True, "See the docs.", "Q001")
    assert db.fetch_question_replies("sess-a", "org-b") == {}
    assert list(db.fetch_question_replies("sess-a", ALL_ORGS)) == ["qa:q-1"]


def test_a_question_reply_is_replaced_and_deleted_with_its_session(test_database):
    _seed()
    _reply("sess-a", "org-a")
    _reply("sess-a", "org-a", evidence_id="Q002", answered=False, reply=None)
    _reply("sess-a", "org-a", source="chat", livestorm_id="msg-9", evidence_id="C0009")
    saved = db.fetch_question_replies("sess-a", "org-a")
    assert set(saved) == {"qa:q-1", "chat:msg-9"}
    assert (saved["qa:q-1"]["answered"], saved["qa:q-1"]["reply"], saved["qa:q-1"]["evidence_id"]) == (False, None, "Q002")
    assert db.admin_delete_session("sess-a") is True
    assert db.fetch_question_replies("sess-a", ALL_ORGS) == {}


def _decide(session_id, organization_id, decision, **fields):
    values = {"source": "chat", "livestorm_id": "msg-9", "evidence_id": "C0009", "updated_by": "user-a"}
    return db.save_located_decision(session_id, organization_id=organization_id, decision=decision, **{**values, **fields})


def test_a_located_answers_decision_is_scoped_and_keeps_what_the_host_wrote(test_database):
    _seed()
    assert _decide("sess-a", "org-b", "confirmed") is False, "another org writes nothing"
    assert db.fetch_question_replies("sess-a", ALL_ORGS) == {}
    assert _decide("sess-a", "org-a", "rejected") is True
    saved = db.fetch_question_replies("sess-a", "org-a")["chat:msg-9"]
    assert (saved["answered"], saved["located"], saved["reply"]) == (False, "rejected", None), "Not this: still open"
    _reply("sess-a", "org-a", source="chat", livestorm_id="msg-9", evidence_id="C0009", answered=False, reply="Drafting")
    assert db.fetch_question_replies("sess-a", "org-a")["chat:msg-9"]["located"] == "rejected", "a reply keeps the decision"
    assert _decide("sess-a", "org-a", "confirmed") is True
    saved = db.fetch_question_replies("sess-a", "org-a")["chat:msg-9"]
    assert (saved["answered"], saved["located"], saved["reply"]) == (True, "confirmed", "Drafting")
    with pytest.raises(ValueError):
        _decide("sess-a", "org-a", "maybe")


def test_figure_checks_are_scoped_replaced_reopened_and_deleted_with_their_session(test_database):
    _seed()
    check = {"check_key": "acronym:U0001:nps 42", "updated_by": "user-a"}
    assert db.save_figure_check("sess-a", organization_id="org-b", choice="NPS", **check) is False, "another org writes nothing"
    assert db.save_figure_check("sess-a", organization_id="", choice="NPS", **check) is False, "a blank scope writes nothing"
    assert db.save_figure_check("sess-a", organization_id="org-a", choice="NPS", **check) is True
    assert db.save_figure_check("sess-a", organization_id="org-a", choice="NRR", **check) is True
    assert {k: v["choice"] for k, v in db.fetch_figure_checks("sess-a", "org-a").items()} == {"acronym:U0001:nps 42": "NRR"}
    assert db.fetch_figure_checks("sess-a", "org-b") == {} and db.fetch_figure_checks("sess-a", "") == {}
    assert db.save_figure_check("sess-a", organization_id="org-a", choice=None, **check) is True
    assert db.fetch_figure_checks("sess-a", ALL_ORGS) == {}, "reopened"
    db.save_figure_check("sess-a", organization_id="org-a", choice="NPS", **check)
    assert db.admin_delete_session("sess-a") is True
    assert db.fetch_figure_checks("sess-a", ALL_ORGS) == {}


def test_chat_labels_are_scoped_by_org(test_database):
    _seed()
    db.upsert_cached_session("key-x", "sess-legacy", session_payload={"name": "L"})
    label = {"id": "C0001", "livestorm_id": "msg-1", "class": "content", "question": None, "language": "en"}
    for session_id in ("sess-a", "sess-legacy"):
        db.replace_chat_labels(session_id, [label], chat_hash="c" * 64, model="claude-haiku-5-5")
    assert db.fetch_chat_labels("sess-a", "org-a")["labels"] == [label]
    assert db.fetch_chat_labels("sess-a", "org-b") is None
    assert db.fetch_chat_labels("sess-b", "org-b") is None, "no labels yet"
    assert db.fetch_chat_labels("sess-legacy", "org-a") is None, "a row without an org is not any org's"
    assert db.fetch_chat_labels("sess-legacy", ALL_ORGS)["message_count"] == 1
    stamp = db.fetch_chat_labels_stamp("sess-a", "org-a")
    assert set(stamp) == {"label_version", "chat_hash", "created_at"} and stamp["chat_hash"] == "c" * 64
    assert db.fetch_chat_labels_stamp("sess-a", "org-b") is None
    assert db.fetch_chat_labels_stamp("sess-legacy", "org-a") is None


def test_people_attributes_are_per_org(test_database):
    goal = {"slug": "goal", "name": "Your goal", "type": "text", "builtin": False, "options": []}
    db.upsert_org_people_attributes("org-a", [goal], status="ok")
    assert db.get_org_people_attributes("org-a")["attributes"] == [goal]
    assert db.get_org_people_attributes("org-b") is None
    with pytest.raises(ValueError, match="never ALL_ORGS"):
        db.get_org_people_attributes(ALL_ORGS)


def _understanding(**fields):
    from livestorm_app.record.understanding import UNDERSTANDING_VERSION

    return {"version": UNDERSTANDING_VERSION, "onClock": True, "chapters": [], "answers": [], "prompts": [], "figures": [], "groups": [], "coverage": {}, **fields}


def test_the_understanding_is_scoped_replaced_and_deleted_with_its_session(test_database):
    _seed()
    db.upsert_cached_session("key-x", "sess-legacy", session_payload={"name": "L"})
    chapter = {"title": "Opening", "kind": "opening", "first": "U0001", "last": "U0002", "t": 0.0, "tEnd": 9.0}
    for session_id in ("sess-a", "sess-legacy"):
        db.replace_understanding(session_id, _understanding(), dossier_hash="d" * 64, model="claude-sonnet-5-5")
    db.replace_understanding("sess-a", _understanding(chapters=[chapter]), dossier_hash="e" * 64, model="claude-sonnet-5-5")
    stored = db.fetch_understanding("sess-a", "org-a")
    assert (stored["dossier_hash"], stored["model"], stored["result"]["chapters"]) == ("e" * 64, "claude-sonnet-5-5", [chapter])
    assert db.fetch_understanding("sess-a", "org-b") is None
    assert db.fetch_understanding("sess-b", "org-b") is None, "nothing read yet"
    assert db.fetch_understanding("sess-legacy", "org-a") is None, "a row without an org is not any org's"
    assert db.fetch_understanding("sess-legacy", ALL_ORGS)["dossier_hash"] == "d" * 64
    assert db.admin_delete_session("sess-a") is True
    assert db.fetch_understanding("sess-a", ALL_ORGS) is None


def test_a_malformed_understanding_is_refused_before_any_write(test_database):
    _seed()
    with pytest.raises(ValueError):
        db.replace_understanding("sess-a", _understanding(version=0), dossier_hash="d" * 64, model="m")
    with pytest.raises(ValueError):
        db.replace_understanding("sess-a", _understanding(), dossier_hash="not-a-hash", model="m")
    with pytest.raises(RuntimeError):
        db.replace_understanding("sess-missing", _understanding(), dossier_hash="d" * 64, model="m")
    assert db.fetch_understanding("sess-a", ALL_ORGS) is None


# ── Earlier versions of an output (`list_output_versions`) ──


def test_an_outputs_versions_are_read_newest_first_in_scope_only(test_database):
    from livestorm_app import api_logic

    db.upsert_cached_session("key-a", "sess-v", session_payload={"name": "V"}, organization_id="org-a")
    made = {"calls": 1, "job_kinds": ["faq"], "models": ["claude-haiku-5-5"], "prompt_hashes": ["p1"], "job_id": "faq:sess-v:English"}
    db.merge_cached_bundle_entry("sess-v", "faq_bundle", "English", "FAQ one", provenance=made)
    db.merge_cached_bundle_entry("sess-v", "faq_bundle", "English", "FAQ two", provenance=made)
    db.merge_cached_bundle_entry("sess-v", "faq_bundle", "French", "FAQ un", provenance=made)

    rows = db.list_output_versions("sess-v", "faq_bundle", "English", organization_id="org-a")
    assert [row["content"] for row in rows] == ["FAQ two", "FAQ one"]
    assert rows[0]["models"] == ["claude-haiku-5-5"] and rows[0]["calls"] == 1
    assert set(rows[0]) == {"id", "content", "models", "job_kinds", "calls", "created_at"}, "never the hashes or the job id"
    assert db.list_output_versions("sess-v", "faq_bundle", "English", organization_id="org-b") == []
    assert db.list_output_versions("sess-v", "faq_bundle", "English", organization_id="") == []
    assert len(db.list_output_versions("sess-v", "faq_bundle", "English", organization_id="org-a", limit=1)) == 1
    with pytest.raises(ValueError):
        db.list_output_versions("sess-v", "transcript_payload", "English", organization_id="org-a")

    body = api_logic.get_session_output_versions("sess-v", "faq", language="English", organization_id="org-a")
    assert [(v["content"], v["current"]) for v in body["versions"]] == [("FAQ two", True), ("FAQ one", False)]
    assert body["entry"] == "English" and body["versions"][0]["createdAt"]
    with pytest.raises(api_logic.SessionNotFoundError):
        api_logic.get_session_output_versions("sess-v", "faq", organization_id="org-b")
    with pytest.raises(api_logic.UnknownOutputKindError):
        api_logic.get_session_output_versions("sess-v", "transcript", organization_id="org-a")
