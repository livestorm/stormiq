import hashlib
import json
import os
import logging
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, Optional, Union

from psycopg import connect
from psycopg.rows import dict_row


DATABASE_URL_ENV_VARS = ("DATABASE_URL", "POSTGRES_URL", "RENDER_POSTGRES_URL")
logger = logging.getLogger(__name__)


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


@contextmanager
def get_db_connection() -> Iterator[Any]:
    database_url = get_database_url()
    if not database_url:
        raise RuntimeError("Database is not configured.")
    with connect(database_url, row_factory=dict_row) as connection:
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
                ALTER TABLE transcript_jobs
                ADD COLUMN IF NOT EXISTS progress TEXT
                """
            )
        connection.commit()


def fetch_cached_session(api_key: str, session_id: str) -> Optional[Dict[str, Any]]:
    if not database_enabled() or not str(session_id or "").strip():
        return None
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        account_key_hash,
                        session_id,
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
                        created_at,
                        updated_at
                    FROM session_cache
                    WHERE session_id = %s
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (str(session_id).strip(),),
                )
                row = cursor.fetchone()
        return dict(row) if isinstance(row, dict) else None
    except Exception:
        logger.exception("Failed to read cached session for session_id=%s", str(session_id).strip())
        return None


def upsert_cached_session(api_key: str, session_id: str, **fields: Any) -> None:
    if not database_enabled() or not str(api_key or "").strip() or not str(session_id or "").strip():
        return

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
    }
    persisted_fields = {key: value for key, value in fields.items() if key in allowed_fields}
    if not persisted_fields:
        return

    session_id_value = str(session_id).strip()
    account_key_hash = build_account_key_hash(api_key)
    columns = ["account_key_hash", "session_id", *persisted_fields.keys()]
    placeholders = ["%s", "%s"]
    insert_values = [account_key_hash, session_id_value]
    update_clauses = ["account_key_hash = EXCLUDED.account_key_hash"]

    for key, value in persisted_fields.items():
        if isinstance(value, (dict, list)):
            insert_values.append(json.dumps(value, ensure_ascii=False))
            placeholders.append("%s::jsonb")
        else:
            insert_values.append(value)
            placeholders.append("%s")
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
    except Exception:
        logger.exception("Failed to upsert cached session for session_id=%s", session_id_value)


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


def create_transcript_job(session_id: str) -> str:
    """Insert a new transcript job record and return its job_id."""
    job_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO transcript_jobs (job_id, session_id, timestamped, status, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (job_id, str(session_id).strip(), True, "pending", now, now),
            )
        connection.commit()
    return job_id


def update_transcript_job_status(job_id: str, status: str, error: Optional[str] = None) -> None:
    """Update the status (and optional error) of a transcript job."""
    if not database_enabled() or not str(job_id or "").strip():
        return
    now = datetime.now(timezone.utc).isoformat()
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE transcript_jobs
                    SET status = %s, error = %s, updated_at = %s
                    WHERE job_id = %s
                    """,
                    (status, error, now, str(job_id).strip()),
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
