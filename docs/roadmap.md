# Roadmap

What's next, in order. What has shipped is in [changelog.md](changelog.md).

## 1. Redesign

A full visual and UX overhaul that uses Livestorm's design language (colours, Object Sans, icons, radii, shadows, motion, writing voice) while the app stays **StormIQ**: its own name and mark, and the "not an official Livestorm product" notice ([brand.md](brand.md)).

1. Prototype two directions for the key screens; choose one.
2. Design tokens and base components: buttons, cards, accessible tabs, paginated tables, empty states, loading skeletons, toasts.
3. The app shell and navigation, with a StormIQ mark in place of the Livestorm icon the sidebar uses today.
4. Each screen, splitting the oversized views and `styles.css` along the way.
5. Rewrite [product.md](product.md) for the new navigation (it still describes the older Events-based flow).

Object Sans is a licensed font and isn't in the repo; the web-font files have to come from Livestorm's brand kit.

## 2. Card registry for Deep Analysis

Replace the markdown blob and its regex section parser ([ai-outputs.md](ai-outputs.md#deep-analysis)) with a registry of cards.

- A Python registry under `livestorm_app/cards/single/`. Each card declares `id`, `tab`, `order`, `title`, an optional LLM prompt fragment, `build()`, and its view component.
- One LLM call writes every narrative paragraph as structured JSON; deterministic data comes from `build()` over the existing DataFrames.
- Sections that should become deterministic-first cards:
  - **Session Scores**: from `engagement_df` aggregates, no LLM.
  - **Key Moments**: already in `key_moments_df`; the LLM writes a one-liner.
  - **Speaker Dynamics**: a chart row from `speaker_df` and `interruptions_df`.
  - **Friction Signals**: a chart from `low_energy_df`.
  - **Business Signals**: a table from `numbers_df`.
- The frontend renders cards instead of parsing headings. The redesign's analysis screens should be designed around this.

## 3. Cross-session analysis

Comparative (2 sessions) and cumulative (up to 10) analysis. Depends on the card registry.

- A `cross_analyses` table:
  ```sql
  cross_analyses (
    id               TEXT PRIMARY KEY,          -- uuid
    user_id          TEXT,                      -- oauth_connections.user_id
    mode             TEXT,                      -- 'comparative' | 'cumulative'
    session_ids      JSONB,
    session_count    INT,
    status           TEXT,                      -- 'pending' | 'processing' | 'done' | 'error'
    result           JSONB,                     -- card-keyed result map
    context_strategy TEXT,                      -- 'full' | 'trimmed' | 'summary-only'
    error_message    TEXT,
    created_at       TIMESTAMPTZ,
    completed_at     TIMESTAMPTZ
  )
  ```
- Routes: `POST /api/cross-analysis/comparative {sessionIdA, sessionIdB, outputLanguage}`, `POST /api/cross-analysis/cumulative {sessionIds[], outputLanguage}`, `GET /api/cross-analysis/{id}`, `GET /api/cross-analysis`. Org-scoped like every session route.
- A loader, `load_cross_session_sources(session_ids)`, builds one slice per session (meta, transcript and insights, chat and questions frames, speaker names, prior overall and deep analyses) and raises `PriorAnalysisMissingError([ids])` if any session hasn't been analysed: no job is queued without every prerequisite.
- Cumulative starts capped at 10 sessions; raise to 20 once context-strategy switching is tuned.
- Card ideas. Comparative: engagement head-to-head, pacing overlay, audience question overlap, speaker dynamics, friction, cross-pollination recommendations. Cumulative: audience DNA, engagement trend, speaker drift, topic map, unanswered-questions backlog, strategic read, next-session brief.
- English and French, with PDF export, like single-session analyses.

## 4. Card preferences

- Per-tab card grids on every analysis surface, with header icons and an optional info popover per card.
- Per-user card preferences (hide, reorder) in a `user_card_preferences` table keyed by `oauth_connections.user_id`.
- Empty-state cards inline, instead of hiding whole sections behind "Generate" buttons.

## Maybe later

- API-key rotation with failover.
- Usage and cost observability per account.
- A webhook that analyses a session automatically when it ends.
- White-label PDF export.

## Known limitations

- Empty model output for an overall analysis, deep analysis or Smart Recap is saved as the placeholder "No analysis text returned by model." instead of failing the job (`services.analyze_with_openai` substitutes it before `api_logic._require_generated_text` can catch it).
- A worker killed mid-job (OOM) blocks regenerating that output for about 2 hours ([jobs.md](jobs.md#timeouts-and-failures)).
- Sessions cached before org scoping stay invisible to their org until someone refetches them.
- Only the latest version of each output is kept.
