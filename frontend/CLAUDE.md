# Frontend

A Vue 3 single-page app (Composition API, `<script setup>`) with vue-router, built by Vite into `frontend/dist`, which FastAPI serves. There is no Pinia, UI library or chart library: the store is a plain reactive module and charts are hand-built components. Architecture overview: [docs/architecture.md](../docs/architecture.md#frontend-code).

## Commands

From `frontend/`, or with `make web` and `make check` at the repo root:

- `npm run dev`: Vite on :5173, proxying `/api` and `/brand-assets` to the API on :8000
- `npm test`: Vitest (`tests/`)
- `npm run lint`: ESLint
- `npm run build`: production build

## Store rules (`src/store/workspace.js`)

- **Session scope.** Every load, job start, poll and save captures the session it started for (`openSessionScope`: the id plus an `AbortController`). Opening another session or signing out aborts the old scope's requests, stops its pollers and drops late results. After every `await`, check `isCurrent(scope)` or call `assertCurrent(scope)` before writing state. AI jobs run against the session on screen (`displayedSessionScope()`), never against the Search form's inputs.
- **Loading flags** belong to the call that set them last (`beginLoading` returns a token for `endLoading`), so a stale flow's `finally` can't clear a newer flow's flag.
- **Polls survive blips.** Network errors, 408, 429, 5xx and an `unknown` job status are retried with backoff; 5 failures in a row, or any other error (401, 404…), stop the poll.
- **`state.workspace` is `markRaw`**, so Vue tracks the property rather than the thousands of rows inside it. Never mutate it in place; replace it: `setWorkspace({ ...state.workspace, outputs })`. Outputs-only job results are merged with `applyResult`.
- **401 anywhere** calls the handler `App.vue` registers with `onUnauthorized`: it clears the signed-in state and the workspace and shows a "reconnect" message. It doesn't navigate, so the sidebar's Connect button returns to the same page and shared links keep working. Account-level lists (session cards, events) drop responses that arrive after the account changed (`sameAccount`).

## API calls (`src/api.js`)

- Every request goes through `api.js`. Add session endpoints with its `sessionPath(id, suffix)` helper, which encodes the id; never interpolate ids into URLs by hand.
- Pass the scope's `{ signal }`, so switching session cancels the request.
- AI job polls use `getAiJobStatus`, which asks the server for outputs only.

## Views and components

- Routes are in `src/router.js`. Every view except the session list is lazy-loaded; keep it that way so the first page stays small.
- `/single-analysis/:sessionId/...` is a sharing contract. Don't break existing paths; add a redirect when one moves.
- CSV downloads go through `src/utils/csv.js` (`buildCsv`, `escapeCsvCell`). Text cells starting with `=`, `+`, `-`, `@`, tab or CR get a leading `'`, because chat and questions are written by attendees.
- Styling follows [docs/brand.md](../docs/brand.md): Livestorm's design language, StormIQ's own identity. Never use the Livestorm logo as the app logo (`App.vue` still shows a Livestorm icon in the sidebar and top bar; the redesign replaces it with a StormIQ mark).

## Tests

Vitest, with fake timers for polls. `tests/workspace.test.js` mocks `api.js` and covers the store's async behaviour (scopes, aborts, polls, loading flags); `tests/api-and-csv.test.js` covers URL building, 401 handling and CSV escaping. `tests/audience.test.js` covers the audience-only chat split and what counts as an answered question (`src/utils/audience.js`). Add or update a test with every store change.
