# Operations

Running StormIQ locally, testing it, and deploying it. `make help` lists every command used below.

## Local development

Local development never uses the production database: `docker-compose.yml` provides Postgres (loopback port 5433) and Redis (6379).

```bash
cp .env.example .env     # fill in the keys you need; see the comments inside
make setup               # .venv with the locked dev dependencies, and frontend packages
make services            # Postgres + Redis in Docker
make api                 # FastAPI with reload on :8000      (one terminal)
make worker              # arq worker                         (another)
make web                 # Vite on :5173, proxies /api to :8000 (a third)
```

Set `STORMIQ_LOCAL_DEV=1` and `LS_API_KEY` in `.env` to skip OAuth locally ([security.md](security.md#identity)). `.env` is read at startup by `config.load_env_file()`; variables already set in the environment win. A few values are read before `.env` is loaded, so outside Docker their `.env` values are ignored: `TTS_MODEL` and `TTS_VOICE` (read when `config.py` is imported) and the worker's `REDIS_URL` (read when `WorkerSettings` is defined). Set those in your shell if you need non-default values.

`make stack` runs everything in Docker instead (web on `:10000`, worker, Redis, Postgres, all on loopback). In that mode web and worker always use the compose Postgres, whatever `.env` says.

## Tests and checks

CI (`.github/workflows/ci.yml`) runs the same checks on every pull request:

| Command | Runs |
|---|---|
| `make lint` | `ruff check .` and ESLint |
| `make test` | pytest (with the local Postgres and Redis) and Vitest |
| `make build` | the production frontend build |
| `make check` | all three |
| `make docker` | both production images (CI builds them too) |

Backend tests that need Postgres or Redis are skipped unless `TEST_DATABASE_URL` or `TEST_REDIS_URL` is set; the Makefile sets both to the compose services. Both must be local hosts, and the Redis URL must name a dedicated database index (default `/15`), because the fixture flushes it.

Three committed fixtures come from `tests/synthetic_session.py`, a made-up webinar. `frontend/tests/fixtures/record-v1.json` is the v1 record route's response for it, and `record-v1-crowd.json` for a larger made-up webinar (`crowd_row`); after a deliberate change to the record, regenerate both with `.venv/bin/python tests/synthetic_session.py`. `tests/fixtures/classic-workspace.json` freezes a copy of the session and its classic workspace; regenerate it (`--classic`) only when a classic change is intended, and say so in the PR. Tests fail when any of them drifts.

## Dependencies

- Python: edit `requirements.in` or `requirements-dev.in`, then `make lock` (uv, with hashes).
- `anthropic`, the official Anthropic SDK, carries every Anthropic call ([providers.md](providers.md#the-anthropic-sdk)). It is pinned below 2 (`anthropic>=1.17.0,<2`): 1.x sends API version `2023-06-01`, which the classic outputs need for their `temperature`, and 2.x sends `2026-10-21`, which refuses it. Moving to 2.x waits until no call sends `temperature`; a test fails if the SDK sends another version than `config.ANTHROPIC_API_VERSION`. It brings `httpx2`, the maintained fork of `httpx` that the SDK uses for HTTP ([httpx2.pydantic.dev](https://httpx2.pydantic.dev)); the tests declare it in `requirements-dev.in` for its mock transport. FastAPI's `TestClient` still uses `httpx`.
- Frontend: `npm install <pkg>` in `frontend/`; commit `package-lock.json`.
- Node: the version lives in `frontend/.nvmrc` and must match the Dockerfile's build stage; `engines.node` in `frontend/package.json` sets the patched minimum. Bump all three together.

## Deployment

Render, configured in [`render.yaml`](../render.yaml):

| Service | What it runs | Image |
|---|---|---|
| **web** | `uvicorn app:app` on `$PORT`: the API and the built SPA | [`Dockerfile`](../Dockerfile) (two stages: Node builds the frontend, Python runs it) |
| **worker** | `arq livestorm_app.worker.WorkerSettings` | [`docker/Dockerfile.worker`](../docker/Dockerfile.worker) (no frontend, no HTTP) |
| **redis** | Render Key Value: the job queue and progress | managed |
| **Postgres** | Render Postgres; `DATABASE_URL` is set by hand on web and worker in the dashboard (`render.yaml` declares no database) | managed |

- Both images install the hashed lock, run as an unprivileged `stormiq` user, and need no compiler toolchain.
- **Base images are pinned to exact versions:** `node:26.13.0-bookworm-slim` (frontend build only; Node never runs in production) and `python:3.13.18-slim-trixie` (web and worker). Floating tags would let a cached, unpatched base survive rebuilds.
- **Health check:** `GET /api/health` is the web service's `healthCheckPath` (Render waits for it before routing to a new deploy), with a matching Docker `HEALTHCHECK`. The worker has no HTTP check; Render watches the process.
- **Redis:** web and worker share `REDIS_URL`. A restart loses queued jobs and progress, never results ([jobs.md](jobs.md#where-state-lives)). A queue must not evict keys under memory pressure, so the Key Value instance should use the `noeviction` policy. `render.yaml` doesn't set one (Render's default may be `allkeys-lru`): check it in the dashboard.
- **Connections:** each process keeps one Postgres pool (max 10, [data-model.md](data-model.md#connections)). The web process also keeps one Redis connection pool on a background event-loop thread (`queue._QueueBridge`). When Redis is unreachable it tries once, then fails every call immediately for `REDIS_DOWN_COOLDOWN_SECONDS` (5 s): polls report `unknown` and job starts report "queue unavailable" instead of tying up the web server's threads. Both pools close on shutdown.
- **Compression:** Render's edge (Cloudflare) already brotli-compresses responses, so the app adds no compression middleware.

### Worker environment

The worker resolves every credential itself, because job arguments never carry secrets. It needs `DATABASE_URL`, `REDIS_URL`, `GLADIA_KEY`, `CLAUDE_API_KEY` for every text job, `OPENAI_API_KEY` for Surprise me's backup voice only, `ELEVENLABS_API_KEY` for its main voice, and `LIVESTORM_OAUTH_CLIENT_ID` / `LIVESTORM_OAUTH_CLIENT_SECRET` to refresh a user's token when a queued transcription or people sync starts close to its expiry. It logs a warning at startup when `GLADIA_KEY` is missing.

## Environment variables

[`.env.example`](../.env.example) has the variables you're likely to set locally, with placeholders. This is the full list:

```bash
# Text generation: Claude for every job (LLM_PROVIDER is no longer read)
CLAUDE_API_KEY                # Required
ANTHROPIC_LOG                 # Leave unset. When set, the Anthropic SDK logs each request's options, prompts included.
ANTHROPIC_CUSTOM_HEADERS      # Leave unset. The SDK would add its headers to every Anthropic request, so while it is set
                              # every Anthropic call fails before it is sent.
                              # ANTHROPIC_API_KEY and ANTHROPIC_BASE_URL are ignored: llm_client passes the key and
                              # the host to the SDK explicitly.
OPENAI_API_KEY                # Required for Surprise me's backup voice (worker)
ELEVENLABS_API_KEY            # Worker. Surprise me's main voice (ElevenLabs eleven_v3). Unset: every show is
                              # recorded with the OpenAI backup voice and says so. Never logged or sent to the page.
TTS_MODEL                     # Optional. Surprise me's OpenAI speech model (default gpt-4o-mini-tts)
TTS_VOICE                     # Optional. Its OpenAI voice when Admin set none (default cedar)
ORG_DAILY_LLM_BUDGET_USD      # Optional, worker. Daily spend cap per org for automatic text jobs (none exists yet), in
                              # dollars, per UTC day. Unset: no limit. Never applies to jobs a user starts.

# Livestorm
LIVESTORM_OAUTH_CLIENT_ID     # Required for OAuth
LIVESTORM_OAUTH_CLIENT_SECRET # Required for OAuth
LIVESTORM_OAUTH_REDIRECT_URI  # Required for OAuth
LIVESTORM_OAUTH_SCOPES        # Optional; default 'identity:read events:read'
FRONTEND_APP_URL              # Optional; used to build post-OAuth redirect URLs
SESSION_SECRET                # Recommended: HMAC key for the OAuth handshake cookie. Falls back to
                              # LIVESTORM_OAUTH_CLIENT_SECRET; there is no hard-coded fallback.
ADMIN_EMAILS                  # Optional; comma-separated bootstrap admin emails
STORMIQ_LOCAL_DEV             # Optional; "1" turns on local-dev mode. Ignored on Render.
LS_API_KEY                    # Optional; used only in local-dev mode. Never sent to the browser.
LIVESTORM_SYNC_DAILY_CALLS_PER_ORG  # Optional, web and worker. Once one org's people syncs have recorded this many
                              # Livestorm calls in a rolling 24 hours (default 1000), no new sync starts and no
                              # queued one makes a call; the sync already running finishes. A session's first sync
                              # never counts. An invalid value (not a whole number) stops every sync it applies to
                              # until it is fixed.

# Transcription
GLADIA_KEY                    # Required on the worker

# Data
DATABASE_URL                  # Required. POSTGRES_URL or RENDER_POSTGRES_URL also work.
REDIS_URL                     # Required in production; defaults to redis://localhost:6379/0

# Tests only
TEST_DATABASE_URL             # Enables the real-Postgres tests. Must be a local host.
TEST_REDIS_URL                # Enables the real-Redis tests. Local host and a dedicated db index: the fixture flushes it.

PORT                          # Set by Render; defaults to 10000
RENDER                        # Set by Render on every service. Turns off local-dev mode and tags Gladia jobs env=render (local otherwise)
```

## Observability

- **Request timing.** Every `/api/*` request except `/api/health` logs one line, `timing method=GET route=/api/sessions/{session_id} status=200 ms=84.2 bytes=1834402`, with the route template rather than ids. Search Render's logs for `timing route=`.
- **Model usage.** Every answered text-model call (`llm_client.call_llm`, `generate_text`, `generate_json`) writes one `llm_usage` row ([data-model.md](data-model.md#llm_usage)) and logs one line on the worker, `llm_usage job=smart_recap purpose=main org=… session=… job_id=… provider=anthropic model=claude-sonnet-4-6 served=claude-sonnet-4-6 effort=- max_tokens=900 stop=end_turn outcome=ok refusal=- in=2000 out=400 cache_read=0 cache_write=0 cache_write_1h=0 reasoning=- usd=0.012000 ms=8123 request_id=… prompt=- dossier=-`, with no prompt, output or key (`prompt=` and `dossier=` are hashes, set on v1 calls). Two WARNING lines go with it for v1 calls: `read nothing from the prompt cache` (a later call of a run paid for its whole prefix again) and `answered by its fallback` (the requested model declined) ([providers.md](providers.md#request-layouts-and-caching)). Search Render's logs for `llm_usage job=`; `outcome=refused` finds refusals, with their category in `refusal=`. If the row can't be saved, the same line is logged at ERROR after `llm_usage insert failed (…)`, so the numbers survive. The cover image call is not logged there. The table is read with SQL for now; an Admin view is on the [roadmap](roadmap.md#4-models).
- There is no analytics or error-reporting service: logs go to stderr only.

## Benchmark

`make bench` seeds a **local** database with synthetic sessions (10-minute, 2-hour, a running transcription, 40 cards; their people come from many channels and answer select fields, so the v1 record's grouped measures do their full work; the 2-hour session's people are also stored as a synced roster, which its record reads) with `scripts/seed_benchmark_sessions.py`, then times the heaviest routes in-process with `scripts/benchmark_routes.py` (the workspace, the v1 record, polls, the card list): first-request and median ms, response size, and Postgres connections opened. Both scripts refuse non-local databases. Run it before and after a performance change.

## Scripts

`scripts/` holds one-off backfills and diagnostics, run by hand. Read a script's docstring before running it: the backfills write to whatever `DATABASE_URL` points at, and some call paid APIs (`backfill_smart_recap_professional.py` writes a Claude recap per session). `make_favicon.py` rebuilds the favicons from the Sora font, in a throwaway environment with fontTools and Pillow (its docstring says how); it reads no database.

`llm_smoke.py --i-understand-this-costs` makes real, paid calls (well under a dollar in all) to check that the models StormIQ plans to use accept the parameters it plans to send: each planned parameter on the offered Claude models, each v1 job's request as the app itself writes it (`ai/request.py`), three calls of the understanding job over one dossier that show whether a cached prefix is read, and the OpenAI rows' parameters. It reads `CLAUDE_API_KEY` and `OPENAI_API_KEY` from the environment, never `.env`, skips a provider without a key, touches no database, and prints one line per call: whether it was accepted (or the HTTP status and error type it got), the stop reason and, for the cache calls, `cache_read_tokens`. Never a prompt, an answer or a key. The last line counts the calls not accepted and the cache misses (a repeated cached prefix that read 0 tokens from the cache); it exits 1 when either is above 0. Run it by hand only; its docstring lists every call.

`reset_ai_outputs.py` removes every AI output so users regenerate them with the current models and formats (`db.reset_ai_outputs`): the AI bundles, their earlier versions, the chat labels, the understanding, the saved FAQ edits, Surprise me's storyboards, spoken scripts and voice clips, and the desk's decisions on located answers; with `--include-covers`, the bytes still stored in the unused cover image columns (covers were removed in November 2026). It keeps each session's fetched data, its transcript, the host's replies and figure checks, sign-ins, settings and `llm_usage`, and leaves `updated_at` alone. It is a dry run (counts only) unless you pass `--apply`, runs in one transaction, refuses a non-local database unless you also pass `--allow-remote-database`, and doesn't read `.env`. Take a database backup first. On Render: open the web service's Shell and run `python scripts/reset_ai_outputs.py --allow-remote-database`, then again with `--apply`.

`strip_registrant_join_links.py` removes registrant join links from rows saved before they were stripped on fetch. It is a dry run unless you pass `--apply`. It refuses a non-local database unless you also pass `--allow-remote-database`, and it doesn't read `.env`. It prints session ids and counts only.

`diagnose_people_cap.py` calls Livestorm with `LS_API_KEY` from the environment, for the sessions cached in a local database (or named with `--session`), and prints integers and yes/no only: the `include=people` length and the session's counters against the paginated people list's `record_count`, the counts by role and attendance, how registration answers match the people attributes, whether those were refused, whether `RateLimit-Monthly-*` headers came back, and how many calls it made. It writes nothing, refuses a `DATABASE_URL` that isn't local, and doesn't read `.env`. It costs Livestorm calls: about one per 50 registrants, plus five per session.

`diagnose_payload_shapes.py` only reads, and refuses any database that isn't local. It prints the shape of the cached payloads (key paths, JSON types, counts, enum-like values) and a few clock checks, never names, emails, text or URLs. Use it to check a payload question before writing a synthetic test fixture; never commit real payloads.

`probe_alignment_study.py` runs the v1 alignment probe ([product.md §13](product.md#alignment-probe-v1-report)) on the sessions cached in a local database. It only reads, refuses any database that isn't local (the same check), and doesn't read `.env`. It prints one row per session (status, offset applied and ±, the agreed range, anchors agreeing out of found per kind, the duration gap, the reasons), then an ear-check worksheet: each anchor's kind, our own ids, the recording time to listen at (`mm:ss`), the chat side's session time and the offsets it allows. Never text, names or URLs; `--summary` prints only the counts. Check each anchor by ear on the replay before tuning the probe's provisional thresholds (`record/alignment.py`).
