# StormIQ — Product Documentation

A companion application for Livestorm that turns a finished session into a structured workspace: engagement analytics, transcript diagnostics, audience analysis, AI-generated reports, and ready-to-publish marketing content.

This document describes what the app does, the data it produces, and the formulas behind each metric and chart.

> The navigation and routes described here (the Events list, `/session-overview`…) predate the current Search and Single Analysis pages ([architecture.md](architecture.md#pages)) and will be rewritten with the redesign. The views, charts and formulas are current. The redesign can be previewed with `?ui=v1` ([brand.md](brand.md#the-v1-design-switch)): a top bar replaces the sidebar, with a light and dark mode; the views themselves are unchanged so far.

---

## Table of contents

1. [Overview](#1-overview)
2. [How a session moves through the app](#2-how-a-session-moves-through-the-app)
3. [Authentication](#3-authentication)
4. [Events list](#4-events-list)
5. [Session Overview](#5-session-overview)
6. [Transcript](#6-transcript)
7. [Chat & Questions](#7-chat--questions)
8. [Analysis](#8-analysis)
9. [Smart Recap](#9-smart-recap)
10. [Content Repurposing](#10-content-repurposing)
11. [Outputs and exports](#11-outputs-and-exports)
12. [Language support](#12-language-support)
13. [Glossary of metrics and formulas](#13-glossary-of-metrics-and-formulas)

---

## 1. Overview

StormIQ connects to a Livestorm workspace, fetches a finished session (overview, chat, questions, recording transcript), caches everything in its own database, and exposes seven dedicated views:

| View | Purpose |
|---|---|
| Events | Browse Livestorm events and pick a session |
| Session Overview | Session context, audience composition, engagement snapshots |
| Transcript | Spoken content with searchable text, charts, and speaking diagnostics |
| Chat & Questions | Audience messages, submitted questions, activity over time |
| Analysis | Two layers of AI analysis (overall + deep) |
| Smart Recap | Short shareable recap in three tones |
| Content Repurposing | Summary, blog post, follow-up email, and social posts |

The app does not record sessions. It analyses sessions that already exist in Livestorm.

---

## 2. How a session moves through the app

1. The user connects their Livestorm workspace.
2. The user filters and browses **events** in their workspace.
3. The user selects an event, then a specific **session** within it.
4. The app fetches four data sources from Livestorm: session payload, chat messages, questions, recording transcript.
5. If the recording does not have a transcript yet, the app submits the recording for transcription and polls for completion.
6. The Livestorm payloads (minus registrants' personal join links), derived tables, and statistics are cached in Postgres keyed by `session_id`.
7. The user navigates between the views, and on-demand triggers AI generations (analysis, recap, content repurposing).
8. AI outputs are persisted alongside the session, so re-opening the workspace returns to the same state.

---

## 3. Authentication

Two paths are supported:

- **Livestorm OAuth** — the user is redirected to Livestorm, authorises the app, and is sent back with a server-side session cookie. The connected user's identity is shown in the sidebar (in the v1 design, on the top bar's account button).
- **API key fallback** — only for local development: the operator must explicitly turn on local-development mode and configure an `LS_API_KEY`. It is never available in production.

Access is scoped to the user's Livestorm organization: teammates in the same organization share cached sessions and analyses, while sessions from other organizations are not visible at all, not even through a shared link. Without a Livestorm connection, no session data is accessible.

Server-side secrets used by the backend (not exposed to the client) include the AI provider keys (for analysis, content generation and cover images) and the transcription provider key.

---

## 4. Events list

**Route:** `/events`

Lists past Livestorm events in the connected workspace with paginated fetching, filterable by title and scheduling status. Each event card shows:

- Title
- Scheduling status (e.g. on air, finished, cancelled)
- Number of sessions in the event ("n/a" when Livestorm doesn't send a count that is a non-negative integer; it has sent a negative one)
- Last updated date

Clicking an event expands it and shows a "Fetch sessions" action. Sessions are listed; selecting a session triggers the fetch of all four data sources for that session.

---

## 5. Session Overview

**Route:** `/session-overview`

### Hero metrics

Six top-level cards, from the session payload (Chat Messages: from the cached chat, see [§13](#13-glossary-of-metrics-and-formulas)):

| Metric | Source / formula |
|---|---|
| Registrants | `attributes.registrants_count` |
| Attendees | `attributes.attendees_count` |
| Attendance Rate | `attendees_count / registrants_count × 100` |
| Replay Viewers | Count of people with `has_viewed_replay = true` |
| Chat Messages | Audience messages in the chat ([§13](#13-glossary-of-metrics-and-formulas)); the card says "Excludes your team (n)" with the number of team and guest-speaker messages. On an internal session everyone counts and the card says "Internal session: everyone counts" |
| Questions | Sum of `questions_count` across people |

### Tab: Summary
- **Session payload** table — status, timezone, session name, start/end timestamps (UTC), duration label, registrants, attendees, attendance rate

### Tab: People
- **List of People** — name, email, country, city, attendance rate, role, company, job title, attendance duration, message count, question count, upvote count
- **Most Engaged People** — top 12 audience members sorted by engagement score; team members (`role = team_member`) and guest speakers (`is_guest_speaker`) are left out, except on an internal session ([§13](#13-glossary-of-metrics-and-formulas))
  - **Engagement Score formula:**
    `messages_count + (questions_count × 3) + (up_votes_count × 2)`

### Tab: Charts

| Chart | Type | Data |
|---|---|---|
| Attendance By Country | Bar | Top 12 countries by attendees (people with `attended = true`); countries with no attendee are left out |
| People By Role | Pie | Distribution across owner, team member, moderator, guest speaker, participant, viewer |
| Attendance Rate Distribution | Column | Bands: 0%, 1–25%, 26–50%, 51–75%, 76–100% |

---

## 6. Transcript

**Route:** `/transcript`

The transcript view requires a completed transcription job. If still running, a progress message is shown.

### Tab: Transcript
- Full searchable transcript text
- **Editable speaker labels** — speakers can be renamed; labels are persisted with the session and applied throughout the workspace
- Subtitle download (SRT and other formats supported by the transcription provider)
- Raw transcript JSON download

### Tab: Speaking Pace
Summary cards:
- Global WPM
- Min WPM
- Max WPM
- Total speaking time

Chart: **Words Per Minute Over Time** — segment-level words-per-minute across the session.

**WPM formula (per segment):** `(word_count / segment_duration_seconds) × 60`
**Global WPM:** `(total_words / total_speaking_seconds) × 60`

### Tab: Speaker Airtime
- **Speaker Metrics** table — speaking seconds, share %, segment count, word count per speaker
  - **Share % formula:** `speaker_speaking_seconds / total_speaking_seconds × 100`
- **Pie Chart Per Speaker** — share of total speaking time
- **Timeline Per Speaker** — when each speaker spoke across the session
- **Speaker Turns** table — chronological list of segments with excerpts

### Tab: NER (Named Entity Recognition)
Named entities extracted from the transcript, grouped by entity and type, sorted by occurrence count. Entity types include MONEY, DATE_INTERVAL, EVENT, ORGANIZATION, LOCATION_CITY, LOCATION_COUNTRY, NAME, OCCUPATION, DURATION, FILENAME.

For each entity: occurrence count, first-seen timestamp, surrounding context sentence.

### Tab: Words Count
- Per-speaker word count summary cards
- **Words By Speaker** bar chart
- **Words Over Time** line chart — words spoken per minute bucket
- **Words Count** table

### Tab: Speaking Segments
- Summary cards
- **Speaking Duration Before Pause** chart — distribution of speaking-burst durations
- Speaking segments table
- Bursts are merged consecutive segments by the same speaker with gaps below the pause threshold (0.75s)
- **Burst duration bins:** 0–10s, 10–30s, 30–60s, 60s+

### Tab: Silence & Pause
Summary cards: total silences, average pause, longest silence, count of pauses over 1s.

- **Pause Histogram** — distribution of pause durations
  - Bins: `0.75–1s`, `1–2s`, `2–3s`, `3s+`
- **Pause Timeline** — every pause event placed on the session timeline
- **Pause Types** table

**Pause classification:**

| Gap duration | Pause type |
|---|---|
| < 0.3s | Natural flow (not counted as a pause) |
| 0.3–1.0s | Thinking pause |
| 1.0–2.0s | Hesitation |
| ≥ 2.0s | Strong silence |

Pause threshold for inclusion: gaps must be ≥ 0.75s to be flagged. Pauses are detected at the word level when word timings are available, otherwise at the segment level.

### Tab: Utterance Duration
**Distribution Of Utterance Length** — histogram of segment durations.
- Bins: `0–5s`, `5–10s`, `10–20s`, `20–30s`, `30s+`

---

## 7. Chat & Questions

**Route:** `/chat-questions`

### Hero metrics

Chat metrics count **audience messages** only ([§13](#13-glossary-of-metrics-and-formulas)). When the team or guest speakers wrote in the chat, the Messages card says "Excludes your team (n)" with the number of their messages, and the Unique Chatters card with the number of those people. On an internal session, such as a company all-hands, every message counts: the cards, Top Contributors and Activity Over Time say "Internal session: everyone counts".

| Metric | Formula |
|---|---|
| Messages | Number of audience messages |
| Unique Chatters | Count of distinct `author_id` among audience messages |
| Avg Msg Length | Mean character length of audience messages |
| Questions | `len(questions)` |
| Unique Askers | Count of distinct question `asked_by` |

### Tab: Chat
Full chat thread, team messages included: message text, who sent it ("From": Attendee, Team or Guest speaker), created/updated timestamps, author ID.

### Tab: Questions
Submitted questions with: question text, asker, asked-at timestamp, responder, responded-at timestamp, response text.

### Tab: Top Contributors
- **Top 10 chat contributors** by audience message count
- **Top 10 question contributors** by question count
- **Contributors Comparison** chart — side-by-side comparison

### Tab: Activity Over Time
**Activity Timeline** — audience chat messages and questions bucketed by UTC minute across the session, plotted as parallel series.

### Tab: Question Response Coverage
Three cards:
- Answered
- Unanswered
- Coverage Rate — `answered / total_questions × 100`

A question is "answered" if it has a responder or was answered orally (live).

---

## 8. Analysis

**Route:** `/analysis`

Two AI-generated reports, available in **English** or **French**, downloadable as PDF. Both depend on a completed transcript.

### Overall Analysis

A concise stakeholder-facing report. Inputs: session payload, chat, questions, transcript.

Sections:
1. Executive Summary
2. Key Themes
3. Engagement Insights
4. Risks / Friction Signals
5. Actionable Recommendations

### Deep Analysis

A longer host-facing diagnostic. Same inputs, more thorough treatment.

Sections:
1. **Executive Summary** — 5–7 bullets
2. **Session Scores** — five 0–100 scores with one-sentence explanations:
   - Clarity
   - Engagement
   - Interaction
   - Pace
   - Alignment
3. **Key Moments** — up to 6 moments, each with optional timestamp and type tag (`strong`, `confusion`, `engagement`, `drop`)
4. **Speaker And Interaction Analysis**
5. **Audience Intent Analysis**
6. **Cross-Source Synthesis** — comparison of what the speaker said vs. what the audience reacted to in chat vs. what they asked
7. **Friction And Risk Signals**
8. **Business Signals And KPI Mentions**
9. **Actionable Recommendations** (split into Next Session / Follow-up / Optional Improvements)
10. **Risks, Ambiguities, And Data Quality Limits**

### Cross-Source Synthesis charts and tables

When Cross-Source Synthesis is selected, two additional artefacts appear. Both need a transcript and either chat or questions: a session with chat but no questions (or the reverse) still gets them, and the missing source counts 0.

#### Content Pace × Audience Activity chart
A timeline split into 10 buckets representing 0–10%, 10–20%, … 90–100% of session progress. For each bucket:
- `transcript_words` — total spoken words
- `transcript_segments` — number of transcript segments
- `transcript_wpm` — average words-per-minute
- `chat_messages` — audience chat messages produced in that window
- `question_count` — questions asked in that window

Each bucket is also labelled with a session stage based on its midpoint:

| Midpoint position | Stage |
|---|---|
| < 15% | Opening |
| 15–35% | Early |
| 35–65% | Middle |
| 65–85% | Late |
| ≥ 85% | Closing |

#### Segments With The Most Reactions table
The top 8 buckets ranked by `chat_messages + question_count`, showing speaker, session stage, start label, transcript excerpt, chat message count, question count.

### Underlying engagement scoring (used to derive Deep Analysis signals)

Internal per-minute engagement metrics are computed from the transcript and used to inform the AI report. Each one-minute bucket gets:

- **Engagement Score** = `(pace_score × 0.45 + (1 − silence_penalty) × 0.35 + interruption_score × 0.20) × 100`
- **Clarity Score** = `((1 − silence_penalty) × 0.30 + pace_score × 0.35 + (1 − variation_score) × 0.10) × 100`, then adjusted down by `min(filler_count × 3, 20)`
- **Cognitive Load Index** = `(silence_penalty × 0.45 + (1 − pace_score) × 0.20 + variation_score × 0.20) × 100`, adjusted up by `min(filler_count × 3, 20)`

Where each component is min-max scaled within the session:
- `pace_score` — bucket WPM scaled against the 10th–90th percentile of WPM
- `silence_penalty` — pause seconds scaled against the 90th percentile of pause seconds
- `variation_score` — absolute change in WPM vs. previous bucket, scaled against its own 90th percentile
- `interruption_score` — interruption count scaled against the session max

**Energy labels** per bucket:
- "Low energy" — pause_seconds ≥ 8s, or (WPM ≤ 10th percentile AND pause_seconds ≥ 4s)
- "Watch" — pause_seconds ≥ 4s
- Otherwise blank

**Interruption detection:** a speaker change with gap ≤ 0.2s is flagged. Negative gap = "Overlap", non-negative = "Rapid handoff".

**Filler tracking:** counts of `uh`, `um`, `you know`, `so` per 1000 words.

**Key Moment scoring** (the candidates surfaced to the AI):
- +2 if it contains a numeric/monetary/date/duration/event mention
- +2 if it contains a strong-statement keyword (`important`, `key`, `must`, `need to`, `significant`, `critical`, `huge`)
- +1 if it contains a top-25 named entity
- +1 if its WPM is at or above the 90th percentile (pace spike)
- Moments need at least 2 signals OR score ≥ 3, sorted by score descending, top 20 kept

---

## 9. Smart Recap

**Route:** `/smart-recap`

Short standalone recap (title + 2–4 paragraphs) generated from the transcript only. Three tones:

- **Professional** — polished, structured, business-friendly
- **Hype** — high-energy launch-style copy
- **Surprise Me** — unexpected angle that still stays grounded in the transcript

The recap does not reference the fact that it was generated from a transcript. Each tone is downloadable as PDF.

---

## 10. Content Repurposing

**Route:** `/content-repurposing`

Four ready-to-publish asset types, English or French, all PDF-exportable. The transcript is the only input.

### Summary
500–700 words, structured markdown with `##` and `###` headings. Suggested sections:
- Introduction
- Key Points Discussed
- Notable Quotes
- Questions And Answers
- Key Takeaways
- Next Steps Or Call To Action

### Blog Post
1000–1500 words written as a standalone article. Starts with a meta description line followed by `# Title`. Uses descriptive subheadings, flowing paragraphs, and incorporates quotes/examples/numbers from the source. Does not reference the webinar format.

### Follow-up Email
Markdown output with three sections:
1. Subject Line Options
2. Email Version 1 (≈200–300 words, paragraph style)
3. Email Version 2 (alternative angle)

### Social Media Posts
Three platforms, each with one polished post + 2–4 hashtags + optional emoji:
1. LinkedIn
2. Facebook
3. X / Twitter — capped at 280 characters

All four assets are generated together. English is generated first; French is translated from it. A session whose transcript has no text can't be repurposed: the job fails and nothing is saved.

---

## 11. Outputs and exports

| Artefact | Format |
|---|---|
| Session payload, people, country, role, attendance distribution, engagement top, chat, questions, contributors, pause types, pause timeline, speaker airtime, speaker turns, words count, speaking segments, utterance duration, cross-source reaction moments | CSV |
| Full transcript | JSON |
| Subtitles | SRT (and other formats provided by the transcription engine) |
| Overall Analysis, Deep Analysis | PDF (per language) |
| Smart Recap | PDF (per tone) |
| Content Repurposing bundle | PDF (per asset, per language) |

All AI outputs are persisted to the session cache, so re-opening the workspace shows the previously generated content without re-running the model.

Every PDF is headed by the StormIQ mark and ends each page with the "not an official Livestorm product" notice, in French for French exports. Bold text renders as bold (and `***bold italic***` as bold italic), and markdown separator lines (`---`) as a thin rule. Exported files are named `stormiq-…` (for example `stormiq-deep-analysis-english-<session>.pdf`).

---

## 12. Language support

- **Interface:** English by default; the Analysis and Content Repurposing views switch UI labels when French is selected for their output.
- **AI outputs:** Overall Analysis, Deep Analysis, and Content Repurposing assets are available in English and French. English is generated first; French is translated from the English version.
- **Transcription:** the underlying transcription engine handles many languages; the transcript itself is captured in the spoken language.
- **Smart Recap:** generated in English.

---

## 13. Glossary of metrics and formulas

### Audience-level

| Metric | Formula |
|---|---|
| Attendance rate | `attendees_count / registrants_count × 100` |
| Person engagement score | `messages_count + (questions_count × 3) + (up_votes_count × 2)`. `messages_count` is each person's own count from Livestorm, as in the People table's message count and the AI's `top_chatters`. It can be 0 for everyone even when the chat has messages; Chat Messages counts the chat instead |
| Internal session | Team members (`role = team_member`, not guest speakers) are more than half of the attendees (`attended` true). When attendance is unknown or nobody attended, registrants are counted instead. No people: not internal. Decided once per session (`audience.is_internal_session`) |
| Audience message | A chat message where neither `from_team_member` nor `from_guest_speaker` is true. Messages cached without these flags count as audience. On an internal session every message is an audience message |
| Audience member | A person whose `role` is not `team_member` and who is not a guest speaker (`is_guest_speaker`). On an internal session everyone is an audience member. Used by Most Engaged People, and by Session Overview's Chat Messages when no chat is cached |
| Chat Messages (Session Overview) | Number of audience messages in the cached chat; "Excludes your team (n)" counts the chat's other messages. Only when no chat is cached: the sum of `messages_count` over audience members, and over the other people for the team's count. The chat comes first because Livestorm sometimes reports `messages_count` 0 for every person although the chat has messages. The session summary sent to the AI counts the same way, an empty cached chat as 0 (`session_overview.build_session_stats`) |
| Question coverage rate | `answered / total_questions × 100` (answered = has a responder, or `response_orally` is true; older payloads spell it `responded_orally`) |

### Transcript-level

| Metric | Formula |
|---|---|
| Words per minute (segment) | `(word_count / duration_seconds) × 60` |
| Global WPM | `(total_words / total_speaking_seconds) × 60` |
| Speaker share % | `speaker_speaking_seconds / total_speaking_seconds × 100` |
| Filler per 1000 words | `(filler_count / total_words) × 1000` |

### Pause classification

| Gap duration | Type |
|---|---|
| < 0.3s | Natural flow (not counted) |
| 0.3–1.0s | Thinking pause |
| 1.0–2.0s | Hesitation |
| ≥ 2.0s | Strong silence |

Minimum gap to register as a pause: **0.75s**.

### Per-minute engagement scoring (transcript)

All components are min-max scaled within the session.

- **Engagement Score** = `(pace_score × 0.45 + (1 − silence_penalty) × 0.35 + interruption_score × 0.20) × 100`
- **Clarity Score** = `((1 − silence_penalty) × 0.30 + pace_score × 0.35 + (1 − variation_score) × 0.10) × 100`, minus `min(filler_count × 3, 20)`
- **Cognitive Load Index** = `(silence_penalty × 0.45 + (1 − pace_score) × 0.20 + variation_score × 0.20) × 100`, plus `min(filler_count × 3, 20)`

Component scaling:
- `pace_score` = WPM scaled to the 10th–90th percentile of bucket WPM
- `silence_penalty` = pause seconds scaled to the 90th percentile
- `variation_score` = absolute WPM change vs. previous bucket, scaled to 90th percentile
- `interruption_score` = interruption count scaled to the session max

### Session stage mapping (cross-source timeline)

Buckets of 10% of session progress, labelled by their midpoint. Progress runs from each source's first to its last item; an item at exactly 100% (the last message or question) belongs to the 90–100% bucket.

| Midpoint | Stage label |
|---|---|
| < 15% | Opening |
| 15–35% | Early |
| 35–65% | Middle |
| 65–85% | Late |
| ≥ 85% | Closing |

### Session clock (v1 Report)

v1 Report (behind `?ui=v1`, [brand.md](brand.md#the-v1-design-switch)). No page shows it yet. It puts every event of a session on one time base, where the stage mapping above stretches each source to 0–100% on its own. Code: `livestorm_app/record/clock.py` and `record/evidence.py`.

| Term | Rule |
|---|---|
| Session time `t` | Seconds since the session's `started_at`. Chat messages and questions use their raw `created_at`, written answers their `responded_at`. Times are read as unix seconds or ISO dates; zero counts as missing |
| No clock | A session without `started_at` has no clock, and nothing is placed on it. `estimated_started_at` is never used as the start |
| Late start | `started_at − estimated_started_at` |
| Session end `end_s` | `ended_at − started_at`. When `ended_at` is missing or not after the start, the session's `duration` |
| Phase | Before when `t < 0`, after when `t > end_s`, live otherwise |
| Minute | `floor(t / 60)` for a live event. Minutes run from 0 to `ceil(end_s / 60) − 1`, and empty ones count 0. An event exactly at `end_s` is in the last minute. Without an end, minutes run to the last live event |
| Before and after | Events before the start (the waiting room) and after the end are counted apart, with the earliest and the latest time. They never shift the live minutes |
| Lanes per minute | Audience chat, team chat (`from_team_member` or `from_guest_speaker`), questions, written answers, speaking seconds and words. Questions count as audience and written answers as team |
| Speaking seconds | Split across minute borders; an utterance's words are split in proportion to its seconds in each minute. These lanes stay empty until the recording is aligned with the session clock |
| Label | `mm:ss` into the session, with minutes counting past 59 (`125:30`). Before the start `−mm:ss`, after the end `+mm:ss`, both rounded up so a label never reads `−00:00` |
| Evidence ids | Chat messages `C0001`, `C0002`… by `created_at`, then Livestorm id, then text. Questions `Q001`… the same way. Written answers (a question with response text) `A001`… by `responded_at`, then Livestorm id, then text. Items without a readable time come last. A message repeated across pages counts once; if its copies differ, the first copy is kept. The ids don't depend on the clock |
| Has a link | The text holds a URL (`http://`, `https://` or `www.`), or the message's HTML links to one |

### Speaking burst detection

Consecutive segments by the same speaker are merged into a burst when the gap between them is below the pause threshold (0.75s). Burst duration bins: 0–10s, 10–30s, 30–60s, 60s+.

### Interruption detection

A speaker change with inter-segment gap ≤ 0.2s.
- Gap < 0 → Overlap
- 0 ≤ gap ≤ 0.2s → Rapid handoff

### Key moment scoring

A transcript sentence is a candidate key moment if it accumulates ≥ 2 signals or a score ≥ 3 from:

| Signal | Weight |
|---|---|
| Contains numeric/monetary/duration/event mention | +2 |
| Contains a strong-statement keyword (`important`, `key`, `must`, `need to`, `significant`, `critical`, `huge`) | +2 |
| Contains a top-25 named entity | +1 |
| Pace ≥ 90th percentile of segment WPM | +1 |

Candidates are sorted by score descending; top 20 are surfaced.
