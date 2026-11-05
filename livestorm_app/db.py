import atexit
import hashlib
import json
import os
import logging
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, Sequence, Union

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


DATABASE_URL_ENV_VARS = ("DATABASE_URL", "POSTGRES_URL", "RENDER_POSTGRES_URL")
logger = logging.getLogger(__name__)


class _AllOrgs:
    """Scope sentinel: read a session regardless of which org owns it.

    Only trusted callers may pass it: admin requests, local-dev mode, and
    worker jobs. Web routes resolve the scope through
    `livestorm_app.auth.Caller.read_scope`, never by hand.
    """

    def __repr__(self) -> str:
        return "ALL_ORGS"


ALL_ORGS = _AllOrgs()

# An org scope is either ALL_ORGS or an organization_id string. A blank or
# missing org id matches no rows: an unknown identity must never widen access.
OrgScope = Union[str, _AllOrgs]


class ScopeMatchesNothing(Exception):
    """Raised by `_org_filter` when the scope cannot match any row."""


def _org_filter(organization_id: OrgScope) -> Optional[str]:
    """Translate a scope into a SQL filter value.

    Returns None for ALL_ORGS (no filter) and the org id otherwise. Raises
    `ScopeMatchesNothing` for blank or non-string scopes so callers can
    return "not found" without querying.
    """
    if organization_id is ALL_ORGS:
        return None
    cleaned = organization_id.strip() if isinstance(organization_id, str) else ""
    if not cleaned:
        raise ScopeMatchesNothing()
    return cleaned


def get_database_url() -> str:
    for env_var in DATABASE_URL_ENV_VARS:
        value = str(os.getenv(env_var, "") or "").strip()
        if value:
            return value
    return ""


def database_enabled() -> bool:
    return bool(get_database_url())


def build_account_key_hash(api_key: str) -> str:
    return hashlib.sha256(str(api_key or "").strip().encode("utf-8")).hexdigest()


# ── Connection pool ─────────────────────────────────────────────────────────
#
# One pool per process (web and worker). Opening a connection to Render's
# Postgres costs a TCP + TLS handshake and authentication; a typical request
# used to pay that several times. Pooled connections are checked before use
# (dropped idle connections are replaced) and recycled after an hour.
# The pool follows the configured URL, so a changed DATABASE_URL (tests use a
# schema per test) gets a fresh pool.

DB_POOL_MAX_SIZE = 10
DB_POOL_TIMEOUT_SECONDS = 15

_pool: Optional[ConnectionPool] = None
_pool_url = ""
_pool_lock = threading.Lock()


def _get_pool(database_url: str) -> ConnectionPool:
    global _pool, _pool_url
    stale: Optional[ConnectionPool] = None
    with _pool_lock:
        if _pool is None or _pool_url != database_url:
            stale = _pool
            _pool = ConnectionPool(
                database_url,
                min_size=1,
                max_size=DB_POOL_MAX_SIZE,
                kwargs={"row_factory": dict_row},
                check=ConnectionPool.check_connection,
                max_idle=300,
                max_lifetime=3600,
                timeout=DB_POOL_TIMEOUT_SECONDS,
                name="stormiq",
                open=True,
            )
            _pool_url = database_url
        pool = _pool
    if stale is not None:
        stale.close()
    return pool


def close_db_pool() -> None:
    """Close the process's pool (app shutdown, worker shutdown, exit)."""
    global _pool, _pool_url
    with _pool_lock:
        pool, _pool, _pool_url = _pool, None, ""
    if pool is not None:
        pool.close()


atexit.register(close_db_pool)


@contextmanager
def get_db_connection() -> Iterator[Any]:
    """A pooled connection. On exit the transaction is committed (or rolled
    back on error) and the connection goes back to the pool."""
    database_url = get_database_url()
    if not database_url:
        raise RuntimeError("Database is not configured.")
    with _get_pool(database_url).connection() as connection:
        yield connection


def ensure_database_schema() -> None:
    if not database_enabled():
        return

    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS session_cache (
                    account_key_hash TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    session_payload JSONB,
                    chat_payload JSONB,
                    questions_payload JSONB,
                    transcript_payload JSONB,
                    transcript_speaker_names JSONB,
                    analysis_md TEXT,
                    analysis_bundle JSONB,
                    deep_analysis_md TEXT,
                    deep_analysis_bundle JSONB,
                    content_repurpose_bundle JSONB,
                    smart_recap_bundle JSONB,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (account_key_hash, session_id)
                )
                """
            )
            cursor.execute(
                """
                ALTER TABLE session_cache
                ADD COLUMN IF NOT EXISTS session_payload JSONB
                """
            )
            cursor.execute(
                """
                ALTER TABLE session_cache
                ADD COLUMN IF NOT EXISTS analysis_bundle JSONB
                """
            )
            cursor.execute(
                """
                ALTER TABLE session_cache
                ADD COLUMN IF NOT EXISTS deep_analysis_bundle JSONB
                """
            )
            cursor.execute(
                """
                ALTER TABLE session_cache
                ADD COLUMN IF NOT EXISTS transcript_speaker_names JSONB
                """
            )
            cursor.execute(
                """
                ALTER TABLE session_cache
                ADD COLUMN IF NOT EXISTS organization_id TEXT
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_session_cache_organization_id
                ON session_cache (organization_id, updated_at DESC)
                """
            )
            cursor.execute(
                """
                ALTER TABLE session_cache
                ADD COLUMN IF NOT EXISTS event_payload JSONB
                """
            )
            cursor.execute(
                """
                ALTER TABLE session_cache
                ADD COLUMN IF NOT EXISTS created_by_user_id TEXT
                """
            )
            cursor.execute(
                """
                ALTER TABLE session_cache
                ADD COLUMN IF NOT EXISTS created_by_email TEXT
                """
            )
            cursor.execute(
                """
                ALTER TABLE session_cache
                ADD COLUMN IF NOT EXISTS created_by_name TEXT
                """
            )
            cursor.execute(
                """
                ALTER TABLE session_cache
                ADD COLUMN IF NOT EXISTS cover_image_bytes BYTEA
                """
            )
            cursor.execute(
                """
                ALTER TABLE session_cache
                ADD COLUMN IF NOT EXISTS cover_image_mime TEXT
                """
            )
            cursor.execute(
                """
                ALTER TABLE session_cache
                ADD COLUMN IF NOT EXISTS cover_image_generated_at TIMESTAMPTZ
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_session_cache_account_hash
                ON session_cache (account_key_hash)
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS oauth_connections (
                    connection_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    user_id TEXT,
                    email TEXT,
                    organization_id TEXT,
                    access_token TEXT NOT NULL,
                    refresh_token TEXT,
                    token_type TEXT NOT NULL DEFAULT 'Bearer',
                    scope TEXT,
                    expires_at TIMESTAMPTZ,
                    profile JSONB,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_oauth_connections_user_id
                ON oauth_connections (provider, user_id)
                """
            )
            cursor.execute(
                """
                DELETE FROM session_cache
                WHERE ctid IN (
                    SELECT ctid
                    FROM (
                        SELECT
                            ctid,
                            ROW_NUMBER() OVER (
                                PARTITION BY session_id
                                ORDER BY updated_at DESC, created_at DESC
                            ) AS row_rank
                        FROM session_cache
                    ) ranked_rows
                    WHERE row_rank > 1
                )
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_session_cache_session_id
                ON session_cache (session_id)
                """
            )
            cursor.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_session_cache_session_id_unique
                ON session_cache (session_id)
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS transcript_jobs (
                    job_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    timestamped BOOLEAN DEFAULT TRUE,
                    status TEXT NOT NULL DEFAULT 'pending',
                    error TEXT,
                    progress TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cursor.execute(
                """
                ALTER TABLE transcript_jobs
                ADD COLUMN IF NOT EXISTS progress TEXT
                """
            )
            # OAuth connection that started a queued job. It doubles as the
            # user's session cookie value, so it is kept here (never in
            # Redis job args or logs) and cleared once the worker claims
            # the job or the job ends.
            cursor.execute(
                """
                ALTER TABLE transcript_jobs
                ADD COLUMN IF NOT EXISTS connection_id TEXT
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_transcript_jobs_session_id
                ON transcript_jobs (session_id, created_at DESC)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_transcript_jobs_status_updated_at
                ON transcript_jobs (status, updated_at)
                """
            )
            # At most one active (pending/running) transcription per session:
            # concurrent fetches can no longer start two paid Gladia jobs.
            # Older duplicates left by the pre-index code are closed first,
            # otherwise the index could not be built.
            cursor.execute(
                """
                UPDATE transcript_jobs AS older
                SET status = 'error',
                    error = 'Superseded by a newer transcription job for the same session.',
                    connection_id = NULL,
                    updated_at = NOW()
                WHERE older.status IN ('pending', 'running')
                  AND EXISTS (
                      SELECT 1 FROM transcript_jobs AS newer
                      WHERE newer.session_id = older.session_id
                        AND newer.status IN ('pending', 'running')
                        AND (newer.created_at, newer.job_id) > (older.created_at, older.job_id)
                  )
                """
            )
            cursor.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_transcript_jobs_one_active_per_session
                ON transcript_jobs (session_id)
                WHERE status IN ('pending', 'running')
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS admin_users (
                    email TEXT PRIMARY KEY,
                    user_id TEXT,
                    promoted_by_email TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS system_settings (
                    key        TEXT PRIMARY KEY,
                    value      TEXT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
        connection.commit()


def fetch_cached_session(
    api_key: str,
    session_id: str,
    *,
    organization_id: OrgScope,
) -> Optional[Dict[str, Any]]:
    """Read a cached session row, scoped by organization.

    `organization_id` is required:
        - an org id string → only that org's row is visible.
        - ALL_ORGS → no filter. Trusted callers only (admin, local dev, worker).
        - a blank string → matches nothing. Fail closed: a caller whose org
          could not be resolved never sees another org's data.

    `api_key` is kept in the signature for back-compat and is not used.
    """
    if not database_enabled() or not str(session_id or "").strip():
        return None
    try:
        org_filter = _org_filter(organization_id)
    except ScopeMatchesNothing:
        logger.warning("fetch_cached_session called with a blank org scope for session_id=%s", str(session_id).strip())
        return None
    where_clause = "WHERE session_id = %s" + (" AND organization_id = %s" if org_filter is not None else "")
    params = (str(session_id).strip(),) + ((org_filter,) if org_filter is not None else ())
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT
                        account_key_hash,
                        session_id,
                        organization_id,
                        session_payload,
                        chat_payload,
                        questions_payload,
                        transcript_payload,
                        transcript_speaker_names,
                        analysis_md,
                        analysis_bundle,
                        deep_analysis_md,
                        deep_analysis_bundle,
                        content_repurpose_bundle,
                        smart_recap_bundle,
                        event_payload,
                        cover_image_bytes IS NOT NULL AS has_cover_image,
                        cover_image_mime,
                        cover_image_generated_at,
                        created_by_user_id,
                        created_by_email,
                        created_by_name,
                        created_at,
                        updated_at
                    FROM session_cache
                    {where_clause}
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    params,
                )
                row = cursor.fetchone()
        return dict(row) if isinstance(row, dict) else None
    except Exception:
        logger.exception("Failed to read cached session for session_id=%s", str(session_id).strip())
        return None


# Light reads for polling and job starts: only these fields can be asked
# for, by name. A 2-hour session row is megabytes; a poll needs a flag.
_SESSION_FIELD_SQL = {
    "has_transcript": "transcript_payload IS NOT NULL",
    "transcript_payload": "transcript_payload",
    "analysis_bundle": "analysis_bundle",
    "analysis_md": "analysis_md",
    "deep_analysis_bundle": "deep_analysis_bundle",
    "deep_analysis_md": "deep_analysis_md",
    "smart_recap_bundle": "smart_recap_bundle",
    "content_repurpose_bundle": "content_repurpose_bundle",
    "updated_at": "updated_at",
}


def fetch_session_fields(
    session_id: str,
    fields: Sequence[str],
    *,
    organization_id: OrgScope,
) -> Optional[Dict[str, Any]]:
    """Read only `fields` of a cached session row. Same scope rules as
    `fetch_cached_session`: None when the row is missing or not visible."""
    unknown = [field for field in fields if field not in _SESSION_FIELD_SQL]
    if unknown or not fields:
        raise ValueError(f"Unknown session fields: {unknown}")
    if not database_enabled() or not str(session_id or "").strip():
        return None
    try:
        org_filter = _org_filter(organization_id)
    except ScopeMatchesNothing:
        return None
    columns = ", ".join(f"{_SESSION_FIELD_SQL[field]} AS {field}" for field in fields)
    where_clause = "WHERE session_id = %s" + (" AND organization_id = %s" if org_filter is not None else "")
    params = (str(session_id).strip(),) + ((org_filter,) if org_filter is not None else ())
    try:
        with get_db_connection() as connection:
            row = connection.execute(f"SELECT {columns} FROM session_cache {where_clause} LIMIT 1", params).fetchone()
        return dict(row) if isinstance(row, dict) else None
    except Exception:
        logger.exception("Failed to read session fields for session_id=%s", str(session_id).strip())
        return None


def upsert_cached_session(api_key: str, session_id: str, **fields: Any) -> None:
    """Insert or update a session_cache row.

    Raises on failure: callers must never report success for data that was
    not saved. `account_key_hash`, `organization_id` and `created_by_*` are
    first-writer-wins; every other field passed here is overwritten. For a
    single entry inside a bundle column, use `merge_cached_bundle_entry`.
    """
    if not database_enabled():
        return
    if not str(api_key or "").strip() or not str(session_id or "").strip():
        raise ValueError("upsert_cached_session requires an api_key and a session_id.")

    allowed_fields = {
        "session_payload",
        "chat_payload",
        "questions_payload",
        "transcript_payload",
        "transcript_speaker_names",
        "analysis_md",
        "analysis_bundle",
        "deep_analysis_md",
        "deep_analysis_bundle",
        "content_repurpose_bundle",
        "smart_recap_bundle",
        "organization_id",
        "event_payload",
        "created_by_user_id",
        "created_by_email",
        "created_by_name",
        "cover_image_bytes",
        "cover_image_mime",
        "cover_image_generated_at",
    }

    # Fields that should be set only on the *first* insert. On subsequent
    # upserts the existing value is preserved via COALESCE — this keeps
    # the original generator attribution stable even when a different
    # user later refetches the same session.
    preserve_on_update = {"created_by_user_id", "created_by_email", "created_by_name", "organization_id"}
    persisted_fields = {key: value for key, value in fields.items() if key in allowed_fields}
    if not persisted_fields:
        return

    session_id_value = str(session_id).strip()
    account_key_hash = build_account_key_hash(api_key)
    columns = ["account_key_hash", "session_id", *persisted_fields.keys()]
    placeholders = ["%s", "%s"]
    insert_values = [account_key_hash, session_id_value]
    # account_key_hash is set on insert only: later writers (worker jobs,
    # teammates) must not replace the original owner's discriminator.
    update_clauses: List[str] = []

    for key, value in persisted_fields.items():
        if isinstance(value, (dict, list)):
            insert_values.append(json.dumps(value, ensure_ascii=False))
            placeholders.append("%s::jsonb")
        else:
            insert_values.append(value)
            placeholders.append("%s")
        if key in preserve_on_update:
            # First-insert wins. On UPDATE, only fill the column when
            # it was NULL or empty before.
            update_clauses.append(
                f"{key} = COALESCE(NULLIF(session_cache.{key}, ''), EXCLUDED.{key})"
            )
        else:
            update_clauses.append(f"{key} = EXCLUDED.{key}")

    update_clauses.append("updated_at = NOW()")

    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    INSERT INTO session_cache ({", ".join(columns)})
                    VALUES ({", ".join(placeholders)})
                    ON CONFLICT (session_id)
                    DO UPDATE SET {", ".join(update_clauses)}
                    """,
                    insert_values,
                )
            connection.commit()
    except Exception as exc:
        logger.exception("Failed to upsert cached session for session_id=%s", session_id_value)
        raise RuntimeError("Could not save the session to the database. Please try again.") from exc


# Bundle columns hold one entry per language (or per tone for Smart Recap).
_BUNDLE_COLUMNS = {"analysis_bundle", "deep_analysis_bundle", "smart_recap_bundle", "content_repurpose_bundle"}
# Legacy single-language TEXT columns that may be written alongside a bundle entry.
_LEGACY_TEXT_COLUMNS = {"analysis_md", "deep_analysis_md"}


def merge_cached_bundle_entry(
    session_id: str,
    column: str,
    key: str,
    value: Any,
    **legacy_text: str,
) -> None:
    """Atomically set `column[key] = value` without touching the bundle's other keys.

    One UPDATE with jsonb_set: concurrent writers to the same row (English
    and French analyses, or two Smart Recap tones) are serialised by
    Postgres and each sees the other's entry, so neither result is lost.
    `legacy_text` optionally sets `analysis_md` / `deep_analysis_md` in the
    same statement. Raises if the row does not exist or the write fails.
    """
    if column not in _BUNDLE_COLUMNS:
        raise ValueError(f"Not a bundle column: {column!r}")
    if not set(legacy_text) <= _LEGACY_TEXT_COLUMNS:
        raise ValueError(f"Unsupported legacy columns: {sorted(set(legacy_text) - _LEGACY_TEXT_COLUMNS)}")
    entry_key = str(key or "").strip()
    session_id_value = str(session_id or "").strip()
    if not entry_key or not session_id_value:
        raise ValueError("merge_cached_bundle_entry requires a session_id and a key.")
    if not database_enabled():
        raise RuntimeError("Database is not configured.")

    # A non-object value (NULL, or a stray JSON scalar) is treated as an empty bundle.
    set_clauses = [
        f"{column} = jsonb_set("
        f"CASE WHEN jsonb_typeof({column}) = 'object' THEN {column} ELSE '{{}}'::jsonb END, "
        f"ARRAY[%s], %s::jsonb, true)"
    ]
    params: List[Any] = [entry_key, json.dumps(value, ensure_ascii=False)]
    for legacy_column, text in legacy_text.items():
        set_clauses.append(f"{legacy_column} = %s")
        params.append(text)
    set_clauses.append("updated_at = NOW()")
    params.append(session_id_value)

    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"UPDATE session_cache SET {', '.join(set_clauses)} WHERE session_id = %s",
                    params,
                )
                updated = cursor.rowcount
            connection.commit()
    except Exception as exc:
        logger.exception("Failed to merge %s[%s] for session_id=%s", column, entry_key, session_id_value)
        raise RuntimeError("Could not save the result to the database. Please try again.") from exc
    if not updated:
        raise RuntimeError(f"Session {session_id_value} is no longer cached; the result was not saved.")


_SQL_WHITESPACE = r"E' \t\n\r\f\v'"


def _has_content_sql(column: str) -> str:
    """SQL flag: does this AI bundle column hold any output?

    True when the bundle (a JSON object) has a non-blank string value, or an
    object value with at least one truthy, non-blank member (Python's
    `str(member or "").strip()`). Computing this in Postgres means the card
    list never transfers the bundles themselves. tests/test_performance.py
    checks it against the Python rule it replaced.
    """
    return f"""EXISTS (
        SELECT 1
        FROM jsonb_each(CASE WHEN jsonb_typeof({column}) = 'object' THEN {column} ELSE '{{}}'::jsonb END) AS entry(key, value)
        WHERE (jsonb_typeof(entry.value) = 'string' AND btrim(entry.value #>> '{{}}', {_SQL_WHITESPACE}) <> '')
           OR (jsonb_typeof(entry.value) = 'object' AND EXISTS (
                SELECT 1 FROM jsonb_each(entry.value) AS member(key, value)
                WHERE (jsonb_typeof(member.value) = 'string' AND btrim(member.value #>> '{{}}', {_SQL_WHITESPACE}) <> '')
                   OR (jsonb_typeof(member.value) = 'number' AND member.value <> '0'::jsonb)
                   OR member.value = 'true'::jsonb
                   OR (jsonb_typeof(member.value) = 'array' AND jsonb_array_length(member.value) > 0)
                   OR (jsonb_typeof(member.value) = 'object' AND member.value <> '{{}}'::jsonb)
           ))
    )"""


def list_workspace_sessions(organization_id: str) -> List[Dict[str, Any]]:
    """Return the card-list fields of every cached session of an organization.

    Reverse-chronological by `updated_at`. Only what a card shows is read:
    the session's attributes object (not its attendee list), the event
    title and language, and has-output flags computed in SQL. Payloads, transcripts and
    AI bundles stay in the database; a 2-hour session row is megabytes.

    Returns [] when the database is disabled, the organization_id is
    blank, or the SELECT fails. Empty list ≠ error so the new frontend
    can treat both as "no sessions yet."
    """
    org_filter = str(organization_id or "").strip()
    if not database_enabled() or not org_filter:
        return []
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT
                        session_id,
                        organization_id,
                        CASE WHEN jsonb_typeof(session_payload -> 'data' -> 'attributes') = 'object'
                             THEN session_payload -> 'data' -> 'attributes' END AS session_attributes,
                        CASE WHEN jsonb_typeof(event_payload -> 'data' -> 'attributes') = 'object'
                             THEN event_payload -> 'data' -> 'attributes' ->> 'title' END AS event_title,
                        CASE WHEN jsonb_typeof(event_payload -> 'data' -> 'attributes') = 'object'
                             THEN event_payload -> 'data' -> 'attributes' ->> 'language' END AS event_language,
                        transcript_payload IS NOT NULL AS has_transcript,
                        {_has_content_sql("analysis_bundle")} AS has_overall_analysis,
                        {_has_content_sql("deep_analysis_bundle")} AS has_deep_analysis,
                        {_has_content_sql("smart_recap_bundle")} AS has_smart_recap,
                        {_has_content_sql("content_repurpose_bundle")} AS has_content_repurposing,
                        cover_image_bytes IS NOT NULL AS has_cover_image,
                        cover_image_mime,
                        cover_image_generated_at,
                        created_by_user_id,
                        created_by_email,
                        created_by_name,
                        created_at,
                        updated_at
                    FROM session_cache
                    WHERE organization_id = %s
                    ORDER BY updated_at DESC
                    """,
                    (org_filter,),
                )
                rows = cursor.fetchall()
        return [dict(row) for row in rows] if rows else []
    except Exception:
        logger.exception("Failed to list workspace sessions for organization_id=%s", org_filter)
        return []


def fetch_cover_image(
    session_id: str,
    *,
    organization_id: OrgScope,
) -> Optional[Dict[str, Any]]:
    """Return the cover image bytes for a session, scoped by org.

    Same scope rules as `fetch_cached_session`. Heavy column (BYTEA, often
    >1 MB), kept out of the list_workspace_sessions query so the card
    endpoint stays light. Served via GET /api/sessions/{id}/cover.png.
    Returns `{ bytes, mime, generated_at }` when present, or None.
    """
    if not database_enabled() or not str(session_id or "").strip():
        return None
    try:
        org_filter = _org_filter(organization_id)
    except ScopeMatchesNothing:
        return None
    where_clause = "WHERE session_id = %s" + (" AND organization_id = %s" if org_filter is not None else "")
    params = (str(session_id).strip(),) + ((org_filter,) if org_filter is not None else ())
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT cover_image_bytes, cover_image_mime, cover_image_generated_at
                    FROM session_cache
                    {where_clause}
                    LIMIT 1
                    """,
                    params,
                )
                row = cursor.fetchone()
        if not isinstance(row, dict):
            return None
        image_bytes = row.get("cover_image_bytes")
        if not image_bytes:
            return None
        return {
            "bytes": bytes(image_bytes),
            "mime": str(row.get("cover_image_mime") or "image/png"),
            "generated_at": row.get("cover_image_generated_at"),
        }
    except Exception:
        logger.exception("Failed to read cover image for session_id=%s", str(session_id).strip())
        return None


def fetch_oauth_connection(connection_id: str) -> Optional[Dict[str, Any]]:
    if not database_enabled() or not str(connection_id or "").strip():
        return None
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        connection_id,
                        provider,
                        user_id,
                        email,
                        organization_id,
                        access_token,
                        refresh_token,
                        token_type,
                        scope,
                        expires_at,
                        profile,
                        created_at,
                        updated_at
                    FROM oauth_connections
                    WHERE connection_id = %s
                    LIMIT 1
                    """,
                    (str(connection_id).strip(),),
                )
                row = cursor.fetchone()
        return dict(row) if isinstance(row, dict) else None
    except Exception:
        logger.exception("Failed to read oauth connection for connection_id=%s", str(connection_id).strip())
        return None


def upsert_oauth_connection(
    *,
    connection_id: str,
    provider: str,
    user_id: str,
    email: str,
    organization_id: str,
    access_token: str,
    refresh_token: str,
    token_type: str,
    scope: str,
    expires_at: Optional[str],
    profile: Dict[str, Any],
) -> None:
    if not database_enabled() or not str(connection_id or "").strip():
        return
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO oauth_connections (
                        connection_id,
                        provider,
                        user_id,
                        email,
                        organization_id,
                        access_token,
                        refresh_token,
                        token_type,
                        scope,
                        expires_at,
                        profile
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT (connection_id)
                    DO UPDATE SET
                        provider = EXCLUDED.provider,
                        user_id = EXCLUDED.user_id,
                        email = EXCLUDED.email,
                        organization_id = EXCLUDED.organization_id,
                        access_token = EXCLUDED.access_token,
                        refresh_token = EXCLUDED.refresh_token,
                        token_type = EXCLUDED.token_type,
                        scope = EXCLUDED.scope,
                        expires_at = EXCLUDED.expires_at,
                        profile = EXCLUDED.profile,
                        updated_at = NOW()
                    """,
                    (
                        str(connection_id).strip(),
                        str(provider).strip(),
                        str(user_id).strip(),
                        str(email).strip(),
                        str(organization_id).strip(),
                        str(access_token).strip(),
                        str(refresh_token).strip(),
                        str(token_type).strip() or "Bearer",
                        str(scope).strip(),
                        expires_at,
                        json.dumps(profile, ensure_ascii=False),
                    ),
                )
            connection.commit()
    except Exception:
        logger.exception("Failed to upsert oauth connection for connection_id=%s", str(connection_id).strip())


def update_oauth_connection_tokens(
    *,
    connection_id: str,
    access_token: str,
    refresh_token: str,
    token_type: str,
    scope: str,
    expires_at: Optional[str],
) -> None:
    if not database_enabled() or not str(connection_id or "").strip():
        return
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE oauth_connections
                    SET
                        access_token = %s,
                        refresh_token = %s,
                        token_type = %s,
                        scope = %s,
                        expires_at = %s,
                        updated_at = NOW()
                    WHERE connection_id = %s
                    """,
                    (
                        str(access_token).strip(),
                        str(refresh_token).strip(),
                        str(token_type).strip() or "Bearer",
                        str(scope).strip(),
                        expires_at,
                        str(connection_id).strip(),
                    ),
                )
            connection.commit()
    except Exception:
        logger.exception("Failed to update oauth tokens for connection_id=%s", str(connection_id).strip())


def create_transcript_job(session_id: str, connection_id: str = "") -> Dict[str, Any]:
    """Create a pending transcript job, unless the session already has an active one.

    Returns `{job_id, status, created}`. `created` is False when another
    request got there first: the caller must attach to that job instead of
    enqueueing a second one. The partial unique index
    `idx_transcript_jobs_one_active_per_session` makes this atomic.

    `connection_id` is the OAuth connection whose Livestorm token the worker
    will use ("" = LS_API_KEY in local dev); see `claim_transcript_job`.
    """
    session_id = str(session_id).strip()
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            for _attempt in range(2):
                now = datetime.now(timezone.utc).isoformat()
                cursor.execute(
                    """
                    INSERT INTO transcript_jobs (job_id, session_id, timestamped, status, connection_id, created_at, updated_at)
                    VALUES (%s, %s, %s, 'pending', %s, %s, %s)
                    ON CONFLICT (session_id) WHERE status IN ('pending', 'running') DO NOTHING
                    RETURNING job_id, status
                    """,
                    (uuid.uuid4().hex, session_id, True, str(connection_id or "").strip() or None, now, now),
                )
                row = cursor.fetchone()
                if row:
                    connection.commit()
                    return {"job_id": row["job_id"], "status": row["status"], "created": True}
                cursor.execute(
                    """
                    SELECT job_id, status FROM transcript_jobs
                    WHERE session_id = %s AND status IN ('pending', 'running')
                    LIMIT 1
                    """,
                    (session_id,),
                )
                row = cursor.fetchone()
                if row:
                    connection.commit()
                    return {"job_id": row["job_id"], "status": row["status"], "created": False}
                # The active job finished between the two statements; try again.
        connection.commit()
    raise RuntimeError("Could not create a transcription job. Please try again.")


def claim_transcript_job(job_id: str) -> Optional[Dict[str, Any]]:
    """Move a job from 'pending' to 'running' for the worker that will run it.

    Returns `{connection_id}` (the stored credential reference, cleared from
    the row in the same statement), or None when the job is no longer
    pending: it was swept, closed as lost, or already claimed. The worker
    must not start paid work in that case.
    """
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE transcript_jobs AS job
                SET status = 'running', connection_id = NULL, updated_at = NOW()
                FROM (
                    SELECT job_id, connection_id FROM transcript_jobs
                    WHERE job_id = %s AND status = 'pending'
                    FOR UPDATE
                ) AS claimed
                WHERE job.job_id = claimed.job_id
                RETURNING claimed.connection_id
                """,
                (str(job_id).strip(),),
            )
            row = cursor.fetchone()
        connection.commit()
    if not row:
        return None
    return {"connection_id": str(row["connection_id"] or "")}


def update_transcript_job_status(job_id: str, status: str, error: Optional[str] = None) -> None:
    """Update the status (and optional error) of a transcript job.

    A finished job ('completed' or 'error') no longer needs its stored
    connection id, so it is cleared.
    """
    if not database_enabled() or not str(job_id or "").strip():
        return
    now = datetime.now(timezone.utc).isoformat()
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE transcript_jobs
                    SET status = %s, error = %s, updated_at = %s,
                        connection_id = CASE WHEN %s IN ('completed', 'error') THEN NULL ELSE connection_id END
                    WHERE job_id = %s
                    """,
                    (status, error, now, status, str(job_id).strip()),
                )
            connection.commit()
    except Exception:
        logger.exception("Failed to update transcript job status for job_id=%s", str(job_id).strip())


def update_transcript_job_progress(job_id: str, progress: Union[Dict[str, Any], None]) -> None:
    """Store a progress snapshot (dict → JSON) on a transcript job."""
    if not database_enabled() or not str(job_id or "").strip():
        return
    now = datetime.now(timezone.utc).isoformat()
    progress_json = json.dumps(progress, ensure_ascii=False) if progress is not None else None
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE transcript_jobs
                    SET progress = %s, updated_at = %s
                    WHERE job_id = %s
                    """,
                    (progress_json, now, str(job_id).strip()),
                )
            connection.commit()
    except Exception:
        logger.exception("Failed to update transcript job progress for job_id=%s", str(job_id).strip())


def get_transcript_job_for_session(session_id: str) -> Optional[Dict[str, Any]]:
    """Return the most recent transcript job for a session, or None."""
    if not database_enabled() or not str(session_id or "").strip():
        return None
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT job_id, session_id, status, created_at, updated_at, error, progress
                    FROM transcript_jobs
                    WHERE session_id = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (str(session_id).strip(),),
                )
                row = cursor.fetchone()
        return dict(row) if isinstance(row, dict) else None
    except Exception:
        logger.exception("Failed to get transcript job for session_id=%s", str(session_id).strip())
        return None


def delete_oauth_connection(connection_id: str) -> None:
    if not database_enabled() or not str(connection_id or "").strip():
        return
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    DELETE FROM oauth_connections
                    WHERE connection_id = %s
                    """,
                    (str(connection_id).strip(),),
                )
            connection.commit()
    except Exception:
        logger.exception("Failed to delete oauth connection for connection_id=%s", str(connection_id).strip())


# ── Admin CRUD ──────────────────────────────────────────────────────────────

def is_admin_email(email: str) -> bool:
    email = str(email or "").strip().lower()
    if not email or not database_enabled():
        return False
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM admin_users WHERE LOWER(email) = %s LIMIT 1",
                    (email,),
                )
                return cursor.fetchone() is not None
    except Exception:
        logger.exception("Failed to check admin status for email=%s", email)
        return False


def promote_admin(email: str, user_id: str, promoted_by_email: str) -> None:
    if not database_enabled():
        return
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO admin_users (email, user_id, promoted_by_email)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (email) DO UPDATE SET
                        user_id = EXCLUDED.user_id,
                        promoted_by_email = EXCLUDED.promoted_by_email
                    """,
                    (str(email).strip().lower(), str(user_id or "").strip(), str(promoted_by_email or "").strip().lower()),
                )
            connection.commit()
    except Exception:
        logger.exception("Failed to promote admin email=%s", email)


def demote_admin(email: str) -> None:
    if not database_enabled():
        return
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM admin_users WHERE LOWER(email) = %s",
                    (str(email).strip().lower(),),
                )
            connection.commit()
    except Exception:
        logger.exception("Failed to demote admin email=%s", email)


def admin_list_users() -> List[Dict[str, Any]]:
    if not database_enabled():
        return []
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT DISTINCT ON (oc.user_id)
                        oc.user_id,
                        oc.email,
                        oc.organization_id,
                        oc.created_at,
                        oc.updated_at,
                        (au.email IS NOT NULL) AS is_admin
                    FROM oauth_connections oc
                    LEFT JOIN admin_users au ON LOWER(oc.email) = au.email
                    WHERE oc.provider = 'livestorm' AND oc.user_id IS NOT NULL
                    ORDER BY oc.user_id, oc.updated_at DESC
                    """
                )
                users = [dict(row) for row in cursor.fetchall()]
                # Attach session stats per organization
                cursor.execute(
                    """
                    SELECT
                        organization_id,
                        COUNT(*) AS session_count,
                        COUNT(CASE WHEN transcript_payload IS NOT NULL THEN 1 END) AS transcript_count,
                        COUNT(CASE WHEN analysis_bundle IS NOT NULL THEN 1 END) AS overall_count,
                        COUNT(CASE WHEN deep_analysis_bundle IS NOT NULL THEN 1 END) AS deep_count,
                        COUNT(CASE WHEN smart_recap_bundle IS NOT NULL THEN 1 END) AS recap_count,
                        COUNT(CASE WHEN content_repurpose_bundle IS NOT NULL THEN 1 END) AS repurposing_count
                    FROM session_cache
                    WHERE organization_id IS NOT NULL
                    GROUP BY organization_id
                    """
                )
                stats_by_org: Dict[str, Any] = {row["organization_id"]: dict(row) for row in cursor.fetchall()}
                for user in users:
                    org_id = user.get("organization_id") or ""
                    stats = stats_by_org.get(org_id, {})
                    user["session_count"] = stats.get("session_count", 0)
                    user["transcript_count"] = stats.get("transcript_count", 0)
                    user["overall_count"] = stats.get("overall_count", 0)
                    user["deep_count"] = stats.get("deep_count", 0)
                    user["recap_count"] = stats.get("recap_count", 0)
                    user["repurposing_count"] = stats.get("repurposing_count", 0)
        return users
    except Exception:
        logger.exception("Failed to list admin users")
        return []


# ── System settings ───────────────────────────────────────────────────────────

# Settings are read on every LLM call. They are cached per process for a
# minute (another process sees an admin change within that time), and a
# failed read serves the last known value instead of silently falling back
# to the defaults (which could switch the LLM provider or model mid-flight).
SETTINGS_CACHE_TTL_SECONDS = 60.0
_settings_cache: Dict[tuple, tuple] = {}
_settings_cache_lock = threading.Lock()


def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    if not database_enabled():
        return default
    cache_key = (get_database_url(), str(key).strip())
    with _settings_cache_lock:
        cached = _settings_cache.get(cache_key)
    if cached is not None and time.monotonic() - cached[0] < SETTINGS_CACHE_TTL_SECONDS:
        return cached[1] if cached[1] is not None else default
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT value FROM system_settings WHERE key = %s", (cache_key[1],))
                row = cursor.fetchone()
        value = str(row["value"]) if row else None
    except Exception:
        logger.exception("Failed to read system_setting key=%s", key)
        if cached is not None:
            return cached[1] if cached[1] is not None else default
        return default
    with _settings_cache_lock:
        _settings_cache[cache_key] = (time.monotonic(), value)
    return value if value is not None else default


def set_setting(key: str, value: str) -> None:
    if not database_enabled():
        return
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO system_settings (key, value, updated_at)
                    VALUES (%s, %s, NOW())
                    ON CONFLICT (key) DO UPDATE
                        SET value = EXCLUDED.value,
                            updated_at = NOW()
                    """,
                    (str(key).strip(), str(value)),
                )
            connection.commit()
    except Exception:
        logger.exception("Failed to set system_setting key=%s", key)
        raise
    with _settings_cache_lock:
        _settings_cache[(get_database_url(), str(key).strip())] = (time.monotonic(), str(value))


def get_all_settings() -> Dict[str, str]:
    if not database_enabled():
        return {}
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT key, value FROM system_settings ORDER BY key")
                return {row["key"]: row["value"] for row in cursor.fetchall()}
    except Exception:
        logger.exception("Failed to load system_settings")
        return {}


def admin_list_sessions(limit: int = 500) -> List[Dict[str, Any]]:
    if not database_enabled():
        return []
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        session_id,
                        organization_id,
                        created_by_email,
                        created_by_name,
                        created_at,
                        updated_at,
                        (transcript_payload IS NOT NULL) AS has_transcript,
                        (analysis_bundle IS NOT NULL) AS has_overall,
                        (deep_analysis_bundle IS NOT NULL) AS has_deep,
                        (smart_recap_bundle IS NOT NULL) AS has_recap,
                        (content_repurpose_bundle IS NOT NULL) AS has_repurposing,
                        (cover_image_bytes IS NOT NULL) AS has_cover,
                        event_payload->'data'->'attributes'->>'title' AS event_title,
                        session_payload->'data'->'attributes'->>'name' AS session_name
                    FROM session_cache
                    ORDER BY updated_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                return [dict(row) for row in cursor.fetchall()]
    except Exception:
        logger.exception("Failed to list sessions for admin")
        return []


def admin_delete_session(session_id: str) -> bool:
    if not database_enabled() or not str(session_id or "").strip():
        return False
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM session_cache WHERE session_id = %s",
                    (str(session_id).strip(),),
                )
                deleted = cursor.rowcount > 0
            connection.commit()
        return deleted
    except Exception:
        logger.exception("Failed to delete session session_id=%s", session_id)
        return False
