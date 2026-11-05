# Data model

One Postgres database. The URL comes from `DATABASE_URL`, `POSTGRES_URL` or `RENDER_POSTGRES_URL`, in that order. [`db.ensure_database_schema()`](../livestorm_app/db.py) creates and migrates the schema when the web app and the worker start; there is no separate migration tool, so schema changes must be idempotent (`IF NOT EXISTS`). At startup it also deletes duplicate `session_cache` rows (keeping the most recent per session) and closes duplicate active transcript jobs, left over from before the unique indexes existed.

## `session_cache`

Everything StormIQ knows about a session, one row per session.

```sql
session_cache (
  account_key_hash         TEXT NOT NULL,   -- sha256 of the first writer's Livestorm credential (insert-only, informational)
  session_id               TEXT NOT NULL,
  organization_id          TEXT,            -- Livestorm org that owns the session: the access boundary
  session_payload          JSONB,           -- Livestorm session response with its people, minus registrant join links
  event_payload            JSONB,           -- raw Livestorm event response (the session's parent), used for card titles and language
  created_by_user_id       TEXT,            -- Livestorm user who first fetched the session (kept on update)
  created_by_email         TEXT,
  created_by_name          TEXT,
  cover_image_bytes        BYTEA,           -- AI-generated cover (PNG)
  cover_image_mime         TEXT,            -- defaults to image/png
  cover_image_generated_at TIMESTAMPTZ,
  chat_payload             JSONB,           -- Livestorm chat messages
  questions_payload        JSONB,           -- Livestorm questions with askers and responders, minus registrant join links
  transcript_payload       JSONB,           -- full Gladia transcript
  transcript_speaker_names JSONB,           -- { raw_speaker_id: human_label }
  analysis_md              TEXT,            -- legacy: English overall analysis only
  analysis_bundle          JSONB,           -- { English: "...", French: "..." }
  deep_analysis_md         TEXT,            -- legacy: English deep analysis only
  deep_analysis_bundle     JSONB,           -- { English: "...", French: "..." }
  content_repurpose_bundle JSONB,           -- { English: {summary, blog, email, social_media}, French: {...} }
  smart_recap_bundle       JSONB,           -- { professional, hype, surprise }
  created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (account_key_hash, session_id)
)
-- idx_session_cache_session_id_unique   UNIQUE (session_id)
-- idx_session_cache_organization_id     (organization_id, updated_at DESC)
-- idx_session_cache_account_hash        (account_key_hash)
-- idx_session_cache_session_id          (session_id)  -- redundant with the unique index
```

- `organization_id` is stamped from the requesting user's OAuth connection on first fetch, once their Livestorm credentials prove access ([security.md](security.md)). Rows cached before org scoping have a NULL org: only admins (and local-dev callers) can read them until a member of the org fetches the session again, which stamps the org.
- `created_by_*` are kept on update (`COALESCE(NULLIF(col, ''), EXCLUDED.col)`), so the first writer's identity sticks when a teammate refetches.
- The unique index on `session_id` alone means refetching a session from another account updates the same row.

## `transcript_jobs`

```sql
transcript_jobs (
  job_id        TEXT PRIMARY KEY,              -- uuid4 hex
  session_id    TEXT NOT NULL,
  timestamped   BOOLEAN DEFAULT TRUE,
  status        TEXT NOT NULL DEFAULT 'pending', -- 'pending' | 'running' | 'completed' | 'error'
  error         TEXT,
  progress      TEXT,                          -- JSON progress from the Gladia poll
  connection_id TEXT,                          -- OAuth connection that started the job (NULL = LS_API_KEY, local dev).
                                               -- Read and cleared when the worker claims the job; cleared when it ends.
                                               -- Never returned by any route: it is also the session cookie value.
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
-- idx_transcript_jobs_session_id               (session_id, created_at DESC)
-- idx_transcript_jobs_status_updated_at        (status, updated_at)  -- the stuck-job sweeper
-- idx_transcript_jobs_one_active_per_session   UNIQUE (session_id) WHERE status IN ('pending','running')
--   db.create_transcript_job uses INSERT … ON CONFLICT DO NOTHING and attaches to the existing job
```

## `oauth_connections`

```sql
oauth_connections (
  connection_id    TEXT PRIMARY KEY,           -- secrets.token_urlsafe(24); also the session cookie value
  provider         TEXT NOT NULL,              -- 'livestorm'
  user_id          TEXT,                       -- Livestorm /me data.id
  email            TEXT,
  organization_id  TEXT,
  access_token     TEXT NOT NULL,
  refresh_token    TEXT,
  token_type       TEXT NOT NULL DEFAULT 'Bearer',
  scope            TEXT,
  expires_at       TIMESTAMPTZ,
  profile          JSONB,                      -- snapshot of the /me payload and parsed fields
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
-- idx_oauth_connections_user_id   (provider, user_id)
```

## `admin_users`

```sql
admin_users (
  email              TEXT PRIMARY KEY,         -- lower-cased; matched against oauth_connections.email
  user_id            TEXT,
  promoted_by_email  TEXT,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
```

Admins are this table plus the `ADMIN_EMAILS` env var (bootstrap admins, who can't be demoted from the UI).

## `system_settings`

```sql
system_settings (
  key        TEXT PRIMARY KEY,                 -- allow-listed in routes/admin.py: llm_provider, llm_default_model, llm_smart_recap_model, image_model
  value      TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
```

Admin-editable overrides for provider and model selection (`/api/admin/settings`, see [providers.md](providers.md)). Each process caches a value for 60 s and keeps serving the last known value if the database is briefly unreachable.

## Caching rules

- **Cache first.** Every fetch route checks `session_cache` before calling Livestorm or Gladia. `force_refresh: true` is the only bypass. Entries never expire.
- **One row holds the latest of everything.** There is no history of analyses: regenerating an output replaces that language's (or tone's) entry.
- **Bundle columns are written one entry at a time.** AI runners never rewrite a whole bundle: `db.merge_cached_bundle_entry(session_id, column, key, value)` sets one language or tone with a single `jsonb_set` UPDATE, so concurrent jobs on the same session (English and French, or two recap tones) can't erase each other's results. Never write a bundle column with `upsert_cached_session` from a worker job.
- **Writes fail loudly.** `upsert_cached_session`, `merge_cached_bundle_entry` and `rewrite_base_payloads` raise on database errors; a job is only `completed` once its result is saved.
- **No registrant join links.** The session, chat and questions payloads are saved without each person's `password_key` and `connection_link` ([security.md](security.md#data-stormiq-doesnt-keep)). Every other key Livestorm returns is kept.
- **Every write bumps `updated_at`.** The in-process transcript-insights cache is keyed on it ([architecture.md](architecture.md#reading-a-workspace)). The one exception is `db.rewrite_base_payloads`, for maintenance scripts. It replaces base payloads without bumping `updated_at`, so the library keeps its order. It writes only if `updated_at` still matches the value the script read, so it never overwrites a newer save. Use it only for changes nothing in the app reads, such as removing join links. `db.scan_base_payloads` reads the rows for it in batches.

## Connections

Each process (web, worker) keeps one `psycopg_pool` connection pool behind `db.get_db_connection()`: up to `DB_POOL_MAX_SIZE` (10) connections, checked before use and recycled hourly. Web and worker together hold at most 20 connections, which must stay under the Render Postgres plan's limit.
