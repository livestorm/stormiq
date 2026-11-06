"""Org scoping against a real Postgres.

Exercises the actual SQL behind `fetch_cached_session`, `fetch_cover_image`,
`list_workspace_sessions`, the upsert's first-writer-wins org stamp, and the
per-org model spend (`llm_spend_since`).
Skipped unless TEST_DATABASE_URL points at a local database (see conftest).
"""

from datetime import datetime, timedelta, timezone

import pytest

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
