# Jobs and processing flows

How a session is fetched, transcribed and analysed. Slow or paid work (transcription, AI generation, cover images) runs only in the arq worker, never inside a web request; the web process doesn't even import the Gladia client. Code: [`api_logic.py`](../livestorm_app/api_logic.py) (web side), [`queue.py`](../livestorm_app/queue.py), [`worker.py`](../livestorm_app/worker.py), [`progress.py`](../livestorm_app/progress.py).

## Fetching a session

The frontend calls `POST /api/sessions/{id}/fetch-base`, then `POST /api/sessions/{id}/fetch-transcript`. (`POST /fetch` does both in one call. It returns the base workspace plus `transcriptJob: {jobId, jobStatus}`, or the full workspace when the transcript is already cached.)

1. If `session_cache` already has the session and `force_refresh` is false, return it.
2. Otherwise fetch the session, its event, chat and questions from the Livestorm API and upsert them into `session_cache`.
3. Start the transcript job (below) and return `{jobId, jobStatus}` at once.
4. The frontend polls `GET /api/sessions/{id}/transcript-job` every 6 s. Once the job completes, the poll returns the full workspace.

## Transcription

`POST /api/sessions/{id}/fetch-transcript` runs `api_logic.fetch_session_transcript_data`:

1. If `transcript_payload` is already cached (and not `force_refresh`), return the workspace.
2. If the session has an active job (`transcript_jobs.status` is `pending` or `running`), attach to it, even with `force_refresh`. Before attaching, ask arq whether the job is still alive. If arq reports it failed, or has no trace of it 2 minutes after the row was last updated (Redis restart, deploy), the row is marked `error` and a new job starts.
3. Otherwise insert a `transcript_jobs` row (with the caller's connection id) and enqueue `run_transcription` with the arq id `transcription:{job_id}`. A partial unique index allows one active row per session; a request that loses the insert race attaches to the winner's job.

The worker, in `worker.run_transcription(job_id, session_id)`:

1. **Claims the row.** One UPDATE moves it from `pending` to `running` and reads and clears `connection_id`. A row that is no longer `pending` (swept, or closed as lost) is skipped, so no paid work starts.
   Then it **sets the speaker cap** (`_transcription_options`): one light read of the cached session's people (`fetch_session_fields`, `session_payload`) gives the people on stage (`record/inputs.count_people_on_stage`), and Gladia's `max_speakers` follows ([providers.md](providers.md#gladia-configuration)). An unknown count, or any failure here, keeps the default cap of 20; it never fails the job. The job's arguments stay `(job_id, session_id)`.
2. **Waits for a slot.** At most `MAX_CONCURRENT_TRANSCRIPTIONS` (2) run per worker, because download and ffmpeg are memory-heavy. While it waits, progress is refreshed every minute so the sweeper leaves the row alone.
3. **Resolves credentials.** `GLADIA_KEY` comes from the worker's env; the Livestorm token from the claimed connection id, refreshed if close to expiry (`auth.livestorm_auth_for_job`).
4. **Transcribes** in a thread (`asyncio.to_thread`): download the recording, upload it to Gladia, poll. Progress goes to `transcript_jobs.progress` and to Redis. While Gladia works, the transcriber reports progress on every status change and at least once a minute (`PROGRESS_HEARTBEAT_SECONDS`), which keeps `updated_at` fresh.
5. **Saves** `session_cache.transcript_payload`, with the people-on-stage count noted in its `stormiq` block, marks the job `completed` and clears progress. If saving fails, the job is marked `error`, never `completed`.
6. **On any exception, timeout or cancellation** (worker shutdown), sets `status='error'` with a message and clears progress. A cancelled transcription also signals its thread, which stops at its next progress report.

Each transcript poll also reconciles the row with arq (as in step 2), so a job that died in the queue shows as an error within minutes. The poll response carries `progress` (the raw Gladia step) and `progressRedis` (stage-floor progress with a percent).

**Sweeper.** Every 10 minutes, a cron job marks `pending` or `running` rows that have been silent for an hour as `error`. A job that finishes after being swept still writes `completed`.

## AI generation

Four flows, each a queue-backed job:

| Start | Poll | Worker job | One job per |
|---|---|---|---|
| `POST /api/sessions/{id}/analysis` | `GET …/analysis/job?language=` | `run_overall_analysis_job` | language |
| `POST …/deep-analysis` | `GET …/deep-analysis/job?language=` | `run_deep_analysis_job` | language |
| `POST …/smart-recap` | `GET …/smart-recap/job?tone=` | `run_smart_recap_job` | tone |
| `POST …/content-repurposing` | `GET …/content-repurposing/job?language=` | `run_content_repurposing_job` | language |

**Validation.** `outputLanguage` must be `English` or `French`, and `tone` one of `professional`, `hype`, `surprise` (422 otherwise): every distinct value would be a separate paid job.

**A POST returns** either the full workspace (the output is already cached for that language or tone) or `{jobId, jobKind, jobStatus: 'pending'|'running', language|tone}` (a job was enqueued, or the same job is already running).

**Dedupe.** The arq job id is deterministic: `{kind}:{session_id}:{language|tone}` (`queue.ai_job_id`). arq refuses a second job with that id while one is queued or running, so double-clicks and teammates generating the same output share one job. arq keeps a finished job's result for an hour under that id; `queue.enqueue_unique_job_sync` clears it when the user asks again (for example, a retry after an error), so a failure never blocks a retry.

**A poll returns** either the finished output, or `{jobId, jobKind, jobStatus, progress, error?}`, where `jobStatus` is one of `pending`, `running`, `completed`, `error`, `not_found`, or `unknown` when Redis is unreachable (the frontend keeps polling), and `progress` is the Redis stage-floor payload (or null). The frontend sends `outputsOnly=true`, so a finished poll returns only `{sessionId, outputsOnly: true, outputs}` to merge into the workspace on screen; without it, the full workspace. Polls derive the job id server-side and ignore any `jobId` parameter.

**The worker job** reads the active provider's key from its env, publishes stage-floor progress to Redis (`queued` → `loading_sources` → `building_prompt` → `analyzing`), runs the synchronous runner (`api_logic.run_overall_analysis` and the like) in `asyncio.to_thread`, and clears progress when it finishes or fails. Pollers read the durable cache first, so no "done" marker has to linger. Only complete output is saved (see [ai-outputs.md](ai-outputs.md)).

The runner picks its model by job kind (`llm_client.get_llm_model`): `overall_analysis`, `deep_analysis`, `smart_recap`, `content_repurposing`, or `translation` for a French version ([providers.md](providers.md#which-model-a-job-uses)).

Smart Recap is on demand only: nothing generates it automatically.

## Cover images

`run_cover_image_job` renders a landscape cover (1536×1024 by default) from the session's Professional Smart Recap (prompt: `prompts/cover_image_prompt.txt`) and stores the PNG in `session_cache.cover_image_bytes`. It runs in exactly two cases: after a Professional Smart Recap completes (the worker chains it, unless the session already has a cover), and from `scripts/backfill_cover_images.py`. Reading a session never starts one. The arq id is `cover_image:{session_id}`, so a cover that is queued, running, or failed within the last hour is not started again. Failures are logged and swallowed, so a broken image generator never fails the recap.

## Timeouts and failures

| Job | Timeout |
|---|---|
| Transcription | 120 min |
| AI flows | 15 min |
| Cover image | 5 min |
| `ping` | 5 min (arq default) |

- A timeout fails the job. The thread it started is not killed, which is why every HTTP call also has its own timeout ([providers.md](providers.md)).
- arq retries nothing (`max_tries=1`): failures are mostly deterministic, and re-runs cost money. A transcription marks its `transcript_jobs` row `error`. AI and cover jobs log and re-raise; arq keeps the error in the job's result for an hour, and the poll returns it. The user retries from the UI.
- If the worker process is killed mid-job (OOM, not a normal shutdown), arq keeps reporting that job as running until its in-progress key expires: the longest job timeout plus 10 s, about 2 hours. Until then the same output can't be regenerated. Deleting `arq:in-progress:{job id}` in Redis frees it.
- `ping` returns `"pong"`. Enqueue it to check the web → Redis → worker path after a deploy.

## Where state lives

| Question | Source of truth |
|---|---|
| Is this output done? | `session_cache` (durable) |
| Which stage is it in? | Redis key `stormiq:progress:{kind}:{key}` (30 min TTL) |
| Did it fail, and why? | arq job state (1 h TTL); `transcript_jobs` for transcriptions |

A Redis restart loses queued jobs and progress, never results: queued transcriptions show as errors within about 2 minutes, and users retry.

## Writing a worker job

- Handlers take `ctx` (arq's per-job context) and a minimal payload. Never pass a secret or a connection id.
- If users watch the job, publish progress at each meaningful stage (`progress.publish_progress_sync`) and clear it in `finally`; the UI depends on it.
- Re-raise failures so arq records them. If the job has its own Postgres row, as transcription does, mark it `error` first.
- Register the handler in `WorkerSettings.functions` with its own `timeout`, and give it a deterministic job id in `queue.py`.
