import atexit
import hashlib
import json
import math
import os
import logging
import re
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Union

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
                    faq_bundle JSONB,
                    decisions_bundle JSONB,
                    team_digest_bundle JSONB,
                    attendee_followup_bundle JSONB,
                    no_show_followup_bundle JSONB,
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
                ADD COLUMN IF NOT EXISTS faq_bundle JSONB
                """
            )
            cursor.execute(
                """
                ALTER TABLE session_cache
                ADD COLUMN IF NOT EXISTS decisions_bundle JSONB
                """
            )
            cursor.execute(
                """
                ALTER TABLE session_cache
                ADD COLUMN IF NOT EXISTS team_digest_bundle JSONB
                """
            )
            cursor.execute(
                """
                ALTER TABLE session_cache
                ADD COLUMN IF NOT EXISTS attendee_followup_bundle JSONB
                """
            )
            cursor.execute(
                """
                ALTER TABLE session_cache
                ADD COLUMN IF NOT EXISTS no_show_followup_bundle JSONB
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
            # The cover image columns are no longer read or written (the
            # cover feature was removed in November 2026). They are kept,
            # because dropping a column can't be undone; reset_ai_outputs
            # (include_covers) clears what they still hold.
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
            # Set when the session was added with "Add" (`api_logic.
            # fetch_session_transcript_data(analyse=True)`): when the job
            # ends, the worker queues the session analysis.
            cursor.execute(
                """
                ALTER TABLE transcript_jobs
                ADD COLUMN IF NOT EXISTS then_analyse BOOLEAN NOT NULL DEFAULT FALSE
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
            # One row per answered text-model call (ai/usage.py): ids, the model,
            # token counts, the estimated cost, timings and the stop reason.
            # Never a prompt, an output or a key. No foreign key to
            # session_cache: the rows hold no content and outlive a deleted
            # session, so past spend stays counted.
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS llm_usage (
                    id                    BIGSERIAL PRIMARY KEY,
                    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    organization_id       TEXT,
                    session_id            TEXT,
                    job_kind              TEXT NOT NULL,
                    job_id                TEXT,
                    purpose               TEXT NOT NULL,
                    provider              TEXT NOT NULL,
                    model_requested       TEXT NOT NULL,
                    model_served          TEXT,
                    effort                TEXT,
                    max_tokens            INTEGER,
                    streamed              BOOLEAN NOT NULL DEFAULT FALSE,
                    stop_reason           TEXT NOT NULL,
                    refusal_category      TEXT,
                    outcome               TEXT NOT NULL,
                    input_tokens          INTEGER NOT NULL DEFAULT 0,
                    output_tokens         INTEGER NOT NULL DEFAULT 0,
                    cache_read_tokens     INTEGER NOT NULL DEFAULT 0,
                    cache_write_tokens    INTEGER NOT NULL DEFAULT 0,
                    cache_write_1h_tokens INTEGER NOT NULL DEFAULT 0,
                    reasoning_tokens      INTEGER,
                    cost_usd_micros       BIGINT,
                    duration_ms           INTEGER,
                    request_id            TEXT,
                    prompt_hash           TEXT,
                    dossier_hash          TEXT,
                    record_version        TEXT,
                    usage_raw             JSONB
                )
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_llm_usage_session_id
                ON llm_usage (session_id, created_at DESC)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_llm_usage_organization_id
                ON llm_usage (organization_id, created_at DESC)
                """
            )
            _ensure_people_schema(cursor)
            _ensure_chat_labels_schema(cursor)
            _ensure_question_replies_schema(cursor)
            _ensure_figure_checks_schema(cursor)
            _ensure_understanding_schema(cursor)
            _ensure_output_versions_schema(cursor)
            _ensure_org_settings_schema(cursor)
            _ensure_surprise_voice_schema(cursor)
            _ensure_surprise_storyboard_schema(cursor)
            _ensure_surprise_script_schema(cursor)
            _ensure_session_faq_schema(cursor)
        connection.commit()


def _ensure_people_schema(cursor: Any) -> None:
    """The people tables: the workspace's field labels, each session's roster
    header and person rows, and the Livestorm sync jobs.

    The per-session tables reference `session_cache (session_id)` through the
    non-partial unique index `idx_session_cache_session_id_unique`, so they
    must be created after it. `ON DELETE CASCADE`: deleting a cached session
    (`admin_delete_session`) deletes its roster, people and sync rows. They
    carry no `organization_id`: every read joins `session_cache` and filters
    on its org (`fetch_roster_status`, `list_session_people`).
    """
    # One row per org: the registration fields defined in the workspace
    # (`payloads.parse_people_attributes`). No personal data.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS org_people_attributes (
            organization_id TEXT PRIMARY KEY,
            attributes      JSONB NOT NULL DEFAULT '[]'::jsonb,
            status          TEXT NOT NULL,
            fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS session_rosters (
            session_id     TEXT PRIMARY KEY REFERENCES session_cache (session_id) ON DELETE CASCADE,
            roster_version INTEGER NOT NULL,
            source         TEXT NOT NULL,
            complete       BOOLEAN,
            record_count   INTEGER,
            people_count   INTEGER NOT NULL,
            fields         JSONB NOT NULL DEFAULT '[]'::jsonb,
            fields_source  TEXT NOT NULL,
            reason         TEXT NOT NULL,
            synced_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            refresh_due_at TIMESTAMPTZ,
            refreshed_at   TIMESTAMPTZ
        )
        """
    )
    # One row per person, in the `payloads.parse_person` shape: the email
    # domain, never the address (the CHECK refuses an "@"); the referrer's
    # host, never the URL; no join link, phone, address, device or avatar.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS session_people (
            session_id            TEXT NOT NULL REFERENCES session_cache (session_id) ON DELETE CASCADE,
            person_id             TEXT NOT NULL,
            person_ref            TEXT NOT NULL,
            livestorm_role        TEXT,
            is_guest_speaker      BOOLEAN NOT NULL DEFAULT FALSE,
            session_role          TEXT,
            first_name            TEXT,
            last_name             TEXT,
            email_domain          TEXT CHECK (email_domain IS NULL OR position('@' IN email_domain) = 0),
            company               TEXT,
            job_title             TEXT,
            answers               JSONB NOT NULL DEFAULT '{}'::jsonb,
            registered_at         TIMESTAMPTZ,
            registration_type     TEXT,
            referrer_domain       TEXT,
            utm_source            TEXT,
            utm_medium            TEXT,
            utm_campaign          TEXT,
            utm_term              TEXT,
            utm_content           TEXT,
            timezone              TEXT,
            country_code          TEXT,
            country_name          TEXT,
            attended              BOOLEAN,
            attendance_duration_s INTEGER,
            attendance_rate       DOUBLE PRECISION,
            has_viewed_replay     BOOLEAN,
            replay_watched_at     TIMESTAMPTZ,
            ls_messages_count     INTEGER,
            ls_questions_count    INTEGER,
            ls_votes_count        INTEGER,
            ls_up_votes_count     INTEGER,
            ls_updated_at         TIMESTAMPTZ,
            PRIMARY KEY (session_id, person_id)
        )
        """
    )
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_session_people_ref
        ON session_people (session_id, person_ref)
        """
    )
    # Worker jobs that call Livestorm with a user's credentials (the people
    # sync). `connection_id` is also a session cookie value. Three statements
    # clear it: the claim (`claim_livestorm_sync`); any update that moves the
    # row out of 'pending' (`update_livestorm_sync`), which closes a failed
    # enqueue or a lost job; and the stuck-job sweeper
    # (`sweep_stuck_livestorm_syncs`).
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS livestorm_sync_jobs (
            sync_id            TEXT PRIMARY KEY,
            session_id         TEXT NOT NULL REFERENCES session_cache (session_id) ON DELETE CASCADE,
            kind               TEXT NOT NULL,
            reason             TEXT NOT NULL,
            status             TEXT NOT NULL,
            due_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            connection_id      TEXT,
            credential_user_id TEXT,
            pages_fetched      INTEGER,
            record_count       INTEGER,
            people_written     INTEGER,
            error              TEXT,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_livestorm_sync_one_active
        ON livestorm_sync_jobs (session_id, kind)
        WHERE status IN ('pending', 'running')
        """
    )
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_livestorm_sync_one_scheduled
        ON livestorm_sync_jobs (session_id, kind, reason)
        WHERE status = 'scheduled'
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_livestorm_sync_due
        ON livestorm_sync_jobs (status, due_at)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_livestorm_sync_session
        ON livestorm_sync_jobs (session_id, created_at DESC)
        """
    )


def _ensure_chat_labels_schema(cursor: Any) -> None:
    """One row per session: the labels of its chat, stored whole
    (`replace_chat_labels`). Like the people tables, it references
    `session_cache` (deleted with it) and has no org column: every read
    joins `session_cache` on its org (`fetch_chat_labels`)."""
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS session_chat_labels (
            session_id    TEXT PRIMARY KEY REFERENCES session_cache (session_id) ON DELETE CASCADE,
            label_version INTEGER NOT NULL,
            chat_hash     TEXT NOT NULL,
            model         TEXT NOT NULL,
            message_count INTEGER NOT NULL,
            labels        JSONB NOT NULL,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )


def _ensure_understanding_schema(cursor: Any) -> None:
    """One row per session: what the understanding job read in it, checked
    (`record.understanding.check_understanding`), stored whole
    (`replace_understanding`). References `session_cache` (deleted with it)
    and has no org column: every read joins `session_cache` on its org."""
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS session_understanding (
            session_id   TEXT PRIMARY KEY REFERENCES session_cache (session_id) ON DELETE CASCADE,
            version      INTEGER NOT NULL,
            dossier_hash TEXT NOT NULL,
            model        TEXT NOT NULL,
            result       JSONB NOT NULL,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )


def _ensure_output_versions_schema(cursor: Any) -> None:
    """Every AI output as it was saved, one row per save
    (`merge_cached_bundle_entry`): the bundle column and entry it went to,
    the text (or the content bundle's sections) as saved, and what made it
    (`ai.usage.run_provenance`): the models, prompt and dossier hashes and
    record versions of the run's answered calls, and the job's id. The
    bundle column keeps only the latest; these keep the earlier ones.
    References `session_cache` (deleted with it) and has no org column:
    every read joins `session_cache` on its org."""
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS output_versions (
            id              BIGSERIAL PRIMARY KEY,
            session_id      TEXT NOT NULL REFERENCES session_cache (session_id) ON DELETE CASCADE,
            output_column   TEXT NOT NULL,
            entry_key       TEXT NOT NULL,
            content         JSONB NOT NULL,
            job_id          TEXT,
            job_kinds       TEXT[] NOT NULL DEFAULT '{}',
            models          TEXT[] NOT NULL DEFAULT '{}',
            prompt_hashes   TEXT[] NOT NULL DEFAULT '{}',
            dossier_hashes  TEXT[] NOT NULL DEFAULT '{}',
            record_versions TEXT[] NOT NULL DEFAULT '{}',
            calls           INTEGER NOT NULL DEFAULT 0,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_output_versions_session
        ON output_versions (session_id, output_column, entry_key, created_at DESC)
        """
    )


def _ensure_org_settings_schema(cursor: Any) -> None:
    """One row per organization that has a setting a StormIQ admin changed
    (the Admin page's Organizations): whether Surprise Me! is on, who
    changed it and when. An organization without a row has the defaults
    (Surprise Me! on). No personal data but the admin's email."""
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS org_settings (
            organization_id  TEXT PRIMARY KEY,
            surprise_enabled BOOLEAN NOT NULL,
            updated_by_email TEXT,
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )


def _ensure_figure_checks_schema(cursor: Any) -> None:
    """Before you share (`record/figure_checks.py`): the host's answer to
    each check on a figure said on stage, keyed by the check's key (what was
    said, never a renumbered figure id). References `session_cache`
    (deleted with it) and has no org column: every read and write joins
    `session_cache` on its org."""
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS session_figure_checks (
            session_id  TEXT NOT NULL REFERENCES session_cache (session_id) ON DELETE CASCADE,
            check_key   TEXT NOT NULL,
            choice      TEXT NOT NULL,
            updated_by  TEXT,
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (session_id, check_key)
        )
        """
    )


def _ensure_session_faq_schema(cursor: Any) -> None:
    """The editable FAQ (`record/faq_entries.py`): the entries the user saved
    for one session and language, and what the question desk looked like
    then (`desk_stamp`: each desk id `answered` or `open`), to offer what the
    desk answered since. References `session_cache` (deleted with it) and has
    no org column: every read and write joins `session_cache` on its org.
    `updated_by` is a Livestorm user id, never an email."""
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS session_faq (
            session_id  TEXT NOT NULL REFERENCES session_cache (session_id) ON DELETE CASCADE,
            language    TEXT NOT NULL DEFAULT 'English',
            entries     JSONB NOT NULL,
            desk_stamp  JSONB,
            updated_by  TEXT,
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (session_id, language)
        )
        """
    )


def _ensure_surprise_voice_schema(cursor: Any) -> None:
    """Surprise me, read aloud (`ai/surprise_voice.py`): one mp3 per beat of
    the take, keyed by `clip_key` (a hash of the beat's text, the model, the
    voice and the delivery notes), so a replay or a reload costs nothing.
    Each row is a whole clip, written once it is made. References
    `session_cache` (deleted with it) and has no org column: every read joins
    `session_cache` on its org."""
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS surprise_voice_clips (
            session_id  TEXT NOT NULL REFERENCES session_cache (session_id) ON DELETE CASCADE,
            clip_key    TEXT NOT NULL,
            mime        TEXT NOT NULL,
            audio       BYTEA NOT NULL,
            duration_s  DOUBLE PRECISION,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (session_id, clip_key)
        )
        """
    )


def _ensure_surprise_storyboard_schema(cursor: Any) -> None:
    """Surprise me's storyboard (`ai/surprise_story.py`): what the player
    shows for each beat, one row per session, keyed by `story_key` (the
    take's beats and the storyboard's version): a read with another key
    finds none. Written whole by the voice job, after the check. References
    `session_cache` (deleted with it) and has no org column: every read
    joins `session_cache` on its org."""
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS surprise_storyboards (
            session_id  TEXT PRIMARY KEY REFERENCES session_cache (session_id) ON DELETE CASCADE,
            story_key   TEXT NOT NULL,
            storyboard  JSONB NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )


def _ensure_surprise_script_schema(cursor: Any) -> None:
    """Surprise me's performance script (`ai/surprise_performance.py`): the
    lines the voice says, one row per session, keyed by `script_key` (the
    take's beats and the script's version): a read with another key finds
    none. Written whole by the show job, after the check (or the take's own
    beats when the script failed it). Never served to the page: only its
    lines' beats and clip keys are. References `session_cache` (deleted with
    it) and has no org column: every read joins `session_cache` on its org."""
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS surprise_scripts (
            session_id  TEXT PRIMARY KEY REFERENCES session_cache (session_id) ON DELETE CASCADE,
            script_key  TEXT NOT NULL,
            script      JSONB NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )


def _ensure_question_replies_schema(cursor: Any) -> None:
    """What the host saved in the question desk (`record/question_desk.py`):
    one row per question they marked or replied to, keyed by its source
    (`qa` or `chat`) and Livestorm id, with the evidence id it had when
    saved. References `session_cache` (deleted with it) and has no org
    column: every read and write joins `session_cache` on its org.

    `located` is what the host decided on a located answer (the
    understanding job's passage that answers the question): `confirmed`,
    `rejected`, or NULL when they haven't (`save_located_decision`)."""
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS session_question_replies (
            session_id   TEXT NOT NULL REFERENCES session_cache (session_id) ON DELETE CASCADE,
            source       TEXT NOT NULL CHECK (source IN ('qa', 'chat')),
            livestorm_id TEXT NOT NULL,
            evidence_id  TEXT NOT NULL,
            answered     BOOLEAN NOT NULL,
            reply        TEXT,
            updated_by   TEXT,
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (session_id, source, livestorm_id)
        )
        """
    )
    cursor.execute(
        """
        ALTER TABLE session_question_replies
            ADD COLUMN IF NOT EXISTS located TEXT CHECK (located IN ('confirmed', 'rejected'))
        """
    )


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
                        faq_bundle,
                        decisions_bundle,
                        team_digest_bundle,
                        attendee_followup_bundle,
                        no_show_followup_bundle,
                        event_payload,
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


# Light reads for polling, job starts and the v1 record: only these fields
# can be asked for, by name. A 2-hour session row is megabytes; a poll needs
# a flag, and the record reads the base payloads and, on a cache miss only,
# the transcript and its speaker labels.
_SESSION_FIELD_SQL = {
    "has_transcript": "transcript_payload IS NOT NULL",
    "session_payload": "session_payload",
    "event_payload": "event_payload",
    "chat_payload": "chat_payload",
    "questions_payload": "questions_payload",
    "transcript_payload": "transcript_payload",
    "transcript_speaker_names": "transcript_speaker_names",
    "analysis_bundle": "analysis_bundle",
    "analysis_md": "analysis_md",
    "deep_analysis_bundle": "deep_analysis_bundle",
    "deep_analysis_md": "deep_analysis_md",
    "smart_recap_bundle": "smart_recap_bundle",
    "content_repurpose_bundle": "content_repurpose_bundle",
    "faq_bundle": "faq_bundle",
    "decisions_bundle": "decisions_bundle",
    "team_digest_bundle": "team_digest_bundle",
    "attendee_followup_bundle": "attendee_followup_bundle",
    "no_show_followup_bundle": "no_show_followup_bundle",
    "updated_at": "updated_at",
    "organization_id": "organization_id",
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
        "faq_bundle",
        "decisions_bundle",
        "team_digest_bundle",
        "attendee_followup_bundle",
        "no_show_followup_bundle",
        "organization_id",
        "event_payload",
        "created_by_user_id",
        "created_by_email",
        "created_by_name",
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
_BUNDLE_COLUMNS = {
    "analysis_bundle", "deep_analysis_bundle", "smart_recap_bundle", "content_repurpose_bundle", "faq_bundle", "decisions_bundle",
    "team_digest_bundle", "attendee_followup_bundle", "no_show_followup_bundle",
}
# Legacy single-language TEXT columns that may be written alongside a bundle entry.
_LEGACY_TEXT_COLUMNS = {"analysis_md", "deep_analysis_md"}


_PROVENANCE_LISTS = ("job_kinds", "models", "prompt_hashes", "dossier_hashes", "record_versions")


def _provenance_list(provenance: Mapping[str, Any], key: str) -> List[str]:
    values = provenance.get(key)
    if not isinstance(values, (list, tuple)):
        return []
    return [str(value) for value in values if isinstance(value, str) and value]


def merge_cached_bundle_entry(
    session_id: str,
    column: str,
    key: str,
    value: Any,
    *,
    provenance: Optional[Mapping[str, Any]] = None,
    **legacy_text: str,
) -> None:
    """Atomically set `column[key] = value` without touching the bundle's other keys.

    One UPDATE with jsonb_set: concurrent writers to the same row (English
    and French analyses, or two Smart Recap tones) are serialised by
    Postgres and each sees the other's entry, so neither result is lost.
    `legacy_text` optionally sets `analysis_md` / `deep_analysis_md` in the
    same statement. In the same transaction, the saved value is kept as a
    row of `output_versions` with what made it (`provenance`, from
    `ai.usage.run_provenance`: ids and hashes only). Raises if the row does
    not exist or either write fails; then neither is saved.
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
                if updated:
                    made = provenance if isinstance(provenance, Mapping) else {}
                    job_id = made.get("job_id")
                    calls = made.get("calls")
                    cursor.execute(
                        """
                        INSERT INTO output_versions (
                            session_id, output_column, entry_key, content, job_id,
                            job_kinds, models, prompt_hashes, dossier_hashes, record_versions, calls
                        ) VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        [
                            session_id_value, column, entry_key, json.dumps(value, ensure_ascii=False),
                            job_id if isinstance(job_id, str) and job_id else None,
                            *[_provenance_list(made, name) for name in _PROVENANCE_LISTS],
                            calls if isinstance(calls, int) and not isinstance(calls, bool) and calls >= 0 else 0,
                        ],
                    )
            if updated:
                connection.commit()
            else:
                connection.rollback()
    except Exception as exc:
        logger.exception("Failed to merge %s[%s] for session_id=%s", column, entry_key, session_id_value)
        raise RuntimeError("Could not save the result to the database. Please try again.") from exc
    if not updated:
        raise RuntimeError(f"Session {session_id_value} is no longer cached; the result was not saved.")


# The raw Livestorm payloads saved by a base fetch.
BASE_PAYLOAD_COLUMNS = ("session_payload", "chat_payload", "questions_payload")


def scan_base_payloads(after_session_id: str = "", limit: int = 50) -> List[Dict[str, Any]]:
    """Read a batch of rows' base payloads, in `session_id` order.

    For maintenance scripts that walk every cached session: pass the last
    `session_id` of one batch as `after_session_id` to get the next. Reads
    every organization's rows, so web routes never call it. Returns
    `session_id`, `updated_at` and the three base payload columns. Raises
    on failure.
    """
    if not database_enabled():
        raise RuntimeError("Database is not configured.")
    columns = ", ".join(BASE_PAYLOAD_COLUMNS)
    with get_db_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT session_id, updated_at, {columns}
            FROM session_cache
            WHERE session_id > %s
            ORDER BY session_id
            LIMIT %s
            """,
            (str(after_session_id or ""), max(1, int(limit))),
        ).fetchall()
    return [dict(row) for row in rows]


def rewrite_base_payloads(session_id: str, *, expected_updated_at: datetime, **payloads: Any) -> bool:
    """Replace some of a row's base payloads in place, leaving `updated_at` alone.

    For maintenance rewrites that change nothing the app reads, such as
    removing registrant join links. `upsert_cached_session` would set
    `updated_at = NOW()`, which reorders the session library and drops the
    row's cached transcript insights.

    The write happens only if the row's `updated_at` still equals
    `expected_updated_at` (the value read with the payloads). If a fetch or
    an AI result was saved in between, nothing is written and this returns
    False; it also returns False when the row is gone. Each payload must be
    a JSON object. Raises on database errors.
    """
    unknown = sorted(set(payloads) - set(BASE_PAYLOAD_COLUMNS))
    if unknown or not payloads:
        raise ValueError(f"rewrite_base_payloads takes only {BASE_PAYLOAD_COLUMNS}, got {unknown or 'nothing'}.")
    not_objects = sorted(column for column, value in payloads.items() if not isinstance(value, dict))
    if not_objects:
        raise ValueError(f"Base payloads must be JSON objects: {not_objects}")
    session_id_value = str(session_id or "").strip()
    if not session_id_value or not isinstance(expected_updated_at, datetime):
        raise ValueError("rewrite_base_payloads requires a session_id and the updated_at it read.")
    if not database_enabled():
        raise RuntimeError("Database is not configured.")

    set_clauses = [f"{column} = %s::jsonb" for column in payloads]
    params: List[Any] = [json.dumps(value, ensure_ascii=False) for value in payloads.values()]
    params.extend([session_id_value, expected_updated_at])
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"UPDATE session_cache SET {', '.join(set_clauses)} WHERE session_id = %s AND updated_at = %s",
                    params,
                )
                updated = cursor.rowcount
            connection.commit()
    except Exception as exc:
        logger.exception("Failed to rewrite base payloads for session_id=%s", session_id_value)
        raise RuntimeError("Could not rewrite the session's payloads.") from exc
    return bool(updated)


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


def mark_transcript_job_then_analyse(job_id: str) -> None:
    """Have the worker queue the session analysis when this transcript job
    ends (`transcript_jobs.then_analyse`). Raises when the database can't be
    written."""
    if not database_enabled() or not str(job_id or "").strip():
        return
    with get_db_connection() as connection:
        connection.execute("UPDATE transcript_jobs SET then_analyse = TRUE WHERE job_id = %s", (str(job_id).strip(),))
        connection.commit()


def transcript_job_then_analyse(job_id: str) -> bool:
    """Whether the session analysis should follow this transcript job."""
    if not database_enabled() or not str(job_id or "").strip():
        return False
    with get_db_connection() as connection:
        row = connection.execute("SELECT then_analyse FROM transcript_jobs WHERE job_id = %s", (str(job_id).strip(),)).fetchone()
    if row is None:
        return False
    value = row["then_analyse"] if isinstance(row, dict) else row[0]
    return bool(value)


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


# ── People: rosters, person rows, field labels and sync jobs ─────────────
#
# A session's people, as Livestorm listed them, are stored whole: one roster
# header (`session_rosters`) and one row per person (`session_people`),
# replaced together in one transaction. The per-session tables have no org
# column; every read joins `session_cache` and filters on its org, so a blank
# scope matches nothing and never queries. The people sync job
# (`people_sync.py`, `worker.run_people_sync`) writes them (docs/data-model.md).

ROSTER_SOURCES = ("paginated", "session_include")
ROSTER_FIELDS_SOURCES = ("people_attributes", "event_fields", "none")
ROSTER_REASONS = ("initial", "refresh_t7", "manual", "backfill")
PEOPLE_ATTRIBUTES_STATUSES = ("ok", "forbidden", "error")
SYNC_KINDS = ("people",)
SYNC_REASONS = ("initial", "refresh_t7", "manual")
SYNC_STATUSES = ("scheduled", "pending", "running", "completed", "error", "waiting_for_user", "deferred")

# A person row: `livestorm.payloads.parse_person`'s keys (PERSON_KEYS) plus
# its `P-` ref (`record.roster.assign_person_refs`). There is no column for
# an email address or a join link, and a row with any other key is refused.
_PERSON_COLUMNS = (
    "person_id", "person_ref", "livestorm_role", "is_guest_speaker", "session_role",
    "first_name", "last_name", "email_domain", "company", "job_title", "answers",
    "registered_at", "registration_type", "referrer_domain",
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "timezone", "country_code", "country_name",
    "attended", "attendance_duration_s", "attendance_rate", "has_viewed_replay", "replay_watched_at",
    "ls_messages_count", "ls_questions_count", "ls_votes_count", "ls_up_votes_count", "ls_updated_at",
)
# Sent as unix seconds or ISO strings, stored as TIMESTAMPTZ, read back as unix seconds.
_PERSON_TIME_COLUMNS = ("registered_at", "replay_watched_at", "ls_updated_at")
_PERSON_REF = re.compile(r"^P-[0-9a-f]{8,64}$")
# `livestorm.payloads.PEOPLE_ATTRIBUTE_KEYS`: no `user_id`, no free text.
_PEOPLE_ATTRIBUTE_KEYS = ("slug", "name", "type", "builtin", "options")

_ROSTER_HEADER_COLUMNS = (
    "roster_version", "source", "complete", "record_count", "people_count", "fields",
    "fields_source", "reason", "synced_at", "refresh_due_at", "refreshed_at",
)
# What a sync row may say about itself. Never its connection id or credential user.
_SYNC_STATUS_COLUMNS = (
    "sync_id", "kind", "reason", "status", "due_at", "pages_fetched", "record_count",
    "people_written", "error", "created_at", "updated_at",
)
SYNC_ERROR_MAX_CHARS = 1000


def _error_label(exc: BaseException) -> str:
    """An exception's class and SQL state, never its message (which can echo values)."""
    sqlstate = getattr(exc, "sqlstate", None)
    return type(exc).__name__ + (f" sqlstate={sqlstate}" if sqlstate else "")


def _person_time(value: Any) -> Optional[datetime]:
    """A person's time as sent (unix seconds, or an ISO string) as a UTC
    datetime. None when missing, zero (the API's "not set") or unreadable."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        seconds = float(value)
    elif isinstance(value, str):
        text = value.strip()
        try:
            seconds = float(text)
        except ValueError:
            try:
                moment = datetime.fromisoformat(text)
            except ValueError:
                return None
            return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)
    else:
        return None
    if not math.isfinite(seconds) or seconds <= 0:
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _without_nul(value: Any) -> Any:
    """`value` with NUL characters removed from every string in it: Postgres
    refuses them in TEXT and JSONB, and one odd answer must not block a roster."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, Mapping):
        return {_without_nul(key): _without_nul(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_without_nul(item) for item in value]
    return value


def _person_values(session_id: str, people: Sequence[Any]) -> List[List[Any]]:
    """Each person row as INSERT values, after checking it. Raises ValueError
    (naming keys, never values) on a row that isn't a person row."""
    rows: List[List[Any]] = []
    ids: set = set()
    refs: set = set()
    for person in people:
        if not isinstance(person, Mapping):
            raise ValueError("Each person row must be a mapping.")
        unknown = sorted(set(person) - set(_PERSON_COLUMNS))
        if unknown:
            raise ValueError(f"Person rows take parse_person's keys and person_ref only, not {unknown}.")
        person_id = str(person.get("person_id") or "").strip()
        ref = str(person.get("person_ref") or "").strip()
        if not person_id or not _PERSON_REF.match(ref):
            raise ValueError("Each person row needs a person_id and a P- person_ref.")
        if person_id in ids or ref in refs:
            raise ValueError("Person ids and refs must be unique within a roster.")
        ids.add(person_id)
        refs.add(ref)
        domain = person.get("email_domain")
        if domain is not None and (not isinstance(domain, str) or "@" in domain):
            raise ValueError("email_domain holds a domain, never an email address.")
        answers = person.get("answers")
        if answers is not None and not isinstance(answers, Mapping):
            raise ValueError("answers must be an object.")
        values: List[Any] = [session_id]
        for column in _PERSON_COLUMNS:
            value = person.get(column)
            if column == "person_id":
                value = person_id
            elif column == "person_ref":
                value = ref
            elif column == "answers":
                value = json.dumps(_without_nul(dict(answers or {})), ensure_ascii=False)
            elif column == "is_guest_speaker":
                value = value is True
            elif column in _PERSON_TIME_COLUMNS:
                value = _person_time(value)
            values.append(_without_nul(value))
        rows.append(values)
    return rows


def replace_session_roster(
    session_id: str,
    people: Sequence[Mapping[str, Any]],
    *,
    source: str,
    complete: Optional[bool],
    record_count: Optional[int],
    reason: str,
    fields: Sequence[Mapping[str, Any]] = (),
    fields_source: str = "none",
    refresh_due_at: Optional[datetime],
) -> Dict[str, int]:
    """Replace a session's whole roster: its header and every person row.

    `people` are `livestorm.payloads.parse_person` rows, each with its
    `person_ref`. One transaction: upsert the header with `roster_version + 1`
    (1 for a first roster), delete the session's person rows, insert the new
    ones. Any failure rolls it all back, so readers see the previous roster
    and version, never a mix. `refreshed_at` is set when `reason` is
    'refresh_t7' and kept otherwise. `refresh_due_at` has no default: every
    replace sets it, and None clears it, so a caller that keeps a due
    refresh passes the stored value (`fetch_roster_status`).

    Checks before writing (ValueError): the enums, a person row's keys (no
    email address, join link or any key `parse_person` doesn't give), unique
    ids and refs, and `complete=True` only when `record_count` equals the
    number of people. Raises RuntimeError when the database write fails or
    the session isn't cached. Returns `{roster_version, people_count}`.
    """
    session_id_value = str(session_id or "").strip()
    if not session_id_value:
        raise ValueError("replace_session_roster requires a session_id.")
    if source not in ROSTER_SOURCES or fields_source not in ROSTER_FIELDS_SOURCES or reason not in ROSTER_REASONS:
        raise ValueError("Unknown roster source, fields source or reason.")
    if record_count is not None and (isinstance(record_count, bool) or not isinstance(record_count, int) or record_count < 0):
        raise ValueError("record_count must be a non-negative integer or None.")
    if complete not in (True, False, None):
        raise ValueError("complete must be True, False or None.")
    if complete is True and record_count != len(people):
        raise ValueError("A roster is complete only when its people match Livestorm's record_count.")
    if not all(isinstance(field, Mapping) for field in fields):
        raise ValueError("Each field definition must be a mapping.")
    rows = _person_values(session_id_value, people)
    if not database_enabled():
        raise RuntimeError("Database is not configured.")

    columns = ("session_id", *_PERSON_COLUMNS)
    placeholders = ", ".join("%s::jsonb" if column == "answers" else "%s" for column in columns)
    try:
        with get_db_connection() as connection:
            with connection.transaction():
                header = connection.execute(
                    """
                    INSERT INTO session_rosters (
                        session_id, roster_version, source, complete, record_count, people_count,
                        fields, fields_source, reason, synced_at, refresh_due_at, refreshed_at
                    )
                    VALUES (%s, 1, %s, %s, %s, %s, %s::jsonb, %s, %s, NOW(), %s, CASE WHEN %s THEN NOW() END)
                    ON CONFLICT (session_id) DO UPDATE SET
                        roster_version = session_rosters.roster_version + 1,
                        source = EXCLUDED.source,
                        complete = EXCLUDED.complete,
                        record_count = EXCLUDED.record_count,
                        people_count = EXCLUDED.people_count,
                        fields = EXCLUDED.fields,
                        fields_source = EXCLUDED.fields_source,
                        reason = EXCLUDED.reason,
                        synced_at = EXCLUDED.synced_at,
                        refresh_due_at = EXCLUDED.refresh_due_at,
                        refreshed_at = COALESCE(EXCLUDED.refreshed_at, session_rosters.refreshed_at)
                    RETURNING roster_version
                    """,
                    (
                        session_id_value,
                        source,
                        complete,
                        record_count,
                        len(rows),
                        json.dumps([dict(field) for field in fields], ensure_ascii=False),
                        fields_source,
                        reason,
                        refresh_due_at,
                        reason == "refresh_t7",
                    ),
                ).fetchone()
                connection.execute("DELETE FROM session_people WHERE session_id = %s", (session_id_value,))
                if rows:
                    with connection.cursor() as cursor:
                        cursor.executemany(
                            f"INSERT INTO session_people ({', '.join(columns)}) VALUES ({placeholders})",
                            rows,
                        )
    except Exception as exc:
        logger.error("Failed to replace the roster for session_id=%s (%s)", session_id_value, _error_label(exc))
        raise RuntimeError("Could not save the session's people; the previous roster is kept.") from exc
    return {"roster_version": int(header["roster_version"]), "people_count": len(rows)}


def fetch_roster_status(session_id: str, organization_id: OrgScope) -> Optional[Dict[str, Any]]:
    """A session's roster header and its latest people sync, scoped by org.

    Returns `{session_id, roster, sync}`: `roster` is the header (no person)
    or None before the first roster; `sync` is the latest people sync that
    isn't merely scheduled, or None. Never a connection id or a credential
    user id. One query, joined to `session_cache` on its org: None when the
    session isn't cached or isn't in the scope. A blank scope matches nothing
    and never queries. Raises when the database can't be read.
    """
    session_id_value = str(session_id or "").strip()
    if not database_enabled() or not session_id_value:
        return None
    try:
        org_filter = _org_filter(organization_id)
    except ScopeMatchesNothing:
        return None
    where_clause = "c.session_id = %s" + (" AND c.organization_id = %s" if org_filter is not None else "")
    params: List[Any] = ["people", session_id_value] + ([org_filter] if org_filter is not None else [])
    roster_columns = ", ".join(f"r.{column} AS r_{column}" for column in _ROSTER_HEADER_COLUMNS)
    sync_columns = ", ".join(f"s.{column} AS s_{column}" for column in _SYNC_STATUS_COLUMNS)
    with get_db_connection() as connection:
        row = connection.execute(
            f"""
            SELECT c.session_id, r.session_id IS NOT NULL AS has_roster, {roster_columns}, {sync_columns}
            FROM session_cache c
            LEFT JOIN session_rosters r ON r.session_id = c.session_id
            LEFT JOIN LATERAL (
                SELECT {", ".join(_SYNC_STATUS_COLUMNS)}
                FROM livestorm_sync_jobs j
                WHERE j.session_id = c.session_id AND j.kind = %s AND j.status <> 'scheduled'
                ORDER BY j.created_at DESC, j.sync_id DESC
                LIMIT 1
            ) s ON TRUE
            WHERE {where_clause}
            LIMIT 1
            """,
            params,
        ).fetchone()
    if not row:
        return None
    roster = {column: row[f"r_{column}"] for column in _ROSTER_HEADER_COLUMNS} if row["has_roster"] else None
    sync = {column: row[f"s_{column}"] for column in _SYNC_STATUS_COLUMNS} if row["s_sync_id"] else None
    return {"session_id": row["session_id"], "roster": roster, "sync": sync}


def list_session_people(session_id: str, organization_id: OrgScope) -> List[Dict[str, Any]]:
    """A session's stored person rows, scoped by org, ordered by person id.

    Each row has `parse_person`'s keys plus `person_ref`; its times are unix
    seconds as floats, whether `parse_person` gave them as numbers or ISO
    strings (`record.roster` reads both). [] when the session has no rows,
    isn't cached or isn't in the scope. A blank scope matches nothing and
    never queries. Raises when the database can't be read: a failed read must
    never look like an empty roster.
    """
    session_id_value = str(session_id or "").strip()
    if not database_enabled() or not session_id_value:
        return []
    try:
        org_filter = _org_filter(organization_id)
    except ScopeMatchesNothing:
        return []
    columns = ", ".join(
        f"EXTRACT(EPOCH FROM p.{column})::float8 AS {column}" if column in _PERSON_TIME_COLUMNS else f"p.{column}"
        for column in _PERSON_COLUMNS
    )
    where_clause = "p.session_id = %s" + (" AND c.organization_id = %s" if org_filter is not None else "")
    params = (session_id_value,) + ((org_filter,) if org_filter is not None else ())
    with get_db_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT {columns}
            FROM session_people p
            JOIN session_cache c ON c.session_id = p.session_id
            WHERE {where_clause}
            ORDER BY p.person_id
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


# How many times `fetch_session_roster` reads again when a sync replaces the
# roster while it is being read.
ROSTER_READ_ATTEMPTS = 3


def fetch_session_roster(session_id: str, organization_id: OrgScope) -> Optional[Dict[str, Any]]:
    """A session's stored roster, header and person rows of one version, scoped by org.

    Returns `{roster, people}`: the header as `fetch_roster_status` gives it
    and the rows as `list_session_people` gives them. None when the session
    has no stored roster, isn't cached or isn't in the scope; a blank scope
    matches nothing and never queries.

    The header and the rows are two reads, and a sync can replace the roster
    between them (`replace_session_roster`, one transaction, which bumps
    `roster_version`). So the header is read again after the rows: the same
    version on both sides means no replace was committed in between, and the
    rows belong to it. Otherwise the rows are read again, up to
    `ROSTER_READ_ATTEMPTS` times, then it raises: a caller never gets one
    version's header with another's people. Raises when the database can't
    be read.
    """
    status = fetch_roster_status(session_id, organization_id)
    header = status.get("roster") if status else None
    for _attempt in range(ROSTER_READ_ATTEMPTS):
        if header is None:
            return None
        people = list_session_people(session_id, organization_id)
        status = fetch_roster_status(session_id, organization_id)
        latest = status.get("roster") if status else None
        if latest is not None and latest.get("roster_version") == header.get("roster_version"):
            return {"roster": header, "people": people}
        header = latest
    raise RuntimeError("The session's people changed while they were being read. Please try again.")


def get_org_people_attributes(organization_id: OrgScope) -> Optional[Dict[str, Any]]:
    """One org's cached people attributes: `{organization_id, attributes,
    status, fetched_at}`, or None when there are none.

    One org only: a blank scope returns None without querying, and
    `ALL_ORGS` raises ValueError. Raises when the database can't be read.
    """
    try:
        org_filter = _org_filter(organization_id)
    except ScopeMatchesNothing:
        return None
    if org_filter is None:
        raise ValueError("get_org_people_attributes reads one organization, never ALL_ORGS.")
    if not database_enabled():
        return None
    with get_db_connection() as connection:
        row = connection.execute(
            """
            SELECT organization_id, attributes, status, fetched_at
            FROM org_people_attributes
            WHERE organization_id = %s
            """,
            (org_filter,),
        ).fetchone()
    return dict(row) if row else None


def upsert_org_people_attributes(organization_id: str, attributes: Sequence[Mapping[str, Any]], *, status: str) -> None:
    """Save one org's people attributes (`livestorm.payloads.parse_people_attributes`).

    `status` is 'ok', 'forbidden' (the token can't read them) or 'error'.
    Only an 'ok' save replaces the stored attributes; the others keep the
    last good ones and record the status and time. Each attribute takes the
    parsed keys only (never `user_id`). Raises on failure.
    """
    org_value = organization_id.strip() if isinstance(organization_id, str) else ""
    if not org_value:
        raise ValueError("upsert_org_people_attributes requires an organization_id.")
    if status not in PEOPLE_ATTRIBUTES_STATUSES:
        raise ValueError(f"Unknown people attributes status; expected one of {PEOPLE_ATTRIBUTES_STATUSES}.")
    for attribute in attributes:
        if not isinstance(attribute, Mapping):
            raise ValueError("Each people attribute must be a mapping.")
        unknown = sorted(set(attribute) - set(_PEOPLE_ATTRIBUTE_KEYS))
        if unknown:
            raise ValueError(f"People attributes take {_PEOPLE_ATTRIBUTE_KEYS} only, not {unknown}.")
    if not database_enabled():
        raise RuntimeError("Database is not configured.")
    try:
        with get_db_connection() as connection:
            connection.execute(
                """
                INSERT INTO org_people_attributes (organization_id, attributes, status, fetched_at)
                VALUES (%s, %s::jsonb, %s, NOW())
                ON CONFLICT (organization_id) DO UPDATE SET
                    attributes = CASE WHEN EXCLUDED.status = 'ok'
                                      THEN EXCLUDED.attributes
                                      ELSE org_people_attributes.attributes END,
                    status = EXCLUDED.status,
                    fetched_at = EXCLUDED.fetched_at
                """,
                (org_value, json.dumps([dict(attribute) for attribute in attributes], ensure_ascii=False), status),
            )
            connection.commit()
    except Exception as exc:
        logger.error("Failed to save people attributes for organization_id=%s (%s)", org_value, _error_label(exc))
        raise RuntimeError("Could not save the workspace's people attributes.") from exc


def create_livestorm_sync(
    session_id: str,
    *,
    reason: str,
    kind: str = "people",
    connection_id: str = "",
    status: str = "pending",
    due_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Create a Livestorm sync row, unless the session already has one in flight.

    `status` 'pending' (a sync to enqueue now) or 'scheduled' (one for a
    later `due_at`). A session holds at most one pending or running sync per
    kind, and one scheduled sync per kind and reason (partial unique
    indexes), so a lost race attaches: `created` is False and the existing
    row's id comes back. Returns `{sync_id, status, created}`.

    `connection_id` is the OAuth connection the worker will use ("" means
    `LS_API_KEY`, local dev). It is also a session cookie value: it stays on
    the row only until `claim_livestorm_sync`, is never returned, and a
    scheduled row can't hold one. Raises when the session isn't cached or the
    write fails.
    """
    session_id_value = str(session_id or "").strip()
    connection_value = str(connection_id or "").strip() or None
    if not session_id_value:
        raise ValueError("create_livestorm_sync requires a session_id.")
    if kind not in SYNC_KINDS or reason not in SYNC_REASONS or status not in ("pending", "scheduled"):
        raise ValueError("Unknown sync kind, reason or starting status.")
    if status == "scheduled" and connection_value:
        raise ValueError("A scheduled sync never holds a connection id.")
    if not database_enabled():
        raise RuntimeError("Database is not configured.")
    if status == "pending":
        conflict = "ON CONFLICT (session_id, kind) WHERE status IN ('pending', 'running') DO NOTHING"
        existing = "status IN ('pending', 'running')"
        existing_params: tuple = (session_id_value, kind)
    else:
        conflict = "ON CONFLICT (session_id, kind, reason) WHERE status = 'scheduled' DO NOTHING"
        existing = "status = 'scheduled' AND reason = %s"
        existing_params = (session_id_value, kind, reason)
    try:
        with get_db_connection() as connection:
            for _attempt in range(2):
                row = connection.execute(
                    f"""
                    INSERT INTO livestorm_sync_jobs (sync_id, session_id, kind, reason, status, due_at, connection_id)
                    VALUES (%s, %s, %s, %s, %s, COALESCE(%s, NOW()), %s)
                    {conflict}
                    RETURNING sync_id, status
                    """,
                    (uuid.uuid4().hex, session_id_value, kind, reason, status, due_at, connection_value),
                ).fetchone()
                if row:
                    connection.commit()
                    return {"sync_id": row["sync_id"], "status": row["status"], "created": True}
                row = connection.execute(
                    f"""
                    SELECT sync_id, status FROM livestorm_sync_jobs
                    WHERE session_id = %s AND kind = %s AND {existing}
                    LIMIT 1
                    """,
                    existing_params,
                ).fetchone()
                if row:
                    connection.commit()
                    return {"sync_id": row["sync_id"], "status": row["status"], "created": False}
                # The row in flight finished between the two statements; try again.
            connection.commit()
    except Exception as exc:
        logger.error("Failed to create a %s sync for session_id=%s (%s)", kind, session_id_value, _error_label(exc))
        raise RuntimeError("Could not start reading the session's people. Please try again.") from exc
    raise RuntimeError("Could not start reading the session's people. Please try again.")


def claim_livestorm_sync(sync_id: str) -> Optional[Dict[str, Any]]:
    """Move a sync from 'pending' to 'running' for the worker that will run it.

    Returns `{session_id, kind, reason, connection_id, credential_user_id}`,
    with the stored connection id ("" for `LS_API_KEY`), which the same
    statement clears from the row: it is handed over once. None when the
    sync is no longer pending (claimed, swept or closed); the worker must
    then make no Livestorm call. Raises on database errors.
    """
    sync_id_value = str(sync_id or "").strip()
    if not sync_id_value:
        return None
    if not database_enabled():
        raise RuntimeError("Database is not configured.")
    try:
        with get_db_connection() as connection:
            row = connection.execute(
                """
                UPDATE livestorm_sync_jobs AS job
                SET status = 'running', connection_id = NULL, updated_at = NOW()
                FROM (
                    SELECT sync_id, connection_id FROM livestorm_sync_jobs
                    WHERE sync_id = %s AND status = 'pending'
                    FOR UPDATE
                ) AS claimed
                WHERE job.sync_id = claimed.sync_id
                RETURNING job.session_id, job.kind, job.reason, claimed.connection_id, job.credential_user_id
                """,
                (sync_id_value,),
            ).fetchone()
            connection.commit()
    except Exception as exc:
        logger.error("Failed to claim sync_id=%s (%s)", sync_id_value, _error_label(exc))
        raise RuntimeError("Could not claim the people sync.") from exc
    if not row:
        return None
    return {
        "session_id": row["session_id"],
        "kind": row["kind"],
        "reason": row["reason"],
        "connection_id": str(row["connection_id"] or ""),
        "credential_user_id": str(row["credential_user_id"] or ""),
    }


def update_livestorm_sync(
    sync_id: str,
    *,
    status: str,
    error: Optional[str] = None,
    pages_fetched: Optional[int] = None,
    record_count: Optional[int] = None,
    people_written: Optional[int] = None,
    only_from: Optional[Sequence[str]] = None,
) -> bool:
    """Set a sync's status, its error (None clears it) and any counts given.

    A row that leaves 'pending' drops its connection id in the same
    statement, so a finished, failed or deferred sync never keeps one. The
    error is cut to `SYNC_ERROR_MAX_CHARS`; it must never hold a key, token or
    upstream body. `only_from`, when given, updates the row only while its
    status is one of these (closing a lost sync must not overwrite one that
    finished meanwhile). Returns False when no row was updated. Raises on
    database errors.
    """
    sync_id_value = str(sync_id or "").strip()
    if not sync_id_value:
        raise ValueError("update_livestorm_sync requires a sync_id.")
    if status not in SYNC_STATUSES:
        raise ValueError(f"Unknown sync status; expected one of {SYNC_STATUSES}.")
    from_statuses = None if only_from is None else [str(value) for value in only_from]
    if from_statuses is not None and not set(from_statuses) <= set(SYNC_STATUSES):
        raise ValueError(f"Unknown sync status in only_from; expected some of {SYNC_STATUSES}.")
    if not database_enabled():
        raise RuntimeError("Database is not configured.")
    error_value = str(error)[:SYNC_ERROR_MAX_CHARS] if error is not None else None
    try:
        with get_db_connection() as connection:
            cursor = connection.execute(
                """
                UPDATE livestorm_sync_jobs
                SET status = %s,
                    error = %s,
                    pages_fetched = COALESCE(%s, pages_fetched),
                    record_count = COALESCE(%s, record_count),
                    people_written = COALESCE(%s, people_written),
                    connection_id = CASE WHEN %s = 'pending' THEN connection_id END,
                    updated_at = NOW()
                WHERE sync_id = %s
                  AND (%s::text[] IS NULL OR status = ANY(%s::text[]))
                """,
                (
                    status, error_value, pages_fetched, record_count, people_written, status, sync_id_value,
                    from_statuses, from_statuses,
                ),
            )
            updated = cursor.rowcount
            connection.commit()
    except Exception as exc:
        logger.error("Failed to update sync_id=%s (%s)", sync_id_value, _error_label(exc))
        raise RuntimeError("Could not update the people sync.") from exc
    return bool(updated)


def touch_livestorm_sync(sync_id: str) -> bool:
    """Refresh a running sync's `updated_at`, so the stuck-job sweeper leaves
    a sync alone while it waits for its org's lock. False when the row is no
    longer 'running' (swept or closed): the job should then stop. Raises on
    database errors."""
    sync_id_value = str(sync_id or "").strip()
    if not sync_id_value:
        return False
    if not database_enabled():
        raise RuntimeError("Database is not configured.")
    try:
        with get_db_connection() as connection:
            cursor = connection.execute(
                "UPDATE livestorm_sync_jobs SET updated_at = NOW() WHERE sync_id = %s AND status = 'running'",
                (sync_id_value,),
            )
            touched = cursor.rowcount
            connection.commit()
    except Exception as exc:
        logger.error("Failed to touch sync_id=%s (%s)", sync_id_value, _error_label(exc))
        raise RuntimeError("Could not update the people sync.") from exc
    return bool(touched)


SYNC_SWEEPER_ERROR = "The people sync stalled with no progress (closed by the sweeper). Please try again."


def sweep_stuck_livestorm_syncs(stale_before: datetime) -> int:
    """Close the syncs stuck in 'pending' or 'running' (no update since
    `stale_before`): 'error', with the connection id cleared in the same
    statement. A row whose job was lost would otherwise keep a session-cookie
    value in Postgres and block the session's one active sync. Returns how
    many rows it closed. Raises on database errors."""
    if not database_enabled():
        raise RuntimeError("Database is not configured.")
    try:
        with get_db_connection() as connection:
            cursor = connection.execute(
                """
                UPDATE livestorm_sync_jobs
                SET status = 'error', error = %s, connection_id = NULL, updated_at = NOW()
                WHERE status IN ('pending', 'running') AND updated_at < %s
                """,
                (SYNC_SWEEPER_ERROR, stale_before),
            )
            closed = cursor.rowcount or 0
            connection.commit()
    except Exception as exc:
        logger.error("Failed to sweep stuck syncs (%s)", _error_label(exc))
        raise RuntimeError("Could not sweep the stuck people syncs.") from exc
    return int(closed)


def livestorm_sync_calls_since(organization_id: OrgScope, since: datetime, *, kind: str = "people") -> int:
    """The Livestorm calls one org's syncs made since `since`, for its daily cap.

    Sums `pages_fetched` (every page a sync asked Livestorm for, people and
    attributes) over the sync rows of the org's sessions updated since then.
    Each session's first 'initial' sync is left out: it never counts toward
    the cap. One org only, through `session_cache`: a blank scope returns 0
    without querying, `ALL_ORGS` raises ValueError. Raises on database errors.
    """
    try:
        org_filter = _org_filter(organization_id)
    except ScopeMatchesNothing:
        return 0
    if org_filter is None:
        raise ValueError("livestorm_sync_calls_since sums one organization, never ALL_ORGS.")
    if kind not in SYNC_KINDS:
        raise ValueError(f"Unknown sync kind; expected one of {SYNC_KINDS}.")
    if not database_enabled():
        return 0
    with get_db_connection() as connection:
        row = connection.execute(
            """
            SELECT COALESCE(SUM(j.pages_fetched), 0)::bigint AS calls
            FROM livestorm_sync_jobs j
            JOIN session_cache c ON c.session_id = j.session_id
            WHERE c.organization_id = %s
              AND j.kind = %s
              AND j.updated_at >= %s
              AND j.pages_fetched IS NOT NULL
              AND NOT (
                  j.reason = 'initial'
                  AND NOT EXISTS (
                      SELECT 1 FROM livestorm_sync_jobs e
                      WHERE e.session_id = j.session_id AND e.kind = j.kind AND e.reason = 'initial'
                        AND (e.created_at, e.sync_id) < (j.created_at, j.sync_id)
                  )
              )
            """,
            (org_filter, kind, since),
        ).fetchone()
    return int(row["calls"] or 0)


def livestorm_sync_is_first_initial(sync_id: str) -> bool:
    """Whether a sync is its session's first 'initial' sync, the one the daily
    cap leaves out (the same rule as `livestorm_sync_calls_since`). False for
    an unknown sync. Raises on database errors."""
    sync_id_value = str(sync_id or "").strip()
    if not sync_id_value:
        return False
    if not database_enabled():
        raise RuntimeError("Database is not configured.")
    with get_db_connection() as connection:
        row = connection.execute(
            """
            SELECT j.reason = 'initial' AND NOT EXISTS (
                SELECT 1 FROM livestorm_sync_jobs e
                WHERE e.session_id = j.session_id AND e.kind = j.kind AND e.reason = 'initial'
                  AND (e.created_at, e.sync_id) < (j.created_at, j.sync_id)
            ) AS first_initial
            FROM livestorm_sync_jobs j
            WHERE j.sync_id = %s
            """,
            (sync_id_value,),
        ).fetchone()
    return bool(row and row["first_initial"])


# ── The understanding job's result (record/understanding.py) ──────────────
#
# One row per session, replaced whole by the analysis the user starts. A
# failed write keeps the previous one. Reads join `session_cache` on its org.


def replace_understanding(session_id: str, result: Mapping[str, Any], *, dossier_hash: str, model: str) -> None:
    """Store a session's checked understanding (`check_understanding`'s
    result), replacing any earlier one. `dossier_hash` is the SHA-256 of the
    dossier it was read from. Raises ValueError for a malformed call and
    RuntimeError when the write fails or the session isn't cached; the
    earlier result is kept."""
    from livestorm_app.record.understanding import UNDERSTANDING_VERSION

    session_id_value = str(session_id or "").strip()
    if not session_id_value:
        raise ValueError("replace_understanding requires a session_id.")
    if not isinstance(dossier_hash, str) or not _CHAT_HASH.match(dossier_hash):
        raise ValueError("dossier_hash is a lowercase SHA-256 hex digest.")
    model_value = str(model or "").strip()
    if not model_value:
        raise ValueError("replace_understanding requires the model that read the session.")
    if not isinstance(result, Mapping) or result.get("version") != UNDERSTANDING_VERSION:
        raise ValueError("replace_understanding takes a checked result of the current version.")
    if not database_enabled():
        raise RuntimeError("Database is not configured.")
    try:
        with get_db_connection() as connection:
            connection.execute(
                """
                INSERT INTO session_understanding (session_id, version, dossier_hash, model, result, created_at)
                VALUES (%s, %s, %s, %s, %s::jsonb, NOW())
                ON CONFLICT (session_id) DO UPDATE SET
                    version = EXCLUDED.version,
                    dossier_hash = EXCLUDED.dossier_hash,
                    model = EXCLUDED.model,
                    result = EXCLUDED.result,
                    created_at = NOW()
                """,
                (session_id_value, UNDERSTANDING_VERSION, dossier_hash, model_value, json.dumps(result, ensure_ascii=False)),
            )
            connection.commit()
    except Exception as exc:
        logger.error("Failed to save the understanding for session_id=%s (%s)", session_id_value, _error_label(exc))
        raise RuntimeError("Could not save what was read in the session; the previous result is kept.") from exc


def fetch_understanding(session_id: str, organization_id: OrgScope) -> Optional[Dict[str, Any]]:
    """A session's stored understanding, scoped by org: `{version,
    dossier_hash, model, result, created_at}`, or None when there is none,
    the session isn't in the scope, or the scope is blank (never queries).
    Returned as stored, whatever its version: the reader compares it with
    the session it shows. Raises when the database can't be read."""
    session_id_value = str(session_id or "").strip()
    if not database_enabled() or not session_id_value:
        return None
    try:
        org_filter = _org_filter(organization_id)
    except ScopeMatchesNothing:
        return None
    where_clause = "u.session_id = %s" + (" AND c.organization_id = %s" if org_filter is not None else "")
    params = (session_id_value,) + ((org_filter,) if org_filter is not None else ())
    with get_db_connection() as connection:
        row = connection.execute(
            f"""
            SELECT u.version, u.dossier_hash, u.model, u.result, u.created_at
            FROM session_understanding u
            JOIN session_cache c ON c.session_id = u.session_id
            WHERE {where_clause}
            LIMIT 1
            """,
            params,
        ).fetchone()
    return dict(row) if row else None


# ── Before you share: the host's figure checks (record/figure_checks.py) ──
#
# One row per check the host answered. Reads and writes join `session_cache`
# on its org, so an out-of-scope session reads nothing and writes nothing.


MAX_OUTPUT_VERSIONS = 20


def list_output_versions(
    session_id: str,
    output_column: str,
    entry_key: str,
    *,
    organization_id: OrgScope,
    limit: int = MAX_OUTPUT_VERSIONS,
) -> List[Dict[str, Any]]:
    """The saved versions of one AI output (`output_versions`), newest
    first: `{id, content, models, job_kinds, calls, created_at}`, at most
    `limit`. Never the hashes or the job's id. Empty when there are none,
    the session isn't in the scope, or the scope is blank (never queries).
    Raises when the database can't be read."""
    if output_column not in _BUNDLE_COLUMNS:
        raise ValueError(f"Not a bundle column: {output_column!r}")
    session_id_value = str(session_id or "").strip()
    entry = str(entry_key or "").strip()
    if not database_enabled() or not session_id_value or not entry:
        return []
    try:
        org_filter = _org_filter(organization_id)
    except ScopeMatchesNothing:
        return []
    where_clause = "v.session_id = %s AND v.output_column = %s AND v.entry_key = %s" + (
        " AND c.organization_id = %s" if org_filter is not None else ""
    )
    params = (session_id_value, output_column, entry) + ((org_filter,) if org_filter is not None else ())
    with get_db_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT v.id, v.content, v.models, v.job_kinds, v.calls, v.created_at
            FROM output_versions v
            JOIN session_cache c ON c.session_id = v.session_id
            WHERE {where_clause}
            ORDER BY v.created_at DESC, v.id DESC
            LIMIT %s
            """,
            params + (max(1, int(limit)),),
        ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "content": row["content"],
            "models": list(row["models"] or []),
            "job_kinds": list(row["job_kinds"] or []),
            "calls": int(row["calls"] or 0),
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def fetch_figure_checks(session_id: str, organization_id: OrgScope) -> Dict[str, Dict[str, Any]]:
    """A session's answered figure checks by key: `{choice, updated_at}`.
    Empty when there are none, the session isn't in the scope, or the scope
    is blank (never queries). Raises when the database can't be read."""
    session_id_value = str(session_id or "").strip()
    if not database_enabled() or not session_id_value:
        return {}
    try:
        org_filter = _org_filter(organization_id)
    except ScopeMatchesNothing:
        return {}
    where_clause = "f.session_id = %s" + (" AND c.organization_id = %s" if org_filter is not None else "")
    params = (session_id_value,) + ((org_filter,) if org_filter is not None else ())
    with get_db_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT f.check_key, f.choice, f.updated_at
            FROM session_figure_checks f
            JOIN session_cache c ON c.session_id = f.session_id
            WHERE {where_clause}
            ORDER BY f.check_key
            """,
            params,
        ).fetchall()
    return {row["check_key"]: {"choice": row["choice"], "updated_at": row["updated_at"]} for row in rows}


def save_figure_check(
    session_id: str,
    *,
    organization_id: OrgScope,
    check_key: str,
    choice: Optional[str],
    updated_by: Optional[str],
) -> bool:
    """Save the host's answer to one figure check, replacing the previous
    one; `choice` None removes it (the check is open again). Written only
    when the session is in the scope: False when it isn't, or the scope is
    blank (never queries). `updated_by` is the Livestorm user id, never an
    email. Raises RuntimeError when the write fails."""
    session_id_value = str(session_id or "").strip()
    key_value = str(check_key or "").strip()
    if not session_id_value or not key_value:
        raise ValueError("save_figure_check requires a session id and a check key.")
    try:
        org_filter = _org_filter(organization_id)
    except ScopeMatchesNothing:
        return False
    if not database_enabled():
        raise RuntimeError("Database is not configured.")
    scope_clause = " AND c.organization_id = %s" if org_filter is not None else ""
    scope_params = (org_filter,) if org_filter is not None else ()
    try:
        with get_db_connection() as connection:
            visible = connection.execute(
                f"SELECT 1 FROM session_cache c WHERE c.session_id = %s{scope_clause} LIMIT 1",
                (session_id_value,) + scope_params,
            ).fetchone()
            if visible is None:
                return False
            if choice is None:
                connection.execute(
                    "DELETE FROM session_figure_checks WHERE session_id = %s AND check_key = %s",
                    (session_id_value, key_value),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO session_figure_checks (session_id, check_key, choice, updated_by, updated_at)
                    VALUES (%s, %s, %s, %s, NOW())
                    ON CONFLICT (session_id, check_key) DO UPDATE SET
                        choice = EXCLUDED.choice,
                        updated_by = EXCLUDED.updated_by,
                        updated_at = NOW()
                    """,
                    (session_id_value, key_value, str(choice), str(updated_by or "").strip() or None),
                )
            connection.commit()
    except Exception as exc:
        logger.error("Failed to save a figure check for session_id=%s (%s)", session_id_value, _error_label(exc))
        raise RuntimeError("Could not save the figure check.") from exc
    return True


# ── The editable FAQ (record/faq_entries.py) ──────────────────────────────
#
# One row per session and language once the user saved. Reads and writes
# join `session_cache` on its org, so an out-of-scope session reads nothing
# and writes nothing.


def fetch_session_faq(session_id: str, organization_id: OrgScope, language: str = "English") -> Optional[Dict[str, Any]]:
    """A session's saved FAQ: `{entries, desk_stamp, updated_by, updated_at}`,
    or None when it was never saved, the session isn't in the scope, or the
    scope is blank (never queries). Raises when the database can't be read."""
    session_id_value = str(session_id or "").strip()
    language_value = str(language or "").strip() or "English"
    if not database_enabled() or not session_id_value:
        return None
    try:
        org_filter = _org_filter(organization_id)
    except ScopeMatchesNothing:
        return None
    where_clause = "f.session_id = %s AND f.language = %s" + (" AND c.organization_id = %s" if org_filter is not None else "")
    params = (session_id_value, language_value) + ((org_filter,) if org_filter is not None else ())
    with get_db_connection() as connection:
        row = connection.execute(
            f"""
            SELECT f.entries, f.desk_stamp, f.updated_by, f.updated_at
            FROM session_faq f
            JOIN session_cache c ON c.session_id = f.session_id
            WHERE {where_clause}
            LIMIT 1
            """,
            params,
        ).fetchone()
    if row is None:
        return None
    return {
        "entries": list(row["entries"] or []),
        "desk_stamp": dict(row["desk_stamp"] or {}),
        "updated_by": row["updated_by"],
        "updated_at": row["updated_at"],
    }


def save_session_faq(
    session_id: str,
    *,
    organization_id: OrgScope,
    language: str = "English",
    entries: Sequence[Mapping[str, Any]],
    desk_stamp: Mapping[str, str],
    updated_by: Optional[str],
) -> bool:
    """Save a session's FAQ whole, replacing the previous one. Written only
    when the session is in the scope: False when it isn't, or the scope is
    blank (never queries). `updated_by` is the Livestorm user id, never an
    email. Raises RuntimeError when the write fails."""
    session_id_value = str(session_id or "").strip()
    language_value = str(language or "").strip() or "English"
    if not session_id_value:
        raise ValueError("save_session_faq requires a session id.")
    try:
        org_filter = _org_filter(organization_id)
    except ScopeMatchesNothing:
        return False
    if not database_enabled():
        raise RuntimeError("Database is not configured.")
    scope_clause = " AND c.organization_id = %s" if org_filter is not None else ""
    scope_params = (org_filter,) if org_filter is not None else ()
    try:
        with get_db_connection() as connection:
            visible = connection.execute(
                f"SELECT 1 FROM session_cache c WHERE c.session_id = %s{scope_clause} LIMIT 1",
                (session_id_value,) + scope_params,
            ).fetchone()
            if visible is None:
                return False
            connection.execute(
                """
                INSERT INTO session_faq (session_id, language, entries, desk_stamp, updated_by, updated_at)
                VALUES (%s, %s, %s::jsonb, %s::jsonb, %s, NOW())
                ON CONFLICT (session_id, language) DO UPDATE SET
                    entries = EXCLUDED.entries,
                    desk_stamp = EXCLUDED.desk_stamp,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = NOW()
                """,
                (
                    session_id_value,
                    language_value,
                    json.dumps(_without_nul([dict(entry) for entry in entries]), ensure_ascii=False),
                    json.dumps(_without_nul(dict(desk_stamp or {})), ensure_ascii=False),
                    str(updated_by or "").strip() or None,
                ),
            )
            connection.commit()
    except Exception as exc:
        logger.error("Failed to save the FAQ for session_id=%s (%s)", session_id_value, _error_label(exc))
        raise RuntimeError("Could not save the FAQ.") from exc
    return True


# ── Surprise me's voice clips (ai/surprise_voice.py) ──────────────────────
#
# One row per beat's clip. Reads join `session_cache` on its org, so an
# out-of-scope session reads nothing. Only the worker writes, after OpenAI
# answered with the whole clip.


def fetch_surprise_voice_clips(
    session_id: str, clip_keys: Sequence[str], *, organization_id: OrgScope
) -> Dict[str, Dict[str, Any]]:
    """Which of `clip_keys` this session has, by key: `{duration_s, size}`
    (never the audio). Empty when none, the session isn't in the scope, or
    the scope is blank (never queries). Raises when the database can't be read."""
    session_id_value = str(session_id or "").strip()
    keys = sorted({str(key) for key in clip_keys if str(key or "").strip()})
    if not database_enabled() or not session_id_value or not keys:
        return {}
    try:
        org_filter = _org_filter(organization_id)
    except ScopeMatchesNothing:
        return {}
    scope_clause = " AND c.organization_id = %s" if org_filter is not None else ""
    params = (session_id_value, keys) + ((org_filter,) if org_filter is not None else ())
    with get_db_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT v.clip_key, v.duration_s, octet_length(v.audio) AS size
            FROM surprise_voice_clips v
            JOIN session_cache c ON c.session_id = v.session_id
            WHERE v.session_id = %s AND v.clip_key = ANY(%s){scope_clause}
            """,
            params,
        ).fetchall()
    return {
        row["clip_key"]: {
            "duration_s": float(row["duration_s"]) if row["duration_s"] is not None else None,
            "size": int(row["size"] or 0),
        }
        for row in rows
    }


def fetch_surprise_voice_clip(session_id: str, clip_key: str, *, organization_id: OrgScope) -> Optional[Dict[str, Any]]:
    """One clip's `{bytes, mime}`, or None when the session or the clip isn't
    there or isn't in the scope (a blank scope never queries). Raises when
    the database can't be read."""
    session_id_value = str(session_id or "").strip()
    key_value = str(clip_key or "").strip()
    if not database_enabled() or not session_id_value or not key_value:
        return None
    try:
        org_filter = _org_filter(organization_id)
    except ScopeMatchesNothing:
        return None
    scope_clause = " AND c.organization_id = %s" if org_filter is not None else ""
    params = (session_id_value, key_value) + ((org_filter,) if org_filter is not None else ())
    with get_db_connection() as connection:
        row = connection.execute(
            f"""
            SELECT v.audio, v.mime
            FROM surprise_voice_clips v
            JOIN session_cache c ON c.session_id = v.session_id
            WHERE v.session_id = %s AND v.clip_key = %s{scope_clause}
            LIMIT 1
            """,
            params,
        ).fetchone()
    if not row or row.get("audio") is None:
        return None
    return {"bytes": bytes(row["audio"]), "mime": str(row.get("mime") or "audio/mpeg")}


def save_surprise_voice_clip(
    session_id: str, clip_key: str, *, audio: bytes, mime: str, duration_s: Optional[float]
) -> None:
    """Save one whole clip (the worker, after OpenAI answered). The same key
    saved twice keeps one row. Raises RuntimeError when the write fails, or
    when the session is gone (its row is the foreign key's target)."""
    session_id_value = str(session_id or "").strip()
    key_value = str(clip_key or "").strip()
    if not session_id_value or not key_value or not audio:
        raise ValueError("save_surprise_voice_clip requires a session id, a clip key and the audio.")
    if not database_enabled():
        raise RuntimeError("Database is not configured.")
    try:
        with get_db_connection() as connection:
            connection.execute(
                """
                INSERT INTO surprise_voice_clips (session_id, clip_key, mime, audio, duration_s, created_at)
                VALUES (%s, %s, %s, %s, %s, NOW())
                ON CONFLICT (session_id, clip_key) DO UPDATE SET
                    mime = EXCLUDED.mime,
                    audio = EXCLUDED.audio,
                    duration_s = EXCLUDED.duration_s,
                    created_at = NOW()
                """,
                (session_id_value, key_value, str(mime), bytes(audio), duration_s),
            )
            connection.commit()
    except Exception as exc:
        logger.error("Failed to save a voice clip for session_id=%s (%s)", session_id_value, _error_label(exc))
        raise RuntimeError("Could not save the voice clip.") from exc


def prune_surprise_voice_clips(session_id: str, keep: Sequence[str]) -> int:
    """Remove this session's clips that the current take no longer uses (an
    older take or voice), keeping `keep`. Returns how many went. Raises
    RuntimeError when the write fails."""
    session_id_value = str(session_id or "").strip()
    if not database_enabled() or not session_id_value:
        return 0
    keys = sorted({str(key) for key in keep if str(key or "").strip()})
    try:
        with get_db_connection() as connection:
            removed = connection.execute(
                "DELETE FROM surprise_voice_clips WHERE session_id = %s AND NOT (clip_key = ANY(%s))",
                (session_id_value, keys),
            ).rowcount
            connection.commit()
    except Exception as exc:
        logger.error("Failed to prune voice clips for session_id=%s (%s)", session_id_value, _error_label(exc))
        raise RuntimeError("Could not tidy the voice clips.") from exc
    return int(removed or 0)


# ── Surprise me's storyboard (ai/surprise_story.py) ───────────────────────


def fetch_surprise_storyboard(session_id: str, story_key: str, *, organization_id: OrgScope) -> Optional[Dict[str, Any]]:
    """The session's storyboard (`{version, source, scenes}`) when it was
    made for `story_key`; None when there is none, it is for another take,
    or the session isn't in the scope (a blank scope never queries). Raises
    when the database can't be read."""
    session_id_value = str(session_id or "").strip()
    key_value = str(story_key or "").strip()
    if not database_enabled() or not session_id_value or not key_value:
        return None
    try:
        org_filter = _org_filter(organization_id)
    except ScopeMatchesNothing:
        return None
    scope_clause = " AND c.organization_id = %s" if org_filter is not None else ""
    params = (session_id_value, key_value) + ((org_filter,) if org_filter is not None else ())
    with get_db_connection() as connection:
        row = connection.execute(
            f"""
            SELECT s.storyboard
            FROM surprise_storyboards s
            JOIN session_cache c ON c.session_id = s.session_id
            WHERE s.session_id = %s AND s.story_key = %s{scope_clause}
            LIMIT 1
            """,
            params,
        ).fetchone()
    if not row or not isinstance(row.get("storyboard"), dict):
        return None
    return dict(row["storyboard"])


def save_surprise_storyboard(session_id: str, story_key: str, storyboard: Mapping[str, Any]) -> None:
    """Save the session's storyboard whole (the voice job, after the check),
    replacing one for an older take. Raises RuntimeError when the write
    fails, or when the session is gone."""
    session_id_value = str(session_id or "").strip()
    key_value = str(story_key or "").strip()
    if not session_id_value or not key_value or not isinstance(storyboard, Mapping):
        raise ValueError("save_surprise_storyboard requires a session id, a key and the storyboard.")
    if not database_enabled():
        raise RuntimeError("Database is not configured.")
    try:
        with get_db_connection() as connection:
            connection.execute(
                """
                INSERT INTO surprise_storyboards (session_id, story_key, storyboard, created_at)
                VALUES (%s, %s, %s::jsonb, NOW())
                ON CONFLICT (session_id) DO UPDATE SET
                    story_key = EXCLUDED.story_key,
                    storyboard = EXCLUDED.storyboard,
                    created_at = NOW()
                """,
                (session_id_value, key_value, json.dumps(_without_nul(dict(storyboard)), ensure_ascii=False)),
            )
            connection.commit()
    except Exception as exc:
        logger.error("Failed to save a storyboard for session_id=%s (%s)", session_id_value, _error_label(exc))
        raise RuntimeError("Could not save the storyboard.") from exc


# ── Surprise me's performance script (ai/surprise_performance.py) ─────────


def fetch_surprise_script(session_id: str, script_key: str, *, organization_id: OrgScope) -> Optional[Dict[str, Any]]:
    """The session's performance script (`{version, source, lines}`) when it
    was made for `script_key`; None when there is none, it is for another
    take, or the session isn't in the scope (a blank scope never queries).
    Raises when the database can't be read."""
    session_id_value = str(session_id or "").strip()
    key_value = str(script_key or "").strip()
    if not database_enabled() or not session_id_value or not key_value:
        return None
    try:
        org_filter = _org_filter(organization_id)
    except ScopeMatchesNothing:
        return None
    scope_clause = " AND c.organization_id = %s" if org_filter is not None else ""
    params = (session_id_value, key_value) + ((org_filter,) if org_filter is not None else ())
    with get_db_connection() as connection:
        row = connection.execute(
            f"""
            SELECT s.script
            FROM surprise_scripts s
            JOIN session_cache c ON c.session_id = s.session_id
            WHERE s.session_id = %s AND s.script_key = %s{scope_clause}
            LIMIT 1
            """,
            params,
        ).fetchone()
    if not row or not isinstance(row.get("script"), dict):
        return None
    return dict(row["script"])


def save_surprise_script(session_id: str, script_key: str, script: Mapping[str, Any]) -> None:
    """Save the session's performance script whole (the show job, after the
    check), replacing one for an older take. Raises RuntimeError when the
    write fails, or when the session is gone."""
    session_id_value = str(session_id or "").strip()
    key_value = str(script_key or "").strip()
    if not session_id_value or not key_value or not isinstance(script, Mapping):
        raise ValueError("save_surprise_script requires a session id, a key and the script.")
    if not database_enabled():
        raise RuntimeError("Database is not configured.")
    try:
        with get_db_connection() as connection:
            connection.execute(
                """
                INSERT INTO surprise_scripts (session_id, script_key, script, created_at)
                VALUES (%s, %s, %s::jsonb, NOW())
                ON CONFLICT (session_id) DO UPDATE SET
                    script_key = EXCLUDED.script_key,
                    script = EXCLUDED.script,
                    created_at = NOW()
                """,
                (session_id_value, key_value, json.dumps(_without_nul(dict(script)), ensure_ascii=False)),
            )
            connection.commit()
    except Exception as exc:
        logger.error("Failed to save a performance script for session_id=%s (%s)", session_id_value, _error_label(exc))
        raise RuntimeError("Could not save the performance script.") from exc


# ── The question desk's saved replies (record/question_desk.py) ───────────
#
# One row per question the host marked or replied to. Reads and writes join
# `session_cache` on its org, so an out-of-scope session reads nothing and
# writes nothing.


def fetch_question_replies(session_id: str, organization_id: OrgScope) -> Dict[str, Dict[str, Any]]:
    """A session's saved replies by `question_desk.saved_key` ("qa:<id>",
    "chat:<id>"): `{answered, reply, located, evidence_id, updated_at}`
    (`located`: `confirmed`, `rejected` or None). Empty when
    there are none, the session isn't in the scope, or the scope is blank
    (never queries). Raises when the database can't be read."""
    session_id_value = str(session_id or "").strip()
    if not database_enabled() or not session_id_value:
        return {}
    try:
        org_filter = _org_filter(organization_id)
    except ScopeMatchesNothing:
        return {}
    where_clause = "r.session_id = %s" + (" AND c.organization_id = %s" if org_filter is not None else "")
    params = (session_id_value,) + ((org_filter,) if org_filter is not None else ())
    with get_db_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT r.source, r.livestorm_id, r.evidence_id, r.answered, r.reply, r.located, r.updated_at
            FROM session_question_replies r
            JOIN session_cache c ON c.session_id = r.session_id
            WHERE {where_clause}
            ORDER BY r.source, r.livestorm_id
            """,
            params,
        ).fetchall()
    return {
        f"{row['source']}:{row['livestorm_id']}": {
            "answered": bool(row["answered"]),
            "reply": row["reply"],
            "located": row["located"],
            "evidence_id": row["evidence_id"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    }


def save_question_reply(
    session_id: str,
    *,
    organization_id: OrgScope,
    source: str,
    livestorm_id: str,
    evidence_id: str,
    answered: bool,
    reply: Optional[str],
    updated_by: Optional[str],
) -> bool:
    """Save what the host marked for one question, replacing what they saved
    before. Written only when the session is in the scope (the insert
    selects it from `session_cache` with the org filter): False when it
    isn't, or the scope is blank (never queries). `updated_by` is the
    Livestorm user id, never an email. Raises RuntimeError when the write
    fails."""
    session_id_value = str(session_id or "").strip()
    livestorm_id_value = str(livestorm_id or "").strip()
    if not session_id_value or not livestorm_id_value or not str(evidence_id or "").strip():
        raise ValueError("save_question_reply requires a session id, a Livestorm id and an evidence id.")
    if source not in ("qa", "chat"):
        raise ValueError("source is 'qa' or 'chat'.")
    try:
        org_filter = _org_filter(organization_id)
    except ScopeMatchesNothing:
        return False
    if not database_enabled():
        raise RuntimeError("Database is not configured.")
    scope_clause = " AND c.organization_id = %s" if org_filter is not None else ""
    params = (
        str(source),
        livestorm_id_value,
        str(evidence_id).strip(),
        bool(answered),
        reply or None,
        str(updated_by or "").strip() or None,
        session_id_value,
    ) + ((org_filter,) if org_filter is not None else ())
    try:
        with get_db_connection() as connection:
            row = connection.execute(
                f"""
                INSERT INTO session_question_replies (
                    session_id, source, livestorm_id, evidence_id, answered, reply, updated_by, updated_at
                )
                SELECT c.session_id, %s, %s, %s, %s, %s, %s, NOW()
                FROM session_cache c
                WHERE c.session_id = %s{scope_clause}
                LIMIT 1
                ON CONFLICT (session_id, source, livestorm_id) DO UPDATE SET
                    evidence_id = EXCLUDED.evidence_id,
                    answered = EXCLUDED.answered,
                    reply = EXCLUDED.reply,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = NOW()
                RETURNING session_id
                """,
                params,
            ).fetchone()
            connection.commit()
    except Exception as exc:
        logger.error("Failed to save a question reply for session_id=%s (%s)", session_id_value, _error_label(exc))
        raise RuntimeError("Could not save the question's answer.") from exc
    return row is not None


def save_located_decision(
    session_id: str,
    *,
    organization_id: OrgScope,
    source: str,
    livestorm_id: str,
    evidence_id: str,
    decision: str,
    updated_by: Optional[str],
) -> bool:
    """Save what the host decided on a located answer: `confirmed` also
    marks the question answered (a reply they wrote is kept); `rejected`
    changes nothing else, so the question stays as it was and is never
    suggested again. Scoped like `save_question_reply`: False when the
    session isn't in the scope, or the scope is blank (never queries).
    Raises RuntimeError when the write fails."""
    session_id_value = str(session_id or "").strip()
    livestorm_id_value = str(livestorm_id or "").strip()
    if not session_id_value or not livestorm_id_value or not str(evidence_id or "").strip():
        raise ValueError("save_located_decision requires a session id, a Livestorm id and an evidence id.")
    if source not in ("qa", "chat"):
        raise ValueError("source is 'qa' or 'chat'.")
    if decision not in ("confirmed", "rejected"):
        raise ValueError("decision is 'confirmed' or 'rejected'.")
    try:
        org_filter = _org_filter(organization_id)
    except ScopeMatchesNothing:
        return False
    if not database_enabled():
        raise RuntimeError("Database is not configured.")
    scope_clause = " AND c.organization_id = %s" if org_filter is not None else ""
    params = (
        str(source),
        livestorm_id_value,
        str(evidence_id).strip(),
        decision == "confirmed",
        decision,
        str(updated_by or "").strip() or None,
        session_id_value,
    ) + ((org_filter,) if org_filter is not None else ())
    try:
        with get_db_connection() as connection:
            row = connection.execute(
                f"""
                INSERT INTO session_question_replies (
                    session_id, source, livestorm_id, evidence_id, answered, located, updated_by, updated_at
                )
                SELECT c.session_id, %s, %s, %s, %s, %s, %s, NOW()
                FROM session_cache c
                WHERE c.session_id = %s{scope_clause}
                LIMIT 1
                ON CONFLICT (session_id, source, livestorm_id) DO UPDATE SET
                    evidence_id = EXCLUDED.evidence_id,
                    answered = session_question_replies.answered OR EXCLUDED.answered,
                    located = EXCLUDED.located,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = NOW()
                RETURNING session_id
                """,
                params,
            ).fetchone()
            connection.commit()
    except Exception as exc:
        logger.error("Failed to save a located answer's decision for session_id=%s (%s)", session_id_value, _error_label(exc))
        raise RuntimeError("Could not save the decision.") from exc
    return row is not None


# ── Chat labels (record/chat_labels.py) ───────────────────────────────────
#
# One row per session: every chat message's label, written whole by the
# analysis the user starts. A replace swaps the whole set; a failed write
# keeps the previous one. Reads join `session_cache` on its org.

_CHAT_HASH = re.compile(r"^[0-9a-f]{64}$")


def replace_chat_labels(
    session_id: str,
    labels: Sequence[Mapping[str, Any]],
    *,
    chat_hash: str,
    model: str,
) -> Dict[str, Any]:
    """Store a session's chat labels, replacing any earlier set.

    `labels` must pass `record.chat_labels.check_labels` (ValueError
    otherwise, naming ids, never text); the caller has already checked that
    they cover the chat whose `chat_hash` it passes. Stored with
    `LABEL_VERSION`. Raises RuntimeError when the write fails or the session
    isn't cached; the earlier labels are kept. Returns `{label_version,
    message_count}`.
    """
    from livestorm_app.record.chat_labels import LABEL_VERSION, check_labels

    session_id_value = str(session_id or "").strip()
    if not session_id_value:
        raise ValueError("replace_chat_labels requires a session_id.")
    if not isinstance(chat_hash, str) or not _CHAT_HASH.match(chat_hash):
        raise ValueError("chat_hash is a lowercase SHA-256 hex digest.")
    model_value = str(model or "").strip()
    if not model_value:
        raise ValueError("replace_chat_labels requires the model that made the labels.")
    checked = check_labels(labels)
    if not database_enabled():
        raise RuntimeError("Database is not configured.")
    try:
        with get_db_connection() as connection:
            connection.execute(
                """
                INSERT INTO session_chat_labels (
                    session_id, label_version, chat_hash, model, message_count, labels, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, NOW())
                ON CONFLICT (session_id) DO UPDATE SET
                    label_version = EXCLUDED.label_version,
                    chat_hash = EXCLUDED.chat_hash,
                    model = EXCLUDED.model,
                    message_count = EXCLUDED.message_count,
                    labels = EXCLUDED.labels,
                    created_at = NOW()
                """,
                (
                    session_id_value,
                    LABEL_VERSION,
                    chat_hash,
                    model_value,
                    len(checked),
                    json.dumps(checked, ensure_ascii=False),
                ),
            )
            connection.commit()
    except Exception as exc:
        logger.error("Failed to save the chat labels for session_id=%s (%s)", session_id_value, _error_label(exc))
        raise RuntimeError("Could not save the chat labels; the previous labels are kept.") from exc
    return {"label_version": LABEL_VERSION, "message_count": len(checked)}


def fetch_chat_labels(session_id: str, organization_id: OrgScope) -> Optional[Dict[str, Any]]:
    """A session's stored chat labels, scoped by org: `{label_version,
    chat_hash, model, message_count, labels, created_at}`, the labels in id
    order. None when there are none, or the session isn't cached or isn't in
    the scope; a blank scope matches nothing and never queries. Returned as
    stored, whatever their version or chat: the reader compares
    `label_version` and `chat_hash` with the chat it shows. Raises when the
    database can't be read.
    """
    session_id_value = str(session_id or "").strip()
    if not database_enabled() or not session_id_value:
        return None
    try:
        org_filter = _org_filter(organization_id)
    except ScopeMatchesNothing:
        return None
    where_clause = "l.session_id = %s" + (" AND c.organization_id = %s" if org_filter is not None else "")
    params = (session_id_value,) + ((org_filter,) if org_filter is not None else ())
    with get_db_connection() as connection:
        row = connection.execute(
            f"""
            SELECT l.label_version, l.chat_hash, l.model, l.message_count, l.labels, l.created_at
            FROM session_chat_labels l
            JOIN session_cache c ON c.session_id = l.session_id
            WHERE {where_clause}
            LIMIT 1
            """,
            params,
        ).fetchone()
    return dict(row) if row else None


def fetch_chat_labels_stamp(session_id: str, organization_id: OrgScope) -> Optional[Dict[str, Any]]:
    """What identifies a session's stored labels, without the labels:
    `{label_version, chat_hash, created_at}`, scoped like
    `fetch_chat_labels`. The record route reads it on every request for its
    cache key. None when there are none or the session isn't in the scope;
    a blank scope never queries. Raises when the database can't be read."""
    session_id_value = str(session_id or "").strip()
    if not database_enabled() or not session_id_value:
        return None
    try:
        org_filter = _org_filter(organization_id)
    except ScopeMatchesNothing:
        return None
    where_clause = "l.session_id = %s" + (" AND c.organization_id = %s" if org_filter is not None else "")
    params = (session_id_value,) + ((org_filter,) if org_filter is not None else ())
    with get_db_connection() as connection:
        row = connection.execute(
            f"""
            SELECT l.label_version, l.chat_hash, l.created_at
            FROM session_chat_labels l
            JOIN session_cache c ON c.session_id = l.session_id
            WHERE {where_clause}
            LIMIT 1
            """,
            params,
        ).fetchone()
    return dict(row) if row else None


# ── Model usage (ai/usage.py) ──────────────────────────────────────────────

_LLM_USAGE_COLUMNS = (
    "organization_id", "session_id", "job_kind", "job_id", "purpose", "provider",
    "model_requested", "model_served", "effort", "max_tokens", "streamed",
    "stop_reason", "refusal_category", "outcome",
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
    "cache_write_1h_tokens", "reasoning_tokens", "cost_usd_micros", "duration_ms",
    "request_id", "prompt_hash", "dossier_hash", "record_version", "usage_raw",
)


def insert_llm_usage(row: Dict[str, Any]) -> None:
    """Save one `llm_usage` row (built by `ai.usage.build_usage_row`).

    Raises on failure, like every write. Its one caller, `ai.usage.record_call`,
    logs the failure and lets the job go on: the one exception to "writes
    raise" (docs/data-model.md).
    """
    if not database_enabled():
        return
    unknown = sorted(set(row) - set(_LLM_USAGE_COLUMNS))
    if unknown:
        raise ValueError(f"Unknown llm_usage columns: {unknown}")
    columns = [column for column in _LLM_USAGE_COLUMNS if column in row]
    placeholders = ["%s::jsonb" if column == "usage_raw" else "%s" for column in columns]
    values = [
        json.dumps(row[column]) if column == "usage_raw" and row[column] is not None else row[column]
        for column in columns
    ]
    with get_db_connection() as connection:
        connection.execute(
            f"INSERT INTO llm_usage ({', '.join(columns)}) VALUES ({', '.join(placeholders)})",
            values,
        )
        connection.commit()


def llm_spend_since(organization_id: OrgScope, since: datetime) -> int:
    """One org's estimated model spend since `since`, in millionths of a dollar.

    One org only: a blank scope raises `ScopeMatchesNothing` and `ALL_ORGS`
    raises ValueError, so a budget check can never sum every org. Rows
    without a cost (a model with no price in the catalog) count 0. Raises
    when the database can't be read, so a budget check fails closed.
    """
    org_filter = _org_filter(organization_id)
    if org_filter is None:
        raise ValueError("llm_spend_since reads one organization, never ALL_ORGS.")
    if since.tzinfo is None:
        raise ValueError("llm_spend_since needs a timezone-aware datetime.")
    if not database_enabled():
        raise RuntimeError("Database is not configured.")
    with get_db_connection() as connection:
        row = connection.execute(
            """
            SELECT COALESCE(SUM(cost_usd_micros), 0) AS spent
            FROM llm_usage
            WHERE organization_id = %s AND created_at >= %s
            """,
            (org_filter, since),
        ).fetchone()
    return int(row["spent"]) if row else 0


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


def _admin_emails() -> List[str]:
    """Every admin's email, lower case: `ADMIN_EMAILS` and `admin_users`
    (the table is read inside the SQL that uses this)."""
    from livestorm_app.auth import env_admin_emails

    return sorted(env_admin_emails())


# An organization is an admins' organization when one of StormIQ's admins
# signed in from it (`oauth_connections`), now or before (`org_settings`
# remembers it: `remember_admin_organization`).
_ADMIN_ORG_SQL = """
    EXISTS (
        SELECT 1 FROM oauth_connections oc
        WHERE oc.organization_id = {org}
          AND (LOWER(oc.email) = ANY(%s) OR LOWER(oc.email) IN (SELECT LOWER(email) FROM admin_users))
    )
"""


def fetch_surprise_enabled(organization_id: str) -> bool:
    """Whether Surprise Me! is on for an organization: its `org_settings`
    row; without one, on only for the admins' own organization (one of
    StormIQ's admins signed in from it), off for every other: an admin opts
    an organization in from the Admin page. A blank organization (a session
    cached without one, local development) has no setting and is on. Raises
    when the database can't be read: callers fail closed."""
    org = str(organization_id or "").strip()
    if not org or not database_enabled():
        return True
    with get_db_connection() as connection:
        row = connection.execute(
            "SELECT surprise_enabled FROM org_settings WHERE organization_id = %s", (org,)
        ).fetchone()
        if row is not None:
            return bool(row["surprise_enabled"])
        admins = connection.execute(
            "SELECT " + _ADMIN_ORG_SQL.format(org="%s") + " AS admin_org", (org, _admin_emails())
        ).fetchone()
    return bool(admins and admins["admin_org"])


def remember_admin_organization(organization_id: str) -> None:
    """An admin signed in from this organization: Surprise Me! is on for it
    unless an admin already set it (an existing setting is never changed).
    Raises on a failed write."""
    org = str(organization_id or "").strip()
    if not org or not database_enabled():
        return
    with get_db_connection() as connection:
        connection.execute(
            """
            INSERT INTO org_settings (organization_id, surprise_enabled, updated_by_email, updated_at)
            VALUES (%s, TRUE, NULL, NOW())
            ON CONFLICT (organization_id) DO NOTHING
            """,
            (org,),
        )
        connection.commit()


def save_surprise_enabled(organization_id: str, enabled: bool, *, updated_by_email: Optional[str]) -> None:
    """Turn Surprise Me! on or off for an organization (Admin only). Raises
    on a blank organization or a failed write."""
    org = str(organization_id or "").strip()
    if not org:
        raise ValueError("An organization id is required.")
    if not database_enabled():
        raise RuntimeError("Database is not configured.")
    with get_db_connection() as connection:
        connection.execute(
            """
            INSERT INTO org_settings (organization_id, surprise_enabled, updated_by_email, updated_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (organization_id) DO UPDATE
                SET surprise_enabled = EXCLUDED.surprise_enabled,
                    updated_by_email = EXCLUDED.updated_by_email,
                    updated_at = NOW()
            """,
            (org, bool(enabled), str(updated_by_email or "").strip() or None),
        )
        connection.commit()


def admin_list_organizations() -> List[Dict[str, Any]]:
    """Every organization with a cached session or a setting, for the Admin
    page: `{organization_id, sessions, last_session_at, surprise_enabled,
    updated_by_email, updated_at}`, the most sessions first. Raises when the
    database can't be read."""
    if not database_enabled():
        return []
    with get_db_connection() as connection:
        rows = connection.execute(
            """
            WITH orgs AS (
                SELECT organization_id, COUNT(*) AS sessions, MAX(updated_at) AS last_session_at
                FROM session_cache
                WHERE COALESCE(organization_id, '') <> ''
                GROUP BY organization_id
            )
            SELECT COALESCE(o.organization_id, s.organization_id) AS organization_id,
                   COALESCE(o.sessions, 0) AS sessions,
                   o.last_session_at,
                   COALESCE(s.surprise_enabled, {admin_org}) AS surprise_enabled,
                   s.updated_by_email,
                   s.updated_at
            FROM orgs o
            FULL OUTER JOIN org_settings s ON s.organization_id = o.organization_id
            ORDER BY sessions DESC, organization_id
            """.replace("{admin_org}", _ADMIN_ORG_SQL.format(org="COALESCE(o.organization_id, s.organization_id)")),
            (_admin_emails(),),
        ).fetchall()
    return [dict(row) for row in rows]


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
    """Delete a cached session. Its roster, people and sync rows go with it
    (`ON DELETE CASCADE`); its `llm_usage` rows stay."""
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


# ── Reset of AI outputs (scripts/reset_ai_outputs.py) ─────────────────────
#
# Everything a model wrote, or that was derived from what a model wrote, so
# users regenerate it with the current models and formats. What sessions
# were fetched with (details, chat, questions, people), their transcripts,
# the host's own answers (desk replies, figure checks), sign-ins, settings
# and `llm_usage` are kept.

AI_OUTPUT_COLUMNS = (
    "analysis_bundle",
    "deep_analysis_bundle",
    "content_repurpose_bundle",
    "smart_recap_bundle",
    "faq_bundle",
    "decisions_bundle",
    "team_digest_bundle",
    "attendee_followup_bundle",
    "no_show_followup_bundle",
)
# Whole tables of AI-derived rows (each references `session_cache`).
AI_OUTPUT_TABLES = (
    "output_versions",
    "session_chat_labels",
    "session_understanding",
    "session_faq",
    "surprise_storyboards",
    "surprise_scripts",
    "surprise_voice_clips",
)


def count_ai_outputs(*, include_covers: bool = False) -> Dict[str, int]:
    """How many rows the reset would touch, by what: one count per bundle
    column (sessions holding it), per table, the desk's decisions on located
    answers and, with `include_covers`, the cover images. Raises when the
    database can't be read."""
    if not database_enabled():
        raise RuntimeError("Database is not configured.")
    counts: Dict[str, int] = {}
    with get_db_connection() as connection:
        for column in AI_OUTPUT_COLUMNS:
            row = connection.execute(f"SELECT COUNT(*) AS n FROM session_cache WHERE {column} IS NOT NULL").fetchone()
            counts[f"session_cache.{column}"] = int(row["n"])
        if include_covers:
            row = connection.execute("SELECT COUNT(*) AS n FROM session_cache WHERE cover_image_bytes IS NOT NULL").fetchone()
            counts["session_cache.cover_image"] = int(row["n"])
        for table in AI_OUTPUT_TABLES:
            counts[table] = int(connection.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])
        row = connection.execute("SELECT COUNT(*) AS n FROM session_question_replies WHERE located IS NOT NULL").fetchone()
        counts["session_question_replies.located"] = int(row["n"])
    return counts


def reset_ai_outputs(*, include_covers: bool = False) -> Dict[str, int]:
    """Remove every AI output in one transaction (all or nothing): the
    bundle columns set to NULL, the AI-derived tables emptied, the desk's
    decisions on located answers cleared (the host's replies stay) and, with
    `include_covers`, the cover images. `updated_at` is left alone, so the
    session library keeps its order. Returns the rows touched, by what.
    Raises RuntimeError when the write fails (nothing is changed then)."""
    if not database_enabled():
        raise RuntimeError("Database is not configured.")
    done: Dict[str, int] = {}
    try:
        with get_db_connection() as connection:
            for column in AI_OUTPUT_COLUMNS:
                done[f"session_cache.{column}"] = connection.execute(
                    f"UPDATE session_cache SET {column} = NULL WHERE {column} IS NOT NULL"
                ).rowcount
            if include_covers:
                done["session_cache.cover_image"] = connection.execute(
                    """
                    UPDATE session_cache
                    SET cover_image_bytes = NULL, cover_image_mime = NULL, cover_image_generated_at = NULL
                    WHERE cover_image_bytes IS NOT NULL
                    """
                ).rowcount
            for table in AI_OUTPUT_TABLES:
                done[table] = connection.execute(f"DELETE FROM {table}").rowcount
            done["session_question_replies.located"] = connection.execute(
                "UPDATE session_question_replies SET located = NULL WHERE located IS NOT NULL"
            ).rowcount
            connection.commit()
    except Exception as exc:
        logger.error("Failed to reset the AI outputs (%s)", _error_label(exc))
        raise RuntimeError("Could not reset the AI outputs; nothing was changed.") from exc
    return {key: int(value or 0) for key, value in done.items()}
