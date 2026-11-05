# Changelog

What has shipped, newest first. What's next is in [roadmap.md](roadmap.md).

## November 2026: all-hands fixes

- **Chat Messages counts the chat.** Session Overview's Chat Messages added up each person's `messages_count`, which Livestorm sometimes reports as 0 for everyone even when the chat has messages. It now counts the audience messages in the cached chat, as the Chat page does, and uses `messages_count` only when no chat is cached ([product.md §13](product.md#13-glossary-of-metrics-and-formulas)). The session summary sent to the AI counts the same way, an empty cached chat as 0. A person's own message count (People table, Engagement Score) still comes from `messages_count`.
- **Timelines with chat or questions alone.** Content Pace × Audience Activity and Segments With The Most Reactions were empty for a session with chat but no questions, or questions but no chat. A transcript and either source are now enough; the missing source counts 0.
- **Event session count.** The event list showed Livestorm's `sessions_count` as sent, a negative number included. A count that isn't a non-negative integer is left out of the event label and shown as "n/a".

## November 2026: v1 foundation

- **Registrant join links are never stored.** StormIQ doesn't need a registrant's personal join link, so the Livestorm fetchers drop `password_key` and `connection_link` before saving the session, chat and questions payloads. `scripts/strip_registrant_join_links.py` removes them from rows saved earlier, without bumping `updated_at` (`db.rewrite_base_payloads`). Nothing users see changes.
- **Livestorm page size 50.** Chat, questions and event-session calls asked for 100 items per page; the API documents 50 as the maximum. The pagers now ask for 50 and stop when `meta.next_page` is null, even after a full page.
- **Session clock.** `livestorm_app/record/` puts chat messages, questions and written answers on one time base: seconds since `started_at`, binned by minute, with the waiting room and anything after the end kept apart. Each item gets a stable evidence id (`C0001`, `Q001`, `A001`). No page uses it yet. Rules in [product.md §13](product.md#session-clock-v1-report).
- **Long recordings keep their words.** A recording over 135 minutes is transcribed in 135-minute chunks, and joining them went wrong: an empty word list hid every word, so pauses came from segments, not words; words in later chunks kept their chunk's own times; sentences and named entities after the first chunk were dropped; and every chunk reused speaker numbers from 0, so different people shared one. `gladia/transcriber._merge_chunk_results` now shifts everything onto the recording's clock, gives each chunk's speakers their own numbers, sums the billed time and records the chunks in `result.stormiq_chunks`. New long transcripts show word-level pauses, every sentence and named entity, and more speakers, since one person gets a number per chunk. Transcripts saved earlier are not rewritten. Each chunk's Gladia request is now built once, and that same request is sent; the first chunk's request is the one stored.
- **`scripts/diagnose_payload_shapes.py`** prints the shape of the payloads cached in a local database: key paths, JSON types, counts and enum-like values, never names, emails, text or URLs.

## November 2026: internal sessions

- **Internal sessions count everyone.** Audience-only chat metrics left nothing on an internal meeting, such as an all-hands, where everyone is a team member: Messages, Unique Chatters, Avg Msg Length, Top Contributors, Activity Over Time, the cross-source timeline, Most Engaged People and the chat stats sent to the AI dropped to zero, with "Excludes your team (n)". When team members are more than half of the attendees ([product.md §13](product.md#13-glossary-of-metrics-and-formulas)), nothing is left out, the cards say "Internal session: everyone counts" and the AI is told the session is internal. Public sessions are unchanged.

## November 2026: redesign foundation

- **The StormIQ mark replaces the Livestorm icon** in the classic sidebar, the compact top bar and the favicon (`public/favicon.svg`, with `favicon.ico` and `apple-touch-icon.png` for browsers and home screens that don't take SVG). In the classic sidebar it keeps its on-dark colours whatever the system theme. The Livestorm icon PNGs and the `/brand-assets` route that served them are gone.
- **The v1 design, behind a switch.** `?ui=v1` turns it on per browser, `?ui=classic` turns it off; everyone else keeps the classic app. With it on, a top bar (StormIQ mark and Beta tag, Sessions, Add session, a light/dark toggle, the account menu) replaces the sidebar, the footer keeps the "not an official Livestorm product" notice, and the existing screens appear unchanged inside a dark frame until each is rebuilt.
- **Design foundation**: Sora (OFL) as the typeface; `--siq-*` tokens for every colour role in light and dark, plus type, radius, spacing and motion; light and dark mode that follows the system until the user chooses; Lucide icons vendored in `Icon.vue`; base components for cards, card headers, stat cards, collapsible sections, status words, chips, buttons and the "Next time" tip bar. `/design` shows them all with fictional content ([brand.md](brand.md#the-v1-design)).

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
