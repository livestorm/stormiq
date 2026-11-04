# StormIQ

StormIQ analyses finished Livestorm sessions. It fetches a session's overview, chat, questions and recording transcript, caches them, and provides:

- session overview and audience analytics
- transcript diagnostics (pace, airtime, pauses, entities, segments)
- chat and questions analytics
- AI overall and deep analysis, Smart Recap, and content repurposing (English and French, exportable as PDF)

Access is scoped to the user's Livestorm organization through Livestorm OAuth.

- **[CLAUDE.md](CLAUDE.md):** architecture, business rules, routes and schema. It is the source of truth for engineers.
- **[doc.md](doc.md):** product description of every view, chart and formula.
- **[BRAND.md](BRAND.md):** brand guidelines.

## Architecture

| Service | What it runs |
|---|---|
| **web** | FastAPI API plus the built Vue SPA ([Dockerfile](Dockerfile)) |
| **worker** | arq worker for transcription (Gladia) and AI generation jobs ([docker/Dockerfile.worker](docker/Dockerfile.worker)) |
| **redis** | job queue and progress |
| **Postgres** | session cache, OAuth connections, jobs |

Production runs on Render ([render.yaml](render.yaml)).

## Local development

```bash
cp .env.example .env                  # fill in the keys you need; see comments inside
docker compose up --build             # web on http://localhost:10000, plus worker, redis, postgres
```

Local development uses its own Postgres from `docker-compose.yml`. Never point it at the production database. To run the app outside Docker (hot reload), start only the data services, then the app:

```bash
docker compose up -d postgres redis
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app:app --reload                     # API on :8000
arq livestorm_app.worker.WorkerSettings      # worker
cd frontend && npm install && npm run dev    # SPA on :5173, proxies /api to :8000
```

`STORMIQ_LOCAL_DEV=1` with an `LS_API_KEY` lets you skip OAuth locally; see [CLAUDE.md §4](CLAUDE.md).

## Tests and checks

CI runs these on every pull request:

```bash
ruff check .
TEST_DATABASE_URL=postgresql://stormiq:stormiq@localhost:5433/stormiq \
TEST_REDIS_URL=redis://localhost:6379/15 pytest
cd frontend && npm run lint && npm run build
```

Python dependencies are locked with hashes. To change them, edit `requirements.in` (or `requirements-dev.in`) and re-run the `uv pip compile` command shown at the top of the lock file.

## Prompts

All AI prompts live in [`prompts/`](prompts/) as plain text and can be tuned without code changes.
