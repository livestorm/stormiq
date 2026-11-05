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

Set `STORMIQ_LOCAL_DEV=1` and `LS_API_KEY` in `.env` to skip OAuth locally ([security.md](security.md#identity)). `.env` is read at startup by `config.load_env_file()`; variables already set in the environment win. A few values are read before `.env` is loaded, so outside Docker their `.env` values are ignored: `IMAGE_MODEL`, `IMAGE_SIZE` and `IMAGE_QUALITY` (read when `config.py` is imported) and the worker's `REDIS_URL` (read when `WorkerSettings` is defined). Set those in your shell if you need non-default values.

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

## Dependencies

- Python: edit `requirements.in` or `requirements-dev.in`, then `make lock` (uv, with hashes).
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

The worker resolves every credential itself, because job arguments never carry secrets. It needs `DATABASE_URL`, `REDIS_URL`, `GLADIA_KEY`, the LLM key (`CLAUDE_API_KEY` or `OPENAI_API_KEY`), `OPENAI_API_KEY` for covers, and `LIVESTORM_OAUTH_CLIENT_ID` / `LIVESTORM_OAUTH_CLIENT_SECRET` to refresh a user's token when a queued transcription starts close to its expiry. It logs a warning at startup when `GLADIA_KEY` is missing.

## Environment variables

[`.env.example`](../.env.example) has the variables you're likely to set locally, with placeholders. This is the full list:

```bash
# LLM provider (text generation)
LLM_PROVIDER                  # Optional: "anthropic" (default) or "openai". The admin setting overrides it.
CLAUDE_API_KEY                # Required when the provider is anthropic
OPENAI_API_KEY                # Required when the provider is openai; always required for cover images

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

# Transcription
GLADIA_KEY                    # Required on the worker

# Data
DATABASE_URL                  # Required. POSTGRES_URL or RENDER_POSTGRES_URL also work.
REDIS_URL                     # Required in production; defaults to redis://localhost:6379/0

# Cover images
IMAGE_MODEL                   # Optional; default gpt-image-2. The admin setting overrides it.
IMAGE_SIZE                    # Optional; default 1536x1024
IMAGE_QUALITY                 # Optional; default high

# Tests only
TEST_DATABASE_URL             # Enables the real-Postgres tests. Must be a local host.
TEST_REDIS_URL                # Enables the real-Redis tests. Local host and a dedicated db index: the fixture flushes it.

PORT                          # Set by Render; defaults to 10000
```

## Observability

- **Request timing.** Every `/api/*` request except `/api/health` logs one line, `timing method=GET route=/api/sessions/{session_id} status=200 ms=84.2 bytes=1834402`, with the route template rather than ids. Search Render's logs for `timing route=`.
- There is no analytics, error-reporting service or per-user usage tracking: logs go to stderr only.

## Benchmark

`make bench` seeds a **local** database with synthetic sessions (10-minute, 2-hour, a running transcription, 40 cards) with `scripts/seed_benchmark_sessions.py`, then times the heaviest routes in-process with `scripts/benchmark_routes.py`: first-request and median ms, response size, and Postgres connections opened. Both scripts refuse non-local databases. Run it before and after a performance change.

## Scripts

`scripts/` holds one-off backfills and diagnostics, run by hand. Read a script's docstring before running it: the backfills write to whatever `DATABASE_URL` points at, and some call paid APIs (`backfill_cover_images.py` costs about $0.17 per cover).

`strip_registrant_join_links.py` removes registrant join links from rows saved before they were stripped on fetch. It is a dry run unless you pass `--apply`. It refuses a non-local database unless you also pass `--allow-remote-database`, and it doesn't read `.env`. It prints session ids and counts only.
