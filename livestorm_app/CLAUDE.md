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
- Hot paths (polls, job starts) read with `fetch_session_fields`; add a field to `_SESSION_FIELD_SQL` rather than loading the full row. → [docs/data-model.md](../docs/data-model.md)

**Jobs (`queue.py`, `worker.py`)**
- Enqueue only through `queue.enqueue_unique_job_sync`, with a deterministic id (`ai_job_id`, `transcription_job_id`, `cover_image_job_id`).
- Job arguments are ids only: no keys, tokens or connection ids.
- A handler that users watch publishes progress (`progress.publish_progress_sync`) and clears it in `finally`. Every handler re-raises failures so arq records them (marking its own Postgres row `error` first, if it has one, as transcription does) and is registered in `WorkerSettings.functions` with its own `timeout`. → [docs/jobs.md](../docs/jobs.md)

**Outbound calls**
- Every HTTP call has a timeout. Retry only failures where nothing was produced or billed; never retry a POST that starts paid work. → [docs/providers.md](../docs/providers.md)
- The Livestorm HTTP session (`livestorm.client.livestorm_get`) is shared by all users: no cookies, no per-user state.
- A Livestorm fetcher that returns people strips registrant join links before returning (`livestorm.payloads.strip_payload_join_links`); StormIQ never stores them. A pager asks for at most `config.LIVESTORM_MAX_PAGE_SIZE` (50) per page and stops when `meta.next_page` is null.

**Workspace reads**
- Get transcript insights through `api_logic._cached_transcript_insights`, once per read, and copy a shared DataFrame before modifying it.
- Don't add raw upstream payloads to the serialized workspace; add the derived table a view needs.

**Errors**
- Return `{resource, message, details}`. Log upstream URLs and bodies server-side; never return them.

**The v1 record (`record/`)**
- New code for the v1 Report goes in `record/`: pure functions over cached payloads, with no database, network or FastAPI. Classic code (`analytics/`, `api_logic`) doesn't import it, so a bug there can't break a classic read.
- Times come from the raw payload values (`created_at`, `responded_at`: unix seconds or ISO strings), read through `record.clock`, never from the formatted strings of the classic tables. A session without `started_at` has no clock: never fall back to `estimated_started_at`.
- Evidence ids (`C0001`, `Q001`, `A001`) come only from `record.evidence.audience_events`. Anything that stores one also stores the Livestorm id. Rules: [docs/product.md §13](../docs/product.md#session-clock-v1-report).
- v1 code reads person-level data only through `record/roster.py`, loaded by `record.inputs.load_record_inputs`; each people record is parsed by `livestorm.payloads.parse_person`. A person row keeps the email domain, never the address. A v1 prompt gets people only through `roster.prompt_view` (ref, role, job title); a page gets names only through `roster.display_people`, which fails closed. Roles never change for an internal session: read `Roster.meta.internal`. Rules: [docs/product.md §13](../docs/product.md#roster-v1-report).

## Tests (`../tests/`)

| File | Covers |
|---|---|
| `test_access_control.py` | every session route × anonymous, other org, same org, admin |
| `test_db_scope.py` | org-scoped SQL |
| `test_data_integrity.py` | bundle writes, write failures, incomplete AI output, join links never saved, `rewrite_base_payloads` |
| `test_job_reliability.py` | dedupe, retries, timeouts, the transcript job lifecycle, cancellation, the Gladia request built once |
| `test_gladia_merge.py` | merging a long recording's Gladia chunks (synthetic payloads), and what classic insights read from the result |
| `test_performance.py` | the insights cache, field reads, pools, the Redis cooldown, outputs-only polls |
| `test_pure_functions.py` | metrics and formulas, PDF rendering, the Anthropic request's `temperature`, the join-link strip, the Livestorm pagers |
| `test_session_clock.py` | the session clock, its fallbacks, minute bins and evidence ids (`record/`); the payload shapes script's local-database check |
| `test_roster.py` | `parse_person`, roles, organizers, channels, lead time, field definitions, `P-` refs, the prompt and display views, the record-inputs loader (`record/`); classic code never imports `record/` |

`conftest.py` provides `test_database` (needs `TEST_DATABASE_URL`, local only) and `test_redis` (needs `TEST_REDIS_URL`, local, non-zero db index; it flushes that db). `make test-backend` sets both. Tests never call real providers: patch them at the module boundary, for example `monkeypatch.setattr("livestorm_app.transcript_client.fetch_session_transcript", fake)`.
