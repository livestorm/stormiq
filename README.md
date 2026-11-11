# StormIQ

StormIQ analyses finished Livestorm sessions. It fetches a session's overview, chat, questions and recording transcript, caches them, and provides:

- session overview and audience analytics
- transcript diagnostics (pace, airtime, pauses, entities, segments)
- chat and questions analytics
- AI overall and deep analysis, Smart Recap, and content repurposing (English and French, exportable as PDF)

Access is scoped to the user's Livestorm organization through Livestorm OAuth.

## Quick start

```bash
cp .env.example .env     # fill in the keys you need; see the comments inside
make setup               # Python venv and frontend packages
make services            # local Postgres and Redis in Docker
make api                 # API on :8000        (one terminal)
make worker              # background worker   (another)
make web                 # app on :5173        (a third)
```

Or run everything in Docker with `make stack` (app on http://localhost:10000). Local development uses its own Postgres from `docker-compose.yml`; never point it at the production database. `STORMIQ_LOCAL_DEV=1` with an `LS_API_KEY` skips OAuth locally ([security](docs/security.md#identity)).

`make check` runs what CI runs (lint, tests, build); `make help` lists every command.

## Architecture

| Service | What it runs |
|---|---|
| **web** | FastAPI API plus the built Vue SPA ([Dockerfile](Dockerfile)) |
| **worker** | arq worker for transcription (Gladia) and AI generation ([docker/Dockerfile.worker](docker/Dockerfile.worker)) |
| **render** | arq render worker for Surprise me's MP4: HyperFrames (headless Chrome) and ffmpeg ([docker/Dockerfile.render](docker/Dockerfile.render)) |
| **redis** | job queue and progress |
| **Postgres** | session cache, OAuth connections, jobs |

Production runs on Render ([render.yaml](render.yaml)).

## Documentation

- [CLAUDE.md](CLAUDE.md): the rules that must never break, commands, and a map of the code. Start here.
- [docs/](docs/): architecture, security, jobs, data model, API, AI outputs, providers, operations, roadmap and changelog.
- [docs/product.md](docs/product.md): what every view, chart and formula does, for product and sales readers.
- [docs/brand.md](docs/brand.md): brand guidelines.

AI prompts live in [`prompts/`](prompts/) as plain text and can be tuned without code changes.
