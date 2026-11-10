"""The Livestorm OAuth callback's redirect: back to the page the user
connected from, and never off the site."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

import app as app_module
from livestorm_app import oauth_client
from livestorm_app.routes import oauth as oauth_routes


@pytest.fixture
def sign_in(monkeypatch):
    """Run the callback with a handshake whose `return_to` is given; nothing
    reaches Livestorm or the database."""

    def run(return_to):
        monkeypatch.setattr(oauth_routes, "get_frontend_app_url", lambda: "")
        monkeypatch.setattr(oauth_routes, "validate_handshake", lambda token, state: {"verifier": "v", "return_to": return_to})
        monkeypatch.setattr(oauth_routes, "exchange_authorization_code", lambda code, verifier: {"access_token": "t"})
        monkeypatch.setattr(oauth_routes, "fetch_me", lambda token: {})
        monkeypatch.setattr(oauth_routes, "persist_oauth_connection", lambda token, me: {"connection_id": "conn-1"})
        client = TestClient(app_module.app, follow_redirects=False)
        client.cookies.set(oauth_client.LIVESTORM_OAUTH_HANDSHAKE_COOKIE, "signed")
        response = client.get("/api/auth/livestorm/callback", params={"code": "c", "state": "s"})
        assert response.status_code == 302
        location = urlsplit(response.headers["location"])
        assert location.path == "/auth/callback"
        return parse_qs(location.query)

    return run


def test_a_sign_in_returns_to_the_page_it_started_from(sign_in):
    assert sign_in("/single-analysis/abc/report") == {"auth": ["success"], "next": ["/single-analysis/abc/report"]}


def test_a_sign_in_from_the_home_page_names_it(sign_in):
    assert sign_in("/") == {"auth": ["success"], "next": ["/"]}


@pytest.mark.parametrize(
    "return_to",
    ["", None, "https://evil.example", "//evil.example", "/\\evil.example", "evil", "/a\nb", "/auth/callback", "/api/me", "/" + "a" * 600],
)
def test_a_return_path_off_the_site_is_dropped(sign_in, return_to):
    assert sign_in(return_to) == {"auth": ["success"]}


def test_a_failed_sign_in_carries_no_next(monkeypatch):
    monkeypatch.setattr(oauth_routes, "get_frontend_app_url", lambda: "")
    client = TestClient(app_module.app, follow_redirects=False)
    response = client.get("/api/auth/livestorm/callback", params={"code": "", "state": "s"})
    assert response.headers["location"] == "/auth/callback?auth=error"
