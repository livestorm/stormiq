# Frontend

A Vue 3 single-page app (Composition API, `<script setup>`) with vue-router, built by Vite into `frontend/dist`, which FastAPI serves. There is no Pinia, UI library or chart library: the store is a plain reactive module and charts are hand-built components. Architecture overview: [docs/architecture.md](../docs/architecture.md#frontend-code).

## Commands

From `frontend/`, or with `make web` and `make check` at the repo root:

- `npm run dev`: Vite on :5173, proxying `/api` to the API on :8000
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
- Styling follows [docs/brand.md](../docs/brand.md): Livestorm's design language, StormIQ's own identity. Never use the Livestorm logo or icon as the app logo: the app, the favicons (`public/favicon.svg`, `favicon.ico`, `apple-touch-icon.png`) and PDFs use the StormIQ mark (`components/ui/StormIqMark.vue`).

## Design system (the v1 redesign)

The redesign ships screen by screen behind a switch, off by default, so production users keep the classic app until they opt in.

- **The switch.** `?ui=v1` turns the v1 design on and remembers it in this browser; `?ui=classic` turns it off (`src/design/uiVersion.js`, applied once in `main.js`; `isV1()` reads it). It sets `<html data-ui="v1">`. With it on, `App.vue` renders the top-bar shell (`components/ui/TopBar.vue`) instead of the sidebar; views not rebuilt yet render unchanged inside a dark frame. A route built for v1 sets `meta: { v1Native: true }` and fills the page. The account menu and the footer link back to the classic design.
- **Where it lives.** Tokens, the Sora `@font-face` and v1 page basics: `src/design/tokens.css`. Theme: `src/design/theme.js` (pure) and `useTheme.js` (Vue). Base components: `src/components/ui/` (`Card`, `CardHeader`, `IconTile`, `StatCard`, `Collapsible`, `StatusWord`, `Chip`, `UiButton`, `TipBar`, `Icon`, `StormIqMark`, `ThemeToggle`, `TopBar`). Sora is in `public/fonts/` with its licence. Every component is shown at `/design` (v1 only).
- **Rule for new UI: tokens only.** Colours, type sizes, radii, spacing and motion come from `--siq-*` custom properties; no raw hex, rgb or named colours in components (the token file is the one place for values). Never touch `src/styles.css` for v1 work. Shared v1 classes (components, `tokens.css`) start with `siq-`; a view's own scoped classes can use a short prefix of their own (the gallery uses `g-`). `styles.css` still loads under v1 and its element selectors (`a`, `button`, `input`, `select`, `textarea`) don't care about class names: they style the classic views inside the v1 frame, so a v1 form control must set its own font, colours, border, height and `select` chevron instead of inheriting the classic dark ones.
- **Theme.** Light and dark come from the same tokens. The page follows the system until the user clicks the toggle; the choice is saved in `localStorage` (`stormiq:theme`). `index.html` applies the saved switch and theme before first paint (keep it in sync with `uiVersion.js` and `theme.js`; `tests/design.test.js` runs the inline script and checks it agrees with them). Print stays light. In the classic design the mark keeps its on-dark colours (`[data-colors-semantic="dark"]` in `tokens.css`).
- **Less reading.** Prefer cards with an icon-tile header, stat cards and icon bullets to paragraphs; put anything longer than two lines in a `Collapsible`. Status is always icon + word (`StatusWord`), never colour alone.
- **Numbers come from the server.** The v1 Report renders `record.measures[id]` from `GET /api/sessions/{id}/record` and computes no formula: no ratio, median, sum or threshold of its own. A percentage is `value` formatted; the "How we know" drawer shows `num`, `den`, `excluded`, `coverage` and `livestorm` as sent; a hidden value (`insufficient`, `unresolved`, `unavailable`) stays hidden, and an `unresolved` measure has no `num`. "Excludes your team (n)" is `excluded.total`; a time in minutes comes from the measure's own seconds (`coverage.medianAttendanceS`), never from a share times a length. Formulas and states: [docs/product.md §13](../docs/product.md#measures-v1-report).
- **Icons** are Lucide paths vendored in `Icon.vue` (licence in `components/ui/LICENSE-lucide.txt`); Livestorm's icon font is proprietary and can't be in this public repo. Add an icon by copying its SVG children from Lucide into `ICONS`.
- **Accessibility.** 40px targets (`--siq-target`), focus rings from `.siq-fx:focus-visible` (drawn inside, `outline-offset: -3px`, wherever a parent clips: a scrolling tab row, a flush card), transitions on named properties only (never `transition: all`), and `prefers-reduced-motion` honoured. Edges and current-item marks drawn with `box-shadow` also need a `@media (forced-colors: active)` border, because High Contrast removes shadows. A link that must not navigate while disabled uses `RouterLink`'s `custom` slot (a plain `@click` on `RouterLink` runs after it navigates).

## Tests

Vitest, with fake timers for polls. `tests/workspace.test.js` mocks `api.js` and covers the store's async behaviour (scopes, aborts, polls, loading flags); `tests/api-and-csv.test.js` covers URL building, 401 handling and CSV escaping. `tests/audience.test.js` covers the audience-only chat split (and its internal-session option, driven by the workspace's `meta.internalSession`) and what counts as an answered question (`src/utils/audience.js`). `tests/design.test.js` covers the v1 switch and the theme (defaults, persistence, query params, storage that throws, the system preference), the `index.html` pre-paint script against both, and `useTheme`. Add or update a test with every store change.

`tests/fixtures/record-v1.json` is what `GET /api/sessions/{id}/record` returns for a synthetic session ([docs/api.md](../docs/api.md#the-v1-record)). The backend generates it from the real route, and a backend test fails when it drifts; build v1 Report components against it and never edit it by hand.
