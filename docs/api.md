# HTTP API

Every route is a FastAPI handler in [`livestorm_app/routes/`](../livestorm_app/routes/), one module per area (`system`, `oauth`, `admin`, `workspace`, `sessions`, `ai`, `spa`), which [`app.py`](../app.py) includes in that order. The frontend calls them through [`frontend/src/api.js`](../frontend/src/api.js).

**Auth column.** *public*: no identity needed. *caller*: `Depends(get_caller)`; 401 without an identity, and session routes are scoped to the caller's organization ([security.md](security.md)). *admin*: `Depends(require_admin)`; 401, or 403 for non-admins. `{id}` must match `^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$` (422 otherwise).

| Route | Method | Auth | Purpose |
|---|---|---|---|
| `/api/health` | GET | public | Render health check: `{"status": "ok"}` |
| `/api/bootstrap` | GET | public | Initial frontend state: OAuth status, connected user, `isAdmin`, local-dev flag. Never returns an API key. |
| `/api/auth/livestorm/start` | GET | public | Start the OAuth flow (sets the handshake cookie, 302 to Livestorm) |
| `/api/auth/livestorm/callback` | GET | public | OAuth callback: check the handshake, exchange the code, fetch `/me`, set the session cookie, then redirect to `/auth/callback?auth=success` with `next`, the page the user connected from when it is a path on this site (`oauth_client.safe_return_path`), or `?auth=error` |
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
| `/api/sessions/{id}/record` | GET | caller | v1 Report only: the session clock and the recording's alignment, the roster header, the Pulse, the transcript's health and the measures ([below](#the-v1-record)). 404 if not cached |
| `/api/sessions/{id}/record/chat` | GET | caller | v1 Report only: the chat's text by evidence id (`C0001`), with each message's session time and team flag, for the counts that open their messages ([below](#the-v1-record)). No author. 404 if not cached |
| `/api/sessions/{id}/fetch` | POST | caller | Session, chat and questions, then start the transcript job ([jobs.md](jobs.md#fetching-a-session)). Also starts the people sync when the session needs one (`rosterSync`, [below](#fetching-a-session)) |
| `/api/sessions/{id}/fetch-base` | POST | caller | Session, chat and questions only. Also starts the people sync when the session needs one (`rosterSync`, [below](#fetching-a-session)) |
| `/api/sessions/{id}/fetch-transcript` | POST | caller | Start (or attach to) the transcript job |
| `/api/sessions/{id}/transcript-job` | GET | caller | Transcript job status and progress |
| `/api/sessions/{id}/transcript.json` | GET | caller | Full raw transcript as a download (the workspace carries only subtitles) |
| `/api/sessions/{id}/speaker-labels` | POST | caller | Save edited speaker labels |
| `/api/sessions/{id}/roster-status` | GET | caller | v1 Report only: the stored roster's header and the latest people sync, with its progress ([below](#the-people-sync)). 404 if not cached |
| `/api/sessions/{id}/roster-sync` | POST | caller | v1 Report only: start reading the session's people from Livestorm in the worker ([jobs.md](jobs.md#people-sync)). Starts nothing for another org's session; 429 over the org's daily call cap |
| `/api/sessions/{id}/session-analysis` | POST | caller | v1 Report only: start the session analysis (the chat labels, for now) in the worker, or join the run in flight; no body ([jobs.md](jobs.md#the-session-analysis-v1)) |
| `/api/sessions/{id}/session-analysis/job` | GET | caller | v1 Report only: the analysis run's status, `{jobId, jobStatus, error?}`; the job id is derived from the session |
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

## Fetching a session

`POST /api/sessions/{id}/fetch-base` and `POST /api/sessions/{id}/fetch` return the classic workspace (`/fetch` adds `transcriptJob` while the transcript isn't cached) plus one key the classic app ignores:

```
rosterSync: {status, started, skipped}
```

It says what the fetch did about the session's people sync ([jobs.md](jobs.md#people-sync)). `started` is true when the fetch queued one (`status` `pending`): the session had no stored roster yet, or was just fetched again from Livestorm. Otherwise `status` is the latest sync's (`pending`, `running`, `completed`, `error`, or `none` before the first), and `skipped` says why nothing started: null (nothing was needed, or a sync is already running), `other_org`, `cooldown`, `error_backoff` (a sync failed less than an hour ago; `POST /roster-sync` retries it) or `daily_cap`. `status` is `unavailable` when the sync could not be started because the database or the job queue failed; the fetch itself still succeeds. It never carries a sync id, a connection id or a person: poll `GET /api/sessions/{id}/roster-status` for the sync.

## The v1 record

`GET /api/sessions/{id}/record` serves the Report. The workspace route (`GET /api/sessions/{id}`), which the pages not rebuilt yet read, doesn't carry it. It is built by `livestorm_app/record/assemble.py` (`api_logic.get_session_record`); rules and formulas are in [product.md §13](product.md#session-clock-v1-report). Apart from the session's own id, it never carries a Livestorm id: no name, email, company, person or message id, message or transcript text, or speaker label. `frontend/tests/fixtures/record-v1.json` is its response for a synthetic session, and `record-v1-crowd.json` for a larger one whose grouped measures show their rows.

```
{sessionId, updatedAt, meta: {clock}, record: {version, roster, pulse, transcriptHealth, metricsVersion, measuresSource, measures}}
```

The record carries no message text: a count of chat messages lists their evidence ids instead (`m.chat_themes` rows, `ids`). `GET /api/sessions/{id}/record/chat` gives their text, for the page to open a count when someone asks (`api_logic.get_session_chat_messages`, built by `record/messages.py`'s `chat_messages`). Every chat message comes in id order, team messages included and marked; the ids are the record's, as both read `record.evidence.audience_events`. `t` is seconds since the session's start, null without a clock. No author: no name, people id or `P-` ref. It is read on demand and not cached.

```
{sessionId, updatedAt, messages: [{id: "C0001", t: 125.5, text, team}]}
```

| Field | What it holds |
|---|---|
| `meta.clock.startedAtUnix` | `started_at` in unix seconds, or null: no clock. Never `estimated_started_at` |
| `meta.clock.endS`, `endSource` | Seconds from the start to the end, and where they came from: `ended_at`, `duration` or `none` |
| `meta.clock.lateStartS` | The late start in seconds ([product.md §13](product.md#session-clock-v1-report)), or null |
| `meta.clock.timezone` | The session's time zone, as Livestorm sends it |
| `meta.session.title` | The event's title (`event_payload.data.attributes.title`), what the page header names the session by, or null when the event wasn't fetched or has none (`record.assemble.session_meta`) |
| `meta.clock.alignment` | Where the recording sits on the session clock ([product.md §13](product.md#alignment-probe-v1-report)): `{version, status, source, offsetS, plusminusS, allows, nAnchors, nAgree, estimate, durations, drift, anchors, reasons}`. `status` is `verified`, `assumed`, `misaligned` or `unavailable` (no transcript or no `started_at`). `offsetS` is the offset applied (`t = r + offsetS`): the agreed one when verified, 0 when assumed, null otherwise; `plusminusS` is set only when verified. `allows` is `{seconds, minutes}`, the gate's answers for findings measured in seconds and in minutes (audience-only views always run): render from it, never from `status`. `estimate` is the range the most anchors agree on, `{offsetS, lo, hi, nAgree}`, whatever the status (null without anchors). `durations`: `{sessionS, audioS, gapS, endSource, durationAttrS, lo, hi}`, where `gapS` is session − recording and `lo`, `hi` the offsets one continuous recording allows. `drift`: `{early, late}`, each half's agreed range when it is bounded, else null. `anchors`: `[{kind, e, u, t, r, lo, hi, agrees, half}]`, plus `burst` (messages) on a wave; `kind` is `link`, `wave`, `read_aloud` or `media_time`, `e` an evidence id (`C0001`, `Q001`), `u` utterance ids, `t` session seconds, `r` recording seconds. `reasons` lists the codes behind the status. Ids and times only: no word, speaker label or Livestorm id |
| `record.version` | `RECORD_VERSION` in `record/assemble.py` (8) |
| `record.roster` | The roster's header, no person: `source` (`paginated` once a people sync saved the session's roster, `session_include` until then: the people cached with the session), `complete` (true, false or null), `coverage` (`complete` or `partial`), `peopleCount`, `recordCount`, `syncedAt` (ISO 8601, null for `session_include`), and `internal`, the classic internal-session rule applied to the roster's people ([product.md §13](product.md#roster-v1-report)). The roster counts a person once and skips people without an id, so on such a list it can differ from the workspace's `meta.internalSession` |
| `record.roster.sync`, `record.roster.refreshDue` | The people sync's state, read on every request and never cached (`people_sync.roster_live_state`). `sync` is `{status, active, reason, error, updatedAt}`: `status` is the latest sync's (`pending`, `running`, `completed`, `error`) or `none`; `active` is true while it is pending or running, when the page polls `GET /roster-status`; `error` is StormIQ's own message. The status is the stored one: a sync whose queue job was lost is closed by `GET /roster-status`, not by this route. `refreshDue` is true when the stored roster's refresh date has passed (the page then calls `POST /roster-sync` once). No sync id |
| `record.pulse.lanes` | The lanes in display order: `audienceChat`, `teamChat`, `questions`, `answers`, then `speakingS` and `words` when speech is shown |
| `record.pulse.minutes` | One row per live minute, from 0, zero-filled: `{minute, audienceChat, teamChat, questions, answers}`, plus `speakingS` (seconds) and `words` (one decimal: an utterance's words are split across minutes) when speech is shown |
| `record.pulse.edges` | `before` (the waiting room) and `after` (after the end): `{counts: {lane: n}, firstT}` and `{counts, lastT}`, in seconds on the session clock. The counts hold the speech lanes too when speech is shown |
| `record.pulse.unplaced` | Items with no session time (no clock, or a time that can't be read) |
| `record.pulse.speech` | `{shown, offsetS, reasons}`. Speech is shown only when there is a transcript and `meta.clock.alignment.allows.minutes` is true (verified or assumed); `offsetS` is the offset it was placed with. Otherwise `shown` is false, `offsetS` null, and `reasons` gives the alignment's reasons (`no_transcript`, `duration_gap`, `drift`…) |
| `record.transcriptHealth` | How far the transcript can be trusted ([product.md §13](product.md#transcript-health-v1-report)): `{version, state, reasons, model, words, coverage, speakers, language, unclearClusters, gates}`. `state` is `ok`, `degraded`, `poor` or `unavailable` (no Gladia transcript), and `reasons` lists the codes behind it (`no_transcript`, `no_speech`, `low_confidence`, `unclear_words`, `no_word_data`, `little_speech`, `no_speakers`, `split_risk`, `language_mismatch`). `words`: `{count, withConfidence, meanConfidence, lowConfidenceShare, source, rebasedUtterances}`. `coverage`: `{speechS, audioS, liveS, shareOfAudio, shareOfLive, firstSpeechR, tailS}`. `speakers`: `{detected, substantial, labelled, substantialLabelled, expected, expectedSource, cap, chunks, splitRisk}`. `language`: `{detected, expected, match, shares}`. `unclearClusters`: `[{t0R, t1R, nLow, u}]`, recording seconds and utterance ids (`U0001`). `gates`: `{pace, speakerShares}`. `words`, `coverage`, `speakers` and `language` are null when `unavailable`; an unknown value is null, never 0. Times are seconds into the recording, not on the session clock |
| `record.metricsVersion` | `METRICS_VERSION` in `record/measures.py` (5): the version of the whole set of measures |
| `record.measuresSource` | `live`: computed from the cached payloads when the record block is built, and cached with it per process. Nothing is stored yet |
| `record.chatLabels` | The stored chat labels' state: `{state, model, labelledAt, messages}`. `state` is `none` (no session analysis has labelled the chat), `ready` (the labels describe this chat, and the measures read them) or `stale` (they were made from an earlier chat or an older label version; nothing reads them, and a new analysis labels the chat again). Part of the cached block: the cache key holds the labels' version, chat hash and time (`db.fetch_chat_labels_stamp`, read on every request), so a new analysis run shows at once |
| `record.measures` | One entry per measure of the registry, by id, in registry order (the vitals `m.roster_coverage`, `m.audience_registrants`, `m.team_count`, `m.audience_attendees`, `m.show_up_rate`, `m.time_in_room`, `m.wrote_rate`, `m.questions_qa`, `m.time_to_answer`, `m.author_resolution`, `m.livestorm_counts`, then the grouped audience measures `m.chat_concentration`, `m.room_map`, `m.channel_quality`, `m.segment_quality`, `m.lead_time_show_up`, `m.replay_caught_up`, then `m.transcript_health` and `m.clock_alignment`, then the measures that read the chat labels, `m.questions_total` and `m.chat_themes`, unavailable until a session analysis labelled this chat). Each is `{id, version, value, num, den, state, coverage, excluded, livestorm, rows, gate}`; `gate` is `{field, minimum, actual}` when a gate hid the value (the first that failed, and what the session had), null otherwise. `state` is `ok`, `partial`, `approximation`, `insufficient`, `unresolved` or `unavailable`; `value` is null in the last three, whenever there is nothing to divide, and on the measures that have rows only (`m.livestorm_counts`, `m.room_map`, `m.channel_quality`, `m.segment_quality`, `m.lead_time_show_up`); `num` and `den` are null when `unresolved`. `rows` is a list of objects, each with a `key` unique in the measure; a row's own `state` (on channels, segments and lead-time buckets) is `ok` or `insufficient`, or the measure's state when that hides its value, and a hidden row value is null. On `m.channel_quality` and `m.segment_quality`, `wrote` and `wroteRate` are also null when the chat's or the questions' authors are unresolved; the measure is then partial, never unresolved, and `coverage.chatAuthors` or `coverage.questionAuthors` says so. `excluded` is `{team, guestSpeakers, total, internal}` on an audience-only measure (null when it is `unavailable`) and null on the others; `total` is the n of "excludes your team (n)". Each measure's formula, gates and states: [product.md §13](product.md#measures-v1-report). The page renders these fields and computes nothing |

On an internal session nobody is relabelled: its chat stays team chat, and `record.roster.internal` is true. Its audience-only measures count everyone, with `excluded.internal` true.

## The people sync

Two routes for the v1 Report's people list, which the worker reads from Livestorm and stores ([jobs.md](jobs.md#people-sync)). A fetch starts the sync by itself ([above](#fetching-a-session)); these routes follow it and retry it. Both check the caller's scope first: a session out of scope is a 404 and starts nothing. Neither returns a person, a connection id or a token. Times are ISO 8601 strings in UTC.

`GET /api/sessions/{id}/roster-status` (`people_sync.read_roster_status`), polled while a sync runs:

```
{sessionId, status, roster, sync, progress}
```

| Field | What it holds |
|---|---|
| `status` | The latest sync's status (`pending`, `running`, `completed`, `error`), or `none` before the first one. A sync whose queue job is gone is closed as `error` on this read |
| `roster` | The stored roster's header, or null before the first: `{version, source, complete, recordCount, peopleCount, fieldsSource, reason, syncedAt, refreshDueAt, refreshedAt}`. `source` is `paginated` for a synced roster; `complete` is true only when the people match Livestorm's `recordCount` |
| `sync` | The latest sync, or null: `{syncId, reason, status, error, calls, recordCount, peopleWritten, createdAt, updatedAt}`. `reason` is `initial` or `manual`; `calls` is the HTTP requests it sent to Livestorm, retries included; `error` is StormIQ's own message, never an upstream URL or body |
| `progress` | While the sync is active, its Redis progress (`{kind: "roster", stage, percent, updated_at_ms, label?, calls?}`; stages `queued`, `waiting`, `people`, `labels`, `saving`), else null |

`POST /api/sessions/{id}/roster-sync` (`people_sync.request_people_sync`) takes no body and returns:

```
{sessionId, status, syncId, started, skipped}
```

`started` is true when a new sync was queued (`status` `pending`). Otherwise `status` and `syncId` are the latest sync's, and `skipped` says why nothing started: null (a sync is already running; it was joined), `other_org` (the session isn't the caller's org's, for example an admin reading another org's session), or `cooldown` (one completed less than 15 minutes ago). Over the org's daily cap of Livestorm calls (`LIVESTORM_SYNC_DAILY_CALLS_PER_ORG`) it answers **429** `{resource: "People sync", message, details: {resource, dailyCallCap, callsLast24h}}`; when the job queue is unavailable, **503** `{resource: "People sync", message, details: {resource}}`. A sync that was queued can still end in `error` without calling Livestorm: when the cap was reached before it ran, or when another sync of the org was already waiting to run ([jobs.md](jobs.md#people-sync)); its `sync.error` says which.

## Adding a route

1. Put it in the router module for its area in `livestorm_app/routes/`, with any request body in `routes/models.py`. Routes match in registration order, and the SPA catch-all (`routes/spa.py`) must stay last.
2. Take the identity from `livestorm_app.auth` (`Depends(get_caller)` or `Depends(require_admin)`); session routes read with the caller's scope.
3. Add a row to the table above.
4. Run the tests: `tests/test_access_control.py` picks up every `/api/sessions/{session_id}*` route automatically.
