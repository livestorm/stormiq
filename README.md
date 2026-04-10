# Livestorm Session Workspace

Vue + FastAPI workspace to:
- Fetch Livestorm session overview, chat, questions, and transcript data
- Cache fetched sessions in Postgres by `session_id`
- Explore each major area in its own routed frontend view
- Run overall analysis, deep analysis, smart recap, and content repurposing workflows
- Persist speaker labels alongside the cached session

## Quickstart

Backend:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --reload
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

## Render Deployment

This repo is set up to deploy to Render as a single web service:

- FastAPI serves the API routes
- FastAPI also serves the built Vue frontend from `frontend/dist`
- Render only needs to run the Docker image

Recommended Render settings:

- Environment: `Docker`
- Build Command: none
- Start Command: none
  Docker uses the `CMD` from [Dockerfile](/Users/fares/Code/chat-analysis/Dockerfile)

Required environment variables on Render:

- `OPENAI_API_KEY`
- `API_AUTH_KEY`
- the Postgres connection variables used by [db.py](/Users/fares/Code/chat-analysis/livestorm_app/db.py)
- optionally `LS_API_KEY` if you want a server-side default in local-style flows

Local Docker test:

```bash
docker build -t stormiq .
docker run --rm -p 10000:10000 --env-file .env stormiq
```

Then open:

```text
http://localhost:10000
```

## Backend API

The FastAPI backend exposes JSON endpoints for:
- loading past event sessions
- fetching and caching a session workspace
- saving speaker labels
- running overall analysis
- running deep analysis
- generating smart recap output
- generating content repurposing bundles

## Project Structure

```text
assets/
  icons/
frontend/
  src/
livestorm_app/
  api_logic.py
  config.py
  db.py
  services.py
  session_overview.py
prompts/
app.py
README.md
requirements.txt
```

## Frontend Routes

- `/session-overview`
- `/transcript`
- `/chat-questions`
- `/analysis`
- `/smart-recap`
- `/content-repurposing`

## Editable Prompt

You can change the analysis instructions without code edits:

- `prompts/analysis_base_prompt.txt`
- `prompts/analysis_chat_prompt.txt`
- `prompts/analysis_questions_prompt.txt`
- `prompts/analysis_transcript_prompt.txt`
- `prompts/analysis_all_sources_prompt.txt`
- `prompts/analysis_deep_prompt.txt`
- `prompts/content_repurpose_summary_prompt.txt`
- `prompts/content_repurpose_email_prompt.txt`
- `prompts/content_repurpose_blog_prompt.txt`
- `prompts/content_repurpose_social_media_prompt.txt`

## Notes

- Set `OPENAI_API_KEY` for analysis, recap, and content generation.
- Set `API_AUTH_KEY` for transcript fetching.
- In local dev, Vite proxies `/api` and `/brand-assets` to the FastAPI server.
- In production, FastAPI serves the built frontend directly from `frontend/dist`.
