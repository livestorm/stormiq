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
| `/api/admin/settings` | GET, POST | admin | Read or update allow-listed `system_settings` keys |
| `/api/workspace-events` | POST | caller | Paginated list of events (at most 50 per page), filtered by title and status |
| `/api/workspace-sessions` | GET | public¹ | Cached session cards for the caller's own org |
| `/api/event-sessions` | POST | caller | List the sessions of an event |
| `/api/sessions/{id}/cover.png` | GET | caller | Cover image (`Cache-Control: private, max-age=86400`) |
| `/api/sessions/{id}` | GET | caller | Cached workspace (404 if not cached) |
| `/api/sessions/{id}/cached` | GET | caller | Same, but 204 instead of 404 when missing |
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

## Adding a route

1. Put it in the router module for its area in `livestorm_app/routes/`, with any request body in `routes/models.py`. Routes match in registration order, and the SPA catch-all (`routes/spa.py`) must stay last.
2. Take the identity from `livestorm_app.auth` (`Depends(get_caller)` or `Depends(require_admin)`); session routes read with the caller's scope.
3. Add a row to the table above.
4. Run the tests: `tests/test_access_control.py` picks up every `/api/sessions/{session_id}*` route automatically.
