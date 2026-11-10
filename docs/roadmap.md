# Roadmap

What's next, in order. What has shipped is in [changelog.md](changelog.md).

## 1. Redesign

A full visual and UX overhaul that uses Livestorm's design language (colours, Object Sans, icons, radii, shadows, motion, writing voice) while the app stays **StormIQ**: its own name and mark, and the "not an official Livestorm product" notice ([brand.md](brand.md)).

1. ~~Prototype two directions for the key screens; choose one.~~ Done: the Winter direction, light and dark, in Sora.
2. ~~Design tokens and the first base components~~ (cards, stat cards, collapsibles, status words, chips, buttons, tip bar) and ~~the app shell with the StormIQ mark~~: shipped ([changelog](changelog.md)); since November 2026 they are the only design. Still to come: accessible tabs, paginated tables, empty states, loading skeletons, toasts.
3. Each screen, rebuilt on the v1 components, splitting the oversized views and `styles.css` along the way. ~~Done~~ (November 2026): every page (home, Sessions, Add session, the session's Report, Transcript and Follow-up, Admin, the beta notice). Next: the Follow-up design's new parts (the question desk, "Before you share", FAQ and chapters, the open questions CSV).
4. ~~Turn v1 on for everyone and remove the switch~~ (November 2026); next: remove the classic styles (`styles.css`), now that no page uses them.
5. Rewrite [product.md](product.md) for the new navigation (it still describes the older Events-based flow).

The v1 design uses Sora (open licence) instead of Object Sans, which is licensed and isn't in the repo, and Lucide icons instead of Livestorm's proprietary icon font ([brand.md](brand.md#the-v1-design)).

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

## 3. Card preferences

- Per-tab card grids on every analysis surface, with header icons and an optional info popover per card.
- Per-user card preferences (hide, reorder) in a `user_card_preferences` table keyed by `oauth_connections.user_id`.
- Empty-state cards inline, instead of hiding whole sections behind "Generate" buttons.

## 4. Models

- Replace the four OpenAI text models before OpenAI shuts them down on 2027-01-14 ([providers.md](providers.md#choosing-the-provider-and-models)).
- Offer the Claude models that think by default (Claude 5 and later) for the classic outputs once each has a larger token budget and sends its effort. The catalog already offers the 5.5 models for the v1 jobs ([providers.md](providers.md#the-model-catalog)).
- Admin: one model per job, read from the server (`llm_model.<provider>.<kind>` is saved and used, but Admin doesn't show it yet).
- Admin: usage and cost per day, job and model, read from `llm_usage` (every text-model call is logged there; nothing shows it yet).
- Set `ORG_DAILY_LLM_BUDGET_USD` before the first automatic text job ships, and fill the OpenAI prices first if that job may run on OpenAI: with a budget set, an automatic job never calls an unpriced model ([providers.md](providers.md#usage-and-the-daily-budget)).
- Count what a declined attempt cost when the server-side fallback answers: the usage row's tokens and cost are the answering attempt's alone, and the declined one is only in `usage_raw.iterations`, so the daily budget doesn't count it ([providers.md](providers.md#request-layouts-and-caching)).
- Record the tokens of an Anthropic stream that fails or passes its deadline after its first event: they may be billed, but the call leaves no `llm_usage` row, so the daily budget doesn't count them ([providers.md](providers.md#the-anthropic-sdk)).
- Ask the classic content bundle for its JSON with a schema (`llm_client.generate_json`) instead of in the prompt, which would drop the "incomplete JSON" retry ([ai-outputs.md](ai-outputs.md#content-repurposing)).
- Log the cover image call in `llm_usage` and hold it to the daily budget. The worker already starts it on its own after a Professional Smart Recap, through its own OpenAI Images request ([jobs.md](jobs.md#cover-images)).

## Maybe later

- API-key rotation with failover.
- A webhook that analyses a session automatically when it ends.
- White-label PDF export.

## Known limitations

- A worker killed mid-job (OOM) blocks regenerating that output for about 2 hours ([jobs.md](jobs.md#timeouts-and-failures)).
- Sessions cached before org scoping stay invisible to their org until someone refetches them.
- A transcription that times out or is interrupted leaves its Gladia job running, and a retry starts a new one, which Gladia bills again. Each Gladia job carries ids (`custom_metadata`) so a later change can find it and re-attach instead ([providers.md](providers.md#gladia-configuration)). Not built yet.
- Chats cached before StormIQ asked for `include=people` have no people links, so their authors stay unresolved in the v1 Report ([product.md §13](product.md#authors-v1-report)). Not planned: the sessions cached before v1 will be deleted when v1 ships, and every session is fetched again from scratch.
- Only the latest version of each output is kept.
