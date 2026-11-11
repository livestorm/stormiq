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
| `/api/event-sessions` | POST | caller | One page (50) of an event's finished sessions (`filter[status]=past`), newest first: `{eventId, options, total, next}`. `options` are `{id, label, name, startedAt, attendees}` (`startedAt` in unix seconds and `attendees` null when unknown); `total` is Livestorm's count of the event's finished sessions (or null); `next` is the cursor `{page, step, pages}` to send back as `cursor` for the following page, or null. Livestorm can't sort them, so the first call also reads the last page and starts from the end with the newest sessions (`api_logic.fetch_event_sessions`) |
| `/api/sessions/{id}/cover.png` | GET | caller | Cover image (`Cache-Control: private, max-age=86400`) |
| `/api/sessions/{id}` | GET | caller | Cached workspace (404 if not cached) |
| `/api/sessions/{id}/cached` | GET | caller | Same, but 204 instead of 404 when missing |
| `/api/sessions/{id}/record` | GET | caller | v1 Report only: the session clock and the recording's alignment, the roster header, the Pulse, the transcript's health and the measures ([below](#the-v1-record)). 404 if not cached |
| `/api/sessions/{id}/record/chat` | GET | caller | v1 Report only: the chat's text by evidence id (`C0001`), with each message's session time and team flag, for the counts that open their messages ([below](#the-v1-record)). No author. 404 if not cached |
| `/api/sessions/{id}/record/figures` | GET | caller | Before you share: the figures said on stage to check, with the host's answers ([below](#before-you-share)). No model call. 404 if not cached |
| `/api/sessions/{id}/record/figures` | PUT | caller | Answer one figure check: `{key, choice}` (`choice` one of the check's `options[].value`, or null to reopen it). Returns the checks. 404 if not cached, 409 for an unknown check or answer, 422 for a malformed body |
| `/api/sessions/{id}/record/findings` | GET | caller | The Report's findings (`record/findings.py`): `{sessionId, updatedAt, state, version, found: [{id, state, confidence, strength, values}], nothing: [{id, values}], notChecked: [{id, reason}]}`; `state` is the session reading's (`ready`, `stale`, `none`). Values only, never a sentence or a name ([product.md](product.md#session-clock-v1-report)). No model call. 404 if not cached |
| `/api/sessions/{id}/record/voices` | GET | caller | The Report's "Who spoke up" (`record/voices.py`): `{sessionId, updatedAt, version, internal, total: {people, messages, questions}, people: [{name, company, messages, questions, share, sample: {id, t, text}}], others: {people, messages, questions}}`. The audience members who wrote in the chat or asked in the Q&A, the most active first (8 listed, the rest summed in `others`); the team left out except on an internal session. Short names only through `roster.display_people` (null when a person can't be named), never an email, people id or `P-` ref; `sample` is their longest message without a link. No model call; read on demand, not cached. 404 if not cached |
| `/api/sessions/{id}/record/moments` | GET | caller | The Pulse's key moments, told (`record/moments.py`): `{sessionId, updatedAt, version, state, usualPerMinute, surges, speech, moments: [{n, start, end, kind, messages, people, questions, peak, prompt: {t, quote}, said: {t, speakers, text, precise}, written: [{id, t, text, author}], asked: [{id, t, text, author}], themes}]}`. `state`: `ready`, `few_messages` or `no_clock`; `surges` false when the moments are only the busiest minutes; `kind`: `reply`, `content`, `social`, `tech` or `unknown` (no labels). `said` only when the alignment allows minutes (`precise` when it allows seconds); authors as short names through `roster.display_people` (null when a person can't be named), speakers as the user saved them. No model call; read on demand, not cached. 404 if not cached |
| `/api/sessions/{id}/record/chapters` | GET | caller | The chapters the session analysis found and their show notes ([below](#chapters)). No model call. 404 if not cached |
| `/api/sessions/{id}/record/transcript` | GET | caller | The Transcript tab's room view: the speech, the chat, the questions and the written answers as text by id, with speakers' names and authors' short names ([below](#the-v1-record)). 404 if not cached |
| `/api/sessions/{id}/record/questions` | GET | caller | The Follow-up tab's question desk: every audience question with its status and what the host saved ([below](#the-question-desk)). 404 if not cached |
| `/api/sessions/{id}/record/questions` | PUT | caller | Mark one desk question answered (or not), with an optional reply: `{questionId, answered, reply}` (`questionId` like `Q001` or `C0001`, `reply` up to 4,000 characters). Returns the desk. 404 if not cached or not a desk question, 409 if the question has no Livestorm id, 422 for a malformed body |
| `/api/sessions/{id}/record/questions/located` | PUT | caller | The host's decision on an answer located in the talk: `{questionId, decision}`, `decision` `confirm` (marks it answered, noting it was located) or `reject` ("Not this": never suggested again). Returns the desk. 404 if not cached or not a desk question, 409 if the question has no Livestorm id or no located answer to decide on (none, stale, already decided, or `confirm` on a question already answered aloud), 422 for a malformed body |
| `/api/sessions/{id}/record/questions.csv` | GET | caller | The desk's questions as a CSV download, with who asked each one (name and company, on every session; never an email). `?scope=` `open` (default: the questions still open), `all` (with a Status column) or `answers` (with Status and Answer, for a follow-up email); another scope is a 422. `Cache-Control: private, no-store`. 404 if not cached |
| `/api/sessions/{id}/fetch` | POST | caller | Session, chat and questions, then start the transcript job ([jobs.md](jobs.md#fetching-a-session)). Also starts the people sync when the session needs one (`rosterSync`, [below](#fetching-a-session)) |
| `/api/sessions/{id}/fetch-base` | POST | caller | Session, chat and questions only. Also starts the people sync when the session needs one (`rosterSync`, [below](#fetching-a-session)) |
| `/api/sessions/{id}/fetch-transcript` | POST | caller | Start (or attach to) the transcript job. `analyse: true` ("Add") chains the session analysis after it ([jobs.md](jobs.md#the-session-analysis-v1)) |
| `/api/sessions/{id}/transcript-job` | GET | caller | Transcript job status and progress |
| `/api/sessions/{id}/transcript.json` | GET | caller | Full raw transcript as a download (the workspace carries only subtitles) |
| `/api/sessions/{id}/speaker-labels` | POST | caller | Save edited speaker labels |
| `/api/sessions/{id}/roster-status` | GET | caller | v1 Report only: the stored roster's header and the latest people sync, with its progress ([below](#the-people-sync)). 404 if not cached |
| `/api/sessions/{id}/roster-sync` | POST | caller | v1 Report only: start reading the session's people from Livestorm in the worker ([jobs.md](jobs.md#people-sync)). Starts nothing for another org's session; 429 over the org's daily call cap |
| `/api/sessions/{id}/session-analysis` | POST | caller | v1 Report only: start the session analysis (the chat labels, for now) in the worker, or join the run in flight; no body ([jobs.md](jobs.md#the-session-analysis-v1)) |
| `/api/sessions/{id}/session-analysis/job` | GET | caller | v1 Report only: the analysis run's status, `{jobId, jobStatus, error?, progress}`; the job id is derived from the session. `progress` is the run's latest stage from Redis, `{kind, stage, percent, started_at_ms, updated_at_ms}` or null ([jobs.md](jobs.md#the-session-analysis-v1)) |
| `/api/sessions/{id}/analysis` | POST | caller | Start the overall analysis (English or French) |
| `/api/sessions/{id}/faq` | POST | caller | Start the FAQ (English or French), from the question desk ([ai-outputs.md](ai-outputs.md#faq)); poll `GET …/faq/job?jobId=&language=` |
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

`GET /api/sessions/{id}/record/transcript` is the Transcript tab's room view (`api_logic.get_session_transcript_view`, built by `record/transcript_view.py`'s `transcript_view`), read on demand and not cached:

- `clock`: `interleave` is true only when the alignment allows findings to the second (`alignment_allows(…, "seconds")`); then `offsetS` is the offset applied. Otherwise speech has no session time and the page shows speech and the room apart.
- `speakers`: `{key, name, named}` in order of first appearance. `name` is the one saved with "Confirm names", else "Speaker 1", "Speaker 2"…; `named` says whether it was saved.
- `speech`: one line per utterance, `{id, r, rEnd, t, speaker, text, unclear}`. `id` is the utterance's `U` id, `r` and `rEnd` are seconds into the recording, and `t` is `r + offsetS` on the session clock or null. `unclear` lists the character spans of the words below 0.6 confidence, found in the text in order (a word not found is left out).
- `chapters`: `{state, items: [{title, kind, first, r, t}]}`, the chapters the session analysis found ([below](#chapters)), each starting at a speech line (`first`, a `U` id) with that line's `r` and `t` (`t` null when speech is off the clock). A chapter whose first line isn't in `speech` is left out.
- `room`: the chat, the questions and the written answers in session-time order, `{id, kind, t, text, author, team}`, plus `answered` on a question (a written answer replies to it), `replyTo` on an answer, and `asks: true` on a chat message the session analysis labelled a question (about the content or logistics; only while the stored labels describe this chat, and never on the team's own messages except on an internal session). `author` is a short name, "Priya S.", only through `roster.display_people`, which fails closed: an author who doesn't resolve gets "Team" (the message's own team or guest-speaker flag) or "Attendee". Internal sessions are named too (the owner's decision, November 2026). Never an email address, a people id or a `P-` ref. Nothing says a question is open: an answer given out loud leaves no trace.

```
{sessionId, updatedAt, messages: [{id: "C0001", t: 125.5, text, team}]}
```

### Before you share

`GET /api/sessions/{id}/record/figures` (`api_logic.get_session_figure_checks`, built by `record/figure_checks.py`'s `figure_checks`) turns the figures the understanding job flagged into questions, while the stored reading is current:

```
{sessionId, updatedAt, state, unchecked, checks: [{key, kind, question, moments: [{utterance, t, quote}], options: [{value, label}], choice}]}
```

- `kind` `acronym`: a confusable acronym (NPS/NRR, ARR/MRR, CSAT/CSM) in words the transcript is unsure of, one check per figure: "Was it NRR or NPS?", options the two acronyms and `neither`.
- `kind` `two_values`: the same metric said with two values, one check per metric, its moments every time it was said: options each value, `both` (they measure different things) and `neither`.
- `key` names a check by what was said (`acronym:<U id>:<span>`, `values:<metric>`), never by a figure's id, which is renumbered on every reading. The host's answer is saved under it in `session_figure_checks` (`db.save_figure_check`); `choice` is the saved value while it is still one of the options, else null. `unchecked` counts the open checks.
- `state`: the understanding's (`ready`, `none`, `stale`); no checks unless `ready`.
- The Session analysis's brief notes each flagged figure with the host's answer, or "not checked by the host: do not state it as a fact" (`figure_checks.figure_notes`).

### Chapters

`GET /api/sessions/{id}/record/chapters` (`api_logic.get_session_chapters`) reads the chapters the understanding job stored (`record.understanding.chapter_list`), read on demand and not cached:

```
{sessionId, updatedAt, state, placed, chapters: [{title, kind, first, last, r, rEnd, t?, tEnd?, audienceChat?, questions?, chatPerMinute?}], showNotes: {text, youtube}}
```

- `state`: `ready`, `none` (never read, or no transcript) or `stale` (read from the session as it was: no chapters). Chapters are used only while the stored reading came from the session's dossier as it is now, as the question desk's located answers are ([ai-outputs.md](ai-outputs.md#the-understanding-v1)).
- `chapters` in order, without overlap: `kind` is `opening`, `content`, `demo`, `qa`, `close` or `other`; `r` is the first utterance's start and `rEnd` the last one's end, in seconds on the recording's clock (the replay's), so they need no alignment.
- `placed` (`record.assemble.chapter_marks`): true when the chapters could be put on the session clock, where the Pulse shows speech (the alignment allows findings in minutes). Then each chapter also has `t` and `tEnd` (its spoken span, seconds on the session clock, held inside the live session), `audienceChat` and `questions` (the audience's live chat messages and questions from its start to the next chapter's start) and `chatPerMinute` (null for a span under 2 minutes) for the Report's cover chart ([product.md](product.md#session-clock-v1-report)). The Report also reads `state` to offer "Analyse session" again when the chat is labelled but the session isn't read.
- `showNotes` (`record.understanding.show_notes`): `text` has one line per chapter, "04:12 Title", on the recording's clock, the first at 00:00, hours shown when the replay runs past one; `youtube` is true when YouTube would read them as chapters (at least three, each at least ten seconds long).

### The question desk

`GET /api/sessions/{id}/record/questions` (`api_logic.get_session_question_desk`, built by `record/question_desk.py`'s `question_desk`) is read on demand and not cached:

```
{sessionId, updatedAt, version: 1,
 summary: {total, written, aloud, here, covered, open, logistics, chatQuestions, located},
 questions: [{id: "Q001", source: "qa", t, text, author, status, answer, reply, located, canSave}]}
```

- `questions`: the audience's Q&A questions (`source: "qa"`) and the chat messages the session analysis labelled questions about the content (`source: "chat"`), in session-time order. A team member's question is left out, except on an internal session, where everyone is audience. `id` is the record's evidence id; `t` seconds since the start, or null.
- `status`, first match wins: `written` (answered in writing in Livestorm; `answer` holds the text), `aloud` (marked answered aloud in Livestorm), `here` (the host marked it answered in StormIQ; `reply` holds what they wrote, if anything), `covered` (not marked anywhere, but the understanding job located a passage of the talk that answers it, and the host hasn't decided on it), `open`. `canSave` is false for a question without a Livestorm id; such a question is never `covered`.
- `located`: the passage, `{t, quote}` (`t` on the session clock; `quote` the model's words, checked word for word against the transcript), on a `covered` question, on an `aloud` one (where in the talk) and on a `here` one the host confirmed; null otherwise, and always null once the host said "Not this". Answers come from `session_understanding` through `record.understanding.located_answers`, only while it was read from this session's dossier as it is now (same hash and version): question ids shift when the session is fetched again or its chat labelled again.
- `summary`: the count per status, `logistics` (chat questions about the replay or the slides, counted and not listed) and `chatQuestions`, the labels' state: `ready`, `none` (not analysed yet: no chat rows) or `stale` (labels made for another chat: no chat rows), and `located`, the understanding's: `ready`, `none` (never read, or no transcript) or `stale` (read from the session as it was: no located answers).
- `author` as on the Transcript tab: a short name only through `roster.display_people` (fails closed), else "Team" or "Attendee".

`PUT` saves `{answered, reply}` for one question, keyed by its source and Livestorm id (`db.save_question_reply`), so it stays on the right question when the session is fetched again; the reply is trimmed and an empty one is stored as none. `PUT …/questions/located` saves the decision on the same row (`db.save_located_decision`): `confirm` sets `answered` and keeps a reply already written; `reject` changes nothing else. Unmarking a confirmed question keeps the decision, so it isn't suggested again. `GET …/questions.csv` (`record.question_desk.questions_csv`) has three scopes. `open` (the default) lists the `open` and `covered` questions (a located answer isn't an answer until the host confirms it); `all` lists every question with a `Status` column (Answered in writing, Answered aloud, Answered here, Covered in the talk, not marked, Still open); `answers` adds an `Answer` column: Livestorm's written answer, the host's reply, and the passage of the talk ("In the talk at 5:19: …", "Not confirmed. …" for a covered question). The base columns are `Question, Asked by, Company, Where, When`: the asker's full name and company through `roster.export_people`, on every session, internal ones included (the owner's decision), never an email (StormIQ stores none). Cells that start like a formula get a leading `'`, as `src/utils/csv.js` does, and the file starts with a UTF-8 BOM for Excel.

| Field | What it holds |
|---|---|
| `meta.clock.startedAtUnix` | `started_at` in unix seconds, or null: no clock. Never `estimated_started_at` |
| `meta.clock.endS`, `endSource` | Seconds from the start to the end, and where they came from: `ended_at`, `duration` or `none` |
| `meta.clock.lateStartS` | The late start in seconds ([product.md §13](product.md#session-clock-v1-report)), or null |
| `meta.clock.timezone` | The session's time zone, as Livestorm sends it |
| `meta.session.title` | The event's title (`event_payload.data.attributes.title`), what the page header names the session by, or null when the event wasn't fetched or has none (`record.assemble.session_meta`) |
| `meta.clock.alignment` | Where the recording sits on the session clock ([product.md §13](product.md#alignment-probe-v1-report)): `{version, status, source, offsetS, plusminusS, allows, nAnchors, nAgree, estimate, durations, drift, anchors, reasons}`. `status` is `verified`, `assumed`, `misaligned` or `unavailable` (no transcript or no `started_at`). `offsetS` is the offset applied (`t = r + offsetS`): the agreed one when verified, 0 when assumed, null otherwise; `plusminusS` is set only when verified. `allows` is `{seconds, minutes}`, the gate's answers for findings measured in seconds and in minutes (audience-only views always run): render from it, never from `status`. `estimate` is the range the most anchors agree on, `{offsetS, lo, hi, nAgree}`, whatever the status (null without anchors). `durations`: `{sessionS, audioS, gapS, endSource, durationAttrS, lo, hi}`, where `gapS` is session − recording and `lo`, `hi` the offsets one continuous recording allows. `drift`: `{early, late}`, each half's agreed range when it is bounded, else null. `anchors`: `[{kind, e, u, t, r, lo, hi, agrees, half}]`, plus `burst` (messages) on a wave; `kind` is `link`, `wave`, `read_aloud` or `media_time`, `e` an evidence id (`C0001`, `Q001`), `u` utterance ids, `t` session seconds, `r` recording seconds. `reasons` lists the codes behind the status. Ids and times only: no word, speaker label or Livestorm id |
| `record.version` | `RECORD_VERSION` in `record/assemble.py` (10) |
| `record.roster` | The roster's header, no person: `source` (`paginated` once a people sync saved the session's roster, `session_include` until then: the people cached with the session), `complete` (true, false or null), `coverage` (`complete` or `partial`), `peopleCount`, `recordCount`, `syncedAt` (ISO 8601, null for `session_include`), and `internal`, the classic internal-session rule applied to the roster's people ([product.md §13](product.md#roster-v1-report)). The roster counts a person once and skips people without an id, so on such a list it can differ from the workspace's `meta.internalSession` |
| `record.roster.sync`, `record.roster.refreshDue` | The people sync's state, read on every request and never cached (`people_sync.roster_live_state`). `sync` is `{status, active, reason, error, updatedAt}`: `status` is the latest sync's (`pending`, `running`, `completed`, `error`) or `none`; `active` is true while it is pending or running, when the page polls `GET /roster-status`; `error` is StormIQ's own message. The status is the stored one: a sync whose queue job was lost is closed by `GET /roster-status`, not by this route. `refreshDue` is true when the stored roster's refresh date has passed (the page then calls `POST /roster-sync` once). No sync id |
| `record.pulse.lanes` | The lanes in display order: `audienceChat`, `teamChat`, `questions`, `answers`, then `speakingS` and `words` when speech is shown. On an internal session everyone is audience, as the measures count it, so its chat is all `audienceChat` (`assemble.pulse_events`) |
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
