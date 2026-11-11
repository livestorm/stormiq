# Data model

One Postgres database. The URL comes from `DATABASE_URL`, `POSTGRES_URL` or `RENDER_POSTGRES_URL`, in that order. [`db.ensure_database_schema()`](../livestorm_app/db.py) creates and migrates the schema when the web app and the worker start; there is no separate migration tool, so schema changes must be idempotent (`IF NOT EXISTS`). At startup it also deletes duplicate `session_cache` rows (keeping the most recent per session) and closes duplicate active transcript jobs, left over from before the unique indexes existed.

## `session_cache`

Everything StormIQ knows about a session, one row per session.

```sql
session_cache (
  account_key_hash         TEXT NOT NULL,   -- sha256 of the first writer's Livestorm credential (insert-only, informational)
  session_id               TEXT NOT NULL,
  organization_id          TEXT,            -- Livestorm org that owns the session: the access boundary
  session_payload          JSONB,           -- Livestorm session response with its people, minus registrant join links
  event_payload            JSONB,           -- raw Livestorm event response (the session's parent), used for card titles and language
  created_by_user_id       TEXT,            -- Livestorm user who first fetched the session (kept on update)
  created_by_email         TEXT,
  created_by_name          TEXT,
  cover_image_bytes        BYTEA,           -- AI-generated cover (PNG)
  cover_image_mime         TEXT,            -- defaults to image/png
  cover_image_generated_at TIMESTAMPTZ,
  chat_payload             JSONB,           -- Livestorm chat messages, each with its author's people link; no people records
  questions_payload        JSONB,           -- Livestorm questions with askers and responders, minus registrant join links
  transcript_payload       JSONB,           -- full Gladia transcript
  transcript_speaker_names JSONB,           -- { raw_speaker_id: human_label }
  analysis_md              TEXT,            -- legacy: English overall analysis only
  analysis_bundle          JSONB,           -- { English: "...", French: "..." }
  deep_analysis_md         TEXT,            -- legacy: English deep analysis only
  deep_analysis_bundle     JSONB,           -- { English: "...", French: "..." }
  faq_bundle               JSONB,           -- { English: "...", French: "..." }: the Follow-up's FAQ
  decisions_bundle         JSONB,           -- { English: "...", French: "..." }: the Follow-up's decisions and commitments
  team_digest_bundle       JSONB,           -- { English: "...", French: "..." }: the Follow-up's team digest
  attendee_followup_bundle JSONB,           -- { English: "...", French: "..." }: the Follow-up email's Attended tab
  no_show_followup_bundle  JSONB,           -- { English: "...", French: "..." }: the Follow-up email's Not attended tab
  content_repurpose_bundle JSONB,           -- { English: {summary, blog, email, social_media}, French: {...} }
  smart_recap_bundle       JSONB,           -- { professional, hype, surprise }
  created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (account_key_hash, session_id)
)
-- idx_session_cache_session_id_unique   UNIQUE (session_id)
-- idx_session_cache_organization_id     (organization_id, updated_at DESC)
-- idx_session_cache_account_hash        (account_key_hash)
-- idx_session_cache_session_id          (session_id)  -- redundant with the unique index
```

- `organization_id` is stamped from the requesting user's OAuth connection on first fetch, once their Livestorm credentials prove access ([security.md](security.md)). Rows cached before org scoping have a NULL org: only admins (and local-dev callers) can read them until a member of the org fetches the session again, which stamps the org.
- `created_by_*` are kept on update (`COALESCE(NULLIF(col, ''), EXCLUDED.col)`), so the first writer's identity sticks when a teammate refetches.
- The unique index on `session_id` alone means refetching a session from another account updates the same row.
- `chat_payload` keeps each message's `relationships.people.data` (`{type: "people", id}`): its author's people id. The fetcher asks for `include=people` to get it, then drops the `included` people. In the one real session checked, `session_payload` held them all; an author missing from it reads unresolved, and is never stored from the chat ([product.md §13](product.md#authors-v1-report)). A message's `author_id` is a different id, not a people id. Chats cached before StormIQ asked for `include=people` have no relationships, and they keep none: `fetch_session_base_data` returns the cached row whenever it has the session, chat and questions payloads. A cached chat is fetched again only with `forceRefresh` on the fetch routes, which no page sends for the base payloads, or when the caller can't read the cached row (one cached before org scoping) ([roadmap](roadmap.md#known-limitations)). Questions carry people ids (`question_author_id`, `response_author_id`). The v1 record resolves authors by people id only.
- `transcript_payload` is Gladia's result (`result`) plus StormIQ's normalized fields (`text`, `segments`, `subtitles`, …), built by `gladia/transcriber._normalize_transcription`. Gladia puts each word inside its utterance (`result.transcription.utterances[].words`), and classic reads them there (`analytics/transcript._extract_transcript_object`). The normalized top-level `words` list copies `result.transcription.words` only, so it is empty unless Gladia sends that list. Classic doesn't read it: it reads `result.transcription` whenever that exists.
- `result.stormiq_chunks` lists the Gladia jobs a transcript was made of: `[{index, offset_s, duration_s, speaker_base, gladia_job_id}]`, one entry per 135-minute chunk, and one for a shorter recording. Times in the payload are on the whole recording's clock, and chunk `k`'s speakers are numbered from its `speaker_base` ([providers.md](providers.md#gladia-configuration)).
- `stormiq` records how a transcript was made (`gladia/transcriber._provenance`): `{pipeline_version, config, max_speakers, people_on_stage, gladia_jobs, media}`. `pipeline_version` is 2. `config` is the fingerprint of the Gladia settings sent, and `max_speakers` the speaker cap sent. `people_on_stage` is the count the cap was built from, set by the worker, or null when unknown ([providers.md](providers.md#gladia-configuration)). `gladia_jobs` lists each chunk's `{chunk, id, custom_metadata}`: its Gladia job id and the metadata sent with it. `media` is the recording file's `{duration_s, creation_time}`, or null when the probe found neither. Transcripts saved before the speaker cap (November 2026) have no `stormiq` block, and they are not rewritten.
- Transcripts saved before the merge fix (November 2026) have no `stormiq_chunks`. A long one also has an empty `result.transcription.words` list that hides its words from classic, words in later chunks that keep their chunk's own times, sentences and named entities for the first chunk only, and speaker numbers that restart in each chunk. They are not rewritten. The v1 parser (`record/transcript.parse_transcript`) reads their utterances' words and moves the later chunks' words onto their utterances in memory; classic still reads them as before ([product.md §13](product.md#transcript-health-v1-report)).
- The v1 record reads `transcript_payload` and `transcript_speaker_names` through `fetch_session_fields`, only when it builds its block, never on a cache hit ([architecture.md](architecture.md#reading-the-v1-record)).

## `transcript_jobs`

```sql
transcript_jobs (
  job_id        TEXT PRIMARY KEY,              -- uuid4 hex
  session_id    TEXT NOT NULL,
  timestamped   BOOLEAN DEFAULT TRUE,
  status        TEXT NOT NULL DEFAULT 'pending', -- 'pending' | 'running' | 'completed' | 'error'
  error         TEXT,
  progress      TEXT,                          -- JSON progress from the Gladia poll
  then_analyse  BOOLEAN NOT NULL DEFAULT FALSE, -- "Add" started it: the worker queues the session analysis when it ends
  connection_id TEXT,                          -- OAuth connection that started the job (NULL = LS_API_KEY, local dev).
                                               -- Read and cleared when the worker claims the job; cleared when it ends.
                                               -- Never returned by any route: it is also the session cookie value.
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
-- idx_transcript_jobs_session_id               (session_id, created_at DESC)
-- idx_transcript_jobs_status_updated_at        (status, updated_at)  -- the stuck-job sweeper
-- idx_transcript_jobs_one_active_per_session   UNIQUE (session_id) WHERE status IN ('pending','running')
--   db.create_transcript_job uses INSERT … ON CONFLICT DO NOTHING and attaches to the existing job
```

## Stored people

Four tables: `session_rosters`, `session_people`, `org_people_attributes` and `livestorm_sync_jobs`. They hold a session's people as Livestorm lists them on the paginated people endpoint, for the v1 Report ([providers.md](providers.md#livestorm-people)). The people sync job writes them ([jobs.md](jobs.md#people-sync)). The v1 record reads these rows once a sync saved a session's roster, and the people cached with the session (`include=people`) until then ([architecture.md](architecture.md#reading-the-v1-record)). The rules on what a row may hold are in [security.md](security.md#stored-people).

```sql
session_rosters (                               -- one roster header per session
  session_id     TEXT PRIMARY KEY REFERENCES session_cache (session_id) ON DELETE CASCADE,
  roster_version INTEGER NOT NULL,              -- 1, then +1 on every replace
  source         TEXT NOT NULL,                 -- 'paginated' | 'session_include' (a backfill from the cached payload)
  complete       BOOLEAN,                       -- the fetch's coverage (product.md §13, Roster: Coverage); NULL = unknown (no count)
  record_count   INTEGER,                       -- Livestorm's meta.record_count for the list
  people_count   INTEGER NOT NULL,
  fields         JSONB NOT NULL DEFAULT '[]',   -- the registration field definitions used, kept with the roster
  fields_source  TEXT NOT NULL,                 -- 'people_attributes' | 'event_fields' | 'none'
  reason         TEXT NOT NULL,                 -- 'initial' | 'refresh_t7' | 'manual' | 'backfill'
  synced_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  refresh_due_at TIMESTAMPTZ,                   -- when a refresh is due; NULL when none is. Every replace sets it
  refreshed_at   TIMESTAMPTZ                    -- set by a 'refresh_t7' replace, kept by the others
)

session_people (                                -- one row per person: `livestorm.payloads.parse_person` plus a ref
  session_id            TEXT NOT NULL REFERENCES session_cache (session_id) ON DELETE CASCADE,
  person_id             TEXT NOT NULL,          -- the Livestorm people id
  person_ref            TEXT NOT NULL,          -- 'P-' + hex (`record.roster.assign_person_refs`)
  livestorm_role        TEXT,                   -- 'participant' | 'team_member', as sent
  is_guest_speaker      BOOLEAN NOT NULL DEFAULT FALSE,
  session_role          TEXT,
  first_name            TEXT,
  last_name             TEXT,
  email_domain          TEXT CHECK (email_domain IS NULL OR position('@' IN email_domain) = 0),
  company               TEXT,
  job_title             TEXT,
  answers               JSONB NOT NULL DEFAULT '{}',  -- {field slug: text or [texts]}: text and select fields only
  registered_at         TIMESTAMPTZ,
  registration_type     TEXT,
  referrer_domain       TEXT,                   -- the referrer's host, never the URL
  utm_source, utm_medium, utm_campaign, utm_term, utm_content  TEXT,
  timezone              TEXT,
  country_code          TEXT,
  country_name          TEXT,
  attended              BOOLEAN,
  attendance_duration_s INTEGER,
  attendance_rate       DOUBLE PRECISION,       -- Livestorm's 0-100 share of the session
  has_viewed_replay     BOOLEAN,
  replay_watched_at     TIMESTAMPTZ,
  ls_messages_count, ls_questions_count, ls_votes_count, ls_up_votes_count  INTEGER,  -- Livestorm's own counters
  ls_updated_at         TIMESTAMPTZ,
  PRIMARY KEY (session_id, person_id)
)
-- idx_session_people_ref   UNIQUE (session_id, person_ref)

org_people_attributes (                         -- one row per org: the workspace's registration fields
  organization_id TEXT PRIMARY KEY,
  attributes      JSONB NOT NULL DEFAULT '[]',  -- [{slug, name, type, builtin, options: [{value, label, position}]}]
  status          TEXT NOT NULL,                -- 'ok' | 'forbidden' (the token can't read them) | 'error'
  fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
)

livestorm_sync_jobs (                           -- worker jobs that call Livestorm with a user's credentials
  sync_id            TEXT PRIMARY KEY,          -- uuid4 hex
  session_id         TEXT NOT NULL REFERENCES session_cache (session_id) ON DELETE CASCADE,
  kind               TEXT NOT NULL,             -- 'people'
  reason             TEXT NOT NULL,             -- 'initial' | 'refresh_t7' | 'manual'
  status             TEXT NOT NULL,             -- 'scheduled' | 'pending' | 'running' | 'completed' | 'error' | 'waiting_for_user' | 'deferred'
  due_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  connection_id      TEXT,                      -- the OAuth connection that started it (NULL = LS_API_KEY, local dev).
                                                -- Handed to the worker and cleared by the claim; cleared by any update that moves the row out of 'pending'.
                                                -- Never returned: it is also the session cookie value.
  credential_user_id TEXT,                      -- reserved for a later unattended refresh; nothing sets it
  pages_fetched      INTEGER,                   -- every HTTP request the sync sent to Livestorm and got an answer to
                                                -- (people and field-label pages, retries included), set when it ends,
                                                -- failed or not; the per-org daily cap sums it
  record_count       INTEGER,
  people_written     INTEGER,
  error              TEXT,                      -- at most 1,000 characters; never a key, token or upstream body
  created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
-- idx_livestorm_sync_one_active      UNIQUE (session_id, kind) WHERE status IN ('pending','running')
-- idx_livestorm_sync_one_scheduled   UNIQUE (session_id, kind, reason) WHERE status = 'scheduled'
-- idx_livestorm_sync_due             (status, due_at)
-- idx_livestorm_sync_session         (session_id, created_at DESC)
```

- **Deleted with the session.** The three per-session tables reference `session_cache (session_id)` with `ON DELETE CASCADE`, through the unique index `idx_session_cache_session_id_unique` (Postgres accepts a non-partial unique index as a foreign-key target), so `admin_delete_session` deletes a session's roster, people and syncs with it. `org_people_attributes` holds no personal data and isn't per session; it stays. `tests/test_data_integrity.py` checks the cascade and the index on the compose Postgres.
- **Scoped through the session.** These tables have no `organization_id`. `db.fetch_roster_status` (the header and the latest sync that isn't merely scheduled) and `db.list_session_people` join `session_cache` and filter on its org (`_org_filter`), so a blank scope matches nothing and never queries, and a row without an org is visible to admins only. `db.get_org_people_attributes` reads one org, never `ALL_ORGS`. These reads raise when the database can't be read: a failed read must never look like an empty roster.
- **A roster is read whole.** `db.fetch_session_roster` returns a stored roster's header and its person rows, for the v1 record and the speaker cap, through those two scoped reads. A sync can replace the roster between them, so it reads the header again after the rows: the same `roster_version` on both sides means no replace was committed in between. Otherwise it reads the rows again (`ROSTER_READ_ATTEMPTS`, 3), then raises. A caller never gets one version's header with another's people.
- **A roster is replaced whole.** `db.replace_session_roster` upserts the header with `roster_version + 1`, deletes the session's person rows and inserts the new ones, in one transaction. A failure rolls it all back: readers see the previous roster and version, never a mix. It refuses, before writing, a row with any key `parse_person` doesn't give (an `email`, a join link or a full referrer included), an `email_domain` with an "@", repeated ids or refs, and `complete` without a matching count. Times are stored as `TIMESTAMPTZ` and read back as unix seconds (a float), whether `parse_person` gave them as numbers or ISO strings; `record/roster.py` reads both. `refresh_due_at` has no default: every replace sets it, and None clears it, so a replace that keeps a due refresh passes the stored value.
- **Workspace field labels.** `db.upsert_org_people_attributes` replaces an org's attributes only with an `ok` result; `forbidden` or `error` keeps the last good ones and records the status and time. The people sync reuses an `ok` or `forbidden` row read less than 24 hours ago, and otherwise reads them again and saves `ok` or `forbidden`; any other failure fails the sync and saves nothing.
- **Sync rows.** `db.create_livestorm_sync` inserts a `pending` row (or a `scheduled` one, which never holds a connection id) unless the session already has one in flight: a lost race attaches to the existing row (`created: false`), as transcript jobs do. `db.claim_livestorm_sync` moves `pending` to `running` and hands the connection id over once, clearing it in the same statement (like `claim_transcript_job`). `db.update_livestorm_sync` sets the status, error and counts, and clears the connection id when the row leaves `pending`; with `only_from` it updates only a row still in one of those statuses, so closing a lost sync never overwrites one that finished meanwhile. A row that is never claimed is closed that way: by the request after a failed enqueue, or by the next status read when its arq job is gone. `db.sweep_stuck_livestorm_syncs` (the stuck-job sweeper) closes any `pending` or `running` row silent for an hour, connection id cleared, and `db.touch_livestorm_sync` keeps a running sync fresh while it waits for its org's lock. `db.livestorm_sync_calls_since(org, since)` sums one org's `pages_fetched` over the rows updated since then, leaving out each session's first `initial` sync (the daily cap; a blank scope gives 0 without querying, `ALL_ORGS` is refused), and `db.livestorm_sync_is_first_initial(sync_id)` says whether a sync is that exempt one, for the worker's own cap check.

## `session_chat_labels`

One row per session: a label for each chat message, for the v1 Report. The session analysis the user starts writes them ([jobs.md](jobs.md#the-session-analysis-v1)). The labels' contract is in `record/chat_labels.py`.

```sql
session_chat_labels (
  session_id    TEXT PRIMARY KEY REFERENCES session_cache (session_id) ON DELETE CASCADE,
  label_version INTEGER NOT NULL,      -- record.chat_labels.LABEL_VERSION when written
  chat_hash     TEXT NOT NULL,         -- SHA-256 of the labelled chat (record.chat_labels.chat_hash)
  model         TEXT NOT NULL,         -- the model that made the labels
  message_count INTEGER NOT NULL,
  labels        JSONB NOT NULL,        -- [{id, livestorm_id, class, question, language}], in id order
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
```

- **One label per message.** `id` is the message's `C` evidence id (`record/evidence.py`) and `livestorm_id` its own id. `class` is one of `content`, `question`, `tech`, `logistics`, `social`, `laughter`, `off_topic`; a `question` also says whether it is about the content or logistics; `language` is a lowercase ISO 639 code or null. No text, author or name is stored here: the chat stays in `session_cache`.
- **Which chat.** `chat_hash` covers each message's evidence id, Livestorm id and text. A reader shows the labels only when it and `label_version` match the chat it shows, so labels made for an older chat or by an older prompt never appear against a newer one.
- **Replaced whole.** `db.replace_chat_labels` checks the set (`check_labels`: known classes, a kind on questions only, unique ids; errors name ids, never text) before it writes, then upserts the row. A failed write raises and keeps the previous labels.
- **Scoped and deleted through the session**, like the people tables: `db.fetch_chat_labels` joins `session_cache` and filters on its org (a blank scope never queries), and `ON DELETE CASCADE` removes the labels with the session.

## `session_figure_checks`

Before you share (Follow-up tab, `record/figure_checks.py`): the host's answer to each check on a figure said on stage.

```sql
session_figure_checks (
  session_id  TEXT NOT NULL REFERENCES session_cache (session_id) ON DELETE CASCADE,
  check_key   TEXT NOT NULL,          -- acronym:<U id>:<span> or values:<metric>, never a figure id
  choice      TEXT NOT NULL,          -- one of the check's options' values
  updated_by  TEXT,                   -- the Livestorm user id, never an email
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (session_id, check_key)
)
```

- **One row per answered check.** `db.save_figure_check` upserts it, or deletes it when the host reopens the check; it writes only when the session is in the caller's scope, and a failed write raises. `db.fetch_figure_checks` joins `session_cache` on its org.

## `session_faq`

The editable FAQ (Follow-up tab, `record/faq_entries.py`): the entries the user saved for one session and language.

```sql
session_faq (
  session_id  TEXT NOT NULL REFERENCES session_cache (session_id) ON DELETE CASCADE,
  language    TEXT NOT NULL DEFAULT 'English',
  entries     JSONB NOT NULL,         -- [{id, question, answer, source, t, origin, deskIds}], in order
  desk_stamp  JSONB,                  -- { desk id: "answered" | "open" }: the desk at the save
  updated_by  TEXT,                   -- the Livestorm user id, never an email
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (session_id, language)
)
```

- **Saved whole.** `db.save_session_faq` upserts the row with the entries `record.faq_entries.validate_entries` cleaned, only when the session is in the caller's scope (False otherwise, and for a blank scope, which never queries); a failed write raises. `db.fetch_session_faq` joins `session_cache` on its org. Nothing is stored before the first save: the page shows the desk's draft. No org column.
- **`desk_stamp`** is what `desk_updates` compares the desk with, to offer the questions answered or added since. An entry still open keeps its previous mark, so a desk answer it hasn't taken yet is offered again.

## `session_question_replies`

What the host saved in the question desk (Follow-up tab, `record/question_desk.py`): one row per question they marked answered, unmarked or replied to.

```sql
session_question_replies (
  session_id   TEXT NOT NULL REFERENCES session_cache (session_id) ON DELETE CASCADE,
  source       TEXT NOT NULL CHECK (source IN ('qa', 'chat')),
  livestorm_id TEXT NOT NULL,          -- the question's or chat message's own id
  evidence_id  TEXT NOT NULL,          -- its Q or C id when saved (Q001, C0001)
  answered     BOOLEAN NOT NULL,
  reply        TEXT,                   -- what the host wrote, up to 4,000 characters, or null
  located      TEXT CHECK (located IN ('confirmed', 'rejected')),  -- the host's decision on a located answer, or null
  updated_by   TEXT,                   -- the Livestorm user id, never an email
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (session_id, source, livestorm_id)
)
```

- **Keyed by the Livestorm id**, with the evidence id beside it: evidence ids are renumbered when a fetch changes the chat or the questions, Livestorm ids aren't.
- **One write per question.** `db.save_question_reply` upserts the row; a failed write raises. `db.save_located_decision` upserts the host's decision on an answer the understanding job located in the talk: `confirmed` also sets `answered` (a reply is kept), `rejected` changes nothing else. `save_question_reply` never touches `located`. The column is added by `ALTER TABLE … ADD COLUMN IF NOT EXISTS` at startup.
- **Scoped and deleted through the session**, like the chat labels: the read (`db.fetch_question_replies`) and the write both join `session_cache` and filter on its org, so another org's session reads and writes nothing (the write's insert selects the session row with the filter); a blank scope never queries; `ON DELETE CASCADE` removes the rows with the session.

## `session_understanding`

One row per session: what the understanding job read in it, checked (`record/understanding.py`), for the Follow-up tab and later findings ([ai-outputs.md](ai-outputs.md#the-understanding-v1)).

```sql
session_understanding (
  session_id   TEXT PRIMARY KEY REFERENCES session_cache (session_id) ON DELETE CASCADE,
  version      INTEGER NOT NULL,      -- record.understanding.UNDERSTANDING_VERSION when written
  dossier_hash TEXT NOT NULL,         -- SHA-256 of the dossier it was read from
  model        TEXT NOT NULL,
  result       JSONB NOT NULL,        -- {version, onClock, chapters, answers, prompts, figures, groups, coverage}
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
```

- **By id, never by name.** Items cite utterance ids (`U0001`), question ids (`Q001`, `C0001`) and the words said on stage; no name, email or people id.
- **Replaced whole.** `db.replace_understanding` refuses a result of another version or a malformed hash before writing, then upserts the row. A failed write raises and keeps the previous result.
- **Which session.** `dossier_hash` and `version` say what it was read from: the analysis reads the session again only when either differs.
- **Scoped and deleted through the session**, like the chat labels: `db.fetch_understanding` joins `session_cache` and filters on its org (a blank scope never queries), and `ON DELETE CASCADE` removes the row with the session.

## `output_versions`

Every AI output as it was saved, one row per save, so earlier versions are kept (the bundle columns keep only the latest). Written by `db.merge_cached_bundle_entry` in the same transaction as the bundle entry: both are saved, or neither.

```sql
output_versions (
  id              BIGSERIAL PRIMARY KEY,
  session_id      TEXT NOT NULL REFERENCES session_cache (session_id) ON DELETE CASCADE,
  output_column   TEXT NOT NULL,      -- the bundle column: analysis_bundle, faq_bundle, smart_recap_bundle…
  entry_key       TEXT NOT NULL,      -- its language, or the Smart Recap's tone
  content         JSONB NOT NULL,     -- the value saved: the text, or content repurposing's sections
  job_id          TEXT,               -- the deterministic arq id of the job that wrote it
  job_kinds       TEXT[] NOT NULL DEFAULT '{}',  -- the jobs of its model calls (faq, translation…)
  models          TEXT[] NOT NULL DEFAULT '{}',  -- the models that answered
  prompt_hashes   TEXT[] NOT NULL DEFAULT '{}',
  dossier_hashes  TEXT[] NOT NULL DEFAULT '{}',
  record_versions TEXT[] NOT NULL DEFAULT '{}',
  calls           INTEGER NOT NULL DEFAULT 0,    -- answered model calls in the run
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
-- idx_output_versions_session (session_id, output_column, entry_key, created_at DESC)
```

- **What made it.** The worker runs each AI job inside `ai.usage.collect_calls()`; every answered call recorded there (`record_call`) adds its job kind, job id, model and hashes, and the runner passes `ai.usage.run_provenance()` when it saves. Lists keep the order first seen, without repeats or blanks. The same run's calls are in `llm_usage` too, but those rows hold no content and can't tell two runs of the same job apart (the job id repeats).
- **Holds the outputs' text**, like the bundle columns: scoped and deleted through the session (no org column; `ON DELETE CASCADE`). Read only by `db.list_output_versions` (one output's versions, newest first, at most 20, joined to `session_cache` on the caller's org; never the hashes or the job id), for Follow-up's "Earlier versions" (`GET /output-versions`, [api.md](api.md)).

## `org_settings`

One row per organization whose switches a StormIQ admin changed (Admin → Organizations), or an admin signed in from (Surprise Me! on, written once at sign-in and never over an existing row). An organization without a row has the defaults: Surprise Me! off, except for an organization an admin is signed in from (`db.fetch_surprise_enabled`).

```sql
org_settings (
  organization_id  TEXT PRIMARY KEY,
  surprise_enabled BOOLEAN NOT NULL,               -- Surprise Me! on Follow-up
  updated_by_email TEXT,                           -- the admin who changed it
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
```

- Written only by `db.save_surprise_enabled` (`POST /api/admin/organizations/settings`, admins only). Read by `db.fetch_surprise_enabled`, which raises on a failed read: a surprise take's start (`api_logic.start_smart_recap`) and its worker run (`run_smart_recap`) then fail, and `/api/bootstrap` hides Surprise me. A blank organization (a session cached without one, local development) has no setting and is on. Listed for Admin by `db.admin_list_organizations`, with each organization's cached sessions.

## `surprise_voice_clips`

```sql
surprise_voice_clips (
  session_id  TEXT NOT NULL REFERENCES session_cache (session_id) ON DELETE CASCADE,
  clip_key    TEXT NOT NULL,          -- sha256 of the version, provider, model, voice, notes and what is sent for the line
  mime        TEXT NOT NULL,          -- audio/mpeg
  audio       BYTEA NOT NULL,         -- one line's whole mp3
  duration_s  DOUBLE PRECISION,       -- read from the mp3's frames (surprise_voice.mp3_duration_s)
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (session_id, clip_key)
)
```

Surprise me's voice ([jobs.md](jobs.md#surprise-me-show)): one row per line of the performance script, written by the worker only, one whole clip at a time, as ElevenLabs or OpenAI answers (`save_surprise_voice_clip`, which raises). The same line twice is one row. A successful run removes the session's clips the current show no longer uses, the other voice's included (`prune_surprise_voice_clips`). It has no org column: the reads (`fetch_surprise_voice_clips`, which returns lengths and sizes, never audio, and `fetch_surprise_voice_clip`) join `session_cache` on its org, and a blank scope reads nothing. Deleted with its session.

## `surprise_storyboards`

```sql
surprise_storyboards (
  session_id  TEXT PRIMARY KEY REFERENCES session_cache (session_id) ON DELETE CASCADE,
  story_key   TEXT NOT NULL,          -- sha256 of STORYBOARD_VERSION and the take's beats (surprise_story.story_key)
  storyboard  JSONB NOT NULL,         -- {version, source, scenes: [{effect, headline, number, quote} | null]}
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
```

Surprise me's storyboard ([jobs.md](jobs.md#surprise-me-show)): one row per session, written whole by the worker only, after the check (`save_surprise_storyboard`, which raises); a new take's storyboard replaces the old one. A scene's `quote` holds one of the room's redacted lines (id, kind, time, text), never who wrote it. Read by `fetch_surprise_storyboard` only for the take's own key, joined to `session_cache` on its org; a blank scope reads nothing. Deleted with its session.

## `surprise_scripts`

```sql
surprise_scripts (
  session_id  TEXT PRIMARY KEY REFERENCES session_cache (session_id) ON DELETE CASCADE,
  script_key  TEXT NOT NULL,          -- sha256 of SCRIPT_VERSION and the take's beats (surprise_performance.script_key)
  script      JSONB NOT NULL,         -- {version, source: model | take, lines: [{beat, say, tone}]}
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
```

Surprise me's performance script ([jobs.md](jobs.md#surprise-me-show)): one row per session, written whole by the worker only, after the check, or the take's own beats when the script failed it (`source: take`) (`save_surprise_script`, which raises); a new take's script replaces the old one. Never served to the page: `GET /surprise-show` sends each line's beat and clip key only. Read by `fetch_surprise_script` only for the take's own key, joined to `session_cache` on its org; a blank scope reads nothing. Deleted with its session.

## `oauth_connections`

```sql
oauth_connections (
  connection_id    TEXT PRIMARY KEY,           -- secrets.token_urlsafe(24); also the session cookie value
  provider         TEXT NOT NULL,              -- 'livestorm'
  user_id          TEXT,                       -- Livestorm /me data.id
  email            TEXT,
  organization_id  TEXT,
  access_token     TEXT NOT NULL,
  refresh_token    TEXT,
  token_type       TEXT NOT NULL DEFAULT 'Bearer',
  scope            TEXT,
  expires_at       TIMESTAMPTZ,
  profile          JSONB,                      -- snapshot of the /me payload and parsed fields
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
-- idx_oauth_connections_user_id   (provider, user_id)
```

## `admin_users`

```sql
admin_users (
  email              TEXT PRIMARY KEY,         -- lower-cased; matched against oauth_connections.email
  user_id            TEXT,
  promoted_by_email  TEXT,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
```

Admins are this table plus the `ADMIN_EMAILS` env var (bootstrap admins, who can't be demoted from the UI).

## `system_settings`

```sql
system_settings (
  key        TEXT PRIMARY KEY,                 -- allow-listed in routes/admin.py: image_model, tts_voice, llm_default_model, llm_smart_recap_model, llm_model.anthropic.<kind> (an older llm_provider row is ignored)
  value      TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
```

Admin-editable overrides for provider and model selection (`/api/admin/settings`, see [providers.md](providers.md#choosing-the-provider-and-models)). `llm_model.<provider>.<kind>` picks one AI job's model on one provider, for example `llm_model.anthropic.smart_recap`; the two older model keys name no provider: `llm_default_model` covers four jobs and `llm_smart_recap_model` covers Smart Recap. Model values are checked against the catalog when saved (`ai.jobs.check_model_setting`). Each process caches a value for 60 s and keeps serving the last known value if the database is briefly unreachable.

## `llm_usage`

One row per text-model call that got an answer (HTTP 200), made through `llm_client` (`call_llm`, `generate_text`, `generate_json`) and written by `ai.usage.record_call` through `db.insert_llm_usage`. The cover image call has no row. A truncated, refused or retried call has its own row, and so does an answer that failed its schema; a call that failed in transport has none (it is only in the log), and neither has a stream stopped part-way, by an error or its deadline. See [providers.md](providers.md#usage-and-the-daily-budget).

```sql
llm_usage (
  id                    BIGSERIAL PRIMARY KEY,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  organization_id       TEXT,             -- the session's org; NULL when the session has none
  session_id            TEXT,
  job_kind              TEXT NOT NULL,    -- the ai/jobs.py kind: 'overall_analysis', 'translation', …
  job_id                TEXT,             -- the deterministic arq id, e.g. 'overall_analysis:{session}:French'; never a connection id
  purpose               TEXT NOT NULL,    -- 'main' | 'truncation_retry' | 'completeness_retry' | 'language_retry'
  provider              TEXT NOT NULL,    -- 'anthropic' ('openai' on rows from before 2026-11-11)
  model_requested       TEXT NOT NULL,
  model_served          TEXT,             -- the model the provider says answered
  effort                TEXT,             -- the effort a v1 call sent; NULL for a classic output, or a model that takes none
  max_tokens            INTEGER,
  streamed              BOOLEAN NOT NULL DEFAULT FALSE,  -- the answer came as a stream (a v1 call over 16,000 tokens, Anthropic only)
  stop_reason           TEXT NOT NULL,    -- the provider's own value: 'end_turn', 'length', …
  refusal_category      TEXT,             -- Anthropic's stop_details.category, when it gives a short tag
  outcome               TEXT NOT NULL,    -- 'ok' | 'truncated' | 'refused' | 'other_stop' | 'invalid_output' (an answer that failed its schema, or an empty v1 answer)
  input_tokens          INTEGER NOT NULL DEFAULT 0,  -- uncached input
  output_tokens         INTEGER NOT NULL DEFAULT 0,  -- thinking and reasoning included
  cache_read_tokens     INTEGER NOT NULL DEFAULT 0,
  cache_write_tokens    INTEGER NOT NULL DEFAULT 0,  -- all cache writes, 1-hour ones included
  cache_write_1h_tokens INTEGER NOT NULL DEFAULT 0,
  reasoning_tokens      INTEGER,          -- OpenAI only, rows from before 2026-11-11
  cost_usd_micros       BIGINT,           -- estimated cost in millionths of a dollar; NULL for a model with no price
  duration_ms           INTEGER,          -- transport retries included
  request_id            TEXT,             -- the provider's request id
  prompt_hash           TEXT,             -- v1 calls: SHA-256 of the layout, system text, instructions and schema (`LLMRequest.prompt_hash`)
  dossier_hash          TEXT,             -- v1 dossier calls: SHA-256 of the dossier's text (`Dossier.sha256`); never the text
  record_version        TEXT,             -- v1 jobs only
  usage_raw             JSONB             -- the provider's usage object: counts and short tags only
)
-- idx_llm_usage_session_id        (session_id, created_at DESC)
-- idx_llm_usage_organization_id   (organization_id, created_at DESC)  -- the daily budget's spend read
```

- A row holds no prompt, no output, no name and no key ([security.md](security.md#model-usage-rows)). `usage_raw` keeps only numbers and short enum-like tags (`ai.usage.sanitize_usage`).
- There is no foreign key to `session_cache`, on purpose: deleting a session keeps its usage rows, which hold no content, so past spend stays counted.
- `db.llm_spend_since(organization_id, since)` sums one org's `cost_usd_micros`. A blank scope raises and `ALL_ORGS` is refused, so it never sums every org. Rows without a cost count 0.
- Nothing reads the table in the app yet besides the budget check.

## Caching rules

- **Cache first.** Every fetch route checks `session_cache` before calling Livestorm or Gladia. `force_refresh: true` is the only bypass. Entries never expire.
- **One row holds the latest of everything.** Regenerating an output replaces that language's (or tone's) entry in its bundle column; each saved version is also kept in [`output_versions`](#output_versions).
- **Bundle columns are written one entry at a time.** AI runners never rewrite a whole bundle: `db.merge_cached_bundle_entry(session_id, column, key, value)` sets one language or tone with a single `jsonb_set` UPDATE, so concurrent jobs on the same session (English and French, or two recap tones) can't erase each other's results. Never write a bundle column with `upsert_cached_session` from a worker job.
- **Writes fail loudly.** `upsert_cached_session`, `merge_cached_bundle_entry`, `rewrite_base_payloads` and the people writes (`replace_session_roster`, `upsert_org_people_attributes`, `create_livestorm_sync`, `claim_livestorm_sync`, `update_livestorm_sync`, `touch_livestorm_sync`, `sweep_stuck_livestorm_syncs`) raise on database errors; a job is only `completed` once its result is saved.
- **A roster is replaced whole or not at all.** `replace_session_roster` swaps a session's header and person rows in one transaction and bumps `roster_version`. The people sync fetches every page first, so a failed or partial fetch never overwrites a stored roster ([Stored people](#stored-people)).
- **One narrow exception: usage rows.** `insert_llm_usage` raises too, but its one caller, `ai.usage.record_call`, catches the failure, logs the whole row as a line at ERROR (no prompt, output or key) and lets the job go on to save its output. The output is complete and already paid for; failing the job would make the user pay for it again. This is an owner decision, and it covers `llm_usage` only.
- **No registrant join links.** The session, chat and questions payloads are saved without each person's `password_key` and `connection_link` ([security.md](security.md#data-stormiq-doesnt-keep)). Every other key Livestorm returns is kept. `session_people` has no column for them, nor for an email address.
- **Every write bumps `updated_at`.** The in-process transcript-insights cache and the v1 record cache are keyed on it ([architecture.md](architecture.md#reading-a-workspace), [the v1 record](architecture.md#reading-the-v1-record)). The one exception is `db.rewrite_base_payloads`, for maintenance scripts. It replaces base payloads without bumping `updated_at`, so the library keeps its order. It writes only if `updated_at` still matches the value the script read, so it never overwrites a newer save. Use it only for changes nothing in the app reads, such as removing join links. `db.scan_base_payloads` reads the rows for it in batches.

## Connections

Each process (web, worker) keeps one `psycopg_pool` connection pool behind `db.get_db_connection()`: up to `DB_POOL_MAX_SIZE` (10) connections, checked before use and recycled hourly. Web and worker together hold at most 20 connections, which must stay under the Render Postgres plan's limit.
