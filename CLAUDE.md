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

This project has **no Stripe integration, no plans, no top-ups, no quotas, no rate limits**. Every Livestorm workspace that connects can run unlimited analyses. AI costs are absorbed by the operator's OpenAI key.

If a billing model is introduced later, document it here.

---

## 4. Authentication

Two paths, resolved in this order by [`_resolve_livestorm_auth`](app.py):

1. **Inline API key** in the request body (`apiKey` field). Power-user path, mostly used by automation/admin flows.
2. **Livestorm OAuth** session cookie (`livestorm_oauth_connection`). Standard path for end users. PKCE flow with HMAC-signed handshake cookie. Access tokens are refreshed automatically when within 5 minutes of expiry. See [oauth_client.py](livestorm_app/oauth_client.py).
3. **Local API-key fallback** — only enabled when:
   - the request comes from `127.0.0.1`/`localhost`/`::1`, AND
   - `LS_API_KEY` is set in the server environment.

   Used for local dev so the operator doesn't need to OAuth on every restart.

Server-side secrets used by the backend (never sent to the client): `OPENAI_API_KEY`, `GLADIA_KEY`, OAuth client credentials, Postgres URL. See §13.

---

## 5. Processing Flow

### Session fetch (synchronous + threaded job)

```
User selects a session
→ POST /api/sessions/{id}/fetch  (full data)
   or /fetch-base   (just session payload)
   or /fetch-transcript (just transcript)

Backend:
  1. Check session_cache.session_payload → return cached if present (and not force_refresh)
  2. Fetch session payload, chat, questions from Livestorm API
  3. Upsert into session_cache
  4. For transcript: start a *threaded* background job (see below) — returns
     immediately with { jobId, jobStatus: "pending" }
  5. Frontend polls GET /api/sessions/{id}/transcript-job every 6s
  6. When job completes: return full workspace payload
```

### Transcript transcription (queue-backed)

```
POST /api/sessions/{id}/fetch-transcript
→ fetch_session_transcript_data:
   1. Return cached workspace if transcript_payload is already present.
   2. Re-attach to an in-flight job (transcript_jobs.status in 'pending'/'running')
      to dedupe concurrent fetches for the same session.
   3. Otherwise: insert transcript_jobs row, enqueue `run_transcription` arq job,
      return { jobId, jobStatus: 'pending' } immediately.

Worker (livestorm_app.worker.run_transcription):
   1. Mark transcript_jobs.status = 'running'.
   2. Publish stage='fetching_recording' to Redis.
   3. asyncio.to_thread(fetch_session_transcript, ...) — Gladia download +
      upload + poll. The Gladia step→stage mapping inside on_progress
      writes to both transcript_jobs.progress (legacy DB) and Redis
      stage-floor progress.
   4. Publish stage='persisting', upsert session_cache.transcript_payload.
   5. Mark transcript_jobs.status = 'completed', publish stage='done', clear
      Redis progress after a 2s hold so slow pollers still catch 100%.
   6. On any exception: status='error', error_message persisted, Redis cleared.
      arq retries are disabled for this job (max_tries=1) — failures are
      mostly deterministic and re-runs cost Gladia money.

Frontend polls GET /api/sessions/{id}/transcript-job every 6s. The response
carries both `progress` (legacy DB shape — raw Gladia step payload) and
`progressRedis` (new stage-floor shape with percent). Phase 2 commit 3
wires the frontend to prefer `progressRedis`.
```

**Migration status (Phase 2 commit 1):** the legacy `threading.Thread` path has been deleted. The arq worker is now the **only** transcription path. App restarts no longer drop in-flight jobs — they survive in Redis, and the stuck-job sweeper (every 10 minutes, see [worker.py](livestorm_app/worker.py)) marks any genuinely stalled `transcript_jobs` row as `error` so the UI can surface it.

**Auto-enqueue Professional Smart Recap (Phase 4 follow-up):** when `run_transcription` finishes successfully, it now reads the cache and — if `smart_recap_bundle.professional` is empty — enqueues `run_smart_recap_job(session_id, 'professional')` automatically. Every newly transcribed session ends up with a Professional recap in the cache without the user clicking Generate. The recap is the input for the planned card-cover-image generator. Failures to enqueue are logged and swallowed so a Redis hiccup never rolls back a successful transcription.

### AI generation (queue-backed — overall / deep / smart recap / content repurposing)

```
POST /api/sessions/{id}/analysis            -> enqueue overall analysis job
POST /api/sessions/{id}/deep-analysis       -> enqueue deep analysis job
POST /api/sessions/{id}/smart-recap         -> enqueue smart recap job
POST /api/sessions/{id}/content-repurposing -> enqueue content repurposing job

Each POST response is one of:
  - the full serialised workspace (cache hit — the requested bundle is
    already present for the requested language/tone), OR
  - { jobId, jobKind, jobStatus: 'pending', language|tone } (job enqueued).

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
Each reads OPENAI_API_KEY from its own env, publishes stage-floor progress
to Redis ('queued' → 'loading_sources' → 'building_prompt' → 'analyzing'
→ 'persisting' → 'done'), and wraps the existing sync runner in
asyncio.to_thread so the worker event loop stays responsive.

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
- `force_refresh: true` in the fetch payload bypasses the cache and re-fetches from Livestorm/Gladia.

---

## 6. Analysis output schemas

### Overall Analysis (`/api/sessions/{id}/analysis`)

Markdown blob with 5 sections (see [prompts/analysis_base_prompt.txt](prompts/analysis_base_prompt.txt)):

1. Executive Summary
2. Key Themes
3. Engagement Insights
4. Risks / Friction Signals
5. Actionable Recommendations

Persisted as `analysis_bundle: { English: "...", French: "..." }`. Switching language reuses any already-generated language; only the missing language triggers a fresh OpenAI call.

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

Persisted as `deep_analysis_bundle: { English: "...", French: "..." }`. Frontend chops sections via a regex parser ([AnalysisView.vue:172](frontend/src/views/AnalysisView.vue#L172)) with bilingual heading aliases.

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
  account_key_hash         TEXT NOT NULL,        -- sha256 of the Livestorm API key
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
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
-- Indexes:
--   idx_transcript_jobs_session_id          (session_id, created_at DESC)
--   idx_transcript_jobs_status_updated_at   (status, updated_at)   -- used by the stuck-job sweeper
```

### Schema rules

- Caching is per-session, not per-output. Re-running analysis overwrites the previous bundle for that language.
- No "history of analyses" today — `session_cache` stores only the **latest** generation for each output type.
- `force_refresh: true` in any fetch payload bypasses the cache and refetches from upstream.

---

## 10. API Routes

All routes are FastAPI handlers in [app.py](app.py). Frontend wrapper in [frontend/src/api.js](frontend/src/api.js).

| Route | Method | Purpose |
|---|---|---|
| `/api/health` | GET | Render health check |
| `/api/bootstrap` | GET | Initial frontend state: default API key, OAuth status, connected user |
| `/api/auth/livestorm/start` | GET | Begin OAuth flow (sets handshake cookie, 302 to Livestorm) |
| `/api/auth/livestorm/callback` | GET | OAuth callback: exchange code, fetch /me, set session cookie |
| `/api/auth/logout` | POST | Delete connection row, clear session cookie |
| `/api/workspace-events` | POST | Paginated list of events with title/status filters |
| `/api/workspace-sessions` | GET | (Phase 4) Cached session cards for the calling user's Livestorm org |
| `/api/event-sessions` | POST | List sessions inside an event |
| `/api/sessions/{id}` | GET | Read cached workspace (404 if not cached) |
| `/api/sessions/{id}/cached` | GET | Same, but returns 204 instead of 404 when missing |
| `/api/sessions/{id}/fetch` | POST | Full fetch (session + chat + questions + start transcript) |
| `/api/sessions/{id}/fetch-base` | POST | Fetch session payload only |
| `/api/sessions/{id}/fetch-transcript` | POST | Start transcript job, return jobId |
| `/api/sessions/{id}/transcript-job` | GET | Poll transcript job status / progress |
| `/api/sessions/{id}/speaker-labels` | POST | Save edited speaker labels |
| `/api/sessions/{id}/analysis` | POST | Run Overall Analysis (English or French) |
| `/api/sessions/{id}/deep-analysis` | POST | Run Deep Analysis (English or French) |
| `/api/sessions/{id}/analysis-pdf` | GET | Render Overall or Deep PDF (`kind`, `language` query params) |
| `/api/sessions/{id}/smart-recap` | POST | Run Smart Recap for a tone |
| `/api/sessions/{id}/smart-recap-pdf` | GET | Render Smart Recap PDF |
| `/api/sessions/{id}/content-repurposing` | POST | Run Content Repurposing bundle (4 assets) |
| `/api/sessions/{id}/content-repurposing-pdf` | GET | Render a single asset PDF |

Catch-all `GET /{full_path}` serves the Vue SPA from `frontend/dist`.

---

## 11. Architecture

```
chat-analysis/                      # repo name; product name is StormIQ
├── app.py                          # FastAPI app, route handlers, OAuth wiring, SPA static serving
├── livestorm_app/
│   ├── api_logic.py                # Orchestration: fetch flows, analysis runners, PDF builders
│   ├── services.py                 # Pure compute: Livestorm API calls, DataFrames, prompts, OpenAI client
│   ├── session_overview.py         # Session payload → people DF, hero stats, charts
│   ├── transcript_client.py        # Thin wrapper around gladia.transcriber
│   ├── gladia/
│   │   ├── transcriber.py          # Gladia v2 pre-recorded transcription with diarization + NER + subtitles
│   │   └── cli.py                  # Standalone CLI for ad-hoc transcription
│   ├── db.py                       # psycopg connection, schema bootstrap, session_cache + oauth + jobs CRUD
│   ├── config.py                   # Constants: URLs, model names, prompt paths, .env loader
│   ├── oauth_client.py             # Livestorm OAuth PKCE flow, token refresh, profile extraction
│   ├── queue.py                    # Redis + arq queue: get_redis_url, get_arq_pool, enqueue_job (Phase 1)
│   ├── progress.py                 # Redis-backed stage-floor progress reporting (Phase 1)
│   ├── worker.py                   # arq worker entry-point + stuck-job sweeper cron (Phase 1)
│   └── charts/                     # (empty — directories exist for future per-chart Python modules)
├── docker/
│   └── Dockerfile.worker           # Worker image (no frontend, no HTTP) — runs `arq livestorm_app.worker.WorkerSettings`
├── docker-compose.yml              # Local dev: web + worker + redis
├── Dockerfile                      # Web image (FastAPI + built Vue frontend)
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

External services accessed directly today (no provider abstraction layer yet — a target for Phase 2):

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
LS_API_KEY                    # Optional; local-dev fallback only (see §4)
LIVESTORM_OAUTH_CLIENT_ID     # Required for OAuth path
LIVESTORM_OAUTH_CLIENT_SECRET # Required for OAuth path
LIVESTORM_OAUTH_REDIRECT_URI  # Required for OAuth path
LIVESTORM_OAUTH_SCOPES        # Optional; default 'identity:read events:read'
FRONTEND_APP_URL              # Optional; used to build post-OAuth redirect URLs
SESSION_SECRET                # Optional; HMAC key for the handshake cookie (falls back to client_secret)

# Transcription
GLADIA_KEY                    # Required to fetch new transcripts

# Database
DATABASE_URL                  # Required. Also accepts POSTGRES_URL or RENDER_POSTGRES_URL

# Queue / worker (Phase 1)
REDIS_URL                     # Required in prod; defaults to redis://localhost:6379/0 for local dev

# Render
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

The web Dockerfile is a two-stage build: Node stage builds the frontend, Python stage installs requirements and copies everything in. The worker Dockerfile is single-stage and ships **without** the frontend (it doesn't serve HTTP).

**Health check**: `GET /api/health` returns `{"status": "ok"}`. Worker has no HTTP health check — Render's process-running signal is the liveness check.

**Running locally with Docker Compose** (recommended once Phase 2 ships):

```bash
docker compose up --build
```

This boots web (`:10000`), worker, and redis (`:6379`) using the local `.env` for credentials.

**Running locally without Docker**:

```bash
# 1. Redis (any recent version)
brew install redis && brew services start redis     # macOS
# or: docker run -p 6379:6379 redis:7-alpine

# 2. Backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --reload                            # in one terminal
arq livestorm_app.worker.WorkerSettings             # in another terminal

# 3. Frontend
cd frontend && npm install && npm run dev           # in a third terminal
```

**Local Docker test of just the web image**:

```bash
docker build -t stormiq .
docker run --rm -p 10000:10000 --env-file .env stormiq
```

---

## 15. Key Business Rules

1. **Cache first.** Every fetch route checks `session_cache` before calling Livestorm/Gladia. `force_refresh: true` is the only bypass.
2. **Account key hashing.** Never store raw Livestorm API keys in `session_cache`. Always use `sha256(api_key)` as the account discriminator.
3. **OAuth tokens are refreshed proactively.** When a connection is within 5 minutes of expiry, the next request refreshes the access token via `refresh_token`. Failures surface as 401 with "Please reconnect."
4. **Transcript is the gating dependency.** Analysis, Smart Recap, and Content Repurposing all require `transcript_payload` to be present. Views display either a loading state (job in progress) or an unavailable-reason banner.
5. **Bilingual analysis re-uses existing language.** If a bundle already contains the target language, no new OpenAI call is made; the cached markdown is returned. Switching to a missing language generates only that language.
6. **All AI outputs are PDF-exportable.** Every persistent AI artefact (Overall, Deep, Recap, Content Repurposing assets) has a corresponding PDF route built via reportlab in [services.py](livestorm_app/services.py).
7. **Prompts are editable without code.** All AI prompts live in `prompts/*.txt`. Operators can tune them. Changing wording does not require a deploy if the file is mounted/overridden.
8. **Brand-adjacent, not brand-impersonating.** App is called **StormIQ**. Never replace the app logo with the Livestorm logo. See [BRAND.md](BRAND.md).
9. **No telemetry beyond stderr logging.** No analytics, no error-reporting service, no per-user usage tracking today.

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

**Update rule**: every PR that changes business logic, schema, routes, providers, env vars, or deployment must include a diff to CLAUDE.md in the same commit. PRs that change user-visible behaviour must also update doc.md.

---

## 17. Roadmap

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

- ✅ **Server-side AI-job dedupe**: rapid double-clicks on Generate no longer enqueue duplicate jobs. `_start_ai_job` now writes an in-flight marker to Redis (`stormiq:ai-job:in-flight:{kind}:{session_id}:{dimension}`, TTL 1h) before enqueuing. Subsequent calls within the marker's lifetime that find the underlying arq job still pending/running get the existing job_id back. Stale markers (arq says complete/error/not_found) are self-healing — the lookup clears them and falls through to enqueue fresh.
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
