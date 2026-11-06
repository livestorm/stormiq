# Security and access

Who can see what, and how the code enforces it. Code: [`auth.py`](../livestorm_app/auth.py), [`oauth_client.py`](../livestorm_app/oauth_client.py), and the org-scoped reads in [`db.py`](../livestorm_app/db.py).

## Identity

Every request's identity is resolved once, by `livestorm_app/auth.py`. Routes declare it as a FastAPI dependency (`get_caller`, `require_admin` or `get_optional_caller`) and never work out org ids or admin status themselves.

There are two ways in:

1. **Livestorm OAuth**, the standard path. The session cookie is `livestorm_oauth_connection`. The flow uses PKCE, with an HMAC-signed handshake cookie that expires after 10 minutes and is checked server-side. Access tokens are refreshed when they are within 5 minutes of expiry; a failed refresh returns 401 "Please reconnect".
2. **Local-dev mode**, for developers only. It is on when `STORMIQ_LOCAL_DEV=1` **and** the process is not on Render (Render sets `RENDER=true` on every service). Requests must also use a loopback `Host` (`localhost`, `127.0.0.1`, `::1`). That check doesn't authenticate anyone: it stops a web page from reaching a developer's local server through DNS rebinding. In local-dev mode:
   - requests without an OAuth cookie act as a trusted local operator;
   - Livestorm calls use `LS_API_KEY`;
   - `/api/dev/auto-login` creates an OAuth-like identity from `LS_API_KEY`. Its fixed connection id (`dev-auto-login`) is rejected whenever local-dev mode is off;
   - an `apiKey` field in a request body is accepted. Outside local-dev mode it returns 400.

## Caller kinds and read scope

`Caller.read_scope` decides which cached sessions a caller can read:

| Kind | Who | Reads cached sessions of |
|---|---|---|
| `user` | OAuth user | their own `organization_id` only. A connection with no org sees nothing. |
| `admin` | OAuth user in `ADMIN_EMAILS` or the `admin_users` table | every org (`ALL_ORGS`) |
| `local_dev` | no OAuth cookie, local-dev mode on | every org (`ALL_ORGS`) |
| anonymous | everyone else | nothing: **401** on every session, event and admin route |

Teammates in one Livestorm organization share cached sessions and AI outputs. Callers from another organization can't read them, not even with a shared link.

Admins come from the `admin_users` table plus the `ADMIN_EMAILS` env var. `ADMIN_EMAILS` admins can't be demoted from the UI, and nobody can demote themselves.

## Fail-closed rules

- `db.fetch_cached_session`, `db.fetch_session_fields` and `db.fetch_cover_image` require an explicit `organization_id` scope. A blank scope matches no rows. `ALL_ORGS` is a named sentinel that only admin, local-dev and worker code pass.
- A session outside the caller's scope looks exactly like a missing one: 404, or 204 on `/cached`.
- Writes (`speaker-labels`) and AI-job POSTs first check that the row is in the caller's scope.
- Fetch routes stamp `organization_id` on a row only after the caller's own Livestorm credentials prove access to the session (Livestorm returns 404 otherwise). The stamp is first-writer-wins, so stamping without that proof would let an outsider claim a session before its owner fetches it.
- AI job polls derive the job id from the session, kind and language or tone. They ignore any client-supplied `jobId`, because job ids are predictable.
- `tests/test_access_control.py` enumerates every `/api/sessions/{session_id}*` route from `app.routes` and asserts: anonymous → 401; another org → no data, no writes, no enqueues; same org and admin → 2xx. A new route that forgets the dependency fails CI.

## Secrets

The backend uses these secrets and never sends them to the browser: `CLAUDE_API_KEY`, `OPENAI_API_KEY`, `GLADIA_KEY`, the OAuth client credentials, `SESSION_SECRET`, the Postgres URL and `LS_API_KEY`.

- Job arguments never carry secrets or connection ids. arq stores arguments in Redis and logs them, and a connection id is also the user's session cookie. The worker reads API keys from its own environment and the user's Livestorm connection from the job's `transcript_jobs` row (see [jobs.md](jobs.md)).
- The metadata sent with each Gladia job holds ids, counts and labels only: the session and recording ids, the file size, an environment tag (`render` or `local`), the chunk numbers and offset, and a fingerprint of the settings. `transcriber._custom_metadata` takes an allow-listed set of keys and raises on anything else, so a key, token, connection id or the recording's signed URL can't reach it ([providers.md](providers.md#gladia-configuration)). The stored transcript keeps a copy of it.
- `session_cache.account_key_hash` is the SHA-256 of the first writer's Livestorm credential, set on insert and never updated. It is informational; `organization_id` is the access boundary. Rows written before November 2026 may hold the hash of an LLM provider key instead; that can't be repaired and doesn't affect access.
- Error responses are `{resource, message, details: {resource, status_code?}}`. Upstream URLs and response bodies are logged server-side only.

## Data StormIQ doesn't keep

StormIQ never stores a registrant's personal join link; it doesn't need it.

- Livestorm returns one for each person: `password_key` and `connection_link` in `attributes.registrant_detail`.
- `livestorm/client.py` removes both before a fetcher returns the session, chat or questions payload (`payloads.strip_payload_join_links`). So they never reach `session_cache`. Any new fetcher that returns people strips them the same way.
- `scripts/strip_registrant_join_links.py` removes them from rows saved before this rule.

StormIQ doesn't store the chat's people twice. The chat call asks for `include=people` only to get each message's people id; `fetch_chat_messages` drops the `included` people records. The session payload held every chat author in the one real session checked; an author missing from it reads unresolved and is never stored from the chat. The chat payload keeps the ids, not the people.

## What v1 code reads about people

The cached session payload still holds each person's full Livestorm record, minus the join links, until StormIQ stores its own people rows in place of `include=people`. The v1 roster limits what v1 code reads from it. It is built in memory and is not stored (`payloads.parse_person`, `record/roster.py`):

- It reads the email domain, never the email address, and the referrer's host, never the full URL (its query string can hold personal values).
- It leaves out phone, street address, city, social profiles, browser, OS, screen size, IP city, avatar, highlight flag and join links.
- Registration answers are text and select fields only. Never the identity fields (email, names, avatar), Livestorm's built-in contact fields (phone, address, city, Twitter, LinkedIn, Facebook, website), or email, URL and consent fields. The built-in `country` field is kept.
- A v1 prompt gets people only through `roster.prompt_view`: a `P-` ref, a role and a job title. Never a name, email domain, company or Livestorm id.
- A v1 page gets names only through `roster.display_people`, which fails closed: only a session whose profile is "public" lists anyone.
- The v1 record route (`GET /api/sessions/{id}/record`) sends counts, measures and session-level facts only: no name, email, company, person or message id, message or transcript text, or speaker label (`record/assemble.py`, `record/measures.py`, `record/transcript_health.py`). The transcript's health counts the host's speaker labels and never sends them; its unclear clusters cite StormIQ's own utterance ids (`U0001`). The only Livestorm id in it is the session's own. `tests/test_record_route.py` scans its response for them.
- Its channel and segment measures name a channel (a UTM source, a referrer's host or a registration type) or a select answer only when enough audience registrants share it; smaller groups fold into one `(other)` row, so no small group is named. Text answers are never grouped. The minimums: [Channel quality](product.md#channel-quality) and [Segment quality](product.md#segment-quality).

## Model usage rows

Every answered text-model call (`llm_client.call_llm`, `generate_text`, `generate_json`) leaves one `llm_usage` row and one `llm_usage …` log line ([data-model.md](data-model.md#llm_usage)). Both hold ids (organization, session, the arq job id), the model, token counts, the estimated cost, the time taken, the stop reason and the provider's request id. Never a prompt, an output, a name, an email or a key:

- The row is built from the call's ids and the provider's `usage` object, never from the request or the answer (`ai.usage.build_usage_row`). `usage_raw` keeps numbers and short enum-like tags only (`ai.usage.sanitize_usage`), so free text the provider might add is dropped.
- `job_id` is the deterministic arq id, never a connection id.
- A refusal keeps only Anthropic's category, and only when it is a short tag, in its usage row and log line; the error users see never names it. OpenAI's refusal wording and any partial answer are dropped. When a JSON answer fails its schema, the error and the log name how many checks failed and their kinds, never the answer's text or values.
- When the insert fails, the log names the exception's class and SQL state, never its message.
- `tests/test_llm_usage.py` and `tests/test_data_integrity.py` run calls with the key `sk-test-SECRET` and check it never reaches a row, a log line (the ERROR line of a failed insert included) or the error a cut-off, refused or invalid answer raises (`tests/test_llm_responses.py`).

Usage rows stay when their session is deleted: they hold no content, and they keep past spend counted for the daily budget.

## Web hardening

- Cookies are `HttpOnly`, `SameSite=Lax`, and `Secure` everywhere except plain-HTTP local development (on Render, TLS ends at the proxy, so `RENDER` alone turns it on).
- There is no CORS middleware: the SPA is same-origin (Vite proxies `/api` in development).
- `/docs`, `/redoc` and `/openapi.json` are served locally only; they are disabled on Render.
- The SPA catch-all serves only files that resolve inside `frontend/dist` (no `..` escapes) and returns JSON 404s for unknown `/api/*` paths.
- Session ids in paths must match `^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$` (422 otherwise). The frontend encodes every id it puts in a path.
