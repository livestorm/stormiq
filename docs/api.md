# HTTP API

Every route is a FastAPI handler in [`livestorm_app/routes/`](../livestorm_app/routes/), one module per area (`system`, `oauth`, `admin`, `workspace`, `sessions`, `ai`, `spa`), which [`app.py`](../app.py) includes in that order. The frontend calls them through [`frontend/src/api.js`](../frontend/src/api.js).

**Auth column.** *public*: no identity needed. *caller*: `Depends(get_caller)`; 401 without an identity, and session routes are scoped to the caller's organization ([security.md](security.md)). *admin*: `Depends(require_admin)`; 401, or 403 for non-admins. `{id}` must match `^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$` (422 otherwise).

| Route | Method | Auth | Purpose |
|---|---|---|---|
| `/api/health` | GET | public | Render health check: `{"status": "ok"}` |
| `/api/bootstrap` | GET | public | Initial frontend state: OAuth status, connected user, `isAdmin`, local-dev flag. Never returns an API key. |
| `/api/auth/livestorm/start` | GET | public | Start the OAuth flow (sets the handshake cookie, 302 to Livestorm) |
| `/api/auth/livestorm/callback` | GET | public | OAuth callback: check the handshake, exchange the code, fetch `/me`, set the session cookie |
| `/api/auth/logout` | POST | public | Delete the connection row and clear the session cookie |
| `/api/dev/auto-login` | GET | local-dev only | Create a dev identity from `LS_API_KEY` (403 unless local-dev mode) |
| `/api/admin/check` | GET | admin | Confirm admin status |
| `/api/admin/users` | GET | admin | List users and admins |
| `/api/admin/users/promote` | POST | admin | Add an email to `admin_users` |
| `/api/admin/users/demote` | POST | admin | Remove an admin (not `ADMIN_EMAILS` admins, not yourself) |
| `/api/admin/sessions` | GET | admin | List cached sessions across orgs |
| `/api/admin/sessions/{id}` | DELETE | admin | Delete a cached session |
| `/api/admin/settings` | GET, POST | admin | Read or update allow-listed `system_settings` keys. A model id the job can't use gets 400 with the choices ([providers.md](providers.md#what-admin-may-save)) |
| `/api/workspace-events` | POST | caller | Paginated list of events (at most 50 per page), filtered by title and status |
| `/api/workspace-sessions` | GET | public¹ | Cached session cards for the caller's own org |
| `/api/event-sessions` | POST | caller | List the sessions of an event |
| `/api/sessions/{id}/cover.png` | GET | caller | Cover image (`Cache-Control: private, max-age=86400`) |
| `/api/sessions/{id}` | GET | caller | Cached workspace (404 if not cached) |
| `/api/sessions/{id}/cached` | GET | caller | Same, but 204 instead of 404 when missing |
| `/api/sessions/{id}/record` | GET | caller | v1 Report only: the session clock, the roster header, the Pulse, the transcript's health and the measures ([below](#the-v1-record)). 404 if not cached |
| `/api/sessions/{id}/fetch` | POST | caller | Session, chat and questions, then start the transcript job ([jobs.md](jobs.md#fetching-a-session)) |
| `/api/sessions/{id}/fetch-base` | POST | caller | Session, chat and questions only |
| `/api/sessions/{id}/fetch-transcript` | POST | caller | Start (or attach to) the transcript job |
| `/api/sessions/{id}/transcript-job` | GET | caller | Transcript job status and progress |
| `/api/sessions/{id}/transcript.json` | GET | caller | Full raw transcript as a download (the workspace carries only subtitles) |
| `/api/sessions/{id}/speaker-labels` | POST | caller | Save edited speaker labels |
| `/api/sessions/{id}/analysis` | POST | caller | Start the overall analysis (English or French) |
| `/api/sessions/{id}/deep-analysis` | POST | caller | Start the deep analysis (English or French) |
| `/api/sessions/{id}/smart-recap` | POST | caller | Start a Smart Recap (`professional`, `hype` or `surprise`) |
| `/api/sessions/{id}/content-repurposing` | POST | caller | Start content repurposing (four assets, English or French) |
| `/api/sessions/{id}/{flow}/job` | GET | caller | Poll an AI job: `analysis`, `deep-analysis`, `smart-recap`, `content-repurposing`. `outputsOnly=true` returns only the outputs once done ([jobs.md](jobs.md#ai-generation)) |
| `/api/sessions/{id}/analysis-pdf` | GET | caller | Overall or deep analysis PDF (`kind`, `language`) |
| `/api/sessions/{id}/smart-recap-pdf` | GET | caller | Smart Recap PDF (`tone`) |
| `/api/sessions/{id}/content-repurposing-pdf` | GET | caller | One content asset as PDF |

¹ Returns `{sessions: []}` without an OAuth connection or org, so the page can show its empty state.

## Conventions

- **Errors** are `{resource, message, details: {resource, status_code?}}`. Upstream URLs and bodies are never returned.
- **Workspace payload.** Session reads return a serialized workspace: derived tables, the display transcript (`payloads.transcript` is `{subtitles, language, durationSeconds}` or null) and AI outputs. Raw upstream payloads are not sent ([architecture.md](architecture.md#reading-a-workspace)). `meta.internalSession` says whether the session is internal ([product.md §13](product.md#13-glossary-of-metrics-and-formulas)).
- **Caching headers.** Hashed files under `/assets` are `public, max-age=31536000, immutable`; `index.html` is `no-store`.
- **SPA.** The catch-all `GET /{full_path}` serves the Vue app from `frontend/dist` and returns JSON 404s for unknown `/api/*` paths. Files in the build root (`favicon.svg`, `favicon.ico`, `apple-touch-icon.png`, `fonts/`) are served by the same route.
- `/docs`, `/redoc` and `/openapi.json` exist locally only.

## The v1 record

`GET /api/sessions/{id}/record` serves the v1 Report (behind `?ui=v1`). The classic app doesn't call it, and the classic workspace doesn't carry it. It is built by `livestorm_app/record/assemble.py` (`api_logic.get_session_record`); rules and formulas are in [product.md §13](product.md#session-clock-v1-report). Apart from the session's own id, it never carries a Livestorm id: no name, email, company, person or message id, message or transcript text, or speaker label. `frontend/tests/fixtures/record-v1.json` is its response for a synthetic session, and `record-v1-crowd.json` for a larger one whose grouped measures show their rows.

```
{sessionId, updatedAt, meta: {clock}, record: {version, roster, pulse, transcriptHealth, metricsVersion, measuresSource, measures}}
```

| Field | What it holds |
|---|---|
| `meta.clock.startedAtUnix` | `started_at` in unix seconds, or null: no clock. Never `estimated_started_at` |
| `meta.clock.endS`, `endSource` | Seconds from the start to the end, and where they came from: `ended_at`, `duration` or `none` |
| `meta.clock.lateStartS` | The late start in seconds ([product.md §13](product.md#session-clock-v1-report)), or null |
| `meta.clock.timezone` | The session's time zone, as Livestorm sends it |
| `meta.clock.alignment` | null until the recording is aligned with the session clock |
| `record.version` | `RECORD_VERSION` in `record/assemble.py` (4) |
| `record.roster` | The roster's header, no person: `source` (`session_include` for now), `complete` (true, false or null), `coverage` (`complete` or `partial`), `peopleCount`, `recordCount`, `syncedAt`, and `internal`, the classic internal-session rule applied to the roster's people ([product.md §13](product.md#roster-v1-report)). The roster counts a person once and skips people without an id, so on such a list it can differ from the workspace's `meta.internalSession` |
| `record.pulse.lanes` | The lanes in display order: `audienceChat`, `teamChat`, `questions`, `answers` |
| `record.pulse.minutes` | One row per live minute, from 0, zero-filled: `{minute, audienceChat, teamChat, questions, answers}` |
| `record.pulse.edges` | `before` (the waiting room) and `after` (after the end): `{counts: {lane: n}, firstT}` and `{counts, lastT}`, in seconds on the session clock |
| `record.pulse.unplaced` | Items with no session time (no clock, or a time that can't be read) |
| `record.transcriptHealth` | How far the transcript can be trusted ([product.md §13](product.md#transcript-health-v1-report)): `{version, state, reasons, model, words, coverage, speakers, language, unclearClusters, gates}`. `state` is `ok`, `degraded`, `poor` or `unavailable` (no Gladia transcript), and `reasons` lists the codes behind it (`no_transcript`, `no_speech`, `low_confidence`, `unclear_words`, `no_word_data`, `little_speech`, `no_speakers`, `split_risk`, `language_mismatch`). `words`: `{count, withConfidence, meanConfidence, lowConfidenceShare, source, rebasedUtterances}`. `coverage`: `{speechS, audioS, liveS, shareOfAudio, shareOfLive, firstSpeechR, tailS}`. `speakers`: `{detected, substantial, labelled, substantialLabelled, expected, expectedSource, cap, chunks, splitRisk}`. `language`: `{detected, expected, match, shares}`. `unclearClusters`: `[{t0R, t1R, nLow, u}]`, recording seconds and utterance ids (`U0001`). `gates`: `{pace, speakerShares}`. `words`, `coverage`, `speakers` and `language` are null when `unavailable`; an unknown value is null, never 0. Times are seconds into the recording, not on the session clock |
| `record.metricsVersion` | `METRICS_VERSION` in `record/measures.py` (4): the version of the whole set of measures |
| `record.measuresSource` | `live`: computed from the cached payloads when the record block is built, and cached with it per process. Nothing is stored yet |
| `record.measures` | One entry per measure of the registry, by id, in registry order (the vitals `m.roster_coverage`, `m.audience_registrants`, `m.team_count`, `m.audience_attendees`, `m.show_up_rate`, `m.time_in_room`, `m.wrote_rate`, `m.questions_qa`, `m.time_to_answer`, `m.author_resolution`, `m.livestorm_counts`, then the grouped audience measures `m.chat_concentration`, `m.room_map`, `m.channel_quality`, `m.segment_quality`, `m.lead_time_show_up`, `m.replay_caught_up`, then `m.transcript_health`). Each is `{id, version, value, num, den, state, coverage, excluded, livestorm, rows}`. `state` is `ok`, `partial`, `approximation`, `insufficient`, `unresolved` or `unavailable`; `value` is null in the last three, whenever there is nothing to divide, and on the measures that have rows only (`m.livestorm_counts`, `m.room_map`, `m.channel_quality`, `m.segment_quality`, `m.lead_time_show_up`); `num` and `den` are null when `unresolved`. `rows` is a list of objects, each with a `key` unique in the measure; a row's own `state` (on channels, segments and lead-time buckets) is `ok` or `insufficient`, or the measure's state when that hides its value, and a hidden row value is null. On `m.channel_quality` and `m.segment_quality`, `wrote` and `wroteRate` are also null when the chat's or the questions' authors are unresolved; the measure is then partial, never unresolved, and `coverage.chatAuthors` or `coverage.questionAuthors` says so. `excluded` is `{team, guestSpeakers, total, internal}` on an audience-only measure (null when it is `unavailable`) and null on the others; `total` is the n of "excludes your team (n)". Each measure's formula, gates and states: [product.md §13](product.md#measures-v1-report). The page renders these fields and computes nothing |

On an internal session nobody is relabelled: its chat stays team chat, and `record.roster.internal` is true. Its audience-only measures count everyone, with `excluded.internal` true.

## Adding a route

1. Put it in the router module for its area in `livestorm_app/routes/`, with any request body in `routes/models.py`. Routes match in registration order, and the SPA catch-all (`routes/spa.py`) must stay last.
2. Take the identity from `livestorm_app.auth` (`Depends(get_caller)` or `Depends(require_admin)`); session routes read with the caller's scope.
3. Add a row to the table above.
4. Run the tests: `tests/test_access_control.py` picks up every `/api/sessions/{session_id}*` route automatically.
