# Jobs and processing flows

How a session is fetched, transcribed and analysed. Slow or paid work (transcription, the people sync, AI generation, Surprise me's voice) runs only in the arq worker, and Surprise me's video only in the render worker, never inside a web request; the web process doesn't even import the Gladia client. Code: [`api_logic.py`](../livestorm_app/api_logic.py) and [`people_sync.py`](../livestorm_app/people_sync.py) (web side), [`queue.py`](../livestorm_app/queue.py), [`worker.py`](../livestorm_app/worker.py), [`progress.py`](../livestorm_app/progress.py).

## Fetching a session

The frontend calls `POST /api/sessions/{id}/fetch-base`, then `POST /api/sessions/{id}/fetch-transcript`. (`POST /fetch` does both in one call. It returns the base workspace plus `transcriptJob: {jobId, jobStatus}`, or the full workspace when the transcript is already cached.)

1. If `session_cache` already has the session and `force_refresh` is false, return it.
2. Otherwise fetch the session, its event, chat and questions from the Livestorm API and upsert them into `session_cache`.
3. Either way, start the session's people sync in the worker when it has no stored roster yet, or was just fetched again ([People sync](#people-sync)). The response's `rosterSync` says what happened, and a failure here never fails the fetch.
4. Start the transcript job (below) and return `{jobId, jobStatus}` at once.
5. The frontend polls `GET /api/sessions/{id}/transcript-job` every 6 s. Once the job completes, the poll returns the full workspace.

## Transcription

`POST /api/sessions/{id}/fetch-transcript` runs `api_logic.fetch_session_transcript_data`:

1. If `transcript_payload` is already cached (and not `force_refresh`), return the workspace.
2. If the session has an active job (`transcript_jobs.status` is `pending` or `running`), attach to it, even with `force_refresh`. Before attaching, ask arq whether the job is still alive. If arq reports it failed, or has no trace of it 2 minutes after the row was last updated (Redis restart, deploy), the row is marked `error` and a new job starts.
3. Otherwise insert a `transcript_jobs` row (with the caller's connection id) and enqueue `run_transcription` with the arq id `transcription:{job_id}`. A partial unique index allows one active row per session; a request that loses the insert race attaches to the winner's job.

The worker, in `worker.run_transcription(job_id, session_id)`:

1. **Claims the row.** One UPDATE moves it from `pending` to `running` and reads and clears `connection_id`. A row that is no longer `pending` (swept, or closed as lost) is skipped, so no paid work starts.
   Then it **sets the speaker cap** (`_transcription_options`): light reads of the session's people give the people on stage (`record/inputs.count_people_on_stage`), from the stored roster when a people sync saved one (`db.fetch_session_roster`) and from the cached `session_payload` otherwise, as the v1 record does, and Gladia's `max_speakers` follows ([providers.md](providers.md#gladia-configuration)). An unknown count, or any failure here, keeps the default cap of 20; it never fails the job. The job's arguments stay `(job_id, session_id)`.
2. **Waits for a slot.** At most `MAX_CONCURRENT_TRANSCRIPTIONS` (2) run per worker, because download and ffmpeg are memory-heavy. While it waits, progress is refreshed every minute so the sweeper leaves the row alone.
3. **Resolves credentials.** `GLADIA_KEY` comes from the worker's env; the Livestorm token from the claimed connection id, refreshed if close to expiry (`auth.livestorm_auth_for_job`).
4. **Transcribes** in a thread (`asyncio.to_thread`): download the recording, upload it to Gladia, poll. Progress goes to `transcript_jobs.progress` and to Redis. While Gladia works, the transcriber reports progress on every status change and at least once a minute (`PROGRESS_HEARTBEAT_SECONDS`), which keeps `updated_at` fresh.
5. **Saves** `session_cache.transcript_payload`, with the people-on-stage count noted in its `stormiq` block, marks the job `completed` and clears progress. If saving fails, the job is marked `error`, never `completed`.
6. **On any exception, timeout or cancellation** (worker shutdown), sets `status='error'` with a message and clears progress. A cancelled transcription also signals its thread, which stops at its next progress report.

Each transcript poll also reconciles the row with arq (as in step 2), so a job that died in the queue shows as an error within minutes. The poll response carries `progress` (the raw Gladia step) and `progressRedis` (stage-floor progress with a percent).

**Sweeper.** Every 10 minutes, a cron job marks `pending` or `running` rows that have been silent for an hour as `error`. A job that finishes after being swept still writes `completed`.

## People sync

The v1 roster's complete people list comes from Livestorm's paginated people endpoint: 50 people per page, so about 100 calls for 5,000 registrants ([providers.md](providers.md#livestorm-people)). It runs only in the worker, as the job `run_people_sync`, and is saved whole as the session's roster ([data-model.md](data-model.md#stored-people)). The v1 record reads the stored roster once a sync saved one, and the people cached with the session (`include=people`) until then ([architecture.md](architecture.md#reading-the-v1-record)).

**Starting one.** Two things start a sync, and both go through `people_sync.start_people_sync` and its guards:

- **A fetch, automatically.** `POST /api/sessions/{id}/fetch-base` and `/fetch` end with `people_sync.start_sync_on_fetch` (called by `api_logic.fetch_session_base_data`), once the caller's own Livestorm credentials returned the session or the cache answered within their scope. It starts the session's first sync when it has no stored roster yet (`reason` `initial`), and reads the people again after a fresh fetch of a session that has one (`reason` `manual`: the user refreshed the session). A cache hit on a session with a roster starts nothing. These starts are *automatic*: the error backoff below applies to them. The response's `rosterSync` says what happened ([api.md](api.md#fetching-a-session)).
  The start is best-effort. The session's data is already saved when it runs, so a database or Redis failure there never fails the fetch: it is logged with the session id and the error's class only, and `rosterSync.status` reads `unavailable`.
  The classic app opens a cached session with `GET /api/sessions/{id}/cached`, which starts nothing, so in practice a session gets its first sync when it is first fetched; sessions cached before this get theirs from the v1 Report (`POST /roster-sync`) or their next fetch.
- **`POST /api/sessions/{id}/roster-sync`, explicitly** (`people_sync.request_people_sync`): a retry after an error, or a refresh. It checks the caller's scope first (`fetch_session_fields`, which also gives the session's stamped org; 404 outside it).

`start_people_sync` applies these guards in order. A guard that stops the sync starts nothing and answers with the latest status and why (`skipped`):

1. **Same org only.** The session's stamped org must equal the caller's org (`other_org` otherwise): an admin reading another org's session never spends their own token on it. The one exception is local dev with the operator's `LS_API_KEY`.
2. **One sync at a time per session.** An active sync (`pending` or `running`) is first checked against arq, as transcriptions are: a job arq reports failed, or has no trace of 2 minutes after the row's last update, is closed as `error`. A sync still active is joined, not duplicated.
3. **Cooldown.** A sync that completed less than `PEOPLE_SYNC_COOLDOWN_MINUTES` (15) ago starts nothing (`cooldown`).
4. **Error backoff.** An automatic start (a fetch) doesn't retry within an hour of a failed sync (`error_backoff`); an explicit `POST /roster-sync` does. A sync the worker refused before any Livestorm call (the cap reached, or another sync of the org already waiting, steps 2 and 3 below) is a failed sync here too: its row is `error`, so a fetch leaves it alone for the hour and `POST /roster-sync` retries it at once. That is deliberate: the hour protects the org's Livestorm quota from repeated opens, and no new status is needed for it.
5. **Daily cap.** `LIVESTORM_SYNC_DAILY_CALLS_PER_ORG` (default 1,000) caps the Livestorm calls an org's syncs recorded in a rolling 24 hours (`db.livestorm_sync_calls_since`, which sums each sync's calls as recorded when it ends). Every sync counts except a session's first `initial` sync, which is never capped. At or over the cap, `POST /roster-sync` answers 429 ([api.md](api.md#the-people-sync)). Syncs still running count for nothing here, so syncs queued together all pass this check; the worker checks the cap again before any call (its step 3 below).

Then it inserts a `livestorm_sync_jobs` row with the caller's connection id (`reason` `initial` while the session has no roster, `manual` after; a request that loses the insert race joins the winner's row) and enqueues `run_people_sync(sync_id, session_id)` with the arq id `livestorm_sync:{sync_id}` (`queue.livestorm_sync_job_id`). If the enqueue fails, the row is marked `error` at once, which clears its connection id; `POST /roster-sync` then answers 503, and a fetch answers as usual with `rosterSync.status` `unavailable`.

The worker, in `worker.run_people_sync(sync_id, session_id)`:

1. **Claims the row** (`db.claim_livestorm_sync`), which moves it to `running` and hands the connection id over once, clearing it in the same statement. A row that is no longer `pending` (swept, or closed as lost) is skipped with no Livestorm call.
2. **Waits for the org's lock.** At most one people sync per org runs at a time: a Redis lock, `stormiq:livestorm-sync:{org}`, keyed by the session's stamped org. While another sync holds it, the job waits; every minute it refreshes its row's `updated_at`, so the sweeper leaves it alone, and publishes a `waiting` progress. A row closed meanwhile (swept) stops the wait. The wait counts toward the job's timeout. A waiting job holds one of the worker's job slots (arq's `max_jobs`, 10, shared by every org's jobs), so only one sync per org may wait: it holds `stormiq:livestorm-sync-wait:{org}`, and a sync that finds both keys held fails at once, with no Livestorm call, and says another sync of the org is already waiting. One org therefore holds two slots at most. Both keys live 20 minutes, longer than the timeout, so a killed worker's keys free themselves, and only their owner releases them (a token-checked delete in `finally`).
3. **Checks the daily cap again** (`people_sync.check_daily_cap`). Under the org's lock every earlier sync of the org has recorded its calls, so the sum is exact: at or over the cap, the sync fails with no Livestorm call. A sync that starts under the cap runs to its end, so the org can pass the cap by that one sync's calls. A session's first `initial` sync is exempt here too (`db.livestorm_sync_is_first_initial`).
4. **Resolves credentials** (`people_sync._resolve_sync_credentials`), after the wait so the token is fresh. The connection is read from the database and its org must equal the session's stamped org; otherwise the sync fails with no Livestorm call, not even a token refresh. Only then is the token resolved, and refreshed if close to expiry (`auth.livestorm_auth_for_job`).
5. **Reads and saves** in a thread (`people_sync.run_people_sync`): every page of people (`client.fetch_session_people`), then the workspace's field labels (`client.fetch_people_attributes`, reused from `org_people_attributes` when read less than 24 hours ago; a 401 or 403 is stored as `forbidden` and the fields are labelled from the event's own fields), then the field definitions and the `P-` refs, then `db.replace_session_roster` (`source='paginated'`, complete only when the people match Livestorm's `record_count`). A first roster is due its refresh 7 days after the session ends (`refresh_due_at`; none when that is already past, since this sync then is the refresh); later syncs keep the stored value. The pager stops itself 30 seconds before the job's time limit.
6. **Finishes.** The row becomes `completed` with its counts: `pages_fetched` (every HTTP request the sync sent to Livestorm and got an answer to, people and field labels, the HTTP session's retries included: `client.requests_sent`), `record_count` and `people_written`. The arq result is counts only: `{status, calls, people, recordCount}`.
7. **On any failure** (a page, the deadline, a list that never reaches its last page, the database), it raises before the roster is written, so the previous roster stays, and marks the row `error` with a message of our own (never an upstream URL or body) and the calls made so far. The job then fails with `people_sync.PeopleSyncFailed`, which holds that message only: arq pickles a failed job's exception into Redis, and a `requests` exception carries the user's token and the upstream body. On a timeout or a worker shutdown the row is marked `error` too, and the sync's thread stops at its next page and never writes. Nothing is retried; the user retries with `POST /roster-sync`.

`GET /api/sessions/{id}/roster-status` (`people_sync.read_roster_status`) is the poll: the roster header and the latest sync, with the Redis progress while it runs. It closes a lost sync the same way as the second guard above. The stuck-job sweeper also covers `livestorm_sync_jobs`: a `pending` or `running` row silent for an hour becomes `error`, with its connection id cleared (`db.sweep_stuck_livestorm_syncs`).

## AI generation

Four flows, each a queue-backed job:

| Start | Poll | Worker job | One job per |
|---|---|---|---|
| `POST /api/sessions/{id}/analysis` | `GET …/analysis/job?language=` | `run_overall_analysis_job` | language |
| `POST /api/sessions/{id}/faq` | `GET …/faq/job?language=` | `run_faq_job` | language |
| `POST /api/sessions/{id}/decisions` | `GET …/decisions/job?language=` | `run_decisions_job` | language |
| `POST /api/sessions/{id}/team-digest` | `GET …/team-digest/job?language=` | `run_team_digest_job` | language |
| `POST /api/sessions/{id}/attendee-followup` | `GET …/attendee-followup/job?language=` | `run_attendee_followup_job` | language |
| `POST /api/sessions/{id}/no-show-followup` | `GET …/no-show-followup/job?language=` | `run_no_show_followup_job` | language |
| `POST …/deep-analysis` | `GET …/deep-analysis/job?language=` | `run_deep_analysis_job` | language |
| `POST …/smart-recap` | `GET …/smart-recap/job?tone=` | `run_smart_recap_job` | tone |
| `POST …/content-repurposing` | `GET …/content-repurposing/job?language=` | `run_content_repurposing_job` | language |

**Validation.** `outputLanguage` must be `English` or `French`, and `tone` one of `professional`, `hype`, `surprise` (422 otherwise): every distinct value would be a separate paid job.

**A POST returns** either the full workspace (the output is already cached for that language or tone) or `{jobId, jobKind, jobStatus: 'pending'|'running', language|tone}` (a job was enqueued, or the same job is already running).

**Dedupe.** The arq job id is deterministic: `{kind}:{session_id}:{language|tone}` (`queue.ai_job_id`). arq refuses a second job with that id while one is queued or running, so double-clicks and teammates generating the same output share one job. arq keeps a finished job's result for an hour under that id; `queue.enqueue_unique_job_sync` clears it when the user asks again (for example, a retry after an error), so a failure never blocks a retry.

**A poll returns** either the finished output, or `{jobId, jobKind, jobStatus, progress, error?}`, where `jobStatus` is one of `pending`, `running`, `completed`, `error`, `not_found`, or `unknown` when Redis is unreachable (the frontend keeps polling), and `progress` is the Redis stage-floor payload (or null). The frontend sends `outputsOnly=true`, so a finished poll returns only `{sessionId, outputsOnly: true, outputs}` to merge into the workspace on screen; without it, the full workspace. Polls derive the job id server-side and ignore any `jobId` parameter.

**The worker job** reads the active provider's key from its env, publishes stage-floor progress to Redis (`queued` → `loading_sources` → `building_prompt` → `analyzing`), runs the synchronous runner (`api_logic.run_overall_analysis` and the like) in `asyncio.to_thread`, and clears progress when it finishes or fails. Pollers read the durable cache first, so no "done" marker has to linger. Only complete output is saved (see [ai-outputs.md](ai-outputs.md)).

The runner picks its model by job kind (`llm_client.get_llm_model`): `overall_analysis`, `faq`, `decisions`, `team_digest`, `attendee_followup`, `no_show_followup`, `deep_analysis`, `smart_recap`, `content_repurposing`, or `translation` for a French version ([providers.md](providers.md#which-model-a-job-uses)).

**Refusals.** When the model declines, the job fails after that one call with "The model declined this session. Nothing was saved.", which the poll returns as its `error`. It names no category: that stays in the call's `llm_usage` row (`refusal_category`). arq stores the raised exception, and `llm_client.LLMRefused` is rebuilt from its category when the poll reads it back. Nothing is retried, the content-bundle attempts included ([ai-outputs.md](ai-outputs.md#saving-rules)).

**Provider errors.** When the Anthropic call itself fails (an HTTP error, a timeout, a lost connection, after the retries in [providers.md](providers.md#timeouts-and-retries)), the job fails with an `llm_client.LLMProviderError` that names the HTTP status and error type: "The Anthropic call failed (HTTP 529 overloaded_error). Nothing was saved. Please try again." The poll returns it as its `error`. arq could not store the SDK's own errors, so the poll would have shown "unable to deserialize job result".

**Usage.** The runner also builds a `CallContext` from the cached row: the job kind, the session, its org and the arq job id (`api_logic._llm_call_context`). Every text-model call the job makes (`llm_client.call_llm`), a truncation retry, a content-bundle retry or a refused call included, records one `llm_usage` row and one log line ([data-model.md](data-model.md#llm_usage)). If that row can't be saved, the job logs it at ERROR and still saves its output ([data-model.md](data-model.md#caching-rules)).

**Daily budget.** A text job that runs without a user asking will be held to its org's daily budget (`ORG_DAILY_LLM_BUDGET_USD`), checked in the worker before each send, the truncation retry included. No text job runs automatically yet, and the four flows above are started by users, so the budget never applies to them ([providers.md](providers.md#usage-and-the-daily-budget)). The cover image job below does run on its own, but it is outside both usage logging and the budget.

Smart Recap is on demand only: nothing generates it automatically.

### The session analysis (v1)

The v1 Report's analysis, started by a user's action: "Analyse session" on the Report, or "Add" on the Add session page, which chains it after the transcript. `fetch-transcript` with `analyse: true` queues it at once when the transcript is already there, else marks the transcript job (`transcript_jobs.then_analyse`); when that job ends, completed or failed (no recording: the chat is still analysed), `worker._chain_analysis_after` queues the analysis under its usual job id. It never runs on a webhook, a schedule or a page view. One run, two steps: it labels the chat, then reads the session (the understanding job) when it has a transcript.

| Start | Poll | Worker job | One job per |
|---|---|---|---|
| `POST /api/sessions/{id}/session-analysis` | `GET …/session-analysis/job` | `run_session_analysis_job` | session |

- **Start** (`api_logic.start_session_analysis`): the caller must be able to read the session (404 otherwise). It queues the job with the session id only, under the fixed id `session_analysis:{session_id}:_`, and returns `{jobId, jobStatus, enqueued}`; a second click while it runs joins the same job. 503 when the queue is down.
- **Poll** (`api_logic.get_session_analysis_job`): `{jobId, jobStatus, error?, progress}`, with the statuses above. The job id is derived from the session; a `jobId` parameter is ignored.
- **Progress** (`progress.SESSION_ANALYSIS_STAGE_FLOORS`, Redis key `stormiq:progress:session_analysis:{session_id}`, best effort): `labelling` (5 %), `reading` (25 %, only when the model reads the session), `saving` (95 %), `done`. The worker publishes `labelling` before the run starts, so a poll never reads an earlier run's last stage as this one's, and every stage carries `started_at_ms`, when this run started. `run_session_analysis` reports each stage through its `report` callback. The page reads `progress` only while the run is `running`.
- **The run** (`api_logic.run_session_analysis`, in `asyncio.to_thread`; the key comes from the worker's env): it reads the cached chat through the record (`record.inputs.load_record_inputs`), and skips the model when the stored labels already describe this chat (same `chat_hash` and `LABEL_VERSION`). Otherwise it labels the chat (`ai/chat_labelling.label_chat`, [ai-outputs.md](ai-outputs.md#chat-labels-v1)) and stores the labels whole (`db.replace_chat_labels`). Then it reads the session (`api_logic._read_understanding`): it builds the session's dossier with the labels it just stored and the stored roster (`record.understanding.build_dossier`), skips the model when the stored result was read from the same dossier at the same `UNDERSTANDING_VERSION`, else makes one call (`ai/understanding.read_session`, [ai-outputs.md](ai-outputs.md#the-understanding-v1)), checks the answer (`check_understanding`) and stores it whole (`db.replace_understanding`). Without a transcript it reads nothing. It returns `{chatLabels: "labelled" | "up_to_date", messages, understanding: "read" | "up_to_date" | "no_transcript"}`. A failed step stores nothing of its own: labels made before a failed reading stay, and so does the earlier reading. Each call records an `llm_usage` row with its job kind (`chat_labels`, `understanding`) and the run's job id. Not held to the daily budget: the user starts it.

## Surprise me show

`run_surprise_show_job` makes Surprise me's show in one go, then queues its video. It is queued only by `POST /api/sessions/{id}/surprise-show`, the click on "Make the show" (`api_logic.start_surprise_show`), never on its own, and not while the show job or its render is pending or running, or once the video is made from the show as it is. Its only argument is the session id; the arq id is `surprise_show:{session_id}`, so two clicks share one job. The worker reads every key from its own environment (`worker._show_keys`: `CLAUDE_API_KEY`, `OPENAI_API_KEY`, `ELEVENLABS_API_KEY`) and none is ever put in an argument, a log line or an error. The job runs inside `ai.usage.collect_calls`, so a take it writes is kept in `output_versions` with what made it. When it runs (`api_logic.run_surprise_show`) it checks the organization's Surprise me switch again, then makes, in order, only what the session doesn't have yet:

1. **The take** (stage `take`), when there is none: `run_smart_recap(…, "surprise")`, the Smart Recap's surprise tone, with its number check and its one retry; a second refusal fails the job, as before. Without a transcript it fails with `SurpriseVoiceNoTakeError`, and without a Claude key with a plain message.
2. **The performance script** (stage `script`, `api_logic._make_surprise_script`): one Claude call (kind `surprise_performance`) over the take's beats, checked by `ai/surprise_performance.check_script` (every figure the take's and every take figure kept, beats valid and in order, the public redactor's leak check), asked once more with the reason when it fails; a second failure, a failed call or no Claude key reads the take's own beats instead (`fallback_script`), logged by kind only. Saved whole in `surprise_scripts` by `script_key` either way, so it never fails the show.
3. **The voice** (stage `voice`, `api_logic._record_lines`): one clip per line of the script, four at a time (`SURPRISE_VOICE_WORKERS`; two with ElevenLabs, `ELEVENLABS_VOICE_WORKERS`, whose smaller plans answer 429 to more requests at once), with the main voice (`surprise_voice.voice_plan`: ElevenLabs by default). Only the line's text is sent. Each clip is saved whole as soon as the provider answers (`db.save_surprise_voice_clip`), keyed by a hash of the provider, the model, the voice, the notes and what is sent (`clip_key`): a failed run keeps the clips it made, and the user's retry makes only the missing ones. When the main voice fails for any reason (`SpeechFailed`, `voice_elevenlabs.VoiceUnavailable` included), every line is recorded with the backup voice (OpenAI) instead, so a show has one voice, and the status says `voice.fellBack`. A successful voice then drops the session's clips the show no longer uses (`db.prune_surprise_voice_clips`), the other voice's included.
4. **The timing** (stage `timing`, `api_logic._make_surprise_timeline`): each distinct clip's word timestamps from OpenAI `whisper-1` (`ai/surprise_timing.transcribe_words`, four at a time, the clip's mp3 only); then `build_timeline` lays the clips after the fixed 4-second intro with the rehearsal's gaps (0.22 s within a beat, 0.38 s between beats, 0.75 s held before a punchline line), cuts each line into clauses (sentence ends, commas, semicolons, colons, dashes, ellipses; a clause under 0.3 s joins the next) and starts each clause when its first word is heard. A clip whisper can't read (no OpenAI key, a failed call) is timed by estimate, by its clauses' length; it never fails the show. Saved whole in `surprise_timelines` by `timeline_key` (the clip keys, beats and tones).
5. **The shot list** (stage `directing`, `api_logic._make_surprise_shotlist`): one Claude call (kind `surprise_director`) over the clauses, the vitals, the chat per minute and the room's redacted lines (`_video_facts`, from `_surprise_facts`; a line with a placeholder left in it is not offered), checked in code (`ai/surprise_director.check_shots`). A failed call, an unusable answer or no Claude key gives shots from rules (`fallback_shots`), logged by kind only; it never fails the show. An image prompt the redactor finds a name or placeholder in becomes kinetic type. Saved whole in `surprise_shotlists` by `shots_key` at stage `directed`, with the Pulse's counts per minute.
6. **The pictures** (stage `pictures`, `api_logic._make_surprise_pictures`): at most three, made at once by OpenAI Images (`ai/surprise_images.generate_image`), each saved whole as a JPEG in `surprise_show_images` by `image_key` (what is sent); a picture that fails, or no OpenAI key, turns its shot into kinetic type. The shot list is saved again at stage `final`, and the pictures it no longer uses are dropped (`db.prune_surprise_images`).
7. **The render's queueing**: unless the saved video's key (`video/keys.video_key`: `RENDER_VERSION`, the timeline's, the shot list's and the pictures' keys) already matches, the job queues `render_surprise_video` on the render worker's queue (`queue.RENDER_QUEUE`, `stormiq:render`) under the id `surprise_video:{session_id}` (`queue.surprise_video_job_id`), with the session id only. A failed enqueue fails the job with "The video's queue is unavailable…"; the user's retry makes nothing new and queues it again.

Progress (kind `surprise_show`): `queued`, `take`, `script`, then `voice` with `done`, `total` and a `percent` that moves with them, then `timing`, `directing`, `pictures`. A failure's message (`SpeechFailed`, `SurpriseCheckError`, `SurpriseOffError`, `SurpriseVoiceNoTakeError`) is what the page shows; it never holds a key or a provider's answer.

### The render worker

`render_surprise_video` runs only in the render worker (`livestorm_app/render_worker.py`, its own Render service from `docker/Dockerfile.render`; `make render-worker` locally), whose arq `WorkerSettings` takes jobs from `RENDER_QUEUE` only, one at a time (`max_jobs=1`: headless Chrome and ffmpeg take the machine), never retried (`max_tries=1`), with the same `job_result_serializer` (a failed job keeps its message only). The main worker never takes a render, and the render worker nothing else. It needs only `DATABASE_URL`, `REDIS_URL` and `ADMIN_EMAILS` (for `db.fetch_surprise_enabled`'s default, the admins' organization): it calls no provider and reads no key. The web process and the main worker never import it, nor the renderer (`video/render.py`, `film.py`, `mix.py`); they import only `video/keys.py`.

`render_worker.render_and_save` checks the Surprise me switch again, reads what the show job saved (the session's timeline, its final shot list made from that timeline, every voice clip the timeline places and the pictures), and keeps a saved video whose key already matches.

The video is three parts joined end to end: the intro, the show's middle and the outro. The intro and the outro hold nothing from a show (no words, no progress bar, the same still background), so the render worker makes each once and reuses it: `render_worker.ensure_bumpers` reads each from `surprise_bumpers` by `video/render.bumper_key` (`BUMPER_VERSION`, its kind and length, the frame, the encode, the CLI's version, its page's bytes and the files it draws) and, when one is missing (the first render after a deploy that changed one), renders it with `render.render_bumper` and saves it whole (`db.save_surprise_bumper`, which raises; one at a time, as the worker runs one job at a time). Then `video/render.render_video`:

1. writes a HyperFrames project in a temporary folder: `index.html` from `video/film.build_film`, the show's middle only, from the intro's end to the end card, its times shifted to start at 0 (every visible change a GSAP tween on one paused timeline; drawn in a 1920×1080 frame scaled to 1280×720; the director's shots; the held punchline frozen; the room's words HTML-escaped; the progress bar over the middle), the stylesheet, the still background and League Gothic from `video/templates/`, Sora from `frontend/public/`, GSAP from the image (`GSAP_JS_PATH`) and the pictures. Everything is a local file. The parts' lengths are whole frames (`film.spans`): the outro starts on the frame nearest the end card;
2. runs the pinned `hyperframes` CLI (`HYPERFRAMES_BIN`, `render.HYPERFRAMES_VERSION`, 0.9.34) headless: `render --fps 24 --crf 22 --max-bitrate 1200k --workers 1` (`RENDER_WORKERS`), the silent middle. Its timeout grows with the middle's length (`render.picture_timeout`: 3 minutes plus 8 s per second of video, at most 25 minutes); the bumpers are rendered with the same settings;
3. joins intro, middle and outro with ffmpeg's concat demuxer, the picture copied, never encoded again (`render.join_command`), once ffprobe has shown the three share their codec parameters, the SPS and PPS included (`render.same_encoding`); each part starts on a keyframe. If they ever differ (a new ffmpeg or CLI), the join is encoded once with the same CRF and bitrate cap, and the log says which parameter differed (5-minute timeout);
4. runs ffmpeg (`FFMPEG_BIN`) once more (`video/mix.mix_command`): each voice clip at its place on the whole video's clock, the voice brought to -16 LUFS (true peak -1.5 dB), the effects and the jingle (the intro's and the outro's included, placed at 0 and at the join) from `video/templates/audio/` at their gains, no music, a limiter, AAC 96 kbps, the picture copied, `+faststart` (3-minute timeout);

and the worker saves the MP4 whole in `surprise_videos` (`db.save_surprise_video`, which raises), replacing an older one. A tool that fails or runs out of time raises `render.RenderFailed` with a message the page shows ("The video could not render its pictures. Please try again."); the tool's own output is logged (its tail), never returned. Each stage logs its time with the session id only: `[render] session_id=… middle: 2640 frames in 290.0s (9.10 fps)`, then the join, the mix, and the bumpers when one was made. Progress (kind `surprise_video`): `rendering`, `mixing`; the show's status reads it while the render is pending or running.
## Timeouts and failures

| Job | Timeout |
|---|---|
| Transcription | 120 min |
| People sync | 15 min, the wait for the org's lock included |
| AI flows | 15 min |
| The session analysis | 25 min: the chat labels' batches, then the understanding call and its 10-minute stream deadline |
| Surprise me show | 30 min: the take (up to two Claude calls), the script (up to two), one speech call per line, four at a time, 60 s read timeout each, again with the backup voice when the main one fails; one whisper-1 call per clip (60 s); the director (one Claude call); up to three pictures at once (240 s) |
| Surprise me video (the render worker) | 45 min: the middle's picture (3 min plus 8 s per second of video, at most 25 min), on a first render the two bumpers (the same rule each), the join (5 min) and the mix (3 min), one render at a time |
| `ping` | 5 min (arq default) |

- A timeout fails the job. The thread it started is not killed, which is why every HTTP call also has its own timeout ([providers.md](providers.md)).
- arq retries nothing (`max_tries=1`): failures are mostly deterministic, and re-runs cost money. A transcription marks its `transcript_jobs` row `error`, and a people sync its `livestorm_sync_jobs` row. AI and voice jobs log and re-raise; arq keeps the error in the job's result for an hour, and the poll returns it. The worker stores only the error's message there, never the exception object (`queue.job_result_serializer`, set as `WorkerSettings.job_serializer`). The user retries from the UI.
- If the worker process is killed mid-job (OOM, not a normal shutdown), arq keeps reporting that job as running until its in-progress key expires: the longest job timeout plus 10 s, about 2 hours. Until then the same output can't be regenerated. Deleting `arq:in-progress:{job id}` in Redis frees it.
- `ping` returns `"pong"`. Enqueue it to check the web → Redis → worker path after a deploy.

## Where state lives

| Question | Source of truth |
|---|---|
| Is this output done? | `session_cache` (durable) |
| Is a session's people list saved? | `session_rosters` and `session_people` (durable, replaced whole) |
| Which stage is it in? | Redis key `stormiq:progress:{kind}:{key}` (30 min TTL; the people sync's kind is `roster`) |
| Did it fail, and why? | arq job state (1 h TTL; a failed job's message only); `transcript_jobs` for transcriptions, `livestorm_sync_jobs` for people syncs |
| Which connection does a queued job use? | its `transcript_jobs` or `livestorm_sync_jobs` row, only until the worker claims it |
| Is an org's people sync running, or waiting? | Redis keys `stormiq:livestorm-sync:{org}` and `stormiq:livestorm-sync-wait:{org}` (20 min TTL) |
| How many Livestorm calls has an org's sync spent today? | `livestorm_sync_jobs.pages_fetched` over the last 24 hours |

A Redis restart loses queued jobs, progress and the org locks, never results: queued transcriptions and people syncs show as errors within about 2 minutes, and users retry.

## Writing a worker job

- Handlers take `ctx` (arq's per-job context) and a minimal payload. Never pass a secret or a connection id.
- If users watch the job, publish progress at each meaningful stage (`progress.publish_progress_sync`) and clear it in `finally`; the UI depends on it.
- Re-raise failures so arq records them. If the job has its own Postgres row, as transcription and the people sync do, mark it `error` first. arq stores only the exception's message (`queue.job_result_serializer`), and the poll shows it to the user: write a message that says what failed, with no key, token or upstream body in it. The people sync raises its own `people_sync.PeopleSyncFailed`, so a `requests` exception never leaves it.
- Register the handler in `WorkerSettings.functions` with its own `timeout`, and give it a deterministic job id in `queue.py`.
