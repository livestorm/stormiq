# StormIQ

StormIQ analyses finished Livestorm sessions. It fetches a session's details, chat, questions and recording, transcribes the recording, caches everything in Postgres, and turns it into analytics and AI outputs (overall and deep analysis, Smart Recap, content repurposing) in English and French. Access is scoped to the user's Livestorm organization through Livestorm OAuth. Repository: `livestorm/stormiq` (local folder `chat-analysis`).

**Stack.** A FastAPI web process, an arq worker, a render worker (Surprise me's video: Node, the pinned `hyperframes` CLI with headless Chrome, ffmpeg), Redis and Postgres, deployed on Render. A Vue 3 SPA (Vite; no Pinia, UI library or chart library) served by FastAPI. Providers: the Livestorm API, Gladia (transcription), Anthropic (every text job), and for Surprise me ElevenLabs (the voice) and OpenAI (the backup voice, the video's word timings and its illustrations).

## Rules that must never break

1. **Access fails closed.** Every session, event and admin route takes its identity from `livestorm_app/auth.py` (`Depends(get_caller)` or `Depends(require_admin)`), and session reads pass the caller's org scope. A blank scope matches nothing. `tests/test_access_control.py` checks every `/api/sessions/{id}*` route automatically; keep it passing. → [security](docs/security.md)
2. **No secrets where they can leak.** Never put API keys, tokens or connection ids in job arguments (arq stores and logs them, and a connection id is also a session cookie), in responses, or in logs. The worker reads keys from its own environment.
3. **Slow or paid work runs only in the worker** (Surprise me's video in the render worker, on its own queue), as arq jobs with deterministic ids, never inside a web request. arq retries nothing; users retry from the UI. → [jobs](docs/jobs.md)
4. **Never lose or corrupt an output.** Write bundle columns one entry at a time (`db.merge_cached_bundle_entry`), let database writes raise, and save only complete AI output. → [data model](docs/data-model.md), [AI outputs](docs/ai-outputs.md)
5. **Every outbound call has a timeout**, and only failures where nothing was billed are retried. → [providers](docs/providers.md)
6. **Brand-adjacent, never brand-impersonating.** StormIQ uses Livestorm's design language but has its own name: the app's headers show the co-brand "StormIQ for Livestorm" (the StormIQ wordmark, then "for" and the official Livestorm logo, unchanged: `ForLivestorm.vue`). PDFs carry the same lockup (`pdf.py`). Square spots (the favicon, the collapsed sidebar, the compact top bar) show StormIQ's "IQ" monogram (`StormIqMark.vue`, `public/favicon.svg`): the Livestorm logo or icon is never StormIQ's own logo or app icon. Keep the "not an official Livestorm product" notice. → [brand](docs/brand.md)
7. **The repository is public.** No secrets, customer data or details of unfixed vulnerabilities in code, commits, PRs or docs. `docs/improvement-plan.md` is local-only (listed in `.git/info/exclude`): never commit it.
8. **Local means local.** Development and tests use the compose Postgres (port 5433) and Redis; test fixtures and benchmark scripts refuse other hosts. Never print values from `.env`.

## Commands

| Command | Does |
|---|---|
| `make setup` | `.venv` with the locked dev dependencies, plus `npm ci` |
| `make services` | Postgres and Redis in Docker |
| `make api` · `make worker` · `make web` | API on :8000 with reload · arq worker · Vite on :5173 |
| `make render-worker` | the render worker (Surprise me's MP4; needs Node, ffmpeg and `GSAP_JS_PATH`: [operations](docs/operations.md)) |
| `make check` | what CI runs: ruff and ESLint, pytest and Vitest, the frontend build |
| `make test-backend ARGS="-k scope"` | pytest only, with flags; `make test-frontend` for Vitest only |
| `make bench` | the local performance benchmark |
| `make lock` | re-lock Python dependencies after editing `requirements*.in` |

`make help` lists every target. Details: [operations](docs/operations.md).

## Map

| Path | What |
|---|---|
| `app.py` | Entry point: creates the FastAPI app and includes the routers |
| `livestorm_app/` | Backend: HTTP routes (`routes/`), identity (`auth.py`), orchestration (`api_logic.py`), the Livestorm client (`livestorm/`), analytics (`analytics/`), the v1 session record (`record/`), prompts and generation (`ai/`), SQL (`db.py`), queue and worker, Surprise me's renderer (`video/`, `render_worker.py`). Rules in [`livestorm_app/CLAUDE.md`](livestorm_app/CLAUDE.md) |
| `frontend/` | Vue SPA: the store (`src/store/workspace.js`), `src/api.js`, `src/views/`. Rules in [`frontend/CLAUDE.md`](frontend/CLAUDE.md) |
| `prompts/` | Every AI prompt as plain text, tunable without code changes |
| `tests/` | pytest, including the access-control route matrix |
| `scripts/` | Backfills, diagnostics and the benchmark; read a script's docstring before running it |
| `docs/` | The documentation below |

## Docs

Read the doc that matches your task before changing code, and update it in the same PR.

| Doc | Covers | Update it when you change |
|---|---|---|
| [architecture](docs/architecture.md) | the system, code map, pages, data pipeline, read-path performance rules | modules, pages, how a workspace is read |
| [security](docs/security.md) | identity, org scope, fail-closed rules, secrets | auth, scoping, cookies, anything secret |
| [jobs](docs/jobs.md) | fetch, transcription, AI and Surprise me jobs; timeouts; where state lives | the queue, the worker, a job's contract |
| [data-model](docs/data-model.md) | tables, indexes, caching rules, connections | the schema, or how rows are written |
| [api](docs/api.md) | every route, its auth, response conventions | routes |
| [ai-outputs](docs/ai-outputs.md) | what each AI output contains, languages, saving rules | output structure, prompts' sections |
| [providers](docs/providers.md) | external services, model selection, timeouts and retries | providers, models, retry rules |
| [operations](docs/operations.md) | local dev, tests, CI, deployment, env vars, benchmark | env vars, deployment, tooling |
| [product](docs/product.md) | what users see: every view, chart and formula, for product and sales readers | anything user-visible, any metric or formula |
| [brand](docs/brand.md) | colours, typography, components, tone | the visual design |
| [roadmap](docs/roadmap.md) · [changelog](docs/changelog.md) | what's next · what has shipped | when something ships, move it across |

## Working here

- One branch and one PR per change, into `main`. CI must pass: ruff, pytest (with Postgres and Redis), Vitest, ESLint, the frontend build, and both Docker images.
- Render auto-deploys `main` (web, worker and render worker), so a merge reaches production within minutes. Never push to `main` directly.
- If a change makes any doc wrong, fix the doc in the same PR. Formulas live only in [product.md §13](docs/product.md#13-glossary-of-metrics-and-formulas).
- In docs, point at code by file and function name, not line number.
