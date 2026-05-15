import hashlib
import json
import os
import logging
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, Union

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
    organization_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Read a cached session row.

    `organization_id`:
        - None (default) → no filter. Used by trusted internal code (worker
          jobs reading cached payloads, polling endpoints that have already
          authorized the caller). Returns the session regardless of which
          org owns it.
        - non-empty str → filter `organization_id = ...`. Used by web routes
          that need to enforce org-scoped visibility — teammates in the
          same org see the cache, cross-org callers don't.
        - empty str ""  → treated like None (no filter). Lets callers pass
          a freshly-resolved org_id without explicit null-checking; if the
          user has no OAuth connection, fall back to legacy behaviour.

    `api_key` is kept in the signature for back-compat — it's no longer
    used in the lookup.
    """
    if not database_enabled() or not str(session_id or "").strip():
        return None
    org_filter = str(organization_id or "").strip() if organization_id is not None else None
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                if org_filter:
                    cursor.execute(
                        """
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
                        WHERE session_id = %s
                          AND organization_id = %s
                        ORDER BY updated_at DESC
                        LIMIT 1
                        """,
                        (str(session_id).strip(), org_filter),
                    )
                else:
                    cursor.execute(
                        """
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
    preserve_on_update = {"created_by_user_id", "created_by_email", "created_by_name"}
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
    except Exception:
        logger.exception("Failed to upsert cached session for session_id=%s", session_id_value)


def list_workspace_sessions(organization_id: str) -> List[Dict[str, Any]]:
    """Return cached session rows belonging to the given organization.

    Reverse-chronological by `updated_at`. The session_payload column
    carries enough Livestorm attribute data for the card list; chat /
    questions / transcript / bundles are NOT loaded here — those are
    only fetched when the user opens a specific session.

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
                    """
                    SELECT
                        session_id,
                        organization_id,
                        session_payload,
                        event_payload,
                        transcript_payload IS NOT NULL AS has_transcript,
                        analysis_bundle,
                        deep_analysis_bundle,
                        smart_recap_bundle,
                        content_repurpose_bundle,
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
    organization_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Return the cover image bytes for a session, scoped by org.

    Heavy column (BYTEA, often >1 MB), kept out of the list_workspace_sessions
    query so the card endpoint stays light. Served via its own
    GET /api/sessions/{id}/cover.png route. Returns `{ bytes, mime,
    generated_at }` when present, or None.
    """
    if not database_enabled() or not str(session_id or "").strip():
        return None
    org_filter = str(organization_id or "").strip() if organization_id is not None else None
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                if org_filter:
                    cursor.execute(
                        """
                        SELECT cover_image_bytes, cover_image_mime, cover_image_generated_at
                        FROM session_cache
                        WHERE session_id = %s AND organization_id = %s
                        LIMIT 1
                        """,
                        (str(session_id).strip(), org_filter),
                    )
                else:
                    cursor.execute(
                        """
                        SELECT cover_image_bytes, cover_image_mime, cover_image_generated_at
                        FROM session_cache
                        WHERE session_id = %s
                        LIMIT 1
                        """,
                        (str(session_id).strip(),),
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

def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    if not database_enabled():
        return default
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT value FROM system_settings WHERE key = %s",
                    (str(key).strip(),),
                )
                row = cursor.fetchone()
                return str(row["value"]) if row else default
    except Exception:
        logger.exception("Failed to read system_setting key=%s", key)
        return default


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
