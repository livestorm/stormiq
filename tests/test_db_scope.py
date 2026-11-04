"""Org scoping against a real Postgres.

Exercises the actual SQL behind `fetch_cached_session`, `fetch_cover_image`,
`list_workspace_sessions` and the upsert's first-writer-wins org stamp.
Skipped unless TEST_DATABASE_URL points at a local database (see conftest).
"""

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
