"""Access control: every session route fails closed.

The DB, Redis, Livestorm and PDF rendering are replaced with in-memory
fakes. Session `sess-a` belongs to org `org-a` and every payload carries
MARKER; the tests assert MARKER never reaches anyone outside org-a and that
outsiders cannot write to the row or enqueue paid work on it.

Session routes are discovered from `app.routes`, so a route added later
without the auth dependency fails these tests automatically.
"""

from __future__ import annotations

import time

import pytest
import requests
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import app as app_module
from livestorm_app import api_logic, auth, oauth_client
from livestorm_app.db import ALL_ORGS


SESSION_ID = "sess-a"
OWNER_ORG = "org-a"
MARKER = "ORG_A_CONFIDENTIAL_MARKER"
UPSTREAM_SECRET = "UPSTREAM_DEBUG_SECRET"
ADMIN_EMAIL = "admin@example.com"


def _connection(connection_id: str, org: str, email: str) -> dict:
    return {
        "connection_id": connection_id,
        "organization_id": org,
        "email": email,
        "user_id": f"user-{connection_id}",
        "access_token": f"token-{connection_id}",
        "token_type": "Bearer",
        "profile": {"full_name": email, "email": email},
    }


CONNECTIONS = {
    "conn-a": _connection("conn-a", OWNER_ORG, "a@example.com"),
    "conn-b": _connection("conn-b", "org-b", "b@example.com"),
    "conn-no-org": _connection("conn-no-org", "", "noorg@example.com"),
    "conn-admin": _connection("conn-admin", "org-b", ADMIN_EMAIL),
    oauth_client.DEV_CONNECTION_ID: _connection(oauth_client.DEV_CONNECTION_ID, OWNER_ORG, ADMIN_EMAIL),
}


def _row() -> dict:
    return {
        "session_id": SESSION_ID,
        "organization_id": OWNER_ORG,
        "session_payload": {"name": MARKER},
        "chat_payload": {"data": []},
        "questions_payload": {"data": []},
        "transcript_payload": {"text": MARKER},
        "analysis_bundle": {"English": f"# Analysis {MARKER}"},
        "deep_analysis_bundle": {"English": f"# Deep {MARKER}"},
        "smart_recap_bundle": {"professional": f"# Recap {MARKER}"},
        "content_repurpose_bundle": {
            "English": {"summary": MARKER, "blog": MARKER, "email": MARKER, "social_media": MARKER},
        },
        "has_cover_image": True,
    }


def _visible(scope) -> bool:
    return scope is ALL_ORGS or (isinstance(scope, str) and scope.strip() == OWNER_ORG)


class FakeBackend:
    def __init__(self) -> None:
        self.upserts: list = []
        self.enqueued: list = []

    def fetch_cached_session(self, api_key, session_id, *, organization_id):
        if session_id == SESSION_ID and _visible(organization_id):
            return _row()
        return None

    def fetch_cover_image(self, session_id, *, organization_id):
        if session_id == SESSION_ID and _visible(organization_id):
            return {"bytes": MARKER.encode(), "mime": "image/png", "generated_at": None}
        return None

    def upsert_cached_session(self, api_key, session_id, **fields):
        self.upserts.append((session_id, fields))

    def enqueue_job_sync(self, *args, **kwargs):
        self.enqueued.append(args)
        return "job-1"


def _livestorm_refuses(*_args, **_kwargs):
    """Livestorm returns 404 for sessions outside the token's org."""
    response = requests.Response()
    response.status_code = 404
    response.url = f"https://api.livestorm.co/v1/sessions/x?debug={UPSTREAM_SECRET}"
    response._content = f'{{"errors": [{{"meta": "{UPSTREAM_SECRET}"}}]}}'.encode()
    raise requests.HTTPError("404 Client Error", response=response)


# Livestorm grants session access only to tokens of org-a members.
OWNER_ORG_TOKENS = {f"Bearer token-{cid}" for cid, conn in CONNECTIONS.items() if conn["organization_id"] == OWNER_ORG}


def _livestorm_session_details(api_key, session_id):
    if api_key in OWNER_ORG_TOKENS:
        return {"id": session_id}
    return _livestorm_refuses()


@pytest.fixture
def backend(monkeypatch):
    fake = FakeBackend()
    # A configured Gladia key lets fetch-transcript reach its job/stamp path.
    monkeypatch.setenv("GLADIA_KEY", "test-gladia-key")
    monkeypatch.setattr(auth, "refresh_connection_if_needed", lambda cid: CONNECTIONS.get(cid))
    monkeypatch.setattr(auth, "is_admin_email", lambda email: email == ADMIN_EMAIL)
    monkeypatch.setattr(api_logic, "fetch_cached_session", fake.fetch_cached_session)
    monkeypatch.setattr(app_module, "fetch_cover_image", fake.fetch_cover_image)
    monkeypatch.setattr(api_logic, "upsert_cached_session", fake.upsert_cached_session)
    monkeypatch.setattr(api_logic, "enqueue_job_sync", fake.enqueue_job_sync)
    monkeypatch.setattr(
        api_logic,
        "_serialize_cached_session",
        lambda sid, cached: {"sessionId": sid, "outputs": {}, "marker": cached["transcript_payload"]["text"]},
    )
    monkeypatch.setattr(api_logic, "fetch_session_details", _livestorm_session_details)
    monkeypatch.setattr(api_logic, "fetch_chat_and_questions_bundle", _livestorm_refuses)
    monkeypatch.setattr(
        api_logic,
        "get_transcript_job_for_session",
        lambda sid: {"job_id": "job-t", "status": "running", "progress": None, "error": None},
    )
    monkeypatch.setattr(api_logic, "create_transcript_job", lambda sid: "job-t")
    monkeypatch.setattr(api_logic, "read_progress", lambda *a, **k: None)
    monkeypatch.setattr(api_logic, "read_job_status_sync", lambda job_id: {"jobStatus": "running"})
    monkeypatch.setattr(api_logic, "get_in_flight_ai_job_id", lambda *a, **k: None)
    monkeypatch.setattr(api_logic, "set_in_flight_ai_job_id", lambda *a, **k: None)
    monkeypatch.setattr(api_logic, "clear_in_flight_ai_job_id", lambda *a, **k: None)
    monkeypatch.setattr(api_logic, "analysis_markdown_to_pdf_bytes", lambda markdown, title: markdown.encode())
    return fake


@pytest.fixture
def client(backend):
    # Not used as a context manager, so the lifespan (DB schema bootstrap) never runs.
    return TestClient(app_module.app)


# Superset of every query/body parameter the session routes accept; FastAPI
# ignores the ones a given route doesn't declare.
QUERY = {"kind": "overall", "tone": "professional", "item": "summary", "language": "English", "jobId": "job-1"}
BODY = {"tone": "professional", "outputLanguage": "English", "speakerNames": {"spk_0": "Host"}}


def _session_routes():
    for route in app_module.app.routes:
        if isinstance(route, APIRoute) and route.path.startswith("/api/sessions/{session_id}"):
            for method in sorted(route.methods):
                yield pytest.param(method, route.path, id=f"{method} {route.path}")


SESSION_ROUTES = list(_session_routes())
ADMIN_ROUTES = [
    pytest.param(method, route.path, id=f"{method} {route.path}")
    for route in app_module.app.routes
    if isinstance(route, APIRoute) and route.path.startswith("/api/admin/")
    for method in sorted(route.methods)
]


LOCALHOST = {"Host": "localhost"}
REBINDING = {"Host": "attacker.example"}


def _call(
    client: TestClient,
    method: str,
    path: str,
    cookie: str | None = None,
    body: dict | None = None,
    headers: dict | None = None,
    session_id: str = SESSION_ID,
):
    client.cookies.clear()
    if cookie:
        client.cookies.set(oauth_client.LIVESTORM_OAUTH_COOKIE, cookie)
    url = path.replace("{session_id}", session_id)
    kwargs: dict = {"params": QUERY, "headers": headers}
    if method in ("POST", "PUT", "PATCH"):
        kwargs["json"] = BODY if body is None else body
    return client.request(method, url, **kwargs)


def test_route_inventory_covers_every_session_route():
    # Guards the enumeration itself: if this drops, the parametrized tests
    # below would silently check nothing.
    assert len(SESSION_ROUTES) >= 19
    assert len(ADMIN_ROUTES) >= 8


@pytest.mark.parametrize("method,path", SESSION_ROUTES)
def test_anonymous_caller_gets_401(client, backend, method, path):
    response = _call(client, method, path)
    assert response.status_code == 401, response.text
    assert MARKER not in response.text
    assert backend.upserts == [] and backend.enqueued == []


@pytest.mark.parametrize("force_refresh", [False, True], ids=["cached", "forceRefresh"])
@pytest.mark.parametrize("cookie", ["conn-b", "conn-no-org"])
@pytest.mark.parametrize("method,path", SESSION_ROUTES)
def test_other_org_cannot_read_write_or_enqueue(client, backend, method, path, cookie, force_refresh):
    response = _call(client, method, path, cookie=cookie, body={**BODY, "forceRefresh": force_refresh})
    # `/cached` answers "not visible" with an empty 204 by design.
    hidden = response.status_code >= 400 or (response.status_code == 204 and not response.content)
    assert hidden, f"{response.status_code}: {response.text}"
    assert MARKER.encode() not in response.content
    assert b"job-t" not in response.content, "an outsider learned the owner's transcript job"
    assert backend.upserts == [], "an outsider wrote to another org's session"
    assert backend.enqueued == [], "an outsider enqueued work on another org's session"


def test_owner_can_start_transcript_for_an_uncached_session(client, backend):
    # Not cached anywhere yet: access is proven by Livestorm, then the row
    # is stamped with the caller's own org before the job is enqueued.
    response = _call(
        client, "POST", "/api/sessions/{session_id}/fetch-transcript",
        cookie="conn-a", body={"forceRefresh": True}, session_id="sess-new",
    )
    assert response.status_code == 200, response.text
    assert response.json()["jobId"] == "job-t"
    assert backend.upserts and backend.upserts[0][1]["organization_id"] == OWNER_ORG
    assert backend.enqueued and backend.enqueued[0][0] == "run_transcription"


def test_outsider_cannot_claim_an_uncached_session(client, backend):
    # The org stamp is first-writer-wins, so pre-stamping a session the
    # caller can't access in Livestorm would hijack it from its owner.
    response = _call(
        client, "POST", "/api/sessions/{session_id}/fetch-transcript",
        cookie="conn-b", body={"forceRefresh": True}, session_id="sess-new",
    )
    assert response.status_code >= 400
    assert backend.upserts == [] and backend.enqueued == []


@pytest.mark.parametrize("cookie", ["conn-a", "conn-admin"])
@pytest.mark.parametrize("method,path", SESSION_ROUTES)
def test_owner_org_and_admin_can_use_every_route(client, method, path, cookie):
    response = _call(client, method, path, cookie=cookie)
    assert 200 <= response.status_code < 300, response.text


def test_owner_reads_the_workspace_and_pdf(client):
    workspace = _call(client, "GET", "/api/sessions/{session_id}", cookie="conn-a")
    assert workspace.json()["marker"] == MARKER
    pdf = _call(client, "GET", "/api/sessions/{session_id}/analysis-pdf", cookie="conn-a")
    assert MARKER.encode() in pdf.content


def test_expired_cookie_gets_reconnect_message(client):
    response = _call(client, "GET", "/api/sessions/{session_id}", cookie="conn-unknown")
    assert response.status_code == 401
    assert "reconnect" in response.json()["detail"]["message"].lower()


def test_cover_image_is_not_shared_cacheable(client):
    response = _call(client, "GET", "/api/sessions/{session_id}/cover.png", cookie="conn-a")
    assert response.status_code == 200
    assert response.headers["cache-control"].startswith("private")


@pytest.mark.parametrize("method,path", ADMIN_ROUTES)
def test_admin_routes_reject_anonymous_and_non_admins(client, method, path):
    url = path.replace("{session_id}", SESSION_ID)
    body = {"email": "x@example.com", "key": "llm_provider", "value": "anthropic"}
    client.cookies.clear()
    anonymous = client.request(method, url, json=body if method == "POST" else None)
    assert anonymous.status_code == 401
    client.cookies.set(oauth_client.LIVESTORM_OAUTH_COOKIE, "conn-a")
    non_admin = client.request(method, url, json=body if method == "POST" else None)
    assert non_admin.status_code == 403


def test_admin_check_accepts_admin(client):
    client.cookies.set(oauth_client.LIVESTORM_OAUTH_COOKIE, "conn-admin")
    assert client.get("/api/admin/check").status_code == 200


def test_inline_api_key_rejected_outside_local_dev(client, backend):
    response = _call(
        client, "POST", "/api/sessions/{session_id}/speaker-labels",
        cookie="conn-a", body={"apiKey": "any-key", "speakerNames": {"spk_0": "Host"}},
    )
    assert response.status_code == 400
    assert backend.upserts == []


def test_upstream_debug_details_are_not_returned(client):
    # conn-b misses the cache, so fetch-base calls Livestorm, which refuses.
    response = _call(client, "POST", "/api/sessions/{session_id}/fetch-base", cookie="conn-b")
    assert response.status_code == 400
    assert UPSTREAM_SECRET not in response.text
    assert set(response.json()["detail"]["details"]) <= {"resource", "status_code"}


def test_invalid_session_id_is_rejected(client):
    client.cookies.set(oauth_client.LIVESTORM_OAUTH_COOKIE, "conn-a")
    assert client.get("/api/sessions/bad.id").status_code == 422


def test_speaker_labels_are_capped(client, backend):
    too_many = {f"spk_{i}": "Host" for i in range(app_module.MAX_SPEAKER_LABELS + 1)}
    response = _call(
        client, "POST", "/api/sessions/{session_id}/speaker-labels",
        cookie="conn-a", body={"speakerNames": too_many},
    )
    assert response.status_code == 422
    assert backend.upserts == []


# ── Local-dev mode ─────────────────────────────────────────────────────────


def test_local_dev_requires_flag_and_is_never_on_render(monkeypatch):
    monkeypatch.setenv("STORMIQ_LOCAL_DEV", "")
    assert auth.local_dev_enabled() is False
    monkeypatch.setenv("STORMIQ_LOCAL_DEV", "1")
    assert auth.local_dev_enabled() is True
    monkeypatch.setenv("RENDER", "true")
    assert auth.local_dev_enabled() is False


def test_host_header_does_not_enable_local_mode(client, monkeypatch):
    monkeypatch.setenv("LS_API_KEY", "operator-key")
    bootstrap = client.get("/api/bootstrap", headers={"Host": "localhost"})
    assert bootstrap.json()["auth"]["allowLocalApiKeyFallback"] is False
    assert "operator-key" not in bootstrap.text
    response = client.get(f"/api/sessions/{SESSION_ID}", headers={"Host": "localhost"})
    assert response.status_code == 401
    assert client.get("/api/dev/auto-login", headers={"Host": "localhost"}, follow_redirects=False).status_code == 403


def test_bootstrap_never_returns_the_api_key_even_in_local_dev(client, monkeypatch):
    monkeypatch.setenv("STORMIQ_LOCAL_DEV", "1")
    monkeypatch.setenv("LS_API_KEY", "operator-key")
    bootstrap = client.get("/api/bootstrap", headers=LOCALHOST)
    assert bootstrap.json()["auth"]["allowLocalApiKeyFallback"] is True
    assert "operator-key" not in bootstrap.text


def test_dev_connection_cookie_only_works_in_local_dev(client, monkeypatch):
    path = "/api/sessions/{session_id}"
    assert _call(client, "GET", path, cookie=oauth_client.DEV_CONNECTION_ID, headers=LOCALHOST).status_code == 401
    monkeypatch.setenv("STORMIQ_LOCAL_DEV", "1")
    assert _call(client, "GET", path, cookie=oauth_client.DEV_CONNECTION_ID, headers=LOCALHOST).status_code == 200
    # Same flag, but reached through another hostname (DNS rebinding).
    assert _call(client, "GET", path, cookie=oauth_client.DEV_CONNECTION_ID, headers=REBINDING).status_code == 401


def test_local_dev_without_oauth_reads_every_org(client, monkeypatch):
    monkeypatch.setenv("STORMIQ_LOCAL_DEV", "1")
    assert _call(client, "GET", "/api/sessions/{session_id}", headers=LOCALHOST).status_code == 200


def test_local_dev_is_not_reachable_through_dns_rebinding(client, monkeypatch):
    monkeypatch.setenv("STORMIQ_LOCAL_DEV", "1")
    monkeypatch.setenv("LS_API_KEY", "operator-key")
    assert _call(client, "GET", "/api/sessions/{session_id}", headers=REBINDING).status_code == 401
    assert client.get("/api/bootstrap", headers=REBINDING).json()["auth"]["allowLocalApiKeyFallback"] is False
    auto_login = client.get("/api/dev/auto-login", headers=REBINDING, follow_redirects=False)
    assert auto_login.status_code == 403


def test_inline_api_key_accepted_in_local_dev(client, backend, monkeypatch):
    monkeypatch.setenv("STORMIQ_LOCAL_DEV", "1")
    response = _call(
        client, "POST", "/api/sessions/{session_id}/speaker-labels",
        cookie="conn-a", headers=LOCALHOST, body={"apiKey": "any-key", "speakerNames": {"spk_0": "Host"}},
    )
    assert response.status_code == 200, response.text


# ── DB scope primitives (real code, no fakes) ─────────────────────────────


def test_org_filter_semantics():
    from livestorm_app import db

    assert db._org_filter(ALL_ORGS) is None
    assert db._org_filter(" org-a ") == "org-a"
    for blank in ("", "   ", None, 123):
        with pytest.raises(db.ScopeMatchesNothing):
            db._org_filter(blank)


def test_blank_scope_never_queries_the_database(monkeypatch):
    from livestorm_app import db

    def _no_connection():
        raise AssertionError("a blank scope must not reach the database")

    monkeypatch.setattr(db, "database_enabled", lambda: True)
    monkeypatch.setattr(db, "get_db_connection", _no_connection)
    assert db.fetch_cached_session("", SESSION_ID, organization_id="") is None
    assert db.fetch_cover_image(SESSION_ID, organization_id="  ") is None
    with pytest.raises(TypeError):
        db.fetch_cached_session("", SESSION_ID)  # scope is a required argument


# ── Static files and API fallthrough ──────────────────────────────────────


def test_frontend_file_resolution_stays_inside_dist(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html></html>")
    (dist / "favicon.ico").write_text("icon")
    (tmp_path / "secret.txt").write_text("secret")

    assert app_module._resolve_frontend_file("favicon.ico", dist) == (dist / "favicon.ico").resolve()
    assert app_module._resolve_frontend_file("../secret.txt", dist) is None
    assert app_module._resolve_frontend_file("assets/../../secret.txt", dist) is None
    assert app_module._resolve_frontend_file("/etc/passwd", dist) is None
    assert app_module._resolve_frontend_file("bad\x00name", dist) is None
    assert app_module._resolve_frontend_file("assets", dist) is None  # directories are not served
    assert app_module._resolve_frontend_file("", dist) is None


def test_encoded_traversal_does_not_serve_source(client):
    response = client.get("/%2e%2e/%2e%2e/app.py")
    assert "def serve_frontend" not in response.text


def test_unknown_api_path_is_404_not_the_spa(client):
    response = client.get("/api/does-not-exist")
    assert response.status_code == 404


# ── PDF filename and OAuth handshake ──────────────────────────────────────


def test_pdf_filename_is_sanitised():
    response = app_module._pdf_response({"filename": 'evil"\r\nX-Injected: 1.pdf', "content": b"%PDF"})
    disposition = response.headers["content-disposition"]
    assert "\r" not in disposition and "\n" not in disposition
    assert disposition.count('"') == 2


def test_handshake_rejects_expired_and_mismatched_state(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    fresh = oauth_client._sign_payload({"state": "s1", "verifier": "v1", "created_at": int(time.time())})
    assert oauth_client.validate_handshake(fresh, "s1")["verifier"] == "v1"
    with pytest.raises(RuntimeError, match="state mismatch"):
        oauth_client.validate_handshake(fresh, "other-state")
    stale = oauth_client._sign_payload(
        {"state": "s1", "verifier": "v1", "created_at": int(time.time()) - oauth_client.HANDSHAKE_MAX_AGE_SECONDS - 5}
    )
    with pytest.raises(RuntimeError, match="expired"):
        oauth_client.validate_handshake(stale, "s1")


def test_session_secret_has_no_hardcoded_fallback(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "")
    monkeypatch.setenv("LIVESTORM_OAUTH_CLIENT_SECRET", "")
    with pytest.raises(RuntimeError):
        oauth_client.get_session_secret()
