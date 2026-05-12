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
| `/` | Home (empty hero) | — |
| `/events` | Events list with filters | Connected workspace |
| `/session-overview` | Hero metrics, people, charts | Fetched session |
| `/transcript` | 8-tab transcript diagnostics | Completed transcript |
| `/chat-questions` | 5-tab audience analytics | Fetched session |
| `/analysis` | Overall + Deep AI analysis | Completed transcript |
| `/smart-recap` | 3-tone recap | Completed transcript |
| `/content-repurposing` | Summary / blog / email / social | Completed transcript |
| `/auth/callback` | OAuth landing | — |
| `/beta-info` | Beta notice | — |

A session must be fetched before any view past `/events` becomes useful. Transcript-dependent views (transcript, analysis, recap, repurposing) gate themselves until the transcript job completes (or expose an unavailable-reason banner).

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

### Transcript transcription (current — threaded, not queue-backed)

```
fetch_session_transcript_data → spawn threading.Thread:
  → fetch_session_transcript (Gladia: upload audio → poll → JSON)
  → upsert transcript_payload into session_cache
  → update transcript_jobs row status (pending → running → completed | error)
  → publish progress dicts to transcript_jobs.progress (TEXT-encoded JSON)
```

**This is a single-process background thread, not a worker queue.** The transcription thread runs in the same Python process as the FastAPI app. If the app restarts mid-transcription, the job is lost.

> **Phase 1 status** — the queue + worker scaffolding now exists ([queue.py](livestorm_app/queue.py), [worker.py](livestorm_app/worker.py), [progress.py](livestorm_app/progress.py), Redis service in [docker-compose.yml](docker-compose.yml) and [render.yaml](render.yaml)) and a stuck-job sweeper cron is registered. The transcription flow itself has **not** been migrated to the queue yet — that happens in Phase 2 alongside the AI flows. Until then, the threaded path above is still the live one, but app restarts can now be recovered: the sweeper will mark stalled rows as `error` so the UI can surface them.

### AI generation (currently synchronous)

```
POST /api/sessions/{id}/analysis
POST /api/sessions/{id}/deep-analysis
POST /api/sessions/{id}/smart-recap
POST /api/sessions/{id}/content-repurposing

Backend:
  1. Require cached transcript (otherwise 400)
  2. Build prompt(s) from prompts/*.txt + cached payloads
  3. Call OpenAI Chat Completions synchronously (blocks the request)
  4. Persist result into session_cache.{analysis_bundle, deep_analysis_bundle,
     smart_recap_bundle, content_repurpose_bundle}
  5. Return the markdown / JSON bundle
```

The synchronous flow is the source of the timeout issues fixed in commit `1141728`. Long deep-analysis runs on large transcripts approach the proxy timeout. Phase 1 of the roadmap moves these into a worker.

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
  session_payload          JSONB,                -- raw Livestorm session API response
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
-- Plus a UNIQUE index on session_id alone — collapses duplicate rows
-- if a session is fetched from multiple accounts.
```

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
| **OpenAI Chat Completions** | overall, deep, recap, content generation, translation | [services.py](livestorm_app/services.py) — `analyze_with_openai` + bundle/translation variants | `OPENAI_API_KEY` |

### Model selection

Hard-coded in [config.py](livestorm_app/config.py):

```python
DEFAULT_OPENAI_MODEL       = "gpt-4o-mini"   # overall analysis, content repurposing
DEEP_ANALYSIS_OPENAI_MODEL = "gpt-4o-mini"   # deep analysis
SMART_RECAP_OPENAI_MODEL   = "gpt-5.4-mini"  # smart recap only — intentional
```

Smart Recap is the **only** flow that uses gpt-5.4-mini. Overall, Deep, and Content Repurposing all use gpt-4o-mini. If a future change moves another flow to gpt-5.4, document it here.

There is **no DB-backed provider/model switcher** today. Changing model requires a code change and redeploy. Phase 2 introduces a provider/settings abstraction modelled on Crowdlens's `system_settings`.

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
# OpenAI
OPENAI_API_KEY                # Required for analysis, recap, repurposing

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

**Still pending in Phase 1 (next commits on the same branch):**
- Move Gladia transcription from `threading.Thread` to an arq job
- Move overall analysis / deep analysis / smart recap / content repurposing into arq jobs
- Wire progress reads into the existing `/api/sessions/{id}/transcript-job` polling endpoint, and add equivalent polling endpoints for the AI flows
- Frontend: surface stage-floor progress in the analysis / recap / repurposing views (today they show a binary "Generating...")

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
