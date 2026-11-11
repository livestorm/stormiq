# AI outputs

What each AI output contains, how it is stored, and the rules for saving it. The prompts are plain text in [`prompts/`](../prompts/); the jobs that produce the outputs are in [jobs.md](jobs.md#ai-generation), and the models in [providers.md](providers.md).

## Session analysis

The Follow-up tab's "Session analysis" (job kind `overall_analysis`, kept for its queue, bundle and PDF). Since November 2026 it is a v1 job: one Sonnet 5.5 call at medium effort (`ai/session_analysis.py`, the `dossier` layout, 6,000 answer tokens) over the session's **brief**, not the raw Livestorm payloads ([prompt](../prompts/session_analysis.txt)).

- **The brief** (`record.session_brief.build_brief`, built by `api_logic._write_session_analysis`): the session's title, length, language and whether it is internal; the Report's numbers, only those it would show, worded with their units (`number_lines`: attendees and show-up, time in the room, who wrote, questions, Q&A answered, time to answer, speaking pace, talk time per speaker key); the busiest minutes of the audience's chat and what it was about (`chat_lines`); the chapters, the prompts to the room and the figures said on stage from the stored understanding, a flagged figure marked "do not state it as a fact"; the audience's questions with their desk status and where the talk answered them, never their author (`question_lines`); and the transcript by speaker key (`S0`). No name, email, company, people id or `P-` ref; one session gives the same bytes every time.
- **It needs "Analyse session" first**: the brief is built only while the stored understanding was read from the session as it is now (`understanding.current_reading`); otherwise the job fails with `SessionNotAnalysedError` and says to run it on the Report. It also needs the transcript.
- **Four sections**, in the session analysis's language: What happened, What landed with the audience, Questions, What to do next. The prompt has the model quote the brief's numbers as given and compute none, call speakers by their role, and never repeat a flagged figure as a fact.
- About $0.10–0.20 a run on a one-hour session. The French version is a translation of the English one (the `translation` job), as before.

Stored as `analysis_bundle: {English: "...", French: "..."}`. The legacy `analysis_md` column mirrors English only. Analyses written before November 2026 stay as they were until written again.

## FAQ

The Follow-up tab's "FAQ" (job kind `faq`): one Haiku 5.5 call at low effort (`ai/faq.py`, the `dossier` layout, 4,000 answer tokens) over the question desk ([prompt](../prompts/faq.txt)).

- **The brief** (`record.faq_brief.build_faq_brief`, built by `api_logic._write_faq`): the session's title and whether it is internal, then each question of the desk (`question_desk`) in its order, under an id of the brief's own (`q1`): where and when it was asked, its status, its text, and the answers StormIQ holds for it: Livestorm's written answer, the host's answer saved on the desk, and the passage of the talk that answers it with its time. A question marked answered aloud with no passage found says so. Never who asked, never the desk's ids.
- **It needs "Analyse session" first**: the chat must be labelled (the desk lists the chat's questions from the labels) and, when there is a transcript, the session read as it is now (the talk's answers); otherwise the job fails with `SessionNotAnalysedError`. A session without a transcript gets a FAQ from its Q&A and chat alone. No question, no FAQ (`NoQuestionsError`).
- **Two sections**, in the FAQ's language: Questions and answers (each answered question, reworded neutrally and merged with its duplicates, as a level-3 heading, its answer built only from the brief's answers, "(Answered at 5:19 in the replay.)" when it comes from the talk) and Still to answer (left out when nothing is open). The prompt has the model drop every name, greeting and off-topic question.
- The French version is a translation of the English one (the `translation` job), as for the Session analysis.

Stored as `faq_bundle: {English: "...", French: "..."}`.

## Deep Analysis

The Follow-up tab no longer lists it (November 2026); its route, job and stored outputs stay. It reads the classic inputs below.

Chat statistics sent to the model (`llm_inputs.build_derived_stats`) count audience messages only, with `excluded_team_messages` giving the number of team and guest-speaker messages left out. The chat messages themselves are sent with their `from_team_member` and `from_guest_speaker` flags, as the deep analysis already did. The session summary's `total_messages_count` and `participant_summary.top_chatters` leave team members and guest speakers out too, and each question carries `answered_by` and `answered_orally`. `total_messages_count` counts the chat's audience messages, as Session Overview's Chat Messages does, an empty cached chat as 0 ([product.md §13](product.md#13-glossary-of-metrics-and-formulas)); `top_chatters` still ranks people by their own `messages_count`. On an internal session ([product.md §13](product.md#13-glossary-of-metrics-and-formulas)) nothing is left out, and `derived_stats.internal_session` is `true` so the model knows the team's messages are the audience's.

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

## Chat labels (v1)

One label per chat message, for the v1 Report: its class (content, question, tech, logistics, social, laughter, off-topic), what a question is about (content or logistics), and its language as an ISO 639 code ([prompt](../prompts/chat_labels.txt)). It runs in the session analysis the user starts (`POST /api/sessions/{id}/session-analysis`), never on its own, and is skipped when the stored labels already describe the session's chat ([jobs.md](jobs.md#the-session-analysis-v1)).

- **Made by** `ai/chat_labelling.label_chat`, on Haiku 5.5 (`ai.jobs`, kind `chat_labels`), in batches of 150 messages. Every batch sends the same instructions first, cached across the run's batches, and the same schema (`ChatLabelBatch`), since a different schema would miss the cache ([providers.md](providers.md#request-layouts-and-caching)).
- **What the model sees:** each message's `C` id, whether the team wrote it, and its text on one line, cut at 600 characters. Never an author, a name, an email or a people id.
- **Laughter.** A message that is only laughter ("haha", "lol", "mdr", "ptdr", "xD", laughing emoji) is labelled laughter whatever the model says.
- **Complete or nothing.** A batch's answer must label exactly that batch's messages, with a kind on questions only; otherwise the run fails and returns no labels, and the stored labels stay as they were. They are stored whole in `session_chat_labels` ([data-model.md](data-model.md#session_chat_labels)).

## The understanding (v1)

What one model call reads in a session, for the Follow-up tab and the Report's later findings ([prompt](../prompts/understanding.txt)). It runs in the session analysis the user starts, after the chat labels, only when the session has a transcript, and is skipped when the stored result was read from the same dossier ([jobs.md](jobs.md#the-session-analysis-v1)). The question desk shows its located answers ("Covered in the talk, not marked", [api.md](api.md#the-question-desk)) while they were read from the session as it is now (`record.understanding.located_answers`); the Transcript tab and Follow-up's "Chapters and show notes" show its chapters (`record.understanding.chapter_list`, [api.md](api.md#chapters)); the figure checks and the FAQ read it in the next PRs.

- **Made by** `ai/understanding.read_session`, on Sonnet 5.5 at medium effort (`ai.jobs`, kind `understanding`, the `dossier` layout), one call per run, streamed. About $0.20–0.30 a run.
- **What the model sees: the dossier** (`record.understanding.build_dossier`). The utterances by id (`U0001`) with a speaker key (`S0`), never a name, even one the host saved; their times; and the audience's questions by id (`Q001`, `C0001`) with Livestorm's status (written, aloud, open), their time and their text. Times are on the session clock only when the alignment allows minutes; otherwise utterances carry recording times and questions none, and the dossier says so. No name, email, company, people id or `P-` ref. What the host marked on the question desk is not sent. Built with `ai/dossier.py`'s helpers, so one session gives the same bytes every time; its SHA-256 is stored with the result.
- **What it returns** (`SessionUnderstanding`, strict): chapters (title, kind, first and last utterance), answers (the utterances where an open or aloud question is answered, with a quote), prompts (utterances asking the room to respond, with a quote), figures (each number stated as a fact: its words, value, unit and metric as said) and groups (questions asking the same thing).
- **What code keeps** (`record.understanding.check_understanding`). Ids must exist; chapters are kept in order without overlap; a quote or a figure's words must be in its utterances, word for word (case, punctuation and spacing aside); an answer must come after its question on the session clock, so without a clock no answer is kept; a figure must hold a number, read in English or French. Figures get flags for the host: `two_values` (the same metric said with two values) and `acronym` (NPS/NRR, ARR/MRR or CSAT/CSM in words the transcript is unsure of, mean confidence under 0.8). Whatever fails is dropped and counted in `coverage`, never shown. Stored whole in `session_understanding` ([data-model.md](data-model.md#session_understanding)), with `UNDERSTANDING_VERSION`; a change to the dossier, the checks or what is stored bumps it, and the next analysis reads the session again.

## Languages

- The Session analysis, Deep Analysis and Content Repurposing come in English and French. English is generated from the sources; French is a translation of the saved English output, so it needs English first (the API returns 400 otherwise). A language already in the bundle is reused; only a missing one costs a model call.
- Smart Recap is always in English.
- The interface is in English. The Analysis and Content Repurposing views switch their labels when French is selected for their output.
- Gladia transcribes in the spoken language, whatever the interface language.

## Saving rules

- **Only complete output is saved.** Empty output (`api_logic._require_generated_text`), a refusal, output cut off at the token limit, a streamed answer that breaks off or passes its deadline ([providers.md](providers.md#the-anthropic-sdk)), a content-repurposing bundle missing any of its four sections, or content repurposing on an empty transcript fails the job with a clear error and saves nothing. No runner substitutes placeholder text.
- **Refusals.** When the model declines (`stop_reason == "refusal"`), `llm_client` raises `LLMRefused` at once, and the job fails with "The model declined this session. Nothing was saved." The message is the same whatever the reason: Anthropic's category, when it gives a short one, is kept only in the call's `llm_usage` row (`refusal_category`) and its log line. It used to fail as an empty answer. A refusal is never retried: the content-repurposing loop stops after its one paid call instead of trying three times. Any partial text that came before the refusal is dropped ([providers.md](providers.md#structured-outputs-and-refusals)).
- **Truncation.** `llm_client` checks why the model stopped (`stop_reason == "max_tokens"`). Each job's spec says whether a cut-off answer is asked for again (`JobSpec.truncation_retry`). Every classic output does: it is retried once with double the token budget, capped at `TRUNCATION_RETRY_MAX_TOKENS` (16,000) and at the model's own limit (`llm_client.retry_budget`). The v1 jobs are not retried. If the answer is still cut off, `LLMOutputTruncated` is raised and nothing is saved. Each attempt is recorded in `llm_usage`, the cut-off and refused ones included ([providers.md](providers.md#usage-and-the-daily-budget)).
- **Structured answers.** A v1 JSON step (`llm_client.generate_json`) sends its schema and validates the answer against it, bounds included. An answer that doesn't match raises `LLMInvalidOutput` and nothing is returned to save. The classic outputs keep their prompts, models and formats: content repurposing still asks for its JSON in the prompt ([providers.md](providers.md#structured-outputs-and-refusals)).
- **One entry at a time.** Each job writes only its own language or tone (`db.merge_cached_bundle_entry`), see [data-model.md](data-model.md#caching-rules).
- **Every version is kept.** The same write keeps the output as saved in `output_versions`, with the models, prompt and dossier hashes and record versions of the calls that made it ([data-model.md](data-model.md#output_versions)).

## Exports

Every stored AI output has a PDF route, rendered with reportlab in [`pdf.py`](../livestorm_app/pdf.py): overall and deep analysis per language, Smart Recap per tone, and each content asset per language. Each PDF is headed by the "StormIQ for Livestorm" lockup (the wordmark drawn in code, then "for" and Livestorm's official on-light logo, `livestorm_app/assets/livestorm-logo-on-light.png`, rendered unchanged from the brand kit's SVG), and every page carries the footer `pdf.PDF_FOOTER_TEXT` (`pdf.PDF_FOOTER_TEXT_FR` for French exports), the app's "not an official Livestorm product" notice. `**bold**` renders as bold, `***bold italic***` as bold italic and `---` separators as a thin rule; other inline markdown is printed as text. Files are named `stormiq-…`.

## Prompts

All prompts live in `prompts/*.txt` and can be tuned without code changes; the paths are in [`config.py`](../livestorm_app/config.py). The Session analysis has one prompt, `session_analysis.txt`, filled with the output language.
