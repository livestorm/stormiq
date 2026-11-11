"""The per-organization switch for Surprise Me! (`org_settings`): stored
and listed (`db.fetch_surprise_enabled`, `save_surprise_enabled`,
`admin_list_organizations`), set only by a StormIQ admin
(`POST /api/admin/organizations/settings`), enforced when a surprise take
starts and again in the worker, and sent to the page by `/api/bootstrap`,
which fails closed."""

from __future__ import annotations

from unittest import mock

import pytest
import test_surprise
from test_access_control import BODY, OWNER_ORG, SESSION_ID, _call, backend, client  # noqa: F401  (fixtures)

from livestorm_app import api_logic, db
from livestorm_app.routes import system

# ── Stored ──


def _connect(connection_id, email, org):
    db.upsert_oauth_connection(
        connection_id=connection_id, provider="livestorm", user_id=connection_id, email=email, organization_id=org,
        access_token="token", refresh_token="", token_type="Bearer", scope="", expires_at=None, profile={},
    )


def test_an_organization_is_off_until_an_admin_turns_it_on(test_database):
    db.upsert_cached_session("key-a", "sess-o", session_payload={"name": "O"}, organization_id="org-o")
    assert db.fetch_surprise_enabled("org-o") is False, "off by default"
    assert db.fetch_surprise_enabled("") is True, "a session without an organization has no setting"
    db.save_surprise_enabled("org-o", True, updated_by_email="admin@example.com")
    db.save_surprise_enabled("org-settings-only", False, updated_by_email=None)
    assert db.fetch_surprise_enabled("org-o") is True
    rows = {row["organization_id"]: row for row in db.admin_list_organizations()}
    assert (rows["org-o"]["sessions"], rows["org-o"]["surprise_enabled"], rows["org-o"]["updated_by_email"]) == (1, True, "admin@example.com")
    assert rows["org-settings-only"]["sessions"] == 0, "an organization with a setting and no session is listed"
    db.save_surprise_enabled("org-o", False, updated_by_email="admin@example.com")
    assert db.fetch_surprise_enabled("org-o") is False
    with pytest.raises(ValueError):
        db.save_surprise_enabled(" ", False, updated_by_email=None)


def test_the_admins_own_organization_is_on_by_default(test_database, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "boss@stormiq.example")
    db.upsert_cached_session("key-a", "sess-h", session_payload={"name": "H"}, organization_id="org-home")
    db.upsert_cached_session("key-a", "sess-c", session_payload={"name": "C"}, organization_id="org-customer")
    _connect("conn-boss", "Boss@StormIQ.example", "org-home")
    _connect("conn-user", "someone@customer.example", "org-customer")
    assert db.fetch_surprise_enabled("org-home") is True, "an admin signed in from it"
    assert db.fetch_surprise_enabled("org-customer") is False
    rows = {row["organization_id"]: row for row in db.admin_list_organizations()}
    assert (rows["org-home"]["surprise_enabled"], rows["org-customer"]["surprise_enabled"]) == (True, False)
    db.save_surprise_enabled("org-home", False, updated_by_email="boss@stormiq.example")
    assert db.fetch_surprise_enabled("org-home") is False, "an admin's setting wins"


def test_an_admin_signing_in_keeps_their_organization_on_after_they_leave(test_database, monkeypatch):
    from livestorm_app import oauth_client

    monkeypatch.setenv("ADMIN_EMAILS", "")
    db.promote_admin("boss@stormiq.example", "u-boss", "owner@stormiq.example")
    _connect("conn-boss", "boss@stormiq.example", "org-home")
    assert db.fetch_surprise_enabled("org-home") is True, "an admin of the admin_users table signed in from it"
    oauth_client._remember_admin_organization({"email": "boss@stormiq.example", "organization_id": "org-home"})
    oauth_client._remember_admin_organization({"email": "x@customer.example", "organization_id": "org-customer"})
    db.delete_oauth_connection("conn-boss")
    assert db.fetch_surprise_enabled("org-home") is True, "remembered with no connection left"
    assert db.fetch_surprise_enabled("org-customer") is False
    db.save_surprise_enabled("org-other", False, updated_by_email="a@b.example")
    with mock.patch("livestorm_app.auth.is_admin_email", lambda email: True):
        oauth_client._remember_admin_organization({"email": "boss@stormiq.example", "organization_id": "org-other"})
    assert db.fetch_surprise_enabled("org-other") is False, "an existing setting is never changed"


# ── Enforced ──


def test_a_surprise_take_is_refused_when_the_organization_turned_it_off(client, backend, monkeypatch):  # noqa: F811
    seen = []
    monkeypatch.setattr(api_logic, "fetch_surprise_enabled", lambda org: seen.append(org) or False)
    path = "/api/sessions/{session_id}/smart-recap"
    refused = _call(client, "POST", path, cookie="conn-a", body={**BODY, "tone": "surprise"})
    assert refused.status_code == 403, refused.text
    assert refused.json()["detail"]["message"] == api_logic.SURPRISE_OFF_MESSAGE
    assert seen == [OWNER_ORG], "the session's own organization decides"
    assert backend.enqueued == []
    formal = _call(client, "POST", path, cookie="conn-a", body={**BODY, "tone": "professional"})
    assert formal.status_code == 200, "the other tones are never switched off"


def test_the_worker_checks_again_before_any_call():
    with mock.patch.object(api_logic, "fetch_surprise_enabled", lambda org: False):
        with pytest.raises(api_logic.SurpriseOffError):
            test_surprise._run("surprise", "# Title\nT\n# Description\nD.")


def test_a_failed_read_writes_no_take():
    def broken(org):
        raise ConnectionError("database unreachable")

    with mock.patch.object(api_logic, "fetch_surprise_enabled", broken):
        with pytest.raises(ConnectionError):
            test_surprise._run("surprise", "# Title\nT\n# Description\nD.")


# ── Set by an admin, shown to the page ──


def test_only_an_admin_sets_it(client, backend, monkeypatch):  # noqa: F811
    saved = []
    monkeypatch.setattr(
        "livestorm_app.routes.admin.save_surprise_enabled",
        lambda org, enabled, *, updated_by_email: saved.append((org, enabled, updated_by_email)),
    )
    body = {"organizationId": " org-a ", "surpriseEnabled": False}
    assert _call(client, "POST", "/api/admin/organizations/settings", cookie="conn-a", body=body).status_code == 403
    ok = _call(client, "POST", "/api/admin/organizations/settings", cookie="conn-admin", body=body)
    assert ok.status_code == 200 and saved == [("org-a", False, "admin@example.com")]
    blank = _call(client, "POST", "/api/admin/organizations/settings", cookie="conn-admin", body={**body, "organizationId": " "})
    assert blank.status_code == 400


def test_the_page_hides_surprise_me_when_off_or_unknown(client, backend, monkeypatch):  # noqa: F811
    monkeypatch.setattr(system, "fetch_surprise_enabled", lambda org: org == "org-a")
    assert _call(client, "GET", "/api/bootstrap", cookie="conn-a").json()["org"] == {"surpriseEnabled": True}
    assert _call(client, "GET", "/api/bootstrap", cookie="conn-b").json()["org"] == {"surpriseEnabled": False}
    assert _call(client, "GET", "/api/bootstrap").json()["org"] == {"surpriseEnabled": False}, "signed out"

    def broken(org):
        raise ConnectionError("database unreachable")

    monkeypatch.setattr(system, "fetch_surprise_enabled", broken)
    assert _call(client, "GET", "/api/bootstrap", cookie="conn-a").json()["org"] == {"surpriseEnabled": False}, "fails closed"
