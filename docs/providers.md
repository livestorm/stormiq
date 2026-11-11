# External providers

The services StormIQ calls, how models are chosen, and the timeout and retry rules for every outbound call.

| Service | Used for | Code | Credentials |
|---|---|---|---|
| **Livestorm REST API** | session, event, chat, questions, events list; a session's people and the workspace's people attributes ([below](#livestorm-people)) | [`livestorm/client.py`](../livestorm_app/livestorm/client.py) | the user's OAuth token (or `LS_API_KEY` in local-dev mode) |
| **Livestorm OAuth** | sign-in, token refresh, `/me` | [`oauth_client.py`](../livestorm_app/oauth_client.py) | OAuth client id and secret |
| **Gladia v2 (pre-recorded)** | transcription with diarization, named entities, sentences and subtitles; the transcriber also fetches the session's recordings from Livestorm | [`gladia/transcriber.py`](../livestorm_app/gladia/transcriber.py) | `GLADIA_KEY` (worker only) |
| **Anthropic** | every text job: the v1 jobs, overall and deep analysis, Smart Recap, content repurposing, translation | [`llm_client.py`](../livestorm_app/llm_client.py) | `CLAUDE_API_KEY` |
| **OpenAI Images** | cover images only | [`ai/cover_image.py`](../livestorm_app/ai/cover_image.py) | `OPENAI_API_KEY` |

## Choosing the provider and models

Every text job runs on Claude (the owner's decision, 2026-11-11). There is no provider to choose: `llm_client.get_provider` always answers `anthropic`, and an `llm_provider` setting or `LLM_PROVIDER` variable saved before then is ignored. OpenAI is called only for cover images ([below](#what-admin-may-save)).

### The model catalog

[`ai/models.py`](../livestorm_app/ai/models.py) lists every text model id StormIQ knows (`CATALOG`), with what each one accepts: its largest `max_tokens`, whether it thinks by default, its effort levels, `temperature`, mid-conversation system messages, the server-side fallback, the shortest prefix that caches, and structured outputs. Each Claude row also has its price per million tokens (input, output, cache writes and reads, and Haiku 5.5's second rate card above 100K prompt tokens), checked on the date in `PRICES_AS_OF`; usage rows estimate their cost from it ([Usage](#usage-and-the-daily-budget)). A model's status says where it may be chosen:

| Status | Chosen for | Models |
|---|---|---|
| `offered` | the v1 jobs | Claude Haiku 5.5, Sonnet 5.5, Opus 5.5 |
| `legacy` | the classic outputs | Claude Haiku 4.5, Sonnet 4.6, Opus 4.8 |
| `hidden` | nothing, but a setting saved earlier still works | Claude Opus 4.7 (Admin offered it until Opus 4.8 replaced it) |

The Claude 5.5 models think by default, and thinking counts toward `max_tokens`, so they are offered only where the budget has room for it. From Claude 5, the catalog lists only the 5.5 models, on purpose: Opus 5, Sonnet 5, Fable 5 and Fable 5.1 have no row, so Admin refuses them under every key. A model gets a row when a job needs it. The classic outputs' budgets (900 tokens for Smart Recap, 2,500 for the overall analysis) are sized for models that answer directly.

The OpenAI text models (gpt-4o-mini, gpt-4o, gpt-4.1-mini, gpt-4.1, gpt-5.4-mini) left the catalog when every job moved to Claude; OpenAI shuts the first four down on 2027-01-14 anyway.

### One model per job

[`ai/jobs.py`](../livestorm_app/ai/jobs.py) holds one `JobSpec` per AI job kind: its default model, its effort and its answer budget. Only the model can be changed; effort and budget are code.

| Job kind | Used for | Default model | Effort | Answer tokens |
|---|---|---|---|---|
| `overall_analysis` | v1: the Session analysis, over the session's brief | `claude-sonnet-5-5` | medium | 6,000 |
| `faq` | v1: the FAQ, over the question desk | `claude-haiku-5-5` | low | 4,000 |
| `decisions` | v1: decisions and commitments, over the session's dossier | `claude-sonnet-5-5` | medium | 8,000 |
| `team_digest` | v1: the team digest, over the digest's brief | `claude-haiku-5-5` | low | 1,000 |
| `deep_analysis` | Deep Analysis | `claude-haiku-4-5-20251001` | medium | 5,000 |
| `smart_recap` | Smart Recap, every tone | `claude-sonnet-4-6` | low | 900 |
| `content_repurposing` | Content Repurposing | `claude-haiku-4-5-20251001` | medium | 12,000 |
| `translation` | the French versions of the Session analysis, Deep Analysis and Content Repurposing | `claude-haiku-4-5-20251001` | low | 6,000, 8,000 (deep) or 12,000 (content) |
| `profile` | v1: the session profile | `claude-haiku-5-5` | low | 2,000 |
| `chat_labels` | v1: chat labels, per batch | `claude-haiku-5-5` | low | 8,000 |
| `understanding` | v1: the understanding job, one call per session analysis | `claude-sonnet-5-5` | medium | 32,000 |
| `verify_meaning` | v1: the meaning check | `claude-haiku-5-5` | low | 2,000 |

Smart Recap deliberately uses a stronger model than the other classic outputs. The classic outputs still send no effort, and their callers pass the answer tokens above as `max_tokens`. A v1 job sends its effort to every model that takes one ([Request layouts and caching](#request-layouts-and-caching)). The session analysis runs `chat_labels` and `understanding`; `profile` and `verify_meaning` run nowhere yet.

A v1 job's spec also sets how its request is laid out (`layout`), whether its stable prefix may be cached (`cache`, with `cache_ttl`), and whether it asks for the server-side fallback (`fallbacks`):

| Job kind | Layout | Cached prefix |
|---|---|---|
| `profile`, `verify_meaning` | instructions | none |
| `chat_labels` | instructions | the instructions, when a session has at least two batches |
| `understanding` | dossier | the dossier, when a run has at least two passes (one today, so nothing is cached) |

A spec also says whether an answer cut off at the token limit is asked for once more (`truncation_retry`): yes for every classic output, as before; no for the v1 jobs, whose budgets already hold thinking headroom ([Structured outputs and refusals](#structured-outputs-and-refusals)).

**Budget** (`ai.jobs.budget`). A job's `max_tokens` is its answer tokens, plus room for thinking when the model thinks by default (`THINKING_HEADROOM`: 4,000 at low effort, 16,000 at medium, 32,000 at high, 48,000 at xhigh, 64,000 at max), capped at the model's largest `max_tokens`. Above 16,000, `budget` marks the call to stream, and `generate_text` and `generate_json` stream it from Anthropic, stopped at the job's deadline ([The Anthropic SDK](#the-anthropic-sdk)). The classic outputs never stream. The headroom is a guess until `llm_usage` holds enough rows per job to calibrate it. On their defaults, the v1 jobs get 6,000, 12,000, 48,000 (streamed) and 6,000.

### Which model a job uses

`ai.jobs.resolve_model` (and `llm_client.get_llm_model(kind)`, which the classic runners call) takes the first of:

1. the per-job setting `llm_model.anthropic.<kind>`, for example `llm_model.anthropic.smart_recap`;
2. for the classic outputs only, the older key that names no provider: `llm_default_model` (four jobs: overall and deep analysis, content repurposing, translation) or `llm_smart_recap_model` (Smart Recap);
3. the job's default.

A saved id that isn't a Claude model (an OpenAI id saved before 2026-11-11) is skipped with a WARNING, and the next source is used. A saved Claude id that is not offered for the job (a setting saved before the catalog, or Opus 4.7) is still used, with a WARNING in the worker log: swapping it silently would change classic outputs.

### What Admin may save

`POST /api/admin/settings` checks model settings against the catalog (`ai.jobs.check_model_setting`) and refuses anything else with 400 and a message listing the choices:

- `llm_model.anthropic.<kind>` takes a model offered for that job.
- `llm_default_model` and `llm_smart_recap_model` take a classic-output Claude model.
- `llm_provider` is refused: there is no provider to choose.
- `image_model` is saved and sent as given, except that an empty or retired id becomes gpt-image-2 (`cover_image.resolve_image_model`).

The Admin page still has its own lists (`frontend/src/views/AdminView.vue`), and the server accepts every entry (`tests/test_model_catalog.py` checks this):

- **Claude:** Haiku 4.5 and Sonnet 4.6 (the defaults) and Opus 4.8. A saved model that isn't one of these shows as unset.
- **Images:** gpt-image-2. gpt-image-1 was shut down on 2026-10-23: `cover_image.resolve_image_model` sends gpt-image-2 when the setting or `IMAGE_MODEL` still names it, and Admin shows gpt-image-2 as active.

The image model is the `image_model` setting, then `IMAGE_MODEL`, then `gpt-image-2`. Image size and quality come from `IMAGE_SIZE` (default `1536x1024`) and `IMAGE_QUALITY` (default `high`).

**Temperature.** Every classic output asks for a low `temperature` (0.2, or 0.1 for translation). Claude 4.7 and later models reject a non-default temperature, so `llm_client._anthropic_send` sends it only when `llm_client.anthropic_accepts_temperature` says the model accepts it. A catalog model answers from its `temperature_ok`: Haiku 4.5 and Sonnet 4.6 accept it, the others don't. An id outside the catalog keeps the rule by version (`llm_client.claude_version_accepts_temperature`): Claude models up to 4.6 accept it, and any other or unknown id gets no temperature and samples at the default. This holds only on the API version the app sends (`config.ANTHROPIC_API_VERSION`, `2023-06-01`): from `2026-10-21` on, every model rejects `temperature`, and the helper then sends it to none. The SDK sends the version: `anthropic` 1.x sends `2023-06-01`, 2.x sends `2026-10-21`, so the dependency stays below 2 while a classic output sends `temperature` (a test checks the SDK sends `config.ANTHROPIC_API_VERSION`). The classic outputs' request body, `temperature` included, is written by `llm_client._post_message`, not by the SDK ([The Anthropic SDK](#the-anthropic-sdk)).

### Structured outputs and refusals

Each adapter returns one `LLMResult`: the text, why the model stopped (`complete`, `max_tokens`, `refusal` or `other`, plus the API's own value), the refusal category, the model that answered, the request id (`request-id`) and the token usage. There are two ways to call a model:

- `llm_client.call_llm`, for the classic outputs (adapter `llm_client._anthropic_send`). Each passes its own model, messages, `temperature` and `max_tokens`, and gets the text back. Their requests don't change.
- `llm_client.generate_text(kind, request, …)` and `llm_client.generate_json(kind, schema, request, …)`, for the v1 jobs (adapter `llm_client._anthropic_call`). `request` is built for the job with `ai.request.build_request` ([Request layouts and caching](#request-layouts-and-caching)); anything else, or a request built for another job, is refused. The model comes from the job (`ai.jobs.resolve_model`), `max_tokens` from its budget (`ai.jobs.budget`), and no `temperature` is sent. A model outside the catalog, or a JSON step on a model whose row doesn't accept structured outputs (`ModelCaps.structured_outputs`), is refused before any paid call. Nothing calls them yet.

**Why the model stopped** is checked in this order:

| Stop | Anthropic | What happens |
|---|---|---|
| refusal | `stop_reason: "refusal"`; `stop_details.category` when given (it can be null) | `LLMRefused` at once: "The model declined this session. Nothing was saved." Never retried; partial text is dropped |
| cut off | `stop_reason: "max_tokens"` | asked once more at double the budget if the job's spec says so (`JobSpec.truncation_retry`; `llm_client.retry_budget` caps it at 16,000 and the model's limit), else `LLMOutputTruncated` |
| complete | `end_turn` or `stop_sequence` | the text; a JSON step validates it against its schema |
| anything else | `pause_turn`, `tool_use`, … | `call_llm` returns the text as before; `generate_text` and `generate_json` raise `LLMInvalidOutput` |

The text is read from the `text` blocks only, so a leading `thinking` block (the 5.5 models may send an empty one) or a fallback block is never part of the answer. The refusal category is kept only when it is a short tag, and only in the usage row and its log line, never in the message the user sees. No server-side fallback is sent for refusals yet. The owner chose one for the v1 jobs on Sonnet and Opus 5.5; it comes with the v1 request layouts. Haiku 5.5 has none.

**JSON steps.** A JSON step's schema is a Pydantic model registered with `ai.schemas.json_schema`. Registering builds its strict JSON schema at import (`ai.schemas.strict_json_schema`), so an unsupported model fails the import rather than a paid call. The schema is sent as `output_config: {format: {type: "json_schema", schema}}`, with no beta header.

The strict schema is a subset of JSON Schema the API accepts; the API accepts a little more. Every object closes (`additionalProperties: false`) and requires all its fields, and an optional field is `X | None` without a default. Nested models are written out in place, with no `$ref`: the providers accept references, but inlining keeps the schema simple and makes a recursive model easy to refuse. Recursion, free-form objects (`dict[str, X]`), tuples and untyped values are refused. Keywords outside the subset are left out of what is sent: bounds such as `minimum`, `maximum`, `minLength`, `maxLength`, `pattern`, `minItems` and `maxItems`, and `format` and `title`. Pydantic checks them on the answer. The answer must parse and validate against the full model (`model_validate_json`), or `LLMInvalidOutput` is raised. The error names how many checks failed and their kinds, never the answer's text.

A refusal or a cut-off raises before validation, so neither becomes `LLMInvalidOutput`. A complete answer can still fail: it can come back empty, or break a bound that only Pydantic checks, since the model never sees it. There is no repair call, so such an answer fails the step. Put a bound on a JSON step only when breaking it should fail the job.

### Request layouts and caching

A v1 job never hands a model a list of messages. It builds a request with `ai.request.build_request(kind, …)`, and [`ai/request.py`](../livestorm_app/ai/request.py) writes it for the Messages API (`anthropic_call`). The job's spec picks one of three layouts:

| Layout | What comes first | Then | Used by |
|---|---|---|---|
| `dossier` | the frozen system text, then the session dossier as the first user block | the call's own data, if any, then the job's instructions | the understanding passes |
| `instructions` | the job's instructions, as the system prompt | the call's own data (`payload`: one chat batch, say) as the user turn | the profile, the chat labels, the meaning check |
| `legacy` | a classic output's own messages, system entries joined into the system prompt | | no v1 job; the classic outputs still go through `call_llm` |

**Why the dossier comes first.** The passes of one run all read the same dossier, so it is the prefix worth caching, and Anthropic's cache is a prefix match in the order tools, system, messages: anything a job chooses, placed in front of the dossier, would stop the passes from sharing it. So the system text of a dossier job is the same for every job and session, byte for byte, and the instructions come after the dossier.

**Why the instructions use the operator's channel.** The dossier holds what attendees wrote in the chat, and text inside a user turn can be forged by whoever wrote it. So the instructions are sent as a `role: "system"` message after the dossier, where the model accepts one (`ModelCaps.mid_conversation_system`: the 5.5 models and Opus 4.8). It must be the last message. A model without that channel gets them as a text block after the dossier, in the same user turn.

**Caching.** The stable prefix (the dossier block, or the instructions block) carries the request's one `cache_control` marker, and only when both hold: the spec allows it (`JobSpec.cache`), and the run makes at least two calls over that prefix (`build_request(…, shared_calls=n)`). With a single call, a cache write costs 25% more than plain input and nothing reads it back. The lifetime is the spec's (`cache_ttl`: 5 minutes, or 1 hour). A prefix shorter than the model's minimum (`ModelCaps.min_cache_tokens`: 512 tokens on the 5.5 models, 4,096 on Haiku 4.5) is not cached, and the API says nothing. A miss is silent and costs the whole prefix again on every later call, so `build_request(…, call_number=k)` says which call of the run this is, and from the second on `llm_client` logs a WARNING when the call read nothing from the cache (ids and counts only). Calls that share a prefix must run one after the other: an entry can be read only once the call that writes it has started to answer.

**A schema is part of the cached prefix.** Adding `output_config.format` to a request changes what the cache matches, so a JSON step does not read the entry a text step over the same dossier wrote (checked with paid calls, `scripts/llm_smoke.py`: `dossier-with-schema` read nothing). The passes of a run that share a dossier therefore send the same schema, or none; a job that needs several answer shapes asks for one schema that holds them all. Two calls with the same schema do share the entry, the schema included (`dossier-schema-repeat` read it); the script checks this on every run.

**The dossier's bytes.** One byte that differs between two builds of the same record is a cache miss. [`ai/dossier.py`](../livestorm_app/ai/dossier.py) has the `Dossier` type and the helpers a dossier is built from: `stable_dumps` (JSON with sorted keys, no spaces, no ASCII escapes), `fixed_number` (a fixed count of decimals), `normalize_text` (one newline style, NFC, no trailing spaces) and `make_dossier`, which adds the text's SHA-256. A dossier holds no wall-clock time, request id or randomness, and lists its items in id order. It names people by `P-` ref only ([security.md](security.md#what-v1-code-reads-about-people)). The builder that writes a record as a dossier ships with the job that reads it.

**What every v1 call sends**, read from the model's catalog row, never from the shape of its id:

| Parameter | Sent when | Value |
|---|---|---|
| `output_config.effort` | the model takes an effort (`effort_levels`; never Haiku 4.5) | the job's, or the nearest level the model takes below it (`ai.jobs.effort_for`) |
| `thinking` | the model thinks by default (the 5.5 models) | `{"type": "adaptive"}`. Never `disabled`, never a token budget; it is not turned on for a model whose budget has no room for it |
| `output_config.format` | a JSON step | the strict schema ([above](#structured-outputs-and-refusals)) |
| `fallbacks` | the model accepts the server-side fallback (`server_fallback`: Sonnet 5.5 and Opus 5.5, never Haiku 5.5) and the spec keeps it on | `"default"`, with the beta `server-side-fallback-2026-07-01` |
| `temperature` | never | |

**The server-side fallback.** With `fallbacks: "default"`, a request the model declines is answered by the fallback model Anthropic recommends for that refusal category, in the same call. On Sonnet 5.5 only some categories are retried, so a refusal can still come back; it is handled as any refusal. When the fallback answers, the usage row names the model that answered (`model_served`), `usage_raw.iterations` lists both attempts, and `llm_client` logs a WARNING. The row's token counts and cost are the answering attempt's alone: what the declined attempt cost is not in the estimate ([roadmap](roadmap.md#4-models)).

**Hashes.** Each v1 call's usage row holds the `effort` it sent, a `prompt_hash` (the SHA-256 of the layout, the system text, the instructions and the answer's schema: the same for every session until a prompt or a schema changes) and, for a dossier call, the `dossier_hash`. They let usage be compared before and after a prompt change, and a run's calls be matched to one dossier.

### Usage and the daily budget

`llm_client` records every answered text-model call, the truncation retry included, as one `llm_usage` row and one log line (`ai.usage.record_call`; [data-model.md](data-model.md#llm_usage), [operations.md](operations.md#observability)). The API's usage object is read into counts (`ai.usage.anthropic_usage`):

| Count | Field |
|---|---|
| uncached input | `input_tokens` |
| cache read | `cache_read_input_tokens` |
| cache write (and its 1-hour part) | `cache_creation_input_tokens` (`cache_creation.ephemeral_1h_input_tokens`) |
| output, thinking included | `output_tokens` |

Rows written by OpenAI calls before 2026-11-11 keep their `provider` and `reasoning_tokens`.

The cost is estimated when the row is written, from the price of the model that answered (`ai.usage.estimate_cost_usd_micros`); a served id outside the catalog is priced as the model asked for. A model with no price in the catalog gets no cost; its tokens are still counted.

The cover image call (OpenAI Images, `cover_image.generate_cover_image_with_openai`) doesn't go through `llm_client`. It leaves no `llm_usage` row and isn't held to the daily budget, although the worker starts it on its own after a Professional Smart Recap ([jobs.md](jobs.md#cover-images)). Logging it is on the [roadmap](roadmap.md#4-models).

**Daily budget.** `ORG_DAILY_LLM_BUDGET_USD` stops one organization's automatic text jobs for the rest of the UTC day once its estimated spend reaches it. It applies only to the job kinds marked `automatic` in `ai/jobs.py` (`ai.jobs.automatic_kinds`), and none is yet, so today it limits nothing. A job a user starts, every classic output included, is never held to it. `llm_client` checks it in the worker before each send, the truncation retry included (`ai.usage.check_org_budget`):

- An automatic job on a session with no organization never runs: there is nobody to charge, and the spend is never summed over every org (`db.llm_spend_since` refuses a blank scope).
- Unset means no limit. A value that isn't a dollar amount stops automatic jobs until it is fixed.
- With a budget set, the model must have a price in the catalog: an unpriced one would add nothing to the spend, so an automatic job doesn't call it.
- Otherwise the org's estimated spend since midnight UTC must be under the budget. The spend sums every row of the org, user-started jobs included; rows without a cost count 0. If the spend can't be read, the job doesn't run.

## Timeouts and retries

Every outbound call has a timeout. Retries are limited to failures that almost always mean nothing was produced or billed. (A gateway 502/504 or a dropped connection can occasionally hide a billed LLM answer; that rare double charge is accepted over failing the job.)

| Call | Timeout | Retried on | Not retried |
|---|---|---|---|
| Anthropic (the SDK, `llm_client._with_transport_retry`) | 10 s connect, 300 s for the answer or between two chunks of a stream; a stream also stops at its job's wall-clock deadline (600 s) | 429, 500, 502, 503, 504, 529 and connection errors; a stream only before its first event; up to 4 attempts, exponential backoff with jitter, `Retry-After` honoured (capped at 30 s) | every timeout, connect timeouts included; a stream after its first event; other 4xx |
| Livestorm API (`livestorm.client.livestorm_get`, all GETs) | 10 s connect, 30 s read | 429, 5xx and connection errors; 3 retries, backoff 0/1/2 s, `Retry-After` capped at 10 s | other 4xx |
| Gladia JSON calls (`transcriber._urlopen_json`) | 60 s socket | status polls ride out up to 4 consecutive timeouts, 429s or 5xx; the Livestorm recordings GET gets up to 3 attempts | starting a transcription (a POST: a retry could pay twice); 4xx |
| Gladia upload, recording download | 120 s, 60 s socket | — | — |
| Livestorm OAuth (token exchange, refresh, `/me`) | 60 s | — | — |
| OpenAI Images (cover) | 180 s | — | — |

The Livestorm HTTP session is shared by every user's requests, so it keeps no cookies.

Livestorm list calls (chat, questions, an event's sessions, the events list, a session's people, the people attributes) ask for at most 50 items per page, the maximum the API documents (`config.LIVESTORM_MAX_PAGE_SIZE`). Some endpoints accept more today; StormIQ keeps to the documented limit. The pagers follow `meta.next_page` and stop when it is null, even after a full page: asking for a page past the last one returns HTTP 400. Only a payload without pagination metadata stops at the first short page.

The chat call asks for `include=people`, so each message carries its author's people id (`relationships.people.data`). Livestorm doesn't document this link; it was observed on real data. The fetcher drops the `included` people before returning, since the session call asks for `include=people` too. In the one real session checked, the session's list held every chat author; one missing from it reads unresolved ([product.md §13](product.md#authors-v1-report)). The questions call asks for `include=asker,responder` and keeps those people.

### The Anthropic SDK

Anthropic calls go through the official Python SDK (`anthropic`, pinned below 2: [operations.md](operations.md#dependencies)).

- **One client per process** (`llm_client._anthropic_client`), built on first use. The worker passes the key it read from its own environment, and the client is given the key and the host (`config.ANTHROPIC_BASE_URL`) explicitly, so the SDK never reads `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL` or a local profile. The SDK would still add `ANTHROPIC_CUSTOM_HEADERS` to every request, over the key and the API version it is given, so while that variable is set no client is built and every Anthropic call fails with an error that names it (`llm_client._refuse_sdk_custom_headers`).
- **The SDK's retries are off** (`max_retries=0`). They retry timeouts too, and a timed-out call may still be generating, and billing, its answer. `llm_client._with_transport_retry` applies the policy in the table above, the same as before the SDK: it catches `APITimeoutError` before `APIConnectionError` (its parent class), so a timeout is never retried. One thing changed: the SDK reports a connect timeout as a timeout, so a connect timeout is no longer retried (`requests` counted it as a connection error). Nothing was sent then, so a retry would have been safe: the change can fail a job that a retry would have saved, but it never bills twice. The SDK raises a 529 as `OverloadedError`, not as an `InternalServerError`, so the policy reads the status code.
- **Streaming.** A v1 call whose budget is over 16,000 tokens streams (`llm_client._stream_message`). Its job's `call_deadline_s` (600 s unless the spec says otherwise) is a wall-clock limit from the call's start, and the job fails with `LLMDeadlineExceeded` once it passes. It can't be checked only when an event arrives: the SDK drops the server's `ping` events, so a model that thinks for minutes without sending text gives nothing to check, and each ping restarts the 300 s wait between chunks. So a timer (`llm_client._StreamDeadline`) shuts the connection at the deadline, and the read in progress fails at once, pings or silence. A stream asks for a connection of its own (`Connection: close`), so the timer never shuts one another call reuses. A stream is retried only when it fails before its first event; from the first event on, output may be billed, so a failure there, the deadline included, is never retried. A stream that doesn't finish keeps none of its text, and leaves no `llm_usage` row: like any call that failed in transport, it is only in the log.
- **Errors.** A failed call raises `llm_client.LLMProviderError`, which names the HTTP status and the API's error type (or the error's class), never the API's message, a body, a URL or the key, for example "The Anthropic call failed (HTTP 529 overloaded_error). Nothing was saved. Please try again." The poll shows it as the job's `error`: arq can store it, which it can't do for the SDK's own errors.
- **Same requests for the classic outputs.** A classic output's body is written by `llm_client._post_message` as `requests` wrote it, then sent through the SDK client: the classic outputs send the same bytes as before the SDK (`tests/fixtures/anthropic-requests-before-sdk.json`, captured from the old `requests` transport, is checked byte for byte). The SDK's `messages.create` would write the same JSON in other bytes (compact, unescaped UTF-8, its own key order).
- **The SDK's own methods for the v1 jobs.** A v1 call (`llm_client._anthropic_call`) goes through `messages.create`, or `messages.stream` above 16,000 tokens, with the keyword arguments `ai.request.anthropic_call` wrote. A call that asks for the server-side fallback needs a beta, so it goes through `client.beta.messages` with its `betas`. Same client, timeouts, retry policy and errors.
- **The SDK's own logging.** With `ANTHROPIC_LOG` set, the SDK logs each request's options, the prompt included. It stays unset ([operations.md](operations.md#environment-variables)), and `llm_client` keeps the SDK's logger at INFO otherwise.

`scripts/llm_smoke.py` checks, with real paid calls, that the planned parameters are accepted ([operations.md](operations.md#scripts)).

## Livestorm people

Two fetchers read people for the v1 roster. Only the people sync job calls them, in the worker, since a large session takes many calls ([jobs.md](jobs.md#people-sync)). Both go through `livestorm_get`, with its timeouts and retries, and retry nothing else. Both take an `on_page` callback, called once per answer with the number of HTTP requests behind it (`client.requests_sent`: the session's retries after a 429, a 5xx or a connection error are counted, from urllib3's retry history), which the sync uses to count its calls and report progress. A request that never gets an answer, even after its retries, raises and isn't counted.

- **A session's people**: `client.fetch_session_people(key, session_id, deadline=…)` reads `GET /sessions/{id}/people`, 50 per page from page 0, following `meta.next_page` until it is null. A person listed twice (the list can shift while people register) is kept once. Each record comes back without its registrant join link. It returns the people, `record_count` (Livestorm's `meta.record_count`, its count of the whole list) and `complete`, which compares the unique people with that count (None when Livestorm sends no count; the rule is [Coverage in product.md §13](product.md#roster-v1-report)). It never returns part of a list: a failed page raises, and so do passing the deadline (`LivestormDeadlineExceeded`, checked before each page) and a list that never reaches its last page (`LivestormPaginationError`: a `next_page` it already read, or more than `config.MAX_PAGES` pages).
- **The workspace's people attributes**: `client.fetch_people_attributes(key)` reads `GET /people_attributes` (paginated the same way): each registration field's slug, label, type and select options (`payloads.parse_people_attributes`). A 401 or 403 is not a failure: the result says `forbidden`, and the people sync then labels fields from the event's own fields. Other failures raise, as for a session's people. Livestorm's OAuth scope list names `/people/*` under `events:read` but not `/people_attributes`, so StormIQ's token may be refused here.

**Rate limits.** API tokens: 10,000 calls per 30 days and 5 per second. OAuth apps: 5 per second per user access token; whether they have a monthly quota isn't documented. A session of 5,000 registrants takes 100 calls to list. Each response may carry `RateLimit-Monthly-Remaining`; `fetch_session_people` returns it as a number (`monthly_remaining`, None when absent), from the last page that sent it. Whether responses to OAuth tokens carry these headers is not confirmed yet. `scripts/diagnose_people_cap.py` checks the counts and the headers on local sessions ([operations.md](operations.md#scripts)). StormIQ bounds its own use: one people sync per org at a time, and a daily cap per org, `LIVESTORM_SYNC_DAILY_CALLS_PER_ORG` (default 1,000 calls in a rolling 24 hours, a session's first sync excepted). Once an org's recorded calls reach the cap, no sync makes another call; the one sync that started under it can pass it by its own calls ([jobs.md](jobs.md#people-sync)).

## Gladia configuration

`DEFAULT_GLADIA_OPTIONS` in [`gladia/transcriber.py`](../livestorm_app/gladia/transcriber.py):

- diarization on, with 1 to 20 speakers;
- named-entity recognition on;
- sentence segmentation on;
- subtitles on, in SRT and VTT.

**Speaker cap.** For a new transcript, the worker narrows the speaker range to the people on stage (`worker._transcription_options`). It counts them with `record/inputs.count_people_on_stage`, which reads the same people as the v1 roster, and sets `max_speakers` from that count with `transcriber.max_speakers_for`. When the count is unknown, the cap stays at the default 20. The rule is in [product.md §13](product.md#transcript-level). Transcripts saved earlier are not touched.

**Job metadata.** Each chunk's Gladia job carries `custom_metadata` (`transcriber._custom_metadata`), so a later change can find a job it started: re-attaching after a timeout, which isn't built yet ([roadmap](roadmap.md#known-limitations)). The keys:

- `stormiq_session_id`, `recording_id` and `file_size` (the Livestorm recording's id and size in bytes, as listed);
- `env`: `render` on Render, `local` anywhere else (`transcriber.environment_tag`);
- `chunk`, `chunks` and `offset_s` (the chunk's start in the recording);
- `config`: the first 12 hex characters of the SHA-256 of the request without `audio_url` and `custom_metadata` (`transcriber.gladia_config_fingerprint`). The same settings always give the same `config`.

Only these keys are sent, as strings or numbers, at most 2,000 characters of JSON. Anything else raises before the POST. The metadata never holds a key, token, connection id or URL ([security.md](security.md#secrets)).

**Provenance.** Before extracting the audio, the transcriber reads the recording file's duration and `creation_time` tag from its container (`_probe_media_info`): ffprobe when installed, otherwise the header `ffmpeg -i` prints, with a 60 s timeout. A failed probe leaves `media` null; it never fails the job. The stored transcript records how it was made in `stormiq` ([data-model.md](data-model.md#session_cache)).

Gladia's standard plan accepts up to 135 minutes of audio per request, so longer recordings are split into 135-minute chunks (`_split_audio_file`), each transcribed as its own Gladia job. Each chunk's request is built once (`_build_gladia_request`), and that same request is sent. The first chunk's request is stored as `request_payload`, and every chunk's Gladia job id is kept in `result.stormiq_chunks`.

`_merge_chunk_results` combines the chunks into one transcript on the recording's clock. Each chunk's result starts at 0 s and numbers its speakers from 0, so the merge:

- shifts utterances and the words inside them, sentences (with their words), named entities and subtitles by the chunk's offset;
- gives each chunk's speakers their own numbers, above the highest number of the chunks before it (`_shift_utterance`). Gladia can't tell that two chunks heard the same person, so one person gets one number per chunk;
- writes `result.transcription.words` only when a chunk had that list, so an empty list never hides the utterances' words;
- sums `billing_time` and `transcription_time`, and keeps every chunk's languages;
- records the chunks in `result.stormiq_chunks` ([data-model.md](data-model.md#session_cache)).

For debugging, `python -m livestorm_app.gladia.cli <session_id>` transcribes a Livestorm session from the command line and writes the JSON locally. It uses the keys in your environment and costs Gladia credit.
