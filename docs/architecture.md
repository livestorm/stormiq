# Architecture

How StormIQ is put together, where the code lives, and the rules that keep reads fast.

## System

```
Browser ── Vue SPA ──/api──▶ web: FastAPI (app.py) ───────────▶ Postgres
                                │  enqueue, job status            ▲  session cache, OAuth
                                ▼                                 │  connections, jobs
                             Redis: arq queue + progress ◀──── worker: arq
                                                                  │
                                       Livestorm API, Gladia, Anthropic / OpenAI
```

- **web** serves the API and the built SPA. Handlers are plain `def` functions, run in FastAPI's threadpool. It calls the Livestorm API directly (fetching sessions is quick) and hands everything slow or paid to the worker.
- **worker** runs transcription, AI generation and cover images as arq jobs ([jobs.md](jobs.md)).
- **Postgres** is the durable store: one row per session holds the Livestorm payloads (minus registrant join links) and every AI output ([data-model.md](data-model.md)).
- **Redis** holds the job queue and short-lived progress. Losing it loses no results.

## Backend code

| Path | What it does |
|---|---|
| `app.py` | Entry point: creates the FastAPI app (startup and shutdown, middleware, static mounts) and includes the routers |
| `livestorm_app/routes/` | HTTP routes, one module per area: `system`, `oauth`, `admin`, `workspace`, `sessions`, `ai`, `spa`; request bodies in `models.py`, shared helpers (session id type, error and PDF responses) in `common.py` |
| `livestorm_app/auth.py` | Request identity (`Caller`), org read scope, local-dev mode ([security.md](security.md)) |
| `livestorm_app/api_logic.py` | Orchestration behind the routes: fetch flows, job starts and polls, workspace serialization, PDF builders, and the synchronous AI runners the worker calls |
| `livestorm_app/livestorm/` | Livestorm REST API: `client.py` (the shared HTTP session, fetchers, error formatting) and `payloads.py` (JSON:API parsing, pagination, event and session pickers, the registrant join-link strip, one person's facts for the v1 roster: `parse_person`) |
| `livestorm_app/analytics/` | The deterministic pipeline: `session_overview.py` (people, hero stats, distributions), `audience.py` (chat and questions tables), `transcript.py` (transcript insights), `cross_source.py` (session-stage timeline) |
| `livestorm_app/record/` | The v1 Report's session record, as pure functions over cached payloads: `clock.py` (the session clock and minute bins), `evidence.py` (chat, questions and written answers with stable ids), `roster.py` (one row per person: role, channel, lead time, `P-` ref; the prompt and display views), `transcript.py` (the transcript parser: utterances with stable `U` ids, and their words), `transcript_health.py` (how far the transcript can be trusted), `inputs.py` (the single loader of a session's record inputs, and the one place that picks the people source), `measures.py` (every number the Report shows), `assemble.py` (the record route's response: the clock, the roster header, the Pulse, the transcript's health, the measures). Outside it, only `api_logic.get_session_record` and the worker's `_transcription_options` (which counts the people on stage for the speaker cap) import it, inside the function |
| `livestorm_app/ai/` | `models.py` (the text model catalog), `jobs.py` (one spec per AI job kind and the model each uses), `prompts.py` (system prompts from `prompts/`), `llm_inputs.py` (what the model is sent), `generation.py` (analyses, recap, content, translation), `usage.py` (each text-model call's usage row and log line, the cost estimate, the daily budget for automatic jobs), `cover_image.py` |
| `livestorm_app/pdf.py` | PDF rendering of AI outputs (reportlab) |
| `livestorm_app/db.py` | Connection pool, schema bootstrap, every SQL query |
| `livestorm_app/queue.py` | arq job ids, enqueue and status from sync code (`_QueueBridge`) |
| `livestorm_app/worker.py` | arq job handlers, the stuck-job sweeper, `WorkerSettings` |
| `livestorm_app/progress.py` | Stage-floor progress in Redis |
| `livestorm_app/llm_client.py` | Anthropic / OpenAI abstraction: one `LLMResult` per answered call, retries, truncation handling, a usage row per call |
| `livestorm_app/oauth_client.py` | Livestorm OAuth (PKCE), token refresh, profile parsing |
| `livestorm_app/gladia/` | Gladia transcription client (`transcriber.py`) and a debugging CLI |
| `livestorm_app/transcript_client.py` | The worker's entry point into Gladia (keeps the web process from importing it) |
| `livestorm_app/timing.py` | Per-request timing log |
| `livestorm_app/config.py` | URLs, the Anthropic API version, cover image defaults, prompt paths, `.env` loader |
| `prompts/` | Every AI prompt, as plain text |
| `scripts/` | Backfills, diagnostics, the local benchmark |
| `tests/` | pytest, including the access-control route matrix |

## Frontend code

A Vue 3 single-page app built by Vite into `frontend/dist`, which FastAPI serves in production. In development, `npm run dev` proxies `/api` to uvicorn. Dependencies are only `vue` and `vue-router`; charts are hand-built components.

Two designs live side by side while the redesign ships: the classic app (sidebar, `src/styles.css`) and the v1 design (top bar, light and dark, `src/design/` and `src/components/ui/`), chosen per browser with `?ui=v1` / `?ui=classic` and off by default ([brand.md](brand.md#the-v1-design-switch)).

| Path | What it does |
|---|---|
| `index.html` | Entry page; an inline script applies the v1 switch and a saved theme before first paint |
| `src/main.js` | Resolves the design switch (`initUiVersion`), loads the stylesheets, mounts the app |
| `src/App.vue` | Shell: the classic sidebar or, with the v1 switch on, the v1 top bar; the 401 handler |
| `src/router.js` | Routes; every view but the session list is lazy-loaded |
| `src/store/workspace.js` | The single reactive store (no Pinia): state, loaders, job polling |
| `src/api.js` | `fetch` wrappers for `/api/*` |
| `src/views/` | One file per route |
| `src/components/` | `DataTable`, markdown cards, `AiJobProgress`, `SessionHero`, `KeyMomentsTimeline`, and chart cards under `charts/` |
| `src/utils/csv.js` | CSV export for every table |
| `src/styles.css` | Global styles of the classic design |
| `src/design/` | The v1 design system: `tokens.css` (every `--siq-*` token for light and dark, Sora), `uiVersion.js` (the switch), `theme.js` and `useTheme.js` (light/dark) |
| `src/components/ui/` | v1 base components: `TopBar`, `ThemeToggle`, `Card`, `CardHeader`, `IconTile`, `StatCard`, `Collapsible`, `StatusWord`, `Chip`, `UiButton`, `TipBar`, `Icon` (vendored Lucide icons), `StormIqMark` |
| `public/` | Copied as-is into the build root: the StormIQ mark as `favicon.svg`, `favicon.ico` and `apple-touch-icon.png`, and `fonts/` (Sora and its licence) |
| `tests/` | Vitest |

The rules for working in it are in [`frontend/CLAUDE.md`](../frontend/CLAUDE.md).

### Pages

| Route | View | Needs |
|---|---|---|
| `/` | redirects to `/single-analysis` | — |
| `/search` | Find a session by session id, by event id, or by browsing the workspace's events | a connection |
| `/single-analysis` | Card grid of the org's cached sessions, with search, filters, sort and grid/list modes | a connection |
| `/single-analysis/:sessionId/session-overview` | Hero metrics, people, charts | a fetched session |
| `/single-analysis/:sessionId/transcript` | Transcript diagnostics (8 tabs) | a transcript |
| `/single-analysis/:sessionId/chat-questions` | Audience analytics (5 tabs) | a fetched session |
| `/single-analysis/:sessionId/analysis` | Overall and deep analysis | a transcript |
| `/single-analysis/:sessionId/smart-recap` | Smart Recap in three tones | a transcript |
| `/single-analysis/:sessionId/content-repurposing` | Summary, blog post, email, social posts | a transcript |
| `/cross-analysis` | Placeholder for cross-session analysis ([roadmap](roadmap.md)) | — |
| `/admin` | Users, admins, cached sessions, model settings | admin |
| `/auth/callback`, `/beta-info` | OAuth landing, beta notice | — |
| `/design` | The v1 component gallery: tokens, type, icons, every base component, with fictional sample content (noindex) | the v1 switch |

`/single-analysis/:sessionId/...` is the sharing contract: a teammate in the same Livestorm org can open any such link and land on that tab with the cached workspace loaded. Older paths (`/events`, `/transcript`, …) redirect to their new equivalents.

## The data pipeline

The deterministic analytics are pandas code in `livestorm_app/analytics/`. The entry points:

- `build_session_overview_data(session_payload, chat_df=...)`: hero stats, people table, country, role and attendance distributions, and `internal_session`, the session's `audience.is_internal_session` decision ([product.md §13](product.md#13-glossary-of-metrics-and-formulas)). Chat Messages counts `chat_df`, the cached chat; pass None only when no chat is cached.
- `build_transcript_insights(transcript_payload)`: about 27 DataFrames (pace, airtime, pauses, named entities, segments, engagement scoring, key moments).
- `build_cross_source_insights(chat_df, questions_df, transcript_payload, transcript_insights=..., internal=...)`: the 10-bucket session-stage timeline and reaction moments, from audience chat only. It needs a transcript and chat or questions; a source passed as None counts 0.
- `build_chat_df_from_payload(payload)`: one row per chat message, keeping the `from_team_member` and `from_guest_speaker` flags. `split_audience_chat(chat_df, internal=...)` returns the audience messages and the number left out (what counts as audience: [product.md §13](product.md#13-glossary-of-metrics-and-formulas)). With `internal=True` it keeps every message.
- `build_question_stats(questions_df)`: answered and unanswered split ([product.md §13](product.md#13-glossary-of-metrics-and-formulas)).

The v1 Report gets its data from `livestorm_app/record/` instead, so the classic pipeline above doesn't change. Its first pieces:

- `clock_base(session_payload)`: the session clock (`started_at`, the end, the late start). No `started_at` means no clock.
- `audience_events(chat_payload, questions_payload, base, authors=None)`: one row per chat message, question and written answer, with its evidence id (`C0001`, `Q001`, `A001`), session time, phase, minute, team flag, `has_link`, and the author's `P-` ref when `authors` resolved it. It reads the raw times and the chat's `html_content`.
- `minute_bins(events, speech_df=None, base=base)`: zero-filled per-minute lanes (speech too, when given), plus the before and after edges.
- `parse_transcript(transcript_payload)` (`record/transcript.py`): the stored Gladia transcript as utterances with stable ids (`U0001`, by start) and words, each with its utterance's id. It reads `result.transcription`: the utterances' own words, or `transcription.words` when Gladia sent a non-empty list there. On a transcript saved before the merge fix it moves the later parts' words onto their utterances, in memory only. `clock.speech_events(utterances, offset_s)` puts the utterances on the session clock once an alignment offset exists.
- `build_transcript_health(transcript, ...)` (`record/transcript_health.py`): word-level confidence, speech coverage as a union of spans, substantial and expected speakers, the split risk, the language, the unclear clusters, a state and the gates for pace and speaker shares. Counts and `U` ids only.
- `roster_from_session_payload(session_payload, event_payload=...)`: one row per person from the cached `include=people` list (`payloads.parse_person`), with a role, an organizer flag, a channel, a lead-time bucket, the registration answers and a `P-` ref. Its meta holds the source, the coverage (partial for this list), counts by role and the internal flag (`audience.is_internal_session`). `prompt_view` and `display_people` are the only ways people leave it.
- `resolve_authors(chat_payload, questions_payload, roster)` (`record/authors.py`): each chat, question and written-answer author resolved to a roster person by people id, never by guess, with coverage per source. A chat message's people id is its `relationships.people` link (`evidence.people_link`); its `author_id` is a different id. `roster.escalate_roles` raises roles with the team flags of the chat messages resolved to each person and the answers they wrote, except on an internal session. It reads the payloads through `evidence.chat_items` and `evidence.question_items`, as `audience_events` does.
- `load_record_inputs(cached, roster_rows=None)`: the payloads, clock, roster and authors of one cached session row, plus its transcript, parsed once, and the transcript's health, built once. Stored people rows (`RosterRows`) win over the include list when given; `record/inputs.py` is the only place that picks the people source.
- `count_people_on_stage(cached, roster_rows=None)` (`record/inputs.py`): the organizers and guest speakers of the same people, or None when nobody is marked. The transcription job's speaker cap reads it (`worker._transcription_options`).
- `compute_measures(inputs)` (`record/measures.py`): one `MeasureValue` per measure of `REGISTRY` (show-up rate, time in the room, wrote in chat or Q&A, questions answered…, and the grouped ones whose numbers are in rows: chat concentration, the room map, channels, segments, show-up by lead time, replay catch-up; and the transcript's mean word confidence), each with its numerator, denominator, state, coverage and exclusions. `validate_measures` checks a set before it is served. `METRICS_VERSION` versions the set, and `REGISTRY_PIN` pins it.
- `assemble_record(inputs)`: what `GET /api/sessions/{id}/record` serves: `meta.clock`, the roster's header, the Pulse, the transcript's health and the measures (`measures_for_read`, live for now) ([api.md](api.md#the-v1-record)).

Every metric, chart and formula is documented once, in [product.md §13](product.md#13-glossary-of-metrics-and-formulas). Don't copy formulas elsewhere; link to it.

## Reading a workspace

Opening a session is the hottest path. These rules keep a 2-hour session around 0.1 s:

- **Derived insights are cached in-process.** `build_transcript_insights` is the costly part of a read. `api_logic._cached_transcript_insights` keeps the last 6 sessions per process, keyed by `(session_id, updated_at)`; every write to a `session_cache` row bumps `updated_at`, so the key changes whenever the input can. The frames are shared between requests, so readers must copy before modifying (`_df_records` and `build_cross_source_insights` do). Build insights once per read and pass them on (`build_cross_source_insights(..., transcript_insights=...)`).
- **Polls and job starts read only what they need.** `db.fetch_session_fields(session_id, [fields], organization_id=...)` reads allow-listed columns or flags, with the same scope rules as `fetch_cached_session`. The transcript poll reads `has_transcript`; AI polls and starts read their own bundle column. The full row is loaded only to return a finished result.
- **The card list never loads payloads.** `db.list_workspace_sessions` selects the session attributes, the event title and language, and has-output flags computed in SQL (`db._has_content_sql`).
- **No raw upstream payloads in the workspace.** The serialized workspace carries derived tables, the display transcript and AI outputs. `payloads.transcript` is `{subtitles, language, durationSeconds}` (or null); the full transcript is a separate download (`/transcript.json`). Add a table back only when a view needs it.
- **The internal-session decision is made once per read.** `api_logic._serialize_cached_session` takes it from `build_session_overview_data`, passes it to `build_derived_stats` and `build_cross_source_insights`, and sends it as `meta.internalSession`; the SPA (`frontend/src/utils/audience.js`) uses it rather than deciding again.
- **System settings** are cached for 60 s per process.

## Reading the v1 record

The v1 Report reads `GET /api/sessions/{id}/record`, never the workspace. `api_logic.get_session_record`:

- **Checks the scope first.** Its first read is `fetch_session_fields(session_id, ["updated_at"], organization_id=caller.read_scope)`. A missing or out-of-scope row stops there with a 404.
- **Caches the computed block per process**, the last 32 sessions, keyed by everything it reads: `(session_id, updated_at, RECORD_VERSION, METRICS_VERSION, HEALTH_VERSION, roster version, metrics fingerprint)`. The last two are None for now. Saving a transcript or speaker labels bumps `updated_at` like any write. When an input can change without bumping `updated_at` (stored rosters, stored measures), its version goes in `_record_input_versions`. Live state that changes without any of these must be read outside the cache, on every request. A hit costs the one `updated_at` read, and never touches the transcript.
- **On a miss, reads the payloads in one query** (`RECORD_FIELDS`: session, event, chat, questions, the transcript and its speaker labels, with `updated_at`), never the AI outputs. The transcript is the largest column, so it is read only here: a session without one reads a NULL. It builds the clock, the roster, the parsed transcript and its health, the minute bins and the measures once (`record.inputs.load_record_inputs`, `record.assemble.assemble_record`). Only the health stays in the cached block, never the transcript or its parsed frames. The measures are computed live on every miss and never written on a read.
- **Leaves classic alone.** `_serialize_cached_session` doesn't run v1 code, and the web process imports `record/` only inside `get_session_record`, so it loads with the first record request. `tests/test_record_route.py` compares the classic workspace of a frozen synthetic session with a snapshot taken before the route existed, as JSON text (key order and int or float count; floats are rounded to 6 places).

On the 2-hour benchmark session (400 people from about 50 channels, with three select fields, one of 200 options, and a 3.6 MB transcript of 15,000 words), `make bench` shows a record read at about 4 ms (median, from the cache; no measure runs on a hit) and about 105 ms on the first request, which builds the block and imports `record/` for the first time. A miss costs about 85 ms, against about 25 ms without the transcript: reading and decoding the transcript takes about 45 ms, parsing it about 20 ms, its health under 1 ms, and the measures about 3 ms. On a made-up 5,000-person session with a 200-option select, a 30-option multiple select and 60 UTM sources, the measures take about 11 ms: the channel and segment measures group people by row position in one pass (`record/measures.py`: `_channel_quality`, `_segment_field`), never with a full-length mask per value. The response is 27 KB (4 KB gzipped).
