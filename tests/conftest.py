"""Test bootstrap.

Runs before any test module imports `app`. `load_env_file()` never
overrides variables that are already set, so blanking these keeps the real
`.env` (DATABASE_URL, API keys, local-dev flags) out of the test process:
no test can reach a real database, Redis, or upstream API by accident.
"""

import os
import sys
from pathlib import Path

for _name in (
    "DATABASE_URL",
    "POSTGRES_URL",
    "RENDER_POSTGRES_URL",
    "LS_API_KEY",
    "STORMIQ_LOCAL_DEV",
    "ADMIN_EMAILS",
    "RENDER",
    "GLADIA_KEY",
    "CLAUDE_API_KEY",
    "OPENAI_API_KEY",
    # Read by the Anthropic SDK itself (llm_client passes the key and host explicitly).
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_CUSTOM_HEADERS",
    "ANTHROPIC_LOG",
    "SESSION_SECRET",
    "LIVESTORM_OAUTH_CLIENT_ID",
    "LIVESTORM_OAUTH_CLIENT_SECRET",
    "LIVESTORM_OAUTH_REDIRECT_URI",
):
    os.environ[_name] = ""
os.environ["REDIS_URL"] = "redis://127.0.0.1:1/0"

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uuid  # noqa: E402
from urllib.parse import parse_qsl, urlencode, urlparse  # noqa: E402

import pytest  # noqa: E402

_LOCAL_DB_HOSTS = {"localhost", "127.0.0.1", "::1", "postgres"}


@pytest.fixture
def test_database(monkeypatch):
    """A real Postgres, isolated in a throwaway schema.

    Skips unless TEST_DATABASE_URL is set (e.g. the compose `postgres`
    service: postgresql://stormiq:stormiq@localhost:5433/stormiq). Refuses
    any non-local host, so a test can never touch a shared database.
    """
    import psycopg

    from livestorm_app.db import ensure_database_schema

    url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set (docker compose up -d postgres)")
    parsed = urlparse(url)
    if (parsed.hostname or "") not in _LOCAL_DB_HOSTS:
        pytest.fail(f"TEST_DATABASE_URL must point at a local database, not {parsed.hostname!r}")

    schema = f"test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA "{schema}"')
    query = dict(parse_qsl(parsed.query))
    query["options"] = f"-csearch_path={schema}"
    monkeypatch.setenv("DATABASE_URL", parsed._replace(query=urlencode(query)).geturl())
    ensure_database_schema()
    try:
        yield
    finally:
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA "{schema}" CASCADE')


@pytest.fixture
def test_redis(monkeypatch):
    """A real Redis for queue tests, flushed before and after each test.

    Skips unless TEST_REDIS_URL is set (e.g. redis://localhost:6379/15).
    Refuses non-local hosts and database 0 (the app's default), because the
    fixture flushes the whole database.
    """
    import redis

    url = os.environ.get("TEST_REDIS_URL", "").strip()
    if not url:
        pytest.skip("TEST_REDIS_URL is not set (docker compose up -d redis)")
    parsed = urlparse(url)
    if (parsed.hostname or "") not in _LOCAL_DB_HOSTS | {"redis"}:
        pytest.fail(f"TEST_REDIS_URL must point at a local Redis, not {parsed.hostname!r}")
    if parsed.path.strip("/") in ("", "0"):
        pytest.fail("TEST_REDIS_URL must name a dedicated database index, e.g. redis://localhost:6379/15")
    monkeypatch.setenv("REDIS_URL", url)
    client = redis.Redis.from_url(url)
    client.flushdb()
    try:
        yield client
    finally:
        client.flushdb()
        client.close()
