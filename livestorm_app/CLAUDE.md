# Backend

The HTTP routes live in `routes/`, one module per area; `../app.py` creates the app and includes their routers in order, with the SPA catch-all (`routes/spa.py`) last. Route handlers are plain `def` functions, run in FastAPI's threadpool. Module map: [docs/architecture.md](../docs/architecture.md#backend-code).

## Rules

**Access**
- A new route goes in the router module for its area (`routes/`), never on `app` directly; request bodies go in `routes/models.py`.
- Routes take their identity from `auth.py`: `Depends(get_caller)`, `Depends(require_admin)` or `Depends(get_optional_caller)`. Never read the session cookie or resolve org ids by hand.
- Session reads pass the caller's scope (`organization_id=caller.read_scope`). Only admin, local-dev and worker code pass `ALL_ORGS`. A blank scope matches nothing, and an out-of-scope session looks missing (404).
- Stamp `organization_id` on a row only after the caller's own Livestorm credentials returned the session. → [docs/security.md](../docs/security.md)

**Database (`db.py`)**
- All SQL lives in `db.py` and goes through the pooled `get_db_connection()`. Pass values as query parameters, never by string formatting.
- Schema changes go in `ensure_database_schema()` and must be idempotent (`IF NOT EXISTS`); there is no migration tool.
- Write an AI bundle with `merge_cached_bundle_entry` (one language or tone), never by rewriting the whole bundle. Writes raise on failure; don't swallow them.
- Hot paths (polls, job starts, the v1 record) read with `fetch_session_fields`; add a field to `_SESSION_FIELD_SQL` rather than loading the full row. → [docs/data-model.md](../docs/data-model.md)

**Jobs (`queue.py`, `worker.py`)**
- Enqueue only through `queue.enqueue_unique_job_sync`, with a deterministic id (`ai_job_id`, `transcription_job_id`, `cover_image_job_id`).
- Job arguments are ids only: no keys, tokens or connection ids.
- A handler that users watch publishes progress (`progress.publish_progress_sync`) and clears it in `finally`. Every handler re-raises failures so arq records them (marking its own Postgres row `error` first, if it has one, as transcription does) and is registered in `WorkerSettings.functions` with its own `timeout`. → [docs/jobs.md](../docs/jobs.md)

**Outbound calls**
- Every HTTP call has a timeout. Retry only failures where nothing was produced or billed; never retry a POST that starts paid work. → [docs/providers.md](../docs/providers.md)
- The Livestorm HTTP session (`livestorm.client.livestorm_get`) is shared by all users: no cookies, no per-user state.
- A Livestorm fetcher that returns people strips registrant join links before returning (`livestorm.payloads.strip_payload_join_links`); StormIQ never stores them. The chat fetcher asks for `include=people` only for each message's people link and drops the `included` people. A pager asks for at most `config.LIVESTORM_MAX_PAGE_SIZE` (50) per page and stops when `meta.next_page` is null.

**Models (`ai/models.py`, `ai/jobs.py`)**
- Text model ids and what each accepts live in `ai/models.py` (the catalog); each AI job's default model, effort and budget in `ai/jobs.py`. A job gets its model from `ai.jobs.resolve_model` (the classic runners call `llm_client.get_llm_model(kind)`); never hard-code a text model id elsewhere. `frontend/src/views/AdminView.vue` keeps its own lists until Admin reads them from the server, and `tests/test_model_catalog.py` checks the server accepts them.
- Read a model's parameters from its catalog row (`temperature_ok`, `effort_levels`, `max_output`…), not from the shape of its id.
- A new model setting key is checked in `ai.jobs.check_model_setting`; a saved id that is no longer offered keeps being used, with a WARNING, so classic outputs never change silently. → [docs/providers.md](../docs/providers.md#choosing-the-provider-and-models)

**Workspace reads**
- Get transcript insights through `api_logic._cached_transcript_insights`, once per read, and copy a shared DataFrame before modifying it.
- Don't add raw upstream payloads to the serialized workspace; add the derived table a view needs.

**Errors**
- Return `{resource, message, details}`. Log upstream URLs and bodies server-side; never return them.

**The v1 record (`record/`)**
- New code for the v1 Report goes in `record/`: pure functions over cached payloads, with no database, network or FastAPI. Classic code (`analytics/`, `api_logic`) doesn't import it, so a bug there can't break a classic read. The one exception is the record route's loader, `api_logic.get_session_record`, which imports it inside the function (`tests/test_roster.py` checks both).
- v1 data is served only by `GET /api/sessions/{id}/record` (`api_logic.get_session_record`, built by `record.assemble.assemble_record`); never add it to `_serialize_cached_session`. Its cache key must cover everything the block reads: when an input can change without bumping `updated_at`, add its version in `_record_input_versions`. Bump `record.assemble.RECORD_VERSION` when the block changes, and regenerate `frontend/tests/fixtures/record-v1.json` (`.venv/bin/python tests/synthetic_session.py`). → [docs/architecture.md](../docs/architecture.md#reading-the-v1-record)
- Times come from the raw payload values (`created_at`, `responded_at`: unix seconds or ISO strings), read through `record.clock`, never from the formatted strings of the classic tables. A session without `started_at` has no clock: never fall back to `estimated_started_at`.
- Evidence ids (`C0001`, `Q001`, `A001`) come only from `record.evidence.audience_events`. Anything that stores one also stores the Livestorm id. Rules: [docs/product.md §13](../docs/product.md#session-clock-v1-report).
- v1 code reads person-level data only through `record/roster.py`, loaded by `record.inputs.load_record_inputs`; each people record is parsed by `livestorm.payloads.parse_person`. A person row keeps the email domain, never the address. A v1 prompt gets people only through `roster.prompt_view` (ref, role, job title); a page gets names only through `roster.display_people`, which fails closed. Roles never change for an internal session: read `Roster.meta.internal`. Rules: [docs/product.md §13](../docs/product.md#roster-v1-report).
- Every number the v1 Report shows is a measure in `record/measures.py` (`REGISTRY`), computed by `compute_measures` and served as `record.measures`; the SPA computes no formula. A new measure gets a `MeasureDef` (id, version, sources, gates, glossary anchor) and its section in [docs/product.md §13](../docs/product.md#measures-v1-report). Changing a measure's formula, exclusions or gates bumps its version, and any change to a version, unit, source, exclusion or gate bumps `METRICS_VERSION` and `REGISTRY_PIN` (`tests/test_measures.py` fails otherwise). An audience-only measure counts everyone on an internal session and says so (`excluded.internal`); a measure that needs authors reads `AuthorIndex` coverage and never guesses.
- Authors are joined to people only through `record.authors.resolve_authors`, by people id. Never match an author by name, message counts or order: an author that doesn't resolve stays unresolved and its coverage says so. A chat message's people id is its `relationships.people` link; its `author_id` is a different id, never a people id. Rules: [docs/product.md §13](../docs/product.md#authors-v1-report).

## Tests (`../tests/`)

| File | Covers |
|---|---|
| `test_access_control.py` | every session route × anonymous, other org, same org, admin; admin routes for anonymous and non-admins; which settings Admin may save |
| `test_db_scope.py` | org-scoped SQL |
| `test_data_integrity.py` | bundle writes, write failures, incomplete AI output, join links never saved, `rewrite_base_payloads` |
| `test_job_reliability.py` | dedupe, retries, timeouts, the transcript job lifecycle, cancellation, the Gladia request built once |
| `test_gladia_merge.py` | merging a long recording's Gladia chunks (synthetic payloads), and what classic insights read from the result |
| `test_performance.py` | the insights cache, the v1 record cache, field reads, pools, the Redis cooldown, outputs-only polls |
| `test_pure_functions.py` | metrics and formulas, PDF rendering, the Anthropic request's `temperature`, the join-link strip, the Livestorm pagers |
| `test_model_catalog.py` | the model catalog, the job registry and budgets, `resolve_model`'s order and stale settings, the model setting check, AdminView's lists against the server, the kind each classic runner asks for |
| `test_session_clock.py` | the session clock, its fallbacks, minute bins and evidence ids (`record/`); the payload shapes script's local-database check and chat author check |
| `test_roster.py` | `parse_person`, roles, organizers, channels, lead time, field definitions, `P-` refs, the prompt and display views, the record-inputs loader (`record/`); classic code never imports `record/` (only the record route's loader does, inside the function) |
| `test_authors.py` | chat, question and answer authors resolved to roster people, their coverage, roles raised by chat flags and written answers, the evidence table's author columns (`record/`); the chat fetcher's `include=people`; classic reading a fetched chat as before; the benchmark seed's chat shape |
| `test_measures.py` | every v1 measure's value, gates and states on a public webinar and an all-team meeting; the version pin; the glossary anchors in `docs/product.md` §13; `validate_measures` (`record/`) |
| `test_record_route.py` | the v1 record route: its response and measures, what it never carries, the internal flag, errors, the frontend fixture, and the classic workspace snapshot |

`synthetic_session.py` is a made-up webinar as a `session_cache` row; it generates `frontend/tests/fixtures/record-v1.json` and froze `fixtures/classic-workspace.json` (how to regenerate each: its docstring). `conftest.py` provides `test_database` (needs `TEST_DATABASE_URL`, local only) and `test_redis` (needs `TEST_REDIS_URL`, local, non-zero db index; it flushes that db). `make test-backend` sets both. Tests never call real providers: patch them at the module boundary, for example `monkeypatch.setattr("livestorm_app.transcript_client.fetch_session_transcript", fake)`.
