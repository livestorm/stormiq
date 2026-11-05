# AI outputs

What each AI output contains, how it is stored, and the rules for saving it. The prompts are plain text in [`prompts/`](../prompts/); the jobs that produce the outputs are in [jobs.md](jobs.md#ai-generation), and the models in [providers.md](providers.md).

## Overall Analysis

Markdown with 5 sections ([prompt](../prompts/analysis_base_prompt.txt)):

1. Executive Summary
2. Key Themes
3. Engagement Insights
4. Risks / Friction Signals
5. Actionable Recommendations

Stored as `analysis_bundle: {English: "...", French: "..."}`. The legacy `analysis_md` column mirrors English only.

Chat statistics sent to the model (`llm_inputs.build_derived_stats`) count audience messages only, with `excluded_team_messages` giving the number of team and guest-speaker messages left out. The chat messages themselves are sent with their `from_team_member` and `from_guest_speaker` flags, as the deep analysis already did. The session summary's `total_messages_count` and `participant_summary.top_chatters` leave team members and guest speakers out too, and each question carries `answered_by` and `answered_orally`. `total_messages_count` counts the chat's audience messages, as Session Overview's Chat Messages does, an empty cached chat as 0 ([product.md §13](product.md#13-glossary-of-metrics-and-formulas)); `top_chatters` still ranks people by their own `messages_count`. On an internal session ([product.md §13](product.md#13-glossary-of-metrics-and-formulas)) nothing is left out, and `derived_stats.internal_session` is `true` so the model knows the team's messages are the audience's.

## Deep Analysis

Markdown with 10 sections, with the headings spelled exactly as in the [prompt](../prompts/analysis_deep_prompt.txt):

1. Executive Summary
2. Session Scores (Clarity, Engagement, Interaction, Pace, Alignment; 0–100 each)
3. Key Moments (timestamped and typed: strong, confusion, engagement, drop)
4. Speaker And Interaction Analysis
5. Audience Intent Analysis
6. Cross-Source Synthesis
7. Friction And Risk Signals
8. Business Signals And KPI Mentions
9. Actionable Recommendations (Next Session, Follow-up, Optional)
10. Risks, Ambiguities, And Data Quality Limits

Stored as `deep_analysis_bundle: {English: "...", French: "..."}`. The legacy `deep_analysis_md` column mirrors English only.

The frontend splits the markdown into sections with a regex parser (`parsedDeepSections` in `frontend/src/views/AnalysisView.vue`) that accepts English and French headings. **Known fragility:** it depends on the model reproducing the exact headings. The card registry on the [roadmap](roadmap.md) replaces it.

## Smart Recap

Markdown with `# Title` and `# Description`, in one of three tones: `professional`, `hype`, `surprise`. Input is the transcript only. Recaps are always written in English; there is no language selector. Stored as `smart_recap_bundle: {professional: "...", hype: "...", surprise: "..."}`. A Professional recap also triggers the session's cover image ([jobs.md](jobs.md#cover-images)).

## Content Repurposing

Four assets generated together as one JSON bundle, from the transcript only ([prompt](../prompts/content_repurpose_bundle_prompt.txt)), then split. The call is retried up to twice when the bundle is incomplete or in the wrong language. An empty transcript makes no model call: `generation.generate_content_repurpose_bundle_with_openai` raises, and the job fails. A section holding the old empty-transcript placeholder ("Transcript is empty.") counts as missing (`generation.missing_bundle_sections`), and stored bundles made of it are treated as absent (`api_logic._stored_content_bundles`), so they can be generated again.

- `summary`: 500–700 words
- `blog`: 1,000–1,500 words, a standalone article
- `email`: subject-line options and 2 versions of 200–300 words
- `social_media`: LinkedIn, Facebook and X posts with hashtags

Stored as `content_repurpose_bundle: {English: {summary, blog, email, social_media}, French: {...}}`.

## Languages

- Overall Analysis, Deep Analysis and Content Repurposing come in English and French. English is generated from the sources; French is a translation of the saved English output, so it needs English first (the API returns 400 otherwise). A language already in the bundle is reused; only a missing one costs a model call.
- Smart Recap is always in English.
- The interface is in English. The Analysis and Content Repurposing views switch their labels when French is selected for their output.
- Gladia transcribes in the spoken language, whatever the interface language.

## Saving rules

- **Only complete output is saved.** Empty output (`api_logic._require_generated_text`), output cut off at the token limit, a content-repurposing bundle missing any of its four sections, or content repurposing on an empty transcript fails the job with a clear error and saves nothing. No runner substitutes placeholder text.
- **Truncation.** `llm_client.call_llm` checks why the model stopped (`stop_reason == "max_tokens"` on Anthropic, `finish_reason == "length"` on OpenAI). A truncated answer is retried once with double the token budget, capped at `TRUNCATION_RETRY_MAX_TOKENS` (16,000). If it's still cut off, `LLMOutputTruncated` is raised and nothing is saved.
- **One entry at a time.** Each job writes only its own language or tone (`db.merge_cached_bundle_entry`), see [data-model.md](data-model.md#caching-rules).

## Exports

Every stored AI output has a PDF route, rendered with reportlab in [`pdf.py`](../livestorm_app/pdf.py): overall and deep analysis per language, Smart Recap per tone, and each content asset per language. Each PDF is headed by the StormIQ mark, drawn in code (never the Livestorm logo), and every page carries the footer `pdf.PDF_FOOTER_TEXT` (`pdf.PDF_FOOTER_TEXT_FR` for French exports), the app's "not an official Livestorm product" notice. `**bold**` renders as bold, `***bold italic***` as bold italic and `---` separators as a thin rule; other inline markdown is printed as text. Files are named `stormiq-…`.

## Prompts

All prompts live in `prompts/*.txt` and can be tuned without code changes; the paths are in [`config.py`](../livestorm_app/config.py). The overall analysis is assembled from the base prompt plus per-source prompts (chat, questions, transcript, all sources).
