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


def test_an_organization_is_on_until_an_admin_turns_it_off(test_database):
    db.upsert_cached_session("key-a", "sess-o", session_payload={"name": "O"}, organization_id="org-o")
    assert db.fetch_surprise_enabled("org-o") is True
    assert db.fetch_surprise_enabled("") is True, "a session without an organization has no setting"
    db.save_surprise_enabled("org-o", False, updated_by_email="admin@example.com")
    db.save_surprise_enabled("org-settings-only", False, updated_by_email=None)
    assert db.fetch_surprise_enabled("org-o") is False
    rows = {row["organization_id"]: row for row in db.admin_list_organizations()}
    assert (rows["org-o"]["sessions"], rows["org-o"]["surprise_enabled"], rows["org-o"]["updated_by_email"]) == (1, False, "admin@example.com")
    assert rows["org-settings-only"]["sessions"] == 0, "an organization with a setting and no session is listed"
    db.save_surprise_enabled("org-o", True, updated_by_email="admin@example.com")
    assert db.fetch_surprise_enabled("org-o") is True
    with pytest.raises(ValueError):
        db.save_surprise_enabled(" ", False, updated_by_email=None)


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
