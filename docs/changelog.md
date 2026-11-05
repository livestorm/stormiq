# Changelog

What has shipped, newest first. What's next is in [roadmap.md](roadmap.md).

## November 2026: fixes before the rebuild

- **Audience-only chat metrics.** Chat from the team and guest speakers no longer counts as audience activity: Messages, Unique Chatters, Avg Msg Length, Top Contributors, Activity Over Time, the cross-source timeline, Session Overview's Chat Messages and the chat stats sent to the AI count the audience only, with "Excludes your team (n)" on the cards. The Chat tab still lists every message, with a "From" column.
- **Session Overview.** Most Engaged People leaves out team members and guest speakers; Attendance By Country plots attendees, not registrants.
- **Cross-source timeline.** The last chat message and the last question were always dropped (they fell in a 100% bucket past the end).
- **Oral answers.** The questions field is read with the API's spelling (`response_orally`, falling back to `responded_orally`), and a question answered orally counts as answered, in the app and in what the AI sees.
- **Card language.** The session-card API returns the event's language; it was read from the session, which has none. Cards don't show it yet.
- **Content Repurposing on an empty transcript** fails the job instead of saving "Transcript is empty." four times. Bundles saved that way earlier are treated as missing, so they can be generated again.
- **PDFs** carry the StormIQ mark instead of the Livestorm logo, a "not an official Livestorm product" footer (in French for French exports), real bold text and no literal `---` lines. Exports are named `stormiq-…` instead of `livestorm-…`.
- **Models.** `temperature` is sent only to the Claude models that accept it, so Opus 4.7 and 4.8 work when chosen; Admin offers Opus 4.8 in place of 4.7. gpt-image-1, shut down by OpenAI, is gone from Admin, and a saved gpt-image-1 setting is sent as gpt-image-2.
- **Surprise Me!** shows a CSS spotlight instead of an image.

## November 2026: structure

- **#11 Module split.** `app.py` (776 lines) became the entry point plus one router per area in `livestorm_app/routes/`; `services.py` (3,000 lines) became `livestorm/` (API client), `analytics/`, `ai/` and `pdf.py`. Code moved verbatim: a before/after snapshot of workspaces, routes, the OpenAPI schema, prompts, LLM inputs and a PDF is byte-identical.
- **#10 Empty AI output fails the job.** An empty model answer for an overall analysis, deep analysis or Smart Recap was saved as the text "No analysis text returned by model."; the job now fails and the user can retry.
- **#8 Project structure.** CLAUDE.md split into focused docs under `docs/` and per-folder CLAUDE.md files; a Makefile; dead code removed; the content-repurposing prompt moved into `prompts/`.

## November 2026: hardening

Security, data-integrity, reliability and performance fixes from the November 2026 review, merged as pull requests #1 to #7.

- **#7 Frontend logic.** Session-scoped async work in the store: switching session aborts the old session's requests and drops late results, and AI jobs always target the session on screen. Loading flags owned by the call that set them. Polls ride out transient failures. Central 401 handling that keeps shared links working. `markRaw` workspace. Lazy-loaded routes (entry bundle 272 → 135 KB). Encoded path params. Formula-safe CSV export and stable table row keys. AI job polls return only the outputs. The first frontend tests (Vitest).
- **#6 Backend performance.** A 2-hour session opens in about 0.1 s instead of 4.5 s: an O(mentions × sentences) entity lookup replaced, and transcript insights built once per read and cached per session version. The workspace response is about 3.5× smaller (raw payloads dropped, raw transcript moved to `transcript.json`). A Postgres connection pool and one long-lived Redis pool. Polls and the card list read only the columns they need. System settings cached for a minute. Immutable caching for hashed assets. A per-request timing log and a local benchmark.
- **#5 Job reliability and cost.** Deterministic arq job ids replace the Redis in-flight marker, and a failed job can be retried at once. Reading a session never starts a paid cover image, and the post-recap cover trigger works again. One active transcription per session, with dead queue jobs detected instead of blocking for an hour. `/fetch` no longer transcribes inside the web request. Retries with backoff and timeouts on every LLM, Livestorm and Gladia call. Per-job timeouts, a two-transcription concurrency cap, and cancellation handling. No credentials in job arguments. Language and tone validated before any paid job. AI job polls no longer trust a client-supplied job id.
- **#4 Base image pins** for the October 2026 Node.js security release: exact `node` and `python` base images and `frontend/.nvmrc`.
- **#3 Data integrity.** Atomic per-entry bundle writes (`merge_cached_bundle_entry`). `account_key_hash` insert-only. Database write failures raise instead of being swallowed. Empty, truncated or incomplete AI output fails the job instead of being saved. A transcription heartbeat for the stuck-job sweeper.
- **#2 Safety net.** A local Postgres in compose and `.env.example`. Hashed dependency locks (uv). Ruff and ESLint, with the issues they found fixed. Real-Postgres and pure-function tests. CI. Slimmer non-root images with health checks.
- **#1 Access control.** Fail-closed org scoping on every session route (`livestorm_app/auth.py`), a path-traversal fix in the SPA route, local-dev mode behind `STORMIQ_LOCAL_DEV` instead of the Host header, Secure cookies, CORS removed, handshake expiry enforced, upstream error details no longer returned, and the access-control route matrix.

## Earlier

### Workspace and shareable session links

- `session_cache.organization_id`: teammates in one Livestorm org share cached results; other orgs can't read them.
- Sidebar with three entries: Search, Single Analysis, Cross Analysis (placeholder).
- Search page with three modes: by session id, by event id, browse the workspace.
- Single Analysis card grid with search, event filter, sort, and a grid/list toggle remembered in `localStorage` (`stormiq:single-analysis:viewMode`).
- Shareable per-session URLs (`/single-analysis/:sessionId/...`); legacy routes redirect.
- Event titles on cards (`session_cache.event_payload`, backfilled by `scripts/backfill_event_payloads.py`).
- "Generated by" attribution (`created_by_*` columns, first writer wins).
- AI cover images from the Professional Smart Recap (`scripts/backfill_cover_images.py`).

### Background jobs

- Redis and an arq worker (`Dockerfile.worker`), stage-floor progress in Redis, and the stuck-job sweeper.
- Transcription moved from a web-process thread to an arq job; restarts no longer kill transcriptions in progress.
- The four AI flows moved to arq jobs with poll routes, so long deep analyses no longer hit the proxy timeout. The frontend polls them and shows an `AiJobProgress` bar (English and French stage labels), including for transcription.
- Server-side dedupe of AI jobs, so double-clicks don't enqueue twice.

### First release

- Livestorm OAuth (PKCE), with an API-key fallback for local development.
- Event browsing with title and status filters; session fetch with chat and questions caching.
- Gladia transcription with progress polling.
- Session Overview, Transcript (8 tabs), and Chat & Questions (5 tabs) views.
- Overall Analysis and Deep Analysis in English and French, Smart Recap in three tones, and Content Repurposing.
- PDF export for every AI output, CSV export for every table, and editable speaker labels.
