# StormIQ — CLAUDE.md
## Authoritative Business Logic & Architecture Reference

> **This file is the single source of truth for StormIQ (repo: chat-analysis).**
> Every feature, every API route, every data-model decision must be consistent with this file.
> Update this file in the same PR as the change that affects it. If a spec or design doc contradicts it, this file wins — fix the spec.
>
> Future LLMs: read this file before making any change. Refuse or flag anything that contradicts it.

---

## 0. The two documentation files

| File | Audience | Updated when |
|---|---|---|
| **CLAUDE.md** (this file) | Engineers + Claude | Every change that touches architecture, schema, routes, providers, business rules, env vars, deployment, or roadmap |
| **doc.md** | Sales + product readers, external audiences | A new user-facing feature ships, a metric or formula changes, a chart is added/removed, or behaviour visible in the UI changes |

`doc.md` is a lightweight derivative — it describes **what the app does** in neutral product terms (views, charts, formulas, exports). `CLAUDE.md` describes **how the app is built and why**. Both must stay accurate; when in doubt, change CLAUDE.md first, then propagate the user-visible bits to doc.md.

---

## 1. What StormIQ Is

**StormIQ** is a Livestorm-adjacent companion application. It takes a finished Livestorm session, fetches its overview / chat / questions / recording transcript, caches them, and exposes:

- Session overview with audience analytics
- Transcript with speaking diagnostics (pace, airtime, pauses, NER, segments)
- Chat & Questions analytics (contributors, activity over time, response coverage)
- AI Overall Analysis (executive view, 5 sections)
- AI Deep Analysis (host-facing diagnostic, 10 sections, with 0–100 session scores)
- Smart Recap in three tones (Professional / Hype / Surprise)
- Content Repurposing — summary, blog post, follow-up email, social posts

All AI outputs are available in **English or French** and exportable as PDF. The app does not record sessions; it analyses sessions Livestorm already recorded.

**Branding**: brand-adjacent to Livestorm (Object Sans, Livestorm Blue, Winter Green). App name is **StormIQ** — never use the Livestorm logo as the app logo. See [BRAND.md](BRAND.md).

---

## 2. Product Surfaces

Single SPA served by FastAPI. No auth-walled vs public split — once connected to a Livestorm workspace, all views are available.

| Route | View | Requires |
|---|---|---|
| `/` | Redirect to `/single-analysis` | — |
| `/search` | (Phase 4) Three modes — by session ID, by event ID, browse workspace | Connected workspace |
| `/single-analysis` | (Phase 4) Card grid of every cached session in the user's org | Connected workspace |
| `/single-analysis/:sessionId` | Redirect to `…/session-overview` | Session in workspace |
| `/single-analysis/:sessionId/session-overview` | Hero metrics, people, charts | Fetched session |
| `/single-analysis/:sessionId/transcript` | 8-tab transcript diagnostics | Completed transcript |
| `/single-analysis/:sessionId/chat-questions` | 5-tab audience analytics | Fetched session |
| `/single-analysis/:sessionId/analysis` | Overall + Deep AI analysis | Completed transcript |
| `/single-analysis/:sessionId/smart-recap` | 3-tone recap | Completed transcript |
| `/single-analysis/:sessionId/content-repurposing` | Summary / blog / email / social | Completed transcript |
| `/cross-analysis` | (Phase 4) Placeholder for Phase 3 cross-session feature | — |
| `/auth/callback` | OAuth landing | — |
| `/beta-info` | Beta notice | — |
| Legacy: `/events`, `/session-overview`, `/transcript`, … | Redirect to the new equivalent (uses `state.workspace.sessionId` to construct the target) | — |

`:sessionId` is the URL contract for sharing — teammates in the same Livestorm org can paste any `/single-analysis/:sessionId/...` link and land on the requested tab with the cached workspace pre-loaded. The store watches the route param and calls `loadSessionById` to materialise the workspace on URL change.

---

## 3. There is no billing model

This project has **no Stripe integration, no plans, no top-ups, no quotas, no rate limits**. Every Livestorm workspace that connects can run unlimited analyses. AI costs are absorbed by the operator's provider keys (Anthropic by default, OpenAI when `LLM_PROVIDER=openai`, and OpenAI for cover images).

If a billing model is introduced later, document it here.

---

## 4. Authentication

Every request's identity is resolved once by [`livestorm_app/auth.py`](livestorm_app/auth.py). Routes declare it as a FastAPI dependency (`get_caller`, `require_admin`, or `get_optional_caller`) and never resolve org ids or admin status by hand.

**Identity sources**

1. **Livestorm OAuth** session cookie (`livestorm_oauth_connection`). The standard path. PKCE flow with an HMAC-signed handshake cookie that expires after 10 minutes, checked server-side. Access tokens are refreshed automatically when within 5 minutes of expiry; a failed refresh returns 401 "Please reconnect". See [oauth_client.py](livestorm_app/oauth_client.py).
2. **Local-dev mode.** Enabled only when `STORMIQ_LOCAL_DEV=1` **and** the process is not on Render (Render sets `RENDER=true` on every service). The env flag is what grants access. A request must also use a loopback `Host` (`localhost`, `127.0.0.1`, `::1`); that check does not authenticate anyone, it stops DNS-rebinding web pages from reaching a developer's local server under another hostname. In local-dev mode:
   - Requests without an OAuth cookie act as a trusted local operator.
   - Livestorm calls use `LS_API_KEY`.
   - `/api/dev/auto-login` seeds an OAuth-like identity from `LS_API_KEY`. Its fixed connection id (`dev-auto-login`) is rejected whenever local-dev mode is off.
   - Inline `apiKey` fields in request bodies are accepted only in this mode; elsewhere they return 400.

**Caller kinds and read scope** (`Caller.read_scope`)

| Kind | Who | Reads cached sessions of |
|---|---|---|
| `user` | OAuth user | their own `organization_id` only. A connection with no org sees nothing. |
| `admin` | OAuth user in `ADMIN_EMAILS` or `admin_users` | every org (`ALL_ORGS`) |
| `local_dev` | no OAuth cookie, local-dev mode on | every org (`ALL_ORGS`) |
| anonymous | everyone else | nothing: **401** on every session, event and admin route |

**Fail-closed rules**

- `db.fetch_cached_session` and `db.fetch_cover_image` require an explicit `organization_id` scope. A blank scope matches no rows. `ALL_ORGS` is a named sentinel that only admin, local-dev and worker code pass.
- A session outside the caller's scope is indistinguishable from a missing one (404, or 204 on `/cached`).
- Writes (`speaker-labels`) and AI-job POSTs first check that the row is in the caller's scope.
- Fetch routes only stamp `organization_id` on a row after the caller's own Livestorm credentials prove access to the session (Livestorm 404s otherwise). The stamp is first-writer-wins, so stamping without that proof would let an outsider claim a session before its owner fetches it.
- `tests/test_access_control.py` enumerates every `/api/sessions/{session_id}*` route from `app.routes` and asserts: anonymous → 401; another org → no data, no writes, no enqueues; same org and admin → 2xx. A new route that forgets the dependency fails CI.

Server-side secrets used by the backend (never sent to the client): `CLAUDE_API_KEY`, `OPENAI_API_KEY`, `GLADIA_KEY`, OAuth client credentials, `SESSION_SECRET`, Postgres URL, and `LS_API_KEY`, which `/api/bootstrap` no longer returns. Error responses carry `{resource, message, details: {resource, status_code?}}`; upstream URLs and response bodies are logged server-side only. See §13.

---

## 5. Processing Flow

### Session fetch

```
User selects a session
→ POST /api/sessions/{id}/fetch-base        (session + chat + questions, synchronous)
   then /fetch-transcript                    (starts the transcript job, see below)
   /fetch does both in one call and returns the base workspace plus
   `transcriptJob: {jobId, jobStatus}` (or the full workspace when the
   transcript is already cached). The frontend uses fetch-base + fetch-transcript.

Backend:
  1. Check session_cache → return cached if present (and not force_refresh)
  2. Fetch session payload, chat, questions from Livestorm API
  3. Upsert into session_cache
  4. Transcript: enqueue a worker job, return { jobId, jobStatus } at once
  5. Frontend polls GET /api/sessions/{id}/transcript-job every 6s
  6. When job completes: the poll returns the full workspace payload
```

Transcription never runs inside a web request: the web process does not import the Gladia client.

### Transcript transcription (queue-backed)

```
POST /api/sessions/{id}/fetch-transcript
→ fetch_session_transcript_data:
   1. Return cached workspace if transcript_payload is already present
      (unless force_refresh).
   2. If the session has an active job (transcript_jobs.status in
      'pending'/'running'), attach to it — force_refresh included. Before
      attaching, ask arq whether that job is still alive; if arq reports it
      failed, or has no trace of it 2 minutes after the row was last updated
      (Redis restart, deploy), the row is marked 'error' and a new job starts.
   3. Otherwise: insert a transcript_jobs row and enqueue `run_transcription`
      with arq id `transcription:{job_id}`, return { jobId, jobStatus: 'pending' }.
      The partial unique index allows one active row per session; a request
      that loses the insert race attaches to the winner's job.

Worker (livestorm_app.worker.run_transcription(job_id, session_id)):
   1. Claim the row: 'pending' → 'running' in one UPDATE that also reads
      and clears transcript_jobs.connection_id. A row that is no longer
      'pending' (swept, closed as lost) is skipped: no paid work.
   2. Wait for a transcription slot (MAX_CONCURRENT_TRANSCRIPTIONS = 2 per
      worker; download + ffmpeg are memory-heavy). While waiting, progress
      is refreshed every minute so the sweeper leaves the row alone.
   3. Resolve credentials: GLADIA_KEY from the worker env; the Livestorm token
      from the claimed connection id (refreshed if close to expiry). Job args
      hold only job_id and session_id: arq stores them in Redis and logs
      them, and a connection id is also the user's session cookie.
   4. asyncio.to_thread(fetch_session_transcript, ...) — download + upload +
      Gladia poll. Progress goes to transcript_jobs.progress and to Redis.
   5. Upsert session_cache.transcript_payload, mark 'completed', clear progress.
   6. On any exception, timeout or cancellation (worker shutdown):
      status='error' with a message, progress cleared. arq retries are disabled
      (max_tries=1) — failures are mostly deterministic and re-runs cost
      Gladia money. The user retries from the UI.

Frontend polls GET /api/sessions/{id}/transcript-job every 6s. Each poll also
reconciles the row with arq (as in step 2), so a job that died in the queue
shows as an error within minutes instead of after the sweeper's hour. The
response carries `progress` (raw Gladia step payload) and `progressRedis`
(stage-floor shape with percent).
```

The arq worker is the **only** transcription path. The stuck-job sweeper (every 10 minutes, see [worker.py](livestorm_app/worker.py)) is the backstop: `running` or `pending` rows silent for an hour are marked `error`. While Gladia processes, the transcriber reports progress on every status change and at least once a minute (`PROGRESS_HEARTBEAT_SECONDS`), which keeps `updated_at` fresh. A job that finishes after being swept still writes `completed`. If saving the transcript fails, the job is marked `error`, never `completed`.

**Smart Recap is on-demand only**: nothing generates it automatically.

**Cover images** are generated by `run_cover_image_job` in exactly two cases: after a Professional Smart Recap completes (the worker chains it, unless the session already has a cover) and by `scripts/backfill_cover_images.py`. Reading a session never starts one. The arq id is `cover_image:{session_id}`, so a cover that is queued, running, or failed within the last hour (arq's retained result) is not started again. Failures are logged and swallowed so a broken image generator never fails the recap.

### AI generation (queue-backed — overall / deep / smart recap / content repurposing)

```
POST /api/sessions/{id}/analysis            -> enqueue overall analysis job
POST /api/sessions/{id}/deep-analysis       -> enqueue deep analysis job
POST /api/sessions/{id}/smart-recap         -> enqueue smart recap job
POST /api/sessions/{id}/content-repurposing -> enqueue content repurposing job

Each POST response is one of:
  - the full serialised workspace (cache hit — the requested bundle is
    already present for the requested language/tone), OR
  - { jobId, jobKind, jobStatus: 'pending'|'running', language|tone }
    (job enqueued, or the same job already in flight).

Request validation: `outputLanguage` must be English or French and `tone`
one of professional / hype / surprise (422 otherwise). Every distinct value
would be a separate paid LLM job.

Dedupe: the arq job id is deterministic, `{kind}:{session_id}:{language|tone}`
(queue.ai_job_id). arq refuses a second job with that id while one is queued
or running, so double-clicks and teammates generating the same output share
one job. arq also keeps a finished job's result for an hour under that id;
`queue.enqueue_unique_job_sync` clears it when the user asks again (e.g.
retry after an error), so a failure never blocks a retry. The poll routes
derive the job id from the session, kind and language/tone; the `jobId`
query parameter is ignored, because predictable ids must not let a caller
read another session's job.

If the worker process is killed mid-job (OOM, not a normal shutdown), arq
keeps reporting that job as running until its in-progress key expires:
the longest job timeout plus 10 s, so about 2 hours. Until then the same
output can't be regenerated (deleting `arq:in-progress:{job id}` in Redis
frees it).

Frontend polls every 4-6s:
  GET /api/sessions/{id}/analysis/job?jobId=...&language=English
  GET /api/sessions/{id}/deep-analysis/job?jobId=...&language=English
  GET /api/sessions/{id}/smart-recap/job?jobId=...&tone=professional
  GET /api/sessions/{id}/content-repurposing/job?jobId=...&language=English

Each GET response is one of:
  - the full serialised workspace (job finished, cache now has the bundle), OR
  - { jobId, jobKind, jobStatus, progress, error? } where:
      jobStatus ∈ {pending, running, completed, error, not_found}
      progress  = Redis stage-floor payload (or null)

Worker side (livestorm_app.worker):
  - run_overall_analysis_job(ctx, session_id, output_language)
  - run_deep_analysis_job(ctx, session_id, output_language)
  - run_smart_recap_job(ctx, session_id, tone)
  - run_content_repurposing_job(ctx, session_id, output_language)
Each reads the active LLM provider's key (CLAUDE_API_KEY by default, OPENAI_API_KEY when
LLM_PROVIDER=openai) from its own env, publishes stage-floor progress
to Redis ('queued' → 'loading_sources' → 'building_prompt' → 'analyzing'),
wraps the existing sync runner in asyncio.to_thread so the worker event
loop stays responsive, and clears progress as soon as it finishes or fails
(pollers read the durable cache first, so no 'done' marker has to linger).

Job timeouts (WorkerSettings.functions): AI flows 15 min, cover image 5 min,
transcription 120 min. A timeout fails the job; the thread it started is not
killed, which is why every HTTP call also has its own timeout (§12). A
cancelled transcription also signals its thread, which stops at its next
progress report (within a minute while Gladia is processing).

Sources of truth:
  - "is this done?"  → session_cache (durable)
  - "what stage?"    → Redis stage-floor key (30m TTL)
  - "did it fail?"   → arq job state (1h TTL by default) — surfaces the
                       worker's exception message
```

**Migration status (Phase 2 commit 2):** all four AI flows now run on the worker. POST routes return immediately with a job id instead of blocking on OpenAI. Long deep-analysis runs on large transcripts no longer hit the proxy timeout (the issue fixed in commit `1141728`). The existing sync `run_overall_analysis` / `run_deep_analysis` / `run_smart_recap` / `run_content_repurposing` functions in api_logic.py are kept — they're now the *worker's* sync payload, called via `asyncio.to_thread` inside each arq job.

### Caching rules

- `session_cache` is keyed by `(account_key_hash, session_id)` with a unique index on `session_id` alone (so re-fetching a session from a different account collapses into the same row).
- `account_key_hash` is `sha256(api_key)` — preserves which account owns the row without storing the raw key.
- All cached fields are JSONB except `analysis_md` / `deep_analysis_md` which are TEXT.
- Cached entries never expire automatically.
- **Bundle columns are written one entry at a time.** AI runners never rewrite a whole bundle: `db.merge_cached_bundle_entry(session_id, column, key, value)` sets one language (or tone) with a single `jsonb_set` UPDATE, so concurrent jobs on the same session (English + French, or two recap tones) cannot erase each other's results. Never write a bundle column with `upsert_cached_session` from a worker job.
- **Writes fail loudly.** `upsert_cached_session` and `merge_cached_bundle_entry` raise on database errors; a job is only `completed` once its result is saved.
- **Only complete AI output is saved.** Empty output, output truncated at the token limit, or an incomplete content-repurposing bundle (any of the four sections missing) fails the job with a clear error and saves nothing.
- `force_refresh: true` in the fetch payload bypasses the cache and re-fetches from Livestorm/Gladia.

### Reading a workspace (performance rules)

- **Derived insights are cached in-process.** `build_transcript_insights` (the ~27 transcript DataFrames) is the costly part of a workspace read. `api_logic._cached_transcript_insights` keeps the last 6 sessions per process, keyed by `(session_id, updated_at)`: every write to a `session_cache` row bumps `updated_at`, so the key changes whenever the input can. The frames are shared between requests, so readers must copy before modifying (`_df_records` and `build_cross_source_insights` do). Insights are built once per read; pass them to `build_cross_source_insights(..., transcript_insights=...)` instead of letting it rebuild them.
- **Polls and job starts read only what they need.** `db.fetch_session_fields(session_id, [fields], organization_id=...)` reads named columns or flags (allow-listed), with the same org-scope rules as `fetch_cached_session`. The transcript poll reads `has_transcript`; AI polls and starts read the bundle column of their kind. The full row is loaded only to return a finished result.
- **The card list never loads payloads.** `db.list_workspace_sessions` selects the session's attributes object, the event title and has-output flags computed in SQL (`db._has_content_sql`).
- **No raw upstream payloads in the workspace.** The serialized workspace carries the derived tables, the display transcript and the AI outputs. `payloads.transcript` is `{subtitles, language, durationSeconds}` (or null when there's no transcript); the full raw transcript is downloaded from `GET /api/sessions/{id}/transcript.json`. Raw session/chat/questions payloads, `text.transcriptPlain`, `tables.transcriptKeyMoments` and `tables.transcriptPace` are not sent (the frontend didn't use them; add a table back when a view needs it).

---

## 6. Analysis output schemas

### Overall Analysis (`/api/sessions/{id}/analysis`)

Markdown blob with 5 sections (see [prompts/analysis_base_prompt.txt](prompts/analysis_base_prompt.txt)):

1. Executive Summary
2. Key Themes
3. Engagement Insights
4. Risks / Friction Signals
5. Actionable Recommendations

Persisted as `analysis_bundle: { English: "...", French: "..." }` (the legacy `analysis_md` column mirrors English only). Switching language reuses any already-generated language; only the missing language triggers a fresh OpenAI call.

### Deep Analysis (`/api/sessions/{id}/deep-analysis`)

Markdown blob with 10 sections (see [prompts/analysis_deep_prompt.txt](prompts/analysis_deep_prompt.txt)):

1. Executive Summary
2. Session Scores (Clarity / Engagement / Interaction / Pace / Alignment, 0–100 each)
3. Key Moments (timestamped, typed: strong / confusion / engagement / drop)
4. Speaker & Interaction Analysis
5. Audience Intent Analysis
6. Cross-Source Synthesis
7. Friction & Risk Signals
8. Business Signals & KPI Mentions
9. Actionable Recommendations (Next Session / Follow-up / Optional)
10. Risks, Ambiguities, And Data Quality Limits

Persisted as `deep_analysis_bundle: { English: "...", French: "..." }` (the legacy `deep_analysis_md` column mirrors English only). Frontend chops sections via a regex parser ([AnalysisView.vue:172](frontend/src/views/AnalysisView.vue#L172)) with bilingual heading aliases.

> **Known fragility**: section parsing depends on the LLM matching exact headings. This is a target for Phase 2 refactor (card registry — see §17).

### Smart Recap (`/api/sessions/{id}/smart-recap`)

Markdown with `# Title` + `# Description`. Three tones: `professional` / `hype` / `surprise`. Persisted as `smart_recap_bundle: { professional: "...", hype: "...", surprise: "..." }`. Transcript-only input.

### Content Repurposing (`/api/sessions/{id}/content-repurposing`)

Four assets generated in **one** OpenAI call as a JSON bundle, then split:

- `summary` — 500–700 words
- `blog` — 1000–1500 words, standalone article
- `email` — subject options + 2 versions, 200–300 words each
- `social_media` — LinkedIn / Facebook / X posts with hashtags

Persisted as `content_repurpose_bundle: { English: {summary, blog, email, social_media}, French: {...} }`. Switching language regenerates the missing language only.

---

## 7. Charts, metrics, and formulas

The deterministic data pipeline lives in [services.py](livestorm_app/services.py) and [session_overview.py](livestorm_app/session_overview.py).

The complete catalog of charts, tables, formulas, and bin definitions is documented in [doc.md §13](doc.md). **Do not duplicate formulas between this file and doc.md.** When a formula changes, update doc.md and reference it here.

Key entry points:

- `build_session_overview_data(session_payload)` → hero stats, people table, country/role/attendance distribution
- `build_transcript_insights(transcript_payload)` → 27 DataFrames covering pace, airtime, pauses, NER, segments, engagement scoring, key moments
- `build_cross_source_insights(chat_df, questions_df, transcript_payload)` → 10-bucket session-stage timeline + reaction moments
- `build_question_stats(questions_df)` → answered/unanswered split

### Engagement scoring (per-minute, transcript-derived)

All components min-max scaled within the session.

- **Engagement Score** = `(pace_score × 0.45 + (1 − silence_penalty) × 0.35 + interruption_score × 0.20) × 100`
- **Clarity Score** = `((1 − silence_penalty) × 0.30 + pace_score × 0.35 + (1 − variation_score) × 0.10) × 100`, then minus `min(filler_count × 3, 20)`
- **Cognitive Load Index** = `(silence_penalty × 0.45 + (1 − pace_score) × 0.20 + variation_score × 0.20) × 100`, then plus `min(filler_count × 3, 20)`

### Person engagement score (audience-side)

`messages_count + (questions_count × 3) + (up_votes_count × 2)` — see [session_overview.py:108-112](livestorm_app/session_overview.py#L108-L112).

### Key moments

A sentence is a candidate if it accumulates ≥ 2 signals or ≥ 3 weighted points from: numeric mention (+2), strong-statement keyword (+2), top-25 named entity (+1), pace ≥ 90th percentile (+1). Top 20 surfaced.

### Pause classification

Gap < 0.3s = natural flow (not counted); 0.3–1.0s = Thinking pause; 1.0–2.0s = Hesitation; ≥ 2.0s = Strong silence. Detection floor: 0.75s.

---

## 8. Language support

- **Interface language**: English by default. Analysis and Content Repurposing views switch UI labels when French is selected for that view's output.
- **Analysis outputs**: English + French. Switching language regenerates only if the target language isn't already in the bundle.
- **Transcription**: Gladia handles many languages; the transcript is captured in the spoken language regardless of UI selection.
- **Smart Recap**: generated in the language of the transcript (no explicit selector).

---

## 9. Database Schema

Postgres, single database. Connection URL resolved from `DATABASE_URL`, `POSTGRES_URL`, or `RENDER_POSTGRES_URL` in that order. Schema is created/migrated by [`ensure_database_schema()`](livestorm_app/db.py#L43) on app startup.

### `session_cache`

Stores everything we know about a session, keyed by session_id.

```sql
session_cache (
  account_key_hash         TEXT NOT NULL,        -- sha256 of the first writer's Livestorm credential (insert-only; informational)
  session_id               TEXT NOT NULL,
  organization_id          TEXT,                 -- Phase 4: Livestorm org that owns the session
  session_payload          JSONB,                -- raw Livestorm session API response
  event_payload            JSONB,                -- raw Livestorm event API response (parent of the session) — used for card titles
  created_by_user_id       TEXT,                 -- Livestorm user_id of the teammate who first fetched the session (preserve-on-update)
  created_by_email         TEXT,                 -- denormalised email of that user
  created_by_name          TEXT,                 -- denormalised display name of that user
  cover_image_bytes        BYTEA,                -- Phase 4: OpenAI-generated cover PNG bytes
  cover_image_mime         TEXT,                 -- mime type (defaults to image/png)
  cover_image_generated_at TIMESTAMPTZ,          -- when the cover was last generated
  chat_payload             JSONB,                -- raw chat messages payload
  questions_payload        JSONB,                -- raw questions payload
  transcript_payload       JSONB,                -- Gladia transcript JSON (full)
  transcript_speaker_names JSONB,                -- { raw_speaker_id: human_label }
  analysis_md              TEXT,                 -- legacy single-language overall analysis
  analysis_bundle          JSONB,                -- { English: "...", French: "..." }
  deep_analysis_md         TEXT,                 -- legacy single-language deep analysis
  deep_analysis_bundle     JSONB,                -- { English: "...", French: "..." }
  content_repurpose_bundle JSONB,                -- { English: {summary, blog, email, social_media}, French: {...} }
  smart_recap_bundle       JSONB,                -- { professional, hype, surprise }
  created_at               TIMESTAMPTZ DEFAULT NOW(),
  updated_at               TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (account_key_hash, session_id)
)
-- Indexes:
--   idx_session_cache_session_id_unique    UNIQUE (session_id)
--   idx_session_cache_organization_id      (organization_id, updated_at DESC)
```

**`organization_id` (Phase 4)** — populated from the requesting user's OAuth connection on first fetch. All read paths from web routes filter by org_id so teammates inside one Livestorm organization share cached results, but cross-org callers cannot read each other's cache. Worker job reads don't filter (they're trusted internal code; the row's org_id is preserved on subsequent upserts). Legacy rows (pre-Phase-4) have NULL org_id and are invisible to the new Single Analysis listing until refetched — refetching is instant on cache hit and stamps org_id.

### `oauth_connections`

```sql
oauth_connections (
  connection_id    TEXT PRIMARY KEY,             -- secrets.token_urlsafe(24)
  provider         TEXT NOT NULL,                -- 'livestorm'
  user_id          TEXT,                         -- Livestorm /me data.id
  email            TEXT,
  organization_id  TEXT,
  access_token     TEXT NOT NULL,
  refresh_token    TEXT,
  token_type       TEXT DEFAULT 'Bearer',
  scope            TEXT,
  expires_at       TIMESTAMPTZ,
  profile          JSONB,                        -- snapshot of /me payload + parsed fields
  created_at       TIMESTAMPTZ DEFAULT NOW(),
  updated_at       TIMESTAMPTZ DEFAULT NOW()
)
```

### `transcript_jobs`

```sql
transcript_jobs (
  job_id      TEXT PRIMARY KEY,                  -- uuid4 hex
  session_id  TEXT NOT NULL,
  timestamped BOOLEAN DEFAULT TRUE,
  status      TEXT NOT NULL DEFAULT 'pending',   -- 'pending' | 'running' | 'completed' | 'error'
  error       TEXT,
  progress    TEXT,                              -- JSON-encoded progress dict from Gladia poll
  connection_id TEXT,                            -- OAuth connection that started the job (NULL = LS_API_KEY, local dev).
                                                 -- Read and cleared when the worker claims the job; cleared when it ends.
                                                 -- Never returned by any route (it is also the session cookie value).
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
-- Indexes:
--   idx_transcript_jobs_session_id          (session_id, created_at DESC)
--   idx_transcript_jobs_status_updated_at   (status, updated_at)   -- used by the stuck-job sweeper
--   idx_transcript_jobs_one_active_per_session  UNIQUE (session_id) WHERE status IN ('pending','running')
--       at most one active transcription per session; db.create_transcript_job uses
--       INSERT … ON CONFLICT DO NOTHING and attaches to the existing job
```

### `admin_users`

```sql
admin_users (
  email              TEXT PRIMARY KEY,           -- lower-cased; matched against oauth_connections.email
  user_id            TEXT,
  promoted_by_email  TEXT,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
```

Admins come from this table plus the `ADMIN_EMAILS` env var (bootstrap admins, which cannot be demoted through the UI). See §4.

### `system_settings`

```sql
system_settings (
  key        TEXT PRIMARY KEY,                   -- allow-listed in app.py: llm_provider, llm_default_model, llm_smart_recap_model, image_model
  value      TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
```

Admin-editable runtime overrides for provider and model selection (`/api/admin/settings`).

### Schema rules

- Caching is per-session, not per-output. Re-running analysis overwrites the previous bundle for that language.
- No "history of analyses" today — `session_cache` stores only the **latest** generation for each output type.
- `force_refresh: true` in any fetch payload bypasses the cache and refetches from upstream.

---

## 10. API Routes

All routes are FastAPI handlers in [app.py](app.py). Frontend wrapper in [frontend/src/api.js](frontend/src/api.js).

**Auth column:** *public* = no identity needed. *caller* = `Depends(get_caller)`: 401 without an identity, and session routes are scoped to `Caller.read_scope` (§4). *admin* = `Depends(require_admin)`: 401, or 403 for non-admins. `{id}` must match `^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$` (422 otherwise).

| Route | Method | Auth | Purpose |
|---|---|---|---|
| `/api/health` | GET | public | Render health check |
| `/api/bootstrap` | GET | public | Initial frontend state: OAuth status, connected user, `isAdmin`, local-dev flag. Never returns an API key. |
| `/api/auth/livestorm/start` | GET | public | Begin OAuth flow (sets handshake cookie, 302 to Livestorm) |
| `/api/auth/livestorm/callback` | GET | public | OAuth callback: validate handshake, exchange code, fetch /me, set session cookie |
| `/api/auth/logout` | POST | public | Delete connection row, clear session cookie |
| `/api/dev/auto-login` | GET | local-dev only | Seed a dev identity from `LS_API_KEY` (403 unless local-dev mode, §4) |
| `/api/admin/check` | GET | admin | Confirm admin status |
| `/api/admin/users` | GET | admin | List users and admins |
| `/api/admin/users/promote` | POST | admin | Add an email to `admin_users` |
| `/api/admin/users/demote` | POST | admin | Remove an admin (not `ADMIN_EMAILS` admins, not yourself) |
| `/api/admin/sessions` | GET | admin | List cached sessions across orgs |
| `/api/admin/sessions/{id}` | DELETE | admin | Delete a cached session |
| `/api/admin/settings` | GET / POST | admin | Read or update allow-listed `system_settings` keys |
| `/api/workspace-events` | POST | caller | Paginated list of events with title/status filters |
| `/api/workspace-sessions` | GET | public* | (Phase 4) Cached session cards for the caller's own org; `{sessions: []}` without an org |
| `/api/event-sessions` | POST | caller | List sessions inside an event |
| `/api/sessions/{id}/cover.png` | GET | caller | Cover image (`Cache-Control: private`) |
| `/api/sessions/{id}` | GET | caller | Read cached workspace (404 if not cached) |
| `/api/sessions/{id}/cached` | GET | caller | Same, but returns 204 instead of 404 when missing |
| `/api/sessions/{id}/fetch` | POST | caller | Full fetch: session + chat + questions, then start the transcript job (`transcriptJob` in the response until the transcript is cached) |
| `/api/sessions/{id}/fetch-base` | POST | caller | Fetch session payload only |
| `/api/sessions/{id}/fetch-transcript` | POST | caller | Start transcript job, return jobId |
| `/api/sessions/{id}/transcript-job` | GET | caller | Poll transcript job status / progress |
| `/api/sessions/{id}/transcript.json` | GET | caller | Full raw transcript as a download (the workspace only carries subtitles, §5) |
| `/api/sessions/{id}/speaker-labels` | POST | caller | Save edited speaker labels |
| `/api/sessions/{id}/analysis` | POST | caller | Run Overall Analysis (English or French) |
| `/api/sessions/{id}/deep-analysis` | POST | caller | Run Deep Analysis (English or French) |
| `/api/sessions/{id}/analysis-pdf` | GET | caller | Render Overall or Deep PDF (`kind`, `language` query params) |
| `/api/sessions/{id}/smart-recap` | POST | caller | Run Smart Recap for a tone (`professional`, `hype`, `surprise`; 422 otherwise) |
| `/api/sessions/{id}/smart-recap-pdf` | GET | caller | Render Smart Recap PDF |
| `/api/sessions/{id}/content-repurposing` | POST | caller | Run Content Repurposing bundle (4 assets, English or French) |
| `/api/sessions/{id}/content-repurposing-pdf` | GET | caller | Render a single asset PDF |
| `/api/sessions/{id}/{flow}/job` | GET | caller | Poll an AI job: `analysis`, `deep-analysis`, `smart-recap`, `content-repurposing` (§5) |

`/docs`, `/redoc` and `/openapi.json` are served locally only; they are disabled on Render.

Catch-all `GET /{full_path}` serves the Vue SPA from `frontend/dist`. It only serves files that resolve *inside* `frontend/dist` (no `..` escapes) and returns 404 JSON for unknown `/api/*` paths instead of `index.html`. There is no CORS middleware: the SPA is same-origin (Vite proxies `/api` in dev).

---

## 11. Architecture

```
chat-analysis/                      # repo name; product name is StormIQ
├── app.py                          # FastAPI app, route handlers, OAuth wiring, SPA static serving
├── livestorm_app/
│   ├── api_logic.py                # Orchestration: fetch flows, analysis runners, PDF builders
│   ├── auth.py                     # Request identity (Caller), org read scope, local-dev mode — see §4
│   ├── services.py                 # Pure compute: Livestorm API calls, DataFrames, prompts, OpenAI client
│   ├── session_overview.py         # Session payload → people DF, hero stats, charts
│   ├── transcript_client.py        # Thin wrapper around gladia.transcriber
│   ├── gladia/
│   │   ├── transcriber.py          # Gladia v2 pre-recorded transcription with diarization + NER + subtitles
│   │   └── cli.py                  # Standalone CLI for ad-hoc transcription
│   ├── db.py                       # psycopg connection, schema bootstrap, session_cache + oauth + jobs CRUD
│   ├── config.py                   # Constants: URLs, model names, prompt paths, .env loader
│   ├── oauth_client.py             # Livestorm OAuth PKCE flow, token refresh, profile extraction
│   ├── queue.py                    # Redis + arq queue: deterministic job ids, enqueue_unique_job_sync, job status
│   ├── progress.py                 # Redis-backed stage-floor progress reporting (Phase 1)
│   ├── worker.py                   # arq worker entry-point + stuck-job sweeper cron (Phase 1)
│   ├── llm_client.py               # LLM provider abstraction (Anthropic default, OpenAI optional) — §12
│   ├── timing.py                   # Per-request timing log for /api/* (route, status, ms, bytes) — §14
│   └── charts/                     # (empty — directories exist for future per-chart Python modules)
├── docker/
│   └── Dockerfile.worker           # Worker image (no frontend, no HTTP) — runs `arq livestorm_app.worker.WorkerSettings`
├── docker-compose.yml              # Local dev: web + worker + redis + postgres (never the prod DB)
├── .github/workflows/ci.yml        # CI: ruff + pytest (with Postgres), eslint + build, Docker builds
├── requirements.in / requirements.txt          # human-edited ranges / hashed lock (uv pip compile)
├── requirements-dev.in / requirements-dev.txt  # dev tools (pytest, httpx, ruff), locked the same way
├── ruff.toml                       # Python lint rules (lint only, no auto-format)
├── .env.example                    # Every env var the code reads, placeholders only
├── scripts/                        # One-off backfills and diagnostics (run manually, read the docstring first);
│                                   #   seed_benchmark_sessions.py + benchmark_routes.py: local performance benchmark (§14)
├── Dockerfile                      # Web image (FastAPI + built Vue frontend)
├── tests/                          # pytest suite (requirements-dev.txt); access-control route matrix
├── prompts/                        # 13 .txt files — editable without code changes
│   ├── analysis_base_prompt.txt
│   ├── analysis_chat_prompt.txt
│   ├── analysis_questions_prompt.txt
│   ├── analysis_transcript_prompt.txt
│   ├── analysis_all_sources_prompt.txt
│   ├── analysis_deep_prompt.txt
│   ├── content_repurpose_summary_prompt.txt
│   ├── content_repurpose_email_prompt.txt
│   ├── content_repurpose_blog_prompt.txt
│   ├── content_repurpose_social_media_prompt.txt
│   ├── smart_recap_professional_prompt.txt
│   ├── smart_recap_hype_prompt.txt
│   └── smart_recap_surprise_prompt.txt
├── frontend/
│   └── src/
│       ├── App.vue                 # Sidebar nav, top bar, route shell
│       ├── api.js                  # Typed fetch wrappers for /api/*
│       ├── router.js               # vue-router routes
│       ├── store/workspace.js      # Single reactive store (no Pinia); state, computed, actions
│       ├── views/                  # One file per route
│       └── components/
│           ├── charts/
│           │   ├── shared/         # BarChartCard, ColumnChartCard, PieChartCard
│           │   ├── transcript/     # 8 chart cards + speakerColors.js
│           │   ├── chat-questions/ # ActivityTimeline, ContributorsComparison
│           │   └── analysis/       # ContentPaceAudienceActivityChartCard
│           ├── DataTable.vue       # Generic sortable table with CSV download
│           ├── FetchSessionForm.vue
│           ├── KeyMomentsTimeline.vue
│           ├── MarkdownCard.vue
│           └── RichMarkdownCard.vue
└── assets/icons/                   # Brand assets served at /brand-assets
```

The frontend is built by Vite to `frontend/dist/` and served by FastAPI as static files in production. In dev, `npm run dev` runs Vite with `/api` and `/brand-assets` proxied to `uvicorn`.

---

## 12. Providers

Text generation goes through the provider abstraction in [llm_client.py](livestorm_app/llm_client.py); Livestorm, Gladia and the OpenAI Images API are called directly:

| Service | Used for | File | Env var |
|---|---|---|---|
| **Livestorm REST API** | session payload, chat, questions, events list | [services.py](livestorm_app/services.py) | OAuth token or `LS_API_KEY` |
| **Gladia v2 pre-recorded** | audio transcription with diarization + NER + sentences + subtitles | [gladia/transcriber.py](livestorm_app/gladia/transcriber.py) | `GLADIA_KEY` |
| **Anthropic Claude** *(default)* | overall, deep, recap, content generation, translation | [llm_client.py](livestorm_app/llm_client.py) via `services.py` | `CLAUDE_API_KEY` |
| **OpenAI Chat Completions** *(optional)* | same flows — active when `LLM_PROVIDER=openai` | [llm_client.py](livestorm_app/llm_client.py) | `OPENAI_API_KEY` |

### Provider selection and model selection

The active text-generation provider is selected via the `LLM_PROVIDER` env var (defaults to `"anthropic"`). Switching providers requires only a redeploy with the matching key — no code change. Cover image generation always uses OpenAI Images API regardless of `LLM_PROVIDER`.

The provider abstraction lives in [llm_client.py](livestorm_app/llm_client.py). Model names are configured in [config.py](livestorm_app/config.py):

```python
# Anthropic (LLM_PROVIDER=anthropic, the default)
DEFAULT_CLAUDE_MODEL       = "claude-haiku-4-5-20251001"  # overall, deep, content, translation
SMART_RECAP_CLAUDE_MODEL   = "claude-sonnet-4-6"          # smart recap — stronger intentionally

# OpenAI (LLM_PROVIDER=openai)
DEFAULT_OPENAI_MODEL       = "gpt-4o-mini"
DEEP_ANALYSIS_OPENAI_MODEL = "gpt-4o-mini"
SMART_RECAP_OPENAI_MODEL   = "gpt-5.4-mini"
```

Smart Recap uses a stronger model than the other flows regardless of provider.

**Truncation:** `call_llm` checks why the model stopped (`stop_reason == "max_tokens"` on Anthropic, `finish_reason == "length"` on OpenAI). A truncated answer is retried once with double the token budget (capped at `TRUNCATION_RETRY_MAX_TOKENS`, 16,000); if it is still cut off, `LLMOutputTruncated` is raised and nothing is saved.

### Retries and timeouts

Every outbound call has a timeout. Retries are limited to failures that almost always mean nothing was produced or billed (a gateway 502/504 or a dropped connection can occasionally hide a billed LLM answer; that rare double charge is accepted over failing the job):

| Call | Timeout | Retried on | Not retried |
|---|---|---|---|
| LLM (`llm_client._post_with_retry`) | 10 s connect, 300 s read | 429, 500, 502, 503, 504, 529 and connection errors; up to 4 attempts, exponential backoff with jitter, `Retry-After` honoured (capped at 30 s) | read timeouts (the model may still be generating and billing that answer), other 4xx |
| Livestorm API (`services.livestorm_get`, all GETs) | 10 s connect, 30 s read | 429, 5xx and connection errors; 3 retries, backoff 0/1/2 s, `Retry-After` capped at 10 s | other 4xx |
| Gladia JSON calls (`transcriber._urlopen_json`) | 60 s socket | status polls ride out up to 4 consecutive timeouts / 429 / 5xx; the recordings GET is retried 3 times | starting a transcription (a POST, so a retry could pay twice); 4xx |
| Gladia upload / recording download | 120 s / 60 s socket | — | — |
| OpenAI Images (cover) | 180 s | — | — |

The Livestorm HTTP session is shared by every user's requests, so it keeps no cookies.

### Gladia configuration

`DEFAULT_GLADIA_OPTIONS` in [gladia/transcriber.py](livestorm_app/gladia/transcriber.py):

- diarization on, with `min_speakers=1, max_speakers=20`
- named-entity recognition on
- sentence segmentation on
- subtitles on, formats `["srt", "vtt"]`

Audio chunk hard cap: 135 minutes per request (Gladia standard plan).

---

## 13. Environment Variables

```bash
# LLM provider (text generation)
LLM_PROVIDER                  # Optional; "anthropic" (default) or "openai"
CLAUDE_API_KEY                # Required when LLM_PROVIDER=anthropic (or unset)
OPENAI_API_KEY                # Required when LLM_PROVIDER=openai; always required for cover image generation

# Livestorm
LS_API_KEY                    # Optional; used only when STORMIQ_LOCAL_DEV=1 (see §4). Never sent to the browser.
STORMIQ_LOCAL_DEV             # Optional; "1" enables local-dev mode (§4). Ignored on Render (RENDER is set there).
ADMIN_EMAILS                  # Optional; comma-separated bootstrap admin emails (see §4, §9 admin_users)
LIVESTORM_OAUTH_CLIENT_ID     # Required for OAuth path
LIVESTORM_OAUTH_CLIENT_SECRET # Required for OAuth path
LIVESTORM_OAUTH_REDIRECT_URI  # Required for OAuth path
LIVESTORM_OAUTH_SCOPES        # Optional; default 'identity:read events:read'
FRONTEND_APP_URL              # Optional; used to build post-OAuth redirect URLs
SESSION_SECRET                # Recommended; HMAC key for the OAuth handshake cookie. Falls back to
                              # LIVESTORM_OAUTH_CLIENT_SECRET; there is no hard-coded fallback.

# Transcription
GLADIA_KEY                    # Required on the worker to transcribe (read from the worker's env, never passed through the queue)

# Database
DATABASE_URL                  # Required. Also accepts POSTGRES_URL or RENDER_POSTGRES_URL

# Queue / worker (Phase 1)
REDIS_URL                     # Required in prod; defaults to redis://localhost:6379/0 for local dev

# Render
# Cover images (OpenAI Images API)
IMAGE_MODEL                   # Optional; default in config.py (gpt-image-1)
IMAGE_SIZE                    # Optional; default 1536x1024
IMAGE_QUALITY                 # Optional; default high

# Tests only
TEST_DATABASE_URL             # Optional; enables real-Postgres tests. Must be a local host (conftest refuses others)
TEST_REDIS_URL                # Optional; enables real-Redis queue tests. Local host and a dedicated db index (e.g. /15): the fixture flushes it

PORT                          # Set by Render; defaults to 10000
```

The backend reads `.env` at startup via [`load_env_file()`](livestorm_app/config.py#L44) — no need for `python-dotenv` autoload.

---

## 14. Deployment Model

**Three services + one external DB**, configured in [`render.yaml`](render.yaml):

1. **web** — FastAPI + built Vue frontend. [Dockerfile](Dockerfile) at repo root. Runs `uvicorn app:app --host 0.0.0.0 --port $PORT`.
2. **worker** — arq worker process. [docker/Dockerfile.worker](docker/Dockerfile.worker). Runs `arq livestorm_app.worker.WorkerSettings`.
3. **redis** — Render Key Value (managed Redis). Connection string is wired into web + worker via `fromService`.
4. **Postgres** — Render-managed Postgres add-on, URL injected as `DATABASE_URL`.

`REDIS_URL` is shared by both web and worker. The web process uses the sync `redis` client to **read** progress; the worker uses `redis.asyncio` to **write** progress and consume the queue. Same Redis instance, different access pattern.

The web Dockerfile is a two-stage build: a Node stage builds the frontend, and the Python stage installs the hashed lock (`requirements.txt`) and copies the app in. The worker Dockerfile is single-stage and ships **without** the frontend (it doesn't serve HTTP). Both images run as an unprivileged `stormiq` user and need no compiler toolchain (every dependency ships wheels).

**Base images are pinned to exact versions**: `node:26.13.0-bookworm-slim` (frontend build only; Node never runs in production) and `python:3.13.18-slim-trixie` (web and worker runtime). Floating tags let a cached, unpatched base survive rebuilds. The Node version lives in `frontend/.nvmrc` (CI reads it) and must match the Dockerfile; `engines.node` in `frontend/package.json` sets the patched minimum (`>=26.12.1`, Node.js October 2026 security release). Bump all three together.

**Health check**: `GET /api/health` returns `{"status": "ok"}`. `render.yaml` sets it as the web service's `healthCheckPath` (Render waits for it before routing to a new deploy), and the web image has a matching Docker `HEALTHCHECK`. Worker has no HTTP health check — Render's process-running signal is the liveness check.

**Worker environment**: the worker resolves every credential itself, because job arguments (stored in Redis) never carry secrets. It needs `DATABASE_URL`, `REDIS_URL`, `GLADIA_KEY`, the LLM key (`CLAUDE_API_KEY` or `OPENAI_API_KEY`), `OPENAI_API_KEY` for covers, and `LIVESTORM_OAUTH_CLIENT_ID` / `LIVESTORM_OAUTH_CLIENT_SECRET` to refresh a user's Livestorm token when a queued transcription starts close to its expiry. It logs a warning at startup when `GLADIA_KEY` is missing. Each worker runs at most `MAX_CONCURRENT_TRANSCRIPTIONS` (2) transcriptions at once; more wait for a slot.

**Connections**: each process (web, worker) keeps one Postgres connection pool (`psycopg_pool`, up to `DB_POOL_MAX_SIZE` = 10 connections, checked before use, recycled hourly) behind `db.get_db_connection()`, so web and worker together hold at most 20 connections: keep that under the Render Postgres plan's limit. The web process also keeps one Redis connection pool on a background event-loop thread (`queue._QueueBridge`) for enqueues and job-status reads from sync handlers. When Redis is unreachable the bridge makes one connection attempt, then fails every call immediately for `REDIS_DOWN_COOLDOWN_SECONDS` (5 s): polls report `unknown` and job starts report "queue unavailable" instead of tying up the web server's threads. Both pools are closed on shutdown.

**Caching headers**: hashed files under `/assets` are served with `Cache-Control: public, max-age=31536000, immutable`; `index.html` is `no-store`. Render's edge (Cloudflare) already brotli-compresses responses, so the app adds no compression middleware.

**Request timing**: every `/api/*` request (except `/api/health`) logs one line, `timing method=GET route=/api/sessions/{session_id} status=200 ms=84.2 bytes=1834402`, with the route template rather than ids. Search Render's logs for `timing route=` to see per-endpoint latency and response sizes.

**Benchmark**: `scripts/seed_benchmark_sessions.py` fills a *local* database with synthetic sessions (10-minute, 2-hour, a running transcription, 40 cards); `scripts/benchmark_routes.py` times the heaviest routes in-process and prints a table (first-request and median ms, response size, Postgres connections opened). Both refuse non-local databases. Use them before and after a performance change.

**Local development never uses the production database.** `docker-compose.yml` provides a local Postgres (loopback port 5433) and Redis; copy `.env.example` to `.env` to start.

**Running locally with Docker Compose** (whole stack):

```bash
docker compose up --build
```

This boots web (`:10000`), worker, redis (`:6379`) and postgres (`:5433`), all on loopback. web and worker always use the compose Postgres, whatever `.env` says; credentials come from `.env`.

**Running locally without Docker for the app** (data services still in Docker):

```bash
# 1. Data services
docker compose up -d postgres redis
# .env: DATABASE_URL=postgresql://stormiq:stormiq@localhost:5433/stormiq
#       REDIS_URL=redis://localhost:6379/0
#       STORMIQ_LOCAL_DEV=1 + LS_API_KEY=... to skip OAuth locally (§4)

# 2. Backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app:app --reload                            # in one terminal
arq livestorm_app.worker.WorkerSettings             # in another terminal

# 3. Frontend
cd frontend && npm install && npm run dev           # in a third terminal
```

**Tests, lint and dependency updates** (CI runs the same on every PR, see `.github/workflows/ci.yml`):

```bash
ruff check .
TEST_DATABASE_URL=postgresql://stormiq:stormiq@localhost:5433/stormiq \
TEST_REDIS_URL=redis://localhost:6379/15 pytest   # omit either var to skip those tests
cd frontend && npm run lint && npm run build

# Change Python dependencies: edit requirements.in (or requirements-dev.in), then re-run the
# `uv pip compile ...` command in the header of requirements.txt (or requirements-dev.txt).
```

**Local Docker test of just the web image**:

```bash
docker build -t stormiq .
docker run --rm -p 10000:10000 --env-file .env stormiq
```

---

## 15. Key Business Rules

1. **Cache first.** Every fetch route checks `session_cache` before calling Livestorm/Gladia. `force_refresh: true` is the only bypass.
2. **Account key hashing.** Never store raw Livestorm API keys in `session_cache`. `account_key_hash` is `sha256` of the first writer's Livestorm credential, set on insert and never updated. It is informational: `organization_id` is the access boundary (§4). Rows written before November 2026 may carry the hash of an LLM provider key instead; that can't be repaired and doesn't matter for access.
3. **OAuth tokens are refreshed proactively.** When a connection is within 5 minutes of expiry, the next request refreshes the access token via `refresh_token`. Failures surface as 401 with "Please reconnect."
4. **Transcript is the gating dependency.** Analysis, Smart Recap, and Content Repurposing all require `transcript_payload` to be present. Views display either a loading state (job in progress) or an unavailable-reason banner.
5. **Bilingual analysis re-uses existing language.** If a bundle already contains the target language, no new OpenAI call is made; the cached markdown is returned. Switching to a missing language generates only that language.
6. **All AI outputs are PDF-exportable.** Every persistent AI artefact (Overall, Deep, Recap, Content Repurposing assets) has a corresponding PDF route built via reportlab in [services.py](livestorm_app/services.py).
7. **Prompts are editable without code.** All AI prompts live in `prompts/*.txt`. Operators can tune them. Changing wording does not require a deploy if the file is mounted/overridden.
8. **Brand-adjacent, not brand-impersonating.** App is called **StormIQ**. Never replace the app logo with the Livestorm logo. See [BRAND.md](BRAND.md).
9. **No telemetry beyond stderr logging.** No analytics, no error-reporting service, no per-user usage tracking today.
10. **Access fails closed.** Every session, event and admin route takes its identity from `livestorm_app.auth` (§4). An unknown identity or a blank org never widens access: it gets 401, or matches no rows. New routes must use `Depends(get_caller)` / `Depends(require_admin)`, and `tests/test_access_control.py` must keep passing.

---

## 16. Branching Convention

We work in phased branches, each cleanly mergeable. CLAUDE.md is updated in the **same PR** as the change.

| Phase | Branch | Scope |
|---|---|---|
| 1 | `feature/worker-redis-infra` | Add Redis + worker queue, migrate analysis flows to background jobs, progress reporting. No user-facing feature change. |
| 2 | `feature/card-registry` | Refactor Deep Analysis from markdown-blob + regex parser to a card registry (det + LLM split). Replace `parsedDeepSections` regex with card-driven renderer. |
| 3 | `feature/cross-analysis` | Comparative (2 sessions) + Cumulative (up to 10 sessions). New `cross_analyses` table, new API routes, new views. Depends on Phase 1 (worker) and Phase 2 (cards). |
| 4 | `feature/ui-polish` | Better UI per the card system: per-tab grids, per-user card preferences, empty-state cards instead of "Generate" buttons. |

Each phase is independently shippable. PRs into `main` only after the phase is feature-complete and CLAUDE.md/doc.md are updated to match.

**CI must pass** (ruff, pytest including the access-control route matrix, eslint, frontend build, Docker builds) before merging.

**Update rule**: every PR that changes business logic, schema, routes, providers, env vars, or deployment must include a diff to CLAUDE.md in the same commit. PRs that change user-visible behaviour must also update doc.md.

---

## 17. Roadmap

### Hardening track (in progress)
Security, data-integrity, reliability and performance fixes from the November 2026 review, tracked as six PRs in a maintainer-local plan (`docs/improvement-plan.md`, deliberately not committed: this repository is public and the plan lists issues that are not fixed yet). The design overhaul follows it.

- ✅ **PR 1 `fix/security-org-scoping`**: fail-closed access control (§4), `livestorm_app/auth.py`, path-traversal fix in the SPA route, local-dev mode behind `STORMIQ_LOCAL_DEV` instead of the Host header, Secure cookies, CORS removed, handshake expiry enforced, upstream error details no longer returned, route-matrix test suite.
- ✅ **PR 2 `chore/safety-net`**: local Postgres in compose and `.env.example`; hashed dependency locks (uv); ruff and ESLint, with the issues they found fixed; real-Postgres and pure-function tests; CI; slimmer non-root images with health checks; Node 24 build stage; docs drift fixed.
- ✅ **PR 3 `fix/data-integrity`**: atomic per-entry bundle writes (`merge_cached_bundle_entry`); `account_key_hash` insert-only; database write failures raise instead of being swallowed; empty, truncated or incomplete AI output fails the job instead of saving partial results; transcription heartbeat for the stuck-job sweeper.
- ✅ **PR 4 `fix/job-reliability-cost`**: deterministic arq job ids replace the Redis in-flight marker, and a failed job can be retried at once; reading a session never starts a paid cover image (and the post-recap cover trigger works again); one active transcription per session (partial unique index), with dead queue jobs detected instead of blocking for an hour; `/fetch` no longer transcribes inside the web request; retries with backoff and timeouts on every LLM, Livestorm and Gladia call; per-job timeouts, a two-transcription concurrency cap, and cancellation handling; no credentials in job arguments (the worker reads the Gladia key from its env and the user's connection from the job row); language and tone validated before any paid job; AI job polls no longer trust a client-supplied job id.
- ✅ **PR 5 `perf/backend`**: a 2-hour session opens in ~0.1 s instead of ~4.5 s (an O(mentions × sentences) entity lookup replaced, transcript insights built once per read and cached per session version); the workspace response is ~3.5× smaller (raw payloads dropped, raw transcript moved to `transcript.json`); Postgres connection pool and one long-lived Redis pool (no new connection per request); polls and the card list read only the columns they need; system settings cached for a minute; immutable caching for hashed assets; per-request timing log; local benchmark scripts (§14).

### Shipped (current state)
- Livestorm OAuth (PKCE) + API-key fallback for local dev
- Workspace event browsing with title/status filters
- Session fetch with full payload + chat + questions caching
- Gladia transcription via threaded background job + DB-backed progress polling
- Session Overview view: hero metrics, people, country/role/attendance charts, engagement-score ranking
- Transcript view: 8 tabs (transcript, pace, airtime, NER, words, segments, silence, utterance)
- Chat & Questions view: 5 tabs (chat, questions, contributors, activity, response coverage)
- Overall Analysis (5-section markdown), bilingual EN/FR
- Deep Analysis (10-section markdown with 0–100 scores), bilingual EN/FR
- Smart Recap in 3 tones
- Content Repurposing bundle (summary, blog, email, social) in EN/FR
- PDF export for every AI output via reportlab
- CSV export on every data table
- Editable speaker labels, persisted in `session_cache`

### In progress — Phase 1: Worker + Redis infrastructure
Branch: `feature/worker-redis-infra`.

**Scaffolding (shipped on the branch — first commit, no flow migrations yet):**
- ✅ Redis service in `docker-compose.yml` and `render.yaml`
- ✅ arq worker process + `Dockerfile.worker`
- ✅ `livestorm_app/queue.py` — Redis connection + arq pool + enqueue helper
- ✅ `livestorm_app/progress.py` — stage-floor progress reporting (sync read for web, async write for worker)
- ✅ `livestorm_app/worker.py` — `WorkerSettings` + stuck-job sweeper cron (every 10 min)
- ✅ `CREATE TABLE IF NOT EXISTS transcript_jobs` + indexes in `ensure_database_schema`
- ✅ `REDIS_URL` env var documented

**Phase 2 — Migrate flows onto the queue** (branch `feature/queue-flows`):

- ✅ **Commit 1**: Gladia transcription migrated from `threading.Thread` to arq job. Stage-floor progress in Redis runs in parallel with the legacy DB progress column. App restarts no longer kill in-flight transcriptions. See §5 for the new flow.
- ✅ **Commit 2** (backend): all four AI flows (overall, deep, recap, repurposing) migrated to arq jobs. New polling routes `GET /api/sessions/{id}/{flow}/job`. POST routes return immediately with a job id; cache hits short-circuit to the full workspace. See §5 for the new contract.
- ✅ **Commit 3** (frontend): store rewritten to poll AI jobs via `pollAiJob` + `state.aiJobs[kind]`. New `<AiJobProgress>` component renders a stage-floor bar with EN/FR labels on AnalysisView, SmartRecapView, ContentRepurposingView. Cache hits short-circuit polling — the workspace is applied immediately. `runAnalysis` / `runDeepAnalysis` / `runSmartRecap` / `runContentRepurposing` keep the same public shape so views need no behaviour changes beyond rendering the new progress bar.

**Phase 2 cleanup** (branch `feature/phase-2-cleanup`):

- ✅ **Server-side AI-job dedupe**: rapid double-clicks on Generate no longer enqueue duplicate jobs. (Originally a Redis in-flight marker; since hardening PR 4, a deterministic arq job id — see §5.)
- ✅ **Transcript view → progressRedis**: transcript polling now populates `state.aiJobs.transcript` from the `progressRedis` field on each poll response. All four transcript-loading panels (TranscriptView + AnalysisView + SmartRecapView + ContentRepurposingView) render `<AiJobProgress flow="transcript">` instead of just the legacy Gladia step message. Stage labels (fetching_recording / uploading_to_gladia / transcribing / post_processing / persisting / done) added to `AiJobProgress` in EN and FR.

### Phase 4 — Workspace UX + shareable session URLs (branch `feature/phase-4-workspace-ui`)

The product shape that replaces the single-active-session flow with a workspace-aware list view and shareable per-session URLs.

- ✅ **Commit 1 (backend)**: `session_cache.organization_id` column + index. All web read paths filter by org_id; teammates in one Livestorm org share cached results (this was already true in practice; the lookup just wasn't filtered) while cross-org callers can no longer read each other's cache. New `GET /api/workspace-sessions` returns the card list for the current user's org. Worker reads remain org-agnostic (trusted internal code). Legacy rows pre-Phase-4 have NULL `organization_id` and are invisible to the new list view until refetched — refetching is instant on cache hit and stamps the org_id.
- ✅ **Commit 2 (frontend)**: sidebar restructured to three top-level items (Search / Single Analysis / Cross Analysis). New `SingleAnalysisListView` renders a card grid with placeholder gradient covers (final cover-image logic TBD by product). New `SearchView` consolidates the three fetch modes (by session ID, by event ID, browse workspace) in one redesigned page. New `CrossAnalysisView` placeholder for Phase 3. Routes parameterised by `:sessionId` so any `/single-analysis/:sessionId/...` URL is shareable; the store's `loadSessionById` watches the route param and materialises the workspace from cache (instant) or fetches it (when a teammate hasn't loaded the session in this browser yet). Legacy routes redirect to the new equivalents.
- Orphaned components removed: `FetchSessionForm.vue` and `EventsView.vue` (their logic moved into `SearchView`).
- ✅ **Event title on cards**: added `session_cache.event_payload` (JSONB) so the card list can show the parent event's title (Livestorm sessions rarely have their own `name` set). `services.fetch_event_details` does the lookup; `fetch_session_base_data` and `fetch_all_session_data` call it alongside the session fetch and store both. `list_workspace_sessions_data` exposes `eventTitle` and a formatted `durationLabel` (e.g. `1h 30m`). Card shows event title, date, duration, and attendee count. Backfill in `scripts/backfill_event_payloads.py` — fetches each missing event once (deduped by event_id), writes to all sessions that reference it.
- ✅ **"Generated by" attribution + sidebar layout fix**: `session_cache` got `created_by_user_id` / `created_by_email` / `created_by_name`. The upsert preserves these on update via `COALESCE(NULLIF(col, ''), EXCLUDED.col)` so the first writer's identity sticks even when a different teammate later refetches the same session. `app._resolve_generator_kwargs(request)` resolves the three fields from the OAuth connection; `api_logic._generator_kwargs` filters out blanks. Card body now reads "Generated by <name> · <date>" (replacing the five status pills) and falls back to "Unknown user" for pre-Phase-4 rows. Sidebar layout: auth + beta-notice wrapped in `.sidebar-footer` with `margin-top: auto`, so they sit tight together at the bottom instead of the auth block floating in the middle.
- ✅ **Workspace filters / search / sort / view modes**: `SingleAnalysisListView` got a controls row with search (across event title + session name + generator), filter (by event with session counts), sort (newest/oldest by generation date or session date; event A→Z/Z→A; generator A→Z), and a Grid ↔ List view-mode toggle persisted in `localStorage` (`stormiq:single-analysis:viewMode`). Filtering and sorting are client-side over the existing workspace-sessions response — one fetch on mount, instant interactions.
- ✅ **AI cover images**: each cached session gets a 16:9 cover rendered by the OpenAI Images API (`gpt-image-1`, 1536×1024, high quality, model + size + quality configurable via `livestorm_app/config.py`). Pipeline: Professional Smart Recap → templated prompt (`prompts/cover_image_prompt.txt`, no logos / faces / text overlays) → one Images call → PNG bytes persisted as `session_cache.cover_image_bytes`. New `GET /api/sessions/{id}/cover.png` route streams the bytes (org-scoped, `Cache-Control: max-age=86400`). Worker auto-enqueues `run_cover_image_job` after every successful Professional recap; failures are logged and swallowed so a broken image generator never breaks recap or transcription. Card view uses the real cover when `hasCoverImage`, falls back to the hash-based gradient + initials otherwise. Backfill in `scripts/backfill_cover_images.py` (~$0.17 per cover at high quality).

### Then — Phase 2: Card registry refactor
- Introduce a Python-side card registry under `livestorm_app/cards/single/`
- Each card declares: `id`, `tab`, `order`, `title`, optional LLM prompt fragment, `build()`, `View` component reference
- One LLM call assembles all narrative paragraphs as structured JSON; deterministic data lives in `build()` from existing DataFrames
- Deep Analysis sections that should become deterministic-first cards:
  - **Session Scores** — derive from `engagement_df` aggregates, drop LLM
  - **Key Moments** — already deterministic in `key_moments_df`; LLM only writes one-liner
  - **Speaker Dynamics** — chart row from `speaker_df` + `interruptions_df`
  - **Friction Signals** — chart from `low_energy_df`
  - **Business Signals** — table from `numbers_df`
- Replace the regex section parser in [AnalysisView.vue:172](frontend/src/views/AnalysisView.vue#L172) with card-driven rendering
- Provider abstraction (LLM + transcription behind interfaces) — sets up for later DB-driven model selection

### Then — Phase 3: Cross-session analysis
- New `cross_analyses` table:
  ```sql
  cross_analyses (
    id              TEXT PRIMARY KEY,            -- uuid
    user_id         TEXT,                        -- oauth_connections.user_id, nullable
    mode            TEXT,                        -- 'comparative' | 'cumulative'
    session_ids     JSONB,                       -- ["sess_a", "sess_b", ...]
    session_count   INT,
    status          TEXT,                        -- 'pending' | 'processing' | 'done' | 'error'
    result          JSONB,                       -- card-keyed result map
    context_strategy TEXT,                       -- 'full' | 'trimmed' | 'summary-only'
    error_message   TEXT,
    created_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ
  )
  ```
- New routes:
  - `POST /api/cross-analysis/comparative { sessionIdA, sessionIdB, outputLanguage }`
  - `POST /api/cross-analysis/cumulative { sessionIds[], outputLanguage }`
  - `GET /api/cross-analysis/{id}` — poll status + return result
  - `GET /api/cross-analysis` — list past cross-analyses
- `CrossSessionSlice` shape (Python equivalent of Crowdlens `CrossVideoSlice`):
  ```python
  @dataclass
  class CrossSessionSlice:
      session_meta: dict
      transcript_payload: dict
      transcript_insights: dict
      chat_df: pd.DataFrame
      questions_df: pd.DataFrame
      speaker_names: dict[str, str]
      prior_overall_analysis: str | None
      prior_deep_analysis: str | None
  ```
- Loader `load_cross_session_sources(session_ids)` raises `PriorAnalysisMissingError([missing_ids])` if any session is unanalyzed — never deducts work or queues a job without all prerequisites.
- Cumulative initial cap: **10 sessions** (matches Crowdlens Pro tier). Raise to 20 once context-strategy switching is tuned.
- Suggested initial card sets:
  - **Comparative**: Engagement Head-to-Head, Pacing Overlay, Audience Question Overlap, Speaker Dynamics Comparison, Friction Comparison, Cross-Pollination Recommendations
  - **Cumulative**: Audience DNA, Engagement Trend, Speaker Drift, Topic Territory Map, Unanswered Questions Backlog, Strategic Read, Next Session Brief
- Bilingual EN/FR like single-session analyses
- PDF export for both modes

### Then — Phase 4: UI polish
- Per-tab grid layout on every analysis surface (mirror Crowdlens `main / content / audience / action`)
- Header icons per card; optional info-popover on each card
- Per-user card preferences (hide/reorder) stored in a new `user_card_preferences` table keyed by `oauth_connections.user_id`
- Empty-state cards inline (don't hide whole sections behind "Generate" buttons)
- Audit current views against [BRAND.md](BRAND.md) typography + colour rules

### Maybe later (no commitment)
- Provider abstraction with DB-backed model selection (`system_settings`-style)
- API-key rotation with failover (Crowdlens `provider_api_keys` pattern)
- Per-account telemetry / observability dashboard
- Webhook trigger to auto-analyze when a Livestorm session ends
- White-label PDF export
