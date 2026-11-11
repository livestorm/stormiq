# StormIQ — Product Documentation

A companion application for Livestorm that turns a finished session into a structured workspace: engagement analytics, transcript diagnostics, audience analysis, AI-generated reports, and ready-to-publish marketing content.

This document describes what the app does, the data it produces, and the formulas behind each metric and chart.

> The navigation and routes described here (the Events list, `/session-overview`…) predate the current Search and Single Analysis pages ([architecture.md](architecture.md#pages)) and will be rewritten with the redesign. The views, charts and formulas are current. Everyone uses the new design ([brand.md](brand.md#one-design)): the home page, Sessions, Add session and each session's Report, Transcript and Follow-up are rebuilt; Admin keeps its older look inside the new shell until it is rebuilt.

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

**The home page** is the site's address, `/`. Signed out, it is the landing page below; signed in, it opens Sessions. Signing out returns to it.

**Signed out**, every page but the beta notice, the design gallery and the OAuth return page shows the landing page instead (`views/LandingView.vue`): what StormIQ does, an example report with made-up numbers (labelled as such), a two-minute promo film ("See it in two minutes": a tour of a made-up session, from the Sessions list and the Report's cover chart to Follow-up; it shows some of what is still being built and says so; it loads only when played), what you get, how it works in three steps, why it can be trusted (your organization only, numbers you can check, AI only when you ask), and "Connect with Livestorm". The top bar keeps only the "StormIQ for Livestorm" lockup, the theme toggle and "Connect with Livestorm": no Sessions or Add session. The URL doesn't change, so connecting from a shared session link brings you back to that session. Nothing shows until the app knows whether you are signed in, so a signed-in user never sees the landing page flash.

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

### The Sessions page (v1)

**Route:** `/sessions` (`views/SessionsView.vue`).

Every session the organization has fetched, as one index. At the top: "Sessions" and "Add session". Three counts over all of them: sessions, sessions with a transcript, and events. Then a search (by title, event or the teammate who added it), an event filter (when there is more than one event), a sort (session date, newest or oldest first, recently added, title) and how many sessions show. One row per session: the date, the title (the event's title, as on the Report, with the session's own name and its length under it, then **what stood out most**: the Report's first finding in one line, once the session is analysed), the session's **shape** (the audience's chat per minute, drawn as the Report's cover chart is, at most 48 points), attendees "of" registrants, **wrote** (the share of attendees who wrote in the chat or the Q&A, "est." when the Report marks it estimated), **questions** with how many are still open (open, and covered in the talk but not confirmed, as on Follow-up), the transcript (Ready or Not yet) and who added it. A number the Report hides stays a dash. The whole row opens the session's Report. On a phone each row is a short block: the title, the lead finding and the shape, then the date, attendees and transcript. Attendees and registrants are Livestorm's counts, the team included; the page says so, as the other numbers are the audience's alone. The shape, numbers and lead finding come from each session's own record (`GET /record/library`), asked only for the rows that come on screen, three at a time (`src/sessions/useLibraryRows.js`); the list makes no comparison between sessions. Wording and order: `src/sessions/list.js`, `src/sessions/library.js`.

### Add session (v1)

**Route:** `/sessions/add` (`views/AddSessionView.vue`; `/search` redirects there).

How a session gets into StormIQ. "Your events" lists the organization's Livestorm events, most recently updated first, 20 at a time with "Load more events": each with its status (Ended, On demand, Live, Upcoming…) and how many sessions it has. A search by title and a status filter ask Livestorm again. Opening an event lists its finished sessions, newest first, 50 at a time with "Show more", under a line saying how many have finished out of all the event has ("2 finished sessions of 6": the others are still to come, never started, cancelled or drafts, and can't be added). The event's own count is Livestorm's, for all its sessions; the status filter is the event's, not its sessions'. Each session shows the date, the session's own name ("Untitled session" when it has none) and Livestorm's attendee count. A session the organization already has says "Added" and opens; any other has "Add". Adding fetches the session from Livestorm (its details, chat, questions and people), starts its transcript, and has the server analyse the session once the transcript is done (or straight away when there is no recording). The Report opens at once, as the fetch starts, on "Preparing your report": the steps (fetching from Livestorm, transcribing with Gladia's progress, reading the chat and the talk with AI, the report), a bar that fills as they go (the fetch, then the transcript's own progress, then the analysis's) and the time so far; if the fetch fails, the page says why (`useSessionReport.fetchFailed`); the Report shows once the first analysis is in. It goes on if the page is left. If the fetch fails, the row says why and offers "Try again". "Have a link or an ID?" takes a session's or an event's Livestorm link or ID: a link holding one ID is read for it, a link holding two is refused rather than guessed. Wording: `src/sessions/add.js`.

---

## 5. Session Overview

Removed in November 2026: the session's [Report](#measures-v1-report) covers what it showed (attendance, the audience, the chat and questions over time). `/sessions/:sessionId/session-overview` opens the Report.

---

## 6. Transcript

**Route:** `/sessions/:sessionId/transcript` (`views/SessionTranscriptView.vue`), the session's second tab (Report · Transcript · Follow-up).

What was said and what the room wrote, as one readable timeline. Data: `GET /api/sessions/{id}/record/transcript` ([api.md](api.md#the-v1-record)); wording and order: `src/transcript/lines.js`.

- **One clock, when it can be trusted.** When the recording is matched to the session's clock to the second (the alignment probe's `seconds`, [§13](#alignment-probe-v1-report)), speech and the room are interleaved: each line at its session time, a speech line before a chat line at the same second. Otherwise the page shows "What was said" and "Chat and questions" apart and says why; speech then carries the recording's own times. Apart, on a wide screen, the chat is a column beside the speech that stays in view and scrolls on its own, with its own "Both · Chat · Questions" switch, its own search ("Search the chat") and an author filter; on a phone it follows the speech.
- **Speech lines:** the time, the speaker (once per stretch of their lines) and the text, in the reading style of Follow-up's text. Each speaker has a colour (up to six, then they repeat), on their name, on a bar down their stretch of lines and on their filter chip (`--siq-speaker-1` … `6`). A word below 0.6 confidence is underlined, dotted, and read out as "low confidence"; neighbouring unclear words make one mark.
- **The room's lines** (indented, with a rule in the chat's or the Q&A's colour): the author's short name ("Priya S."), "Team" or "Attendee" when the people list can't name them (internal sessions are named too), a GIPHY or Tenor GIF link shown as the GIF, under the message's other words (`utils/gif.js`; loaded when scrolled into view, with no referrer, any other link stays text), the source (Chat, Q&A, Answer), and for a question "Answered in writing" when a written answer replies to it. Nothing calls a question open: an answer given out loud leaves no trace. A message before the start reads "Before".
- **Tools,** right above the lines and pinned under the session's tabs while you read (on a wide screen): a search (text and names), "Everything · Speech only · Chat and questions", the speakers as filters, "Both · Chat · Questions" for the room (Questions keeps the Q&A's questions, their written answers and the chat messages the session analysis labelled questions, shown as "Chat question"; before the session is analysed it says it shows the Q&A's only), an author filter ("Everyone in the chat", then each author with their count, the unnamed last) when speech and chat share one clock, the count of lines shown and "Clear". Picking a speaker shows their speech only; picking an author, their messages only. Above them, quieter: "Name speakers" (the names show here, in the subtitles and in the analyses made afterwards), "Subtitles (SRT)" (one cue per line of speech, on the recording's times, made in the browser) and "Raw (JSON)".
- **Chapters:** once the session is analysed, each chapter the analysis found is a heading across the lines, with its start time, before its first line shown (a search or a filter hides a chapter with no line left). Chapters come from the analysis's one model call; code keeps only those whose first and last lines exist, in order and without overlap, and drops them all when the session changed since it was analysed.
- **Without a transcript:** the page says so and offers "Make the transcript" (a paid transcription that runs only on that click); while one is being made it shows the loader every job uses (`components/ui/ProgressSteps.vue`): its steps (downloading the recording, sending it to Gladia, transcribing with Gladia's own line, telling the speakers apart, saving), a bar that fills with the job's progress and the time so far, and updates by itself; a failed one says why and offers "Try again". The chat and questions show meanwhile.

- **Speaking stats** (collapsed, between the tools and the lines): the pace in words a minute ([speaking pace](#speaking-pace)), the longest stretch and who held it ([longest stretch](#longest-stretch)), and the talk time per speaker with its share and pace ([speaker talk time](#speaker-talk-time)), named as on the tab. Its line says the pace and who spoke most. A hidden number says why (too little of the recording transcribed, speakers that couldn't be told apart, less than a minute of speech); an approximation is marked "Estimated". Wording: `src/transcript/stats.js`.

The classic Transcript's eight diagnostic tabs are gone; Speaking stats keeps what they were read for.

---

## 7. Chat & Questions

Removed in November 2026: the [Report](#measures-v1-report) ("The room", "The questions", "The audience") and the [Transcript](#6-transcript) tab, which lists every chat message and question, cover it. `/sessions/:sessionId/chat-questions` opens the Report.

---

### The Follow-up tab (v1)

**Route:** `/sessions/:sessionId/follow-up` (`views/SessionFollowUpView.vue`), the session's third tab. It opens on the question desk (`?output=desk`, or no `?output`); `?output=` names any other output on screen and `?lang=French` its language; the older `/analysis`, `/smart-recap` and `/content-repurposing` paths open the overall analysis, the professional recap and the summary there.

**The question desk** (first in the list, with its count of open questions; `components/report/QuestionDesk.vue`, worded by `src/followup/desk.js`) lists every audience question: the Q&A's, and the chat's questions about the content once the session is analysed ("Analyse session", on the Report; until then, or when the chat changed since, it says so). Questions about the replay or the slides are counted on a line of their own, not listed. Your team's questions are left out, except on an internal session. Each question shows where it was asked, as a coloured badge with its icon and word (**Q&A tab** in orange, **Chat** in blue, with an edge of the same colour), who asked it (a short name, as on the Transcript tab, internal sessions included), its time, and its status, decided by the server: **Answered in writing** (Livestorm's written answer is shown), **Answered aloud** (marked so in Livestorm), **Answered here** (you marked it in StormIQ, with your answer if you wrote one), **Covered in the talk, not marked** or **Still open**. Once the session is analysed, a question nobody marked that the talk seems to answer is "covered": it shows "Seems answered in the talk at 5:19" and the speaker's words, with **Confirm** (marks it answered here, keeping the passage) and **Not this** (it stays open and is never suggested again). A question marked answered aloud in Livestorm shows where in the talk, with Not this. Passages are found by the analysis's one model call and kept only when the words are in the transcript and come after the question, on a verified session clock; when the session changed since it was analysed, the desk says to analyse it again. Filters: Still open (open and covered), Answered, All; and, when the desk has questions from both, **Both · Q&A tab · Chat**, each with its count, the status counts following the source picked. An open question has "Mark answered", which opens an optional answer field; one you marked can be edited or marked as open again. Livestorm's own answers can't be changed here. **Download (CSV)** opens a menu of three files: **Open questions** (the ones still open, covered ones included until you confirm them), **All questions** (with each one's status) and **All questions with answers** (with its status and its answer: Livestorm's written answer, your reply, or the passage of the talk, marked "Not confirmed" for a covered question), for a follow-up email. Each names who asked, by full name and company, on every session (internal ones too), never with an email; the page says the file names people and stays inside your organization. Nothing on the desk calls a model: it reads what the analysis stored. Rules: [api.md](api.md#the-question-desk).

**Before you share** (above the list, `components/report/BeforeYouShare.vue`, worded by `src/followup/figures.js`), when the analysis flagged figures said on stage: each as a question with one-click answers. "Was it NRR or NPS?" for an acronym the recording is unsure of; "Seats per team: which value is right?" when the same metric was said with two values, with each value, "Both: they measure different things" and "Neither: leave it out". Each shows when and how it was said. An answer is saved for the session and can be changed; until every figure is checked, the AI outputs carry a quiet line saying how many aren't, and the card folds to its header once all are. The Session analysis is told the answers, and never states an unchecked figure as a fact. No model call. Rules: [api.md](api.md#before-you-share).

**Chapters and show notes** (`?output=chapters`, under the question desk; `components/report/ChapterNotes.vue`, worded by `src/followup/chapters.js`): the chapters the analysis found, as "00:00 Title" lines on the recording's clock, for a replay page or a YouTube description, with **Copy**. The first starts at 00:00. When YouTube wouldn't read them as chapters (fewer than three, or one under ten seconds) the page says so. Before the session is analysed, or when it changed since, the page says to analyse it. No model call: it reads what the analysis stored. Rules: [api.md](api.md#chapters).

**The AI outputs** of sections 8 to 10 follow: on the left, grouped (Analysis: **Session analysis**, **Decisions and commitments**, **Team digest** and **Smart Recap**; Repurposing: Summary, Blog post, Follow-up email, Social posts, **FAQ**; For the audience: **Attendee follow-up**), each marked Ready once written in the language shown (the FAQ: once an AI one was written before) (on a phone, a picker), and Surprise me apart, in a small card at the bottom ("An unexpected take on the session. Opt-in."), written from what StormIQ measured, a few of the room's chat messages and top questions (quoted only when they make the story better, never with who wrote them) and the transcript, and never saved with a figure the session doesn't have ([ai-outputs.md](ai-outputs.md#smart-recap)). A written take has **Play it**, which opens the player: the take one beat (a sentence; the title first, each paragraph's last sentence its punchline) at a time, each read aloud by an AI voice (OpenAI's gpt-4o-mini-tts; cedar by default, a man who sounds dry and a little sarcastic, warm underneath and never mean; an admin can pick another) and shown as a **storyboard scene**: the voice tells the sentence, the screen tells the story and never writes the sentence out. A scene is one of: a headline of a few words lifted word for word from the sentence (at most 6), slammed in, stamped on, popped in word by word, or over a flash of colour for a punchline; a number the sentence says counting up, big (a percentage fills a ring); one of the room's own chat messages or questions flying in as a chat bubble ("In the chat · 23:05", "Asked in the Q&A · 25:00"), never with who wrote it; or the audience chat's curve (the Pulse's) drawing in to the minute the sentence names, or to the busiest one, with a short headline under it. Claude plans the scenes on the first Play, from the sentences, the room's lines and the vitals, and every word and number is checked against the sentence before it is kept; a scene that fails the check, or a take played before its storyboard exists, gets the page's own scene for that sentence (its key words, a number it says, the Pulse for a time or a peak). **CC** shows the whole sentence as a subtitle, off by default; screen readers always hear it. The first **Play** plans the scenes and records the voice: both are paid and made in the worker from the take's text and the room's redacted lines (no name, no email), and it shows the loader (waiting for a free worker, reading the take, planning the scenes, recording the voice with "3 of 12 sentences"); "Play without sound" starts at once without the voice. Both are kept, so later plays and reloads start at once and cost nothing; a new take or a new voice records again on the next Play only. If OpenAI fails, the player says so ("OpenAI refused to make the voice (HTTP 400).") with **Try the voice again** and **Play without sound**. Controls: previous and next beat, Play, Pause, Resume, From the start, **Sound on/off** (off plays the scenes alone, at each clip's length or a reading pace, and needs no voice), **CC**, a progress bar with one segment a beat, and "Beat 3 of 12". With reduced motion nothing moves: each scene shows whole (`components/report/SurprisePlayer.vue`, `src/followup/surpriseBeats.js`, `surpriseScenes.js`, `useSurpriseVoice.js`; [ai-outputs.md](ai-outputs.md#smart-recap)). A StormIQ admin can turn it off for an organization (Admin → Organizations, for a customer who asks): it then disappears from Follow-up, and a link to it says "Surprise me is turned off for your organization. A StormIQ admin can turn it back on."; on the right, the one on screen. The Deep analysis is no longer made; when one was saved for the session, it shows read-only under **Earlier outputs** (`?output=deep`), with Copy and PDF and no button, and a language with none says "No deep analysis was saved for this session in French. StormIQ no longer makes it." Under a written output, **Earlier versions (n)** lists the versions saved before the one on screen (every save is kept since November 2026), newest first, each with when it was saved and the model that wrote it; one opens read-only with "Copy this version" (`GET /output-versions`, `src/followup/useOutputVersions.js`). An output saved before versions were kept has none. The Smart Recap is one item with a **Formal · Hype** switch (`?output=recap-professional`, `recap-hype`). An output is written only when you click its button ("Write the session analysis"); while it runs it shows the loader every job uses (`ProgressSteps`: waiting for a free worker, reading the session, preparing what the AI reads, writing with AI, checking and saving, a bar that fills with the job's progress and the time so far), and its item in the list shows a small spinner and "Writing" in place of "Ready" (" · writing" in the phone picker); the summary, blog post, follow-up email and social posts are written together. English · Français switches the Analysis and Repurposing outputs; a French version is written from the English one, so it is offered once the English one exists. A written output shows "Copy" (the text as written), "PDF" and the reminder that AI wrote it, then its text. The follow-up email and the social posts are cut at their top-level headings (the subject lines, each email, each platform's post), each part in its own block with its own **Copy**, which copies plain text (no Markdown marks; "Copy all" copies the whole). The model's Markdown is shown as headings, paragraphs and lists, never as HTML, in a reading style: 16px text in a softer ink, lighter in dark mode, with headings that clearly step up (`components/report/MarkdownBlocks.vue`, `src/followup/markdown.js`). Without a transcript, an AI output points to the Transcript tab (the question desk and the FAQ need none). **The FAQ** is edited in place, with no AI (`components/report/FaqEditor.vue`, `GET`/`PUT /record/faq`). Until it is first saved it shows a draft built from the question desk ("Draft from the question desk — edit and save"): one entry per desk question, in the desk's order, the question as asked and its answer, first match wins: Livestorm's written answer ("Written answer"), your reply on the desk ("Host's reply"), the passage of the talk the session analysis located ("From the talk · 12:04"), "Answered live: see the replay at 12:04." for a question marked answered aloud with no words found ("Answered live · 12:04"), else empty ("Not answered yet"). Attendees' names, emails, phone numbers and links are replaced by placeholders, and who asked is never shown. Each entry has its question and answer to edit, its source chip (a typed answer on an open entry is "Added by you"; an emptied one is open again), Copy, Move up, Move down and Remove; "Add a question" adds one. Edits show "Unsaved changes" with **Save** and **Discard**. Once saved, questions answered on the desk since (and new desk questions) show as "1 question newly answered on the desk · Add them": a click fills the entries still open and adds the rest; an answer you wrote is never replaced. **Copy all** copies the FAQ as Markdown; **Download** gives the saved FAQ as Markdown, CSV (question, answer, source, time in the replay) or a PDF with the StormIQ lockup, once there are no unsaved changes. English only for now; French comes later. A FAQ the AI wrote before shows read-only under "Earlier AI version". **Decisions and commitments** ("List the decisions") is read from the transcript on click, with no need to analyse the session first: what was settled on stage and what someone promised to do, each with its owner and due date when said, who said it, when, and the words, in italics. Only what the transcript says word for word is kept; speakers are named as saved on the Transcript tab. No PDF ([ai-outputs.md](ai-outputs.md#decisions-and-commitments)). **The team digest** ("Write the team digest") is about 100 words to paste in the team's Slack or Teams channel, written on click: a first line marking it internal, a short lead, then a few bullets with the Report's numbers (the estimated ones marked "estimated"), the key moments with their times, the questions still open and the decisions if they were listed. Nobody is named. A digest that gives a number or a time StormIQ didn't measure is never saved: the page shows the error and you can write it again. No PDF ([ai-outputs.md](ai-outputs.md#team-digest)). **The attendee follow-up** ("Write the attendee follow-up", under For the audience, on every session) is "You asked, we answered": an email for the attendees, written on click, with a subject line, what the session covered, three takeaways from its chapters and key moments, and two questions the audience asked with their answers (the most upvoted first), then a line with `{{replay_link}}` for your email tool to fill in. Attendees are never named: their names, emails, phone numbers and links are removed before anything is sent, and the email says nothing about how the session ran or how many came. One that gives a number or a time the session doesn't have, or loses `{{replay_link}}`, is never saved: the page shows the error and you can write it again. French is a translation of the English one. No PDF ([ai-outputs.md](ai-outputs.md#attendee-follow-up)). Wording and the outputs' list: `src/followup/outputs.js`.

## 8. Analysis

**Shown on:** [Follow-up](#the-follow-up-tab-v1) (`?output=overall`, `?output=deep`). The deep analysis is shown as one document; the classic view's split into ten tabs and its score bars are gone.

Two AI-generated reports, available in **English** or **French**, downloadable as PDF. Both depend on a completed transcript.

### Session analysis

The team's read of the session, the day after. Written on a click, once the session has been analysed ("Analyse session", on the Report), from what StormIQ measured and checked: the Report's numbers, the chat's busiest minutes and topics, the chapters, the questions and where the talk answered them, and the transcript. Never with a name. Sections:
1. What happened
2. What landed with the audience
3. Questions
4. What to do next

Every number is quoted from the Report; the model computes none. Details: [ai-outputs.md](ai-outputs.md#session-analysis).

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

**Shown on:** [Follow-up](#the-follow-up-tab-v1) (`?output=recap-professional`, `recap-hype`, `recap-surprise`).

Short standalone recap (title + 2–4 paragraphs) generated from the transcript only. Three tones:

- **Professional** — polished, structured, business-friendly
- **Hype** — high-energy launch-style copy
- **Surprise Me** — unexpected angle that still stays grounded in the transcript

The recap does not reference the fact that it was generated from a transcript. Each tone is downloadable as PDF.

---

## 10. Content Repurposing

**Shown on:** [Follow-up](#the-follow-up-tab-v1) (`?output=summary`, `blog`, `email`, `social`).

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
| Session analysis, Deep Analysis | PDF (per language) |
| Smart Recap | PDF (per tone) |
| Content Repurposing bundle | PDF (per asset, per language) |

All AI outputs are persisted to the session cache, so re-opening the workspace shows the previously generated content without re-running the model.

Only a complete output is saved. When the model declines a session, the output shows "The model declined this session. Nothing was saved." and nothing is kept; it used to say the model returned an empty output. An empty answer, or one cut off at its length limit after one retry, fails the same way with its own message ([ai-outputs.md](ai-outputs.md#saving-rules)). When the call to Anthropic itself fails (an HTTP error, a timeout, a lost connection, or a long answer cut off part-way), the output shows "The Anthropic call failed (…). Nothing was saved. Please try again.", naming the HTTP status and error type, for example "HTTP 529 overloaded_error", and nothing is kept; it used to show the HTTP library's message. A long answer that runs past its time limit shows "The model's answer took longer than 600 s, so it was stopped. Nothing was saved." No output a user can start today makes such a long call.

Every PDF is headed by the "StormIQ for Livestorm" lockup and ends each page with the "not an official Livestorm product" notice, in French for French exports. Bold text renders as bold (and `***bold italic***` as bold italic), and markdown separator lines (`---`) as a thin rule. Exported files are named `stormiq-…` (for example `stormiq-deep-analysis-english-<session>.pdf`).

---

## 12. Language support

- **Interface:** English by default; the Analysis and Content Repurposing views switch UI labels when French is selected for their output.
- **AI outputs:** the Session analysis, Deep Analysis, and Content Repurposing assets are available in English and French. English is generated first; French is translated from the English version.
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
| Audience member | A person whose `role` is not `team_member` and who is not a guest speaker (`is_guest_speaker`). On an internal session everyone is an audience member. Used by Most Engaged People, and by Session Overview's Chat Messages when no chat is cached. The v1 roster's rule is in [Roster](#roster-v1-report) |
| Chat Messages (Session Overview) | Number of audience messages in the cached chat; "Excludes your team (n)" counts the chat's other messages. Only when no chat is cached: the sum of `messages_count` over audience members, and over the other people for the team's count. The chat comes first because Livestorm sometimes reports `messages_count` 0 for every person although the chat has messages. The session summary sent to the AI counts the same way, an empty cached chat as 0 (`session_overview.build_session_stats`) |
| Question coverage rate | `answered / total_questions × 100` (answered = has a responder, or `response_orally` is true; older payloads spell it `responded_orally`) |

### Transcript-level

| Metric | Formula |
|---|---|
| Words per minute (segment) | `(word_count / duration_seconds) × 60` |
| Global WPM | `(total_words / total_speaking_seconds) × 60` |
| Speaker share % | `speaker_speaking_seconds / total_speaking_seconds × 100` |
| Speaker cap (new transcripts) | Gladia looks for 1 to `max_speakers` speakers, where `max_speakers = min(20, max(3, people_on_stage + 2))`. People on stage are the session's people that Livestorm marks as an organizer (`session_role` is `organizer`) or a guest speaker (`is_guest_speaker`), each once, attended or not, from the same people as the v1 roster (the session's stored people list once StormIQ has read it from Livestorm, the people cached with the session until then). When there are none, or no people list, the count is unknown and `max_speakers` is 20: a count of 0 would give the floor, too low for a meeting where many people talk. Set once, when the transcript is made; the count is kept in the transcript's `stormiq.people_on_stage` (`record/inputs.count_people_on_stage`, `gladia/transcriber.max_speakers_for`) |
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

The Report. No page shows it yet; the record route sends it as `meta.clock` and the Pulse (`record.pulse`, [api.md](api.md#the-v1-record)). It puts every event of a session on one time base, where the stage mapping above stretches each source to 0–100% on its own. Code: `livestorm_app/record/clock.py` and `record/evidence.py`.

| Term | Rule |
|---|---|
| Session time `t` | Seconds since the session's `started_at`. Chat messages and questions use their raw `created_at`, written answers their `responded_at`. Times are read as unix seconds or ISO dates; zero counts as missing |
| No clock | A session without `started_at` has no clock, and nothing is placed on it. `estimated_started_at` is never used as the start |
| Late start | `started_at − estimated_started_at` |
| Session end `end_s` | `ended_at − started_at`. When `ended_at` is missing or not after the start, the session's `duration` |
| Phase | Before when `t < 0`, after when `t > end_s`, live otherwise |
| Minute | `floor(t / 60)` for a live event. Minutes run from 0 to `ceil(end_s / 60) − 1`, and empty ones count 0. An event exactly at `end_s` is in the last minute. Without an end, minutes run to the last live event |
| Before and after | Events before the start (the waiting room) and after the end are counted apart, with the earliest and the latest time. They never shift the live minutes |
| Unplaced | Chat messages, questions and written answers with no session time: the session has no clock, or the item's time can't be read. Counted apart, in no minute and no edge |
| Lanes per minute | Audience chat, team chat (`from_team_member` or `from_guest_speaker`), questions, written answers, speaking seconds and words. Questions have one lane, whoever asked; written answers have their own. Which items are team: [Authors](#authors-v1-report). On an internal session nobody is relabelled: its chat stays team chat, and the roster says the session is internal |
| Speaking seconds | Each utterance of the transcript ([Transcript health](#transcript-health-v1-report)) is placed at `t = r + offset`, where `r` is seconds into the recording and the offset comes from aligning the recording with the session (`clock.speech_events`). Split across minute borders; an utterance's words are split in proportion to its seconds in each minute. The Pulse shows these lanes only when the [alignment](#alignment-probe-v1-report) allows findings in minutes (verified or assumed), placed with the offset it applies; otherwise it leaves them out and gives the alignment's reasons |
| Chapters on the Report's cover | The chart under the Report's title ("Audience chat per minute") draws the session analysis's chapters on the session clock at `t = r + offset`, only where the Pulse shows speech (the alignment allows findings in minutes); otherwise it draws none. A chapter owns the live time from its start to the next chapter's start, the last one to the session's end. Its messages and questions are the audience's live chat messages and questions in that span (on an internal session, everyone's chat), and its chat rate is messages ÷ the span's minutes, to 0.1, given only for a span of 2 minutes or more (`CHAPTER_RATE_MIN_S`). Below the chart: the **busiest minute** (the most audience chat messages, the earliest on a tie, and the chapter it falls in), the **liveliest chapter** (the highest chat rate) and the chapter with **the most questions**, each only when one chapter leads alone (`record.assemble.chapter_marks`, `src/report/cover.js`) |
| Replaying the cover | Moving across the cover chart (mouse, finger, or arrow keys, Page Up/Down for five minutes, Home/End) shows one live minute at a time: its chapter (as placed above), the audience's chat messages and questions that minute (`record.pulse.minutes`), what was being said (the utterance that covers most of the minute, only when the transcript's clock interleaves speech with the chat, `clock.interleave`), up to 3 messages written in it (the longest without a link, one per person when enough people wrote; the team's only on a session where everyone is the team) and up to 2 questions, by short name as on the Transcript (`GET /record/transcript`, `src/report/coverStory.js`). The key moments (as below) are pinned on the ridge at their busiest minute. Nothing is counted in the page; no model is called |
| Findings: chapter by chapter | "What stood out" on the Report (`record/findings.py`, `detect_chapters`). Each chapter's **unprompted** audience chat per minute: live audience messages in the chapter's span (its start to the next chapter's start), leaving out those the chat labels call greetings or thanks (`social`), logistics, or sound and connection complaints (`tech`, which production friction counts), and any message within 60 s after a speaker's prompt to the room (from the session analysis). The usual chapter is the median of those rates, and the index is a chapter's rate ÷ the usual × 100, for chapters of 2 minutes or more that aren't the Q&A (a Q&A chapter shows its questions instead). Checked when the chapters are on the session clock, the chat is labelled, the session lasts 10 minutes or more, there are 20 unprompted messages or more and two chapters with an index. Found when a chapter reaches 150 or more ("drew the room in") or falls to 50 or less ("the room went quiet"). Shown as a bar per chapter in messages a minute, with the usual chapter as a dashed line. Authors are counted by person, else by Livestorm's author id; when neither is known, no count of people is shown. Estimated |
| Findings: production friction | `detect_friction`. An **incident**: 3 or more audience messages labelled sound, video or connection (`tech`), each within 2 minutes of the previous one, with the silence on stage (gaps of 5 s or more between utterances, on the session clock, never before the first word) from a minute before it to a minute after. A **late start**: `started_at − estimated_started_at` of 3 minutes or more, with the audience's messages before the start. Found when there is an incident or a late start; without chat labels, only the late start is looked at. Estimated for incidents, Measured for a late start |
| Findings: monologue stretches | `detect_monologue`. The longest run of one speaker's utterances in a row, on the session clock (minutes), ended by a change of speaker, a prompt to the room or an utterance that answers an audience question (both from the session analysis). From 8 minutes, the unprompted audience chat (as for chapters, without the 60 s reply window) in its first third is compared with its last third. Found when the last third has half or less of the first, from 6 messages or more in the first third. Shown as a band on the Pulse. Estimated: chat stands in for attention |
| Findings: did the room have time to answer | `detect_prompts`. For each prompt to the room the session analysis found: the speaker's wait (the prompt's end to the next utterance's start), the audience's messages in the 2 minutes after it, the number the session's live rate gives anyway (expected = live audience messages ÷ the session's length × 120 s), and how many came after the speaker spoke again. Replies aren't marked one by one: the messages over the expected number stand for them. Found when a prompt drew 3 or more messages over the expected, with a wait under 10 s and half or more of its messages after the speaker went on. Needs a verified clock (seconds). Each prompt is a triangle under the Pulse's chat, amber when found. Estimated |
| Findings: where the room leaned in, unprompted | `detect_moments`. A **surge minute**: live audience messages (chat and questions) in a minute ≥ max(median + 3 × MAD, 2 × median, 5), over the session's live minutes (MAD: the median of each minute's distance from the median); adjacent surge minutes merge. A surge is a **reply** when a prompt to the room ended in the 60 s before its first minute or during it, a **wave** when half or more of its chat is greetings, thanks, logistics or sound and connection complaints, else an **unprompted moment**. The five busiest moments are numbered in time order and pinned on the Pulse, each with the words being said as it started (shifted back by the room's reaction lag: the median delay of replies to the prompts, else 15 s), only on a verified clock; worded "while the speaker was saying", never as a cause. Needs 30 live audience messages or more and the chat labelled. Estimated |
| Findings: what the room asked for | `detect_demand`. The session analysis groups the audience questions that ask the same thing. For each group of 3 or more questions on the question desk (team and logistics questions left out, as on the desk): how many asked, by how many people, how many are **unanswered** (still open, or only seemingly answered in the talk and not confirmed), and how much of the talk answered them (the seconds of the utterances the analysis located as their answers, each counted once, with the chapter the first is in). Found when half or more of a group's questions are unanswered. Statuses are the desk's, so a question the host marks answered on Follow-up stops counting. Needs the session analysed as it is now. Estimated |
| Key moments, told | Under the Pulse, "What happened at each moment" (`record/moments.py`, `GET /record/moments`). The moments are the surges as above (adjacent minutes merged), any kind: a reply to a prompt, the room's own reaction, a wave of greetings, sound or connection trouble; in a session with no surge, its busiest minutes (3 messages or more, never adjacent). Up to 5, those with the most live audience messages, numbered in time order; the Pulse's pins carry the same numbers. For each: its span, kind, messages, people, questions and its busiest minute; the prompt it answers; what was being said (up to 3 utterances from 20 s before its first message, moved back 15 s unless it answers a prompt, to 40 s after: only when the alignment allows minutes, worded "At" when it allows seconds and "Around" otherwise); up to 3 messages people wrote (the longest distinct ones without a link, leaving out questions and, unless the moment is made of them, greetings, logistics and complaints), the questions asked in it (Q&A, and chat labelled as questions), and the chat labels' counts. Names are short names that fail closed. Needs 10 live audience messages. Estimated |
| Findings: ranking | Each found finding has a strength from 0 to 1, how far past its threshold it is; the strongest comes first. A detector that found nothing adds a tile to "Checked, nothing unusual" (its topic and its verdict); one that couldn't look is counted in one line, and its reason is under "Checks that couldn't run" in "Method and data quality" |
| In short | The Report's first card: one sentence on who came and who wrote (the measures), then up to four points that say first what the rest of the page found: the two strongest findings' headlines, the moment the room reacted most (the key moment with the most messages that is the room's own reaction or a reply to the stage, with what was being said or the prompt it answers), and the questions still open, or that all were answered (the question desk's counts). Before the points load, the question chips show instead (`src/report/summary.js`) |
| The audience: empty parts | A part of "The audience" with no table to show (no channels, no registration question with set answers, no registration times) is left out; "Not shown, and why" in "Method and data quality" lists it with the reason (`audience.js` `emptyAudienceParts`) |
| Findings: words and "Next time" | Every sentence of "What stood out" is a template filled with the server's values, in English (`src/report/findings.js`) or French (`src/report/findingsFr.js`), switched on the card. Each found finding ends with one "Next time" line from a curated library of event craft (`src/report/nextTime.js`: three lines per situation and language, never written by a model, never business advice), picked by a stable hash of the session id and the situation, so a page always reads the same |
| Label | `mm:ss` into the session, with minutes counting past 59 (`125:30`). Before the start `−mm:ss`, after the end `+mm:ss`, both rounded up so a label never reads `−00:00` |
| Evidence ids | Chat messages `C0001`, `C0002`… by `created_at`, then Livestorm id, then text. Questions `Q001`… the same way. Written answers (a question with response text) `A001`… by `responded_at`, then Livestorm id, then text. Items without a readable time come last. A message repeated across pages counts once; if its copies differ, the first copy is kept. The ids don't depend on the clock |
| Has a link | The text holds a URL (`http://`, `https://` or `www.`), or the message's HTML links to one |

### Roster (v1 Report)

The Report. No page shows it yet. One row per person in the session. The people come from one of two sources, and the record says which (`record.roster.source`): the session's complete people list, which StormIQ reads from Livestorm page by page after a session is fetched and stores (`paginated`), or, until that list is stored, the people Livestorm returned with the session (`include=people`, `session_include`). Code: `livestorm_app/record/roster.py`, and `parse_person` in `livestorm/payloads.py`.

| Term | Rule |
|---|---|
| Person | One per Livestorm people id; a person listed twice counts once (the first copy is kept). What a person row reads and leaves out: [security.md](security.md#what-v1-code-reads-about-people) |
| Role | Team when Livestorm's `role` is exactly `team_member`, as in the classic rules. Otherwise guest speaker when `is_guest_speaker` is true. Otherwise audience. A team member who is also a guest speaker is team; an unknown role is audience. The string `"true"` counts as true. What a person did can raise the role (their own chat messages, an answer they wrote): [Authors](#authors-v1-report) |
| Audience member (v1) | A person whose role is audience. On an internal session nobody is relabelled: the roster flags the session as internal, and each audience-only measure counts everyone ([Measures](#measures-v1-report)) |
| Organizer | `session_role` is `organizer` (a host on stage). A flag beside the role; it never changes the role |
| Counts by role | People per role, over everyone and over attendees (`attended` true), after any role raised by chat or an answer. A person whose attendance is unknown is not an attendee |
| Internal | The classic [Internal session](#audience-level) rule, applied to the roster's people. That rule doesn't count a team member who is also a guest speaker as team, while Role does, so a team share taken from the counts by role can differ. Measures read the roster's internal flag; they never recompute it from the counts |
| Coverage | Complete only when the source says the list is complete and the number of people (after repeats and records without an id are dropped) matches Livestorm's own count of the list. Otherwise partial. The stored list is complete when its people match the count Livestorm gives for that list (`record_count`), and partial when they don't. The `include=people` list always reads partial: it isn't known yet whether it is capped, nor whether `registrants_count` counts the team |
| Channel (rule 1) | The first that applies: the UTM source, lower-cased (detail: the UTM medium, lower-cased); the referrer's host without `www.`, unless it is a Livestorm host (`livestorm.co`, `livestorm.io` or a subdomain); `type:` and the registration type, lower-cased; `direct_or_unknown` |
| Lead time | `(started_at − registered_at) / 86,400` days, where `registered_at` is the registration's `created_at`. Buckets count 24-hour periods before the start; time zones and calendar dates play no part: under 24 hours (`same_day`, including exactly at the start), 1–3, 4–7, 8–14, 15+, and after the start (registered after `started_at`). No `started_at`, no lead time: `estimated_started_at` is never used |
| Registration answers | Fields of type text, single select or multiple select, except the identity fields (email, first name, last name, avatar), Livestorm's built-in contact fields (phone, address, city, Twitter, LinkedIn, Facebook, website), company and job title. The built-in country field is kept. Empty answers are left out |
| Field labels | From the workspace's people attributes when StormIQ has them. Otherwise the field's slug made readable (`team_size` → "Team size") |
| Person ref | `P-` and the first 8 hex characters of SHA-256 of `session_id:person_id`. When two people of a session share one, the smallest Livestorm id keeps it and the others get the shortest longer prefix that tells them apart. So the same people always get the same refs, in any order. A ref can change only when a person whose 8-character ref matches it joins or leaves the list: it can lengthen, or go back to the short one. About 3 in 1,000 sessions of 5,000 people have such a match |

### Authors (v1 Report)

The Report. No page shows it yet. Who wrote each chat message, question and written answer, as a person of the [roster](#roster-v1-report). Code: `livestorm_app/record/authors.py`.

| Term | Rule |
|---|---|
| Resolved author | The item's people id is the Livestorm id of a person in the roster. Nothing else counts: an author is never matched by name, by message counts or by order. An author that doesn't resolve is unresolved, and so are its items |
| Question and answer authors | A question carries its asker's people id (`question_author_id`), and a written answer its responder's (`response_author_id`), so both resolve |
| Chat authors | A chat message's author is its people link, `relationships.people.data.id`. StormIQ fetches the chat with `include=people` to get it. Livestorm doesn't document the link. It was checked on one real session: every message carried it, and every linked id was in the session's own people list. If that list turns out to be capped on a large session, an author missing from it reads unresolved, and chat coverage shows partial. A message's `author_id` is a different id: it is never read as a people id. A chat cached before StormIQ asked for the link has none, so its authors read unresolved. StormIQ reads a cached chat from the cache and doesn't ask Livestorm for it again, so they stay unresolved ([roadmap](roadmap.md#known-limitations)) |
| Author coverage | Per source (chat, questions, written answers): items, items without a people id (never resolved: every message of a chat cached before the link), items resolved, distinct people ids and people ids resolved. Resolved share = people ids resolved ÷ distinct people ids. State: resolved when every item resolved, unresolved when none did, partial otherwise, empty with no item. Items count as in [Evidence ids](#session-clock-v1-report): a message repeated across pages counts once |
| Unresolved author's role | A chat author (linked to someone not in the roster): team when one of its messages is `from_team_member`, then guest speaker when one is `from_guest_speaker`, then audience. An asker: audience. A responder: team |
| Role raised by chat or an answer | A person's role rises to team when a chat message resolved to them is `from_team_member` (role source `chat_flag`), or when they wrote an answer, since answering is a team action (`written_answer`). It rises to guest speaker when one of their messages is `from_guest_speaker` and they are not team (`chat_flag`). A role never falls. On an internal session nothing is raised: roles stay Livestorm's. The roster's internal flag never changes: it reads Livestorm's own roles |
| Team item | A chat message is team when its own `from_team_member` or `from_guest_speaker` is true, whether or not its author resolved. A question is team when its asker resolved to a person who is team or a guest speaker (after any role raised), and not team otherwise. A written answer is always team, whatever its responder's role: a responder who reads as audience is most likely a team member registered as a participant |
| Contributor | A person whom a chat message or a question resolved to. One person who chats and asks counts once. Unresolved authors are not contributors |

### Transcript health (v1 Report)

The Report. No page shows it yet; the record route sends it as `record.transcriptHealth` ([api.md](api.md#the-v1-record)). How far a session's transcript can be trusted, from the stored Gladia transcript, before pace or speaker shares are shown. Times are seconds into the recording (`r`): the transcript isn't aligned with the session clock here. Code: `livestorm_app/record/transcript.py` (the parser) and `record/transcript_health.py`. The thresholds are provisional guesses, to tune on real transcripts; changing one bumps the health version (1). Confidence isn't comparable across Gladia models, so the model (`request_params.model`) is kept with the values.

| Term | Rule |
|---|---|
| Utterance ids | `U0001`, `U0002`… over `result.transcription.utterances`, by start, then end, then payload order. Utterances without a start come last. Gladia's own ids are never used. Two parses of the same transcript give the same ids |
| Words | `result.transcription.words` when Gladia sent a non-empty list there; each word belongs to the latest-starting utterance that starts at or before its middle and reaches it (within 1 second), and to none when no utterance does. Otherwise the words inside each utterance (where Gladia puts them). An empty list never hides the utterances' words. The normalized top-level `words` list (a copy) is never read. A confidence outside 0–1 counts as missing |
| Older long transcripts | A transcript saved before the merge fix (no `result.stormiq_chunks`, and the `result.transcription.words` list the old merge always wrote) kept the words of its later parts on their part's own clock. For those only, an utterance whose words lie more than 1 second outside it has them moved by `utterance start − first word start`. Nothing is written back. Its parts are counted from those moves: 1, plus one per group of moves that agree within 60 seconds |
| Parts (chunks) | The entries of `result.stormiq_chunks`. A transcript without them and without the old merge's list is one part. An older one whose utterances carry no words: unknown |
| Mean word confidence | The mean of the words' confidences, over the words that have one. At word level, never utterance level. No such word: empty, never 0 |
| Low-confidence share | Words with a confidence below 0.6 ÷ words with a confidence. Empty without such words |
| Speech seconds | The length of the union of the utterances' spans (start to end, clipped to the recording), so two people talking at once count once |
| Speech coverage | Speech seconds ÷ the recording's length (`result.metadata.audio_duration`, else the file's, else the transcript's `duration_seconds`), and ÷ the live session's length ([session end](#session-clock-v1-report)). The second can pass 1 when the recording runs longer than the session. Also: when the first utterance starts, and the silent tail (the recording's length − the last utterance's end) |
| Detected speakers | Distinct speaker numbers in the utterances. A speaker's seconds are the sum of their utterances' lengths |
| Substantial speaker | At least 30 seconds and at least 2% of all speakers' seconds |
| Labelled speakers | Detected speakers the host named on the Transcript tab. Counted only: a label never leaves the server in the record |
| Expected speakers | The people on stage the transcript's speaker cap was built from (`stormiq.people_on_stage`), else the roster's count of people on stage ([Speaker cap](#transcript-level)), else unknown |
| Split risk | More substantial speakers than expected speakers + 1, or more than one part (each part numbers its speakers on its own) |
| Language | The first detected language (`result.transcription.languages`, else `result.metadata.language`, else the transcript's `language`) against the event's language, by primary subtag (`fr-FR` is `fr`). No verdict (null) when either is unknown; only a known mismatch adds `language_mismatch`. Each language's share of the speech, by the seconds of the utterances in it (by count when no utterance has a length) |
| Unclear clusters | Every 30-second window, starting at a word below 0.6, that holds at least 3 such words; windows that share words merge. The 10 with the most of them (the earliest first on a tie), in time order, with their recording times and utterance ids. They feed "Figures to check" later |
| State | The first that applies. **Unavailable**: no Gladia transcript. **Poor**: no utterance at all (`no_speech`); a mean word confidence below 0.70 (`low_confidence`), or a low-confidence share of 0.25 or more (`unclear_words`). **Degraded**: a mean below 0.85, a low-confidence share of 0.10 or more, no word with a confidence (`no_word_data`), a speech coverage of the recording below 0.60 (`little_speech`), speech in which diarization found no speaker (`no_speakers`), a split risk (`split_risk`), or a language that doesn't match the event's (`language_mismatch`). **Ok** otherwise. Every reason found is listed, the poor ones first |
| Gates | Pace may be shown when the speech coverage of the recording is at least 0.6. Speaker shares may be shown when there is at least one substantial speaker, and either no split risk or the host labelled every substantial speaker. Both are closed without speech |

### Alignment probe (v1 Report)

The Report. No page shows it yet; the record route sends it as `meta.clock.alignment` ([api.md](api.md#the-v1-record)). Where the recording sits on the [session clock](#session-clock-v1-report): `t = r + offset`, where `r` is seconds into the recording. An offset of +30 means the recording started 30 seconds after `started_at`. Code: `livestorm_app/record/alignment.py` (`probe_alignment`, `alignment_allows`). Every threshold below is provisional, to tune on 5 to 10 sessions checked by ear against their replay (`scripts/probe_alignment_study.py`); changing one bumps the alignment version (1).

| Term | Rule |
|---|---|
| Anchor | A moment where speech and chat meet. Each gives a range the offset must lie in. Only our own ids and times leave the server: the message's or question's [evidence id](#session-clock-v1-report), the [utterance ids](#transcript-health-v1-report), `t`, `r` and the range. Never a word, a speaker label or a Livestorm id |
| Tokens | Words lower-cased, accents removed, split on anything that isn't a letter or a digit (`l'intégration` → `l`, `integration`). A content word has at least 3 letters, isn't a number, and isn't a stopword (the classic transcript's list, `analytics/transcript.COMMON_STOPWORDS`, plus a few English and French function words, `alignment.EXTRA_STOPWORDS`) |
| Link | A team chat message with a link ([Has a link](#session-clock-v1-report)), paired with an utterance that announces something in the chat: "in the chat", "into the chat", "the link", "a link", "dans le chat", "le lien", "un lien", "je vous mets", "je vous partage". `r` is the start of the utterance's first cue. Range `[t − r − 90, t − r + 30]`: posted up to 30 s before it is announced, or up to 90 s after. A cue inside a call to the chat ("tell us in the chat") belongs to the call. Bounds the offset from both sides, loosely |
| Wave | A call to the chat: "tell us in the chat", "let us know in the chat", "say hi", "say hello", "where are you joining from" (or "where you are", "where you're"), "dites-nous dans le chat", "dites-moi dans le chat", "écrivez dans le chat", "bonjour à tous". `r` is the end of the utterance's last cue. Paired with a burst: at least max(3, 3 × the session's live audience messages per 20 s) audience messages within 20 s, the first at session time `s`. Range `[s − r − 45, s − r − 3]`: the room starts answering 3 to 45 s after the call. Bounds the offset from above; its lower edge depends on how fast people react |
| Read aloud | An audience question or chat message with 5 to 40 content words that the speakers say in 1 to 3 consecutive utterances: at least 70% of its content words are there, and at least 60% in the same order (longest common subsequence ÷ its content words). Only utterances heard within the search bounds count. Only a unique place counts: two separate places, or more than 24 windows holding 70% of its words, give no anchor. `r` is the first matched word's start. Range `[t − r + 3, t − r + 1800]`: read 3 s to 30 min after it was posted. A question answered aloud with a `responded_at` is also read at most 5 min after it: `≤ responded_at − r + 300`. A place two messages match counts once, for the earlier message. Team chat is never read aloud, even on an internal session: the one typing may be the one speaking, and type the words after saying them. Bounds the offset from below |
| Media time | The recording file's `creation_time` as its start, ±2 s. Off until the tag is shown to mark the recording's start |
| Pairing | Links and waves are paired one to one, nearest first: by how far the pair's range lies from 0, then how far its middle does |
| Internal session | Everyone counts as audience: any chat message can be part of a burst, and any question can be read aloud. Team chat still can't be read aloud |
| One anchor per moment | A message or an utterance feeds at most one anchor: a link first, then a wave, then a read aloud. A later anchor that would reuse either is dropped, so one moment (a link said, then posted with the same words) never confirms itself |
| Duration | The session's length L ([session end](#session-clock-v1-report)) against the recording's length A ([speech coverage](#transcript-health-v1-report)). One continuous recording starts between `min(0, L − A) − 30` and `max(0, L − A) + 30`. A constraint on the offset, never an anchor |
| Search bounds | ±900 s, widened to `|L − A| + 120` s. A pair whose range lies outside them is never made |
| Agreed range | A sweep over the anchors' ranges, clipped to the duration constraint (to the search bounds when a length is unknown), finds the range covered by the most anchors; on a tie, the one holding 0, else the nearest to 0. The anchors covering it agree |
| Offset | 0 when it lies in the agreed range (the recording starts at `started_at`), otherwise the range's middle. ± is the distance to the range's farther edge |
| Drift | The sweep again on each half of the recording (anchors by `r`, split at half its length), without the duration constraint. A half is bounded when at least 2 of its anchors agree on at most 60 s. Two bounded halves whose agreed ranges lie more than 30 s apart are drift: no change of 30 s or less fits both, so the recording has a pause or a cut, which one offset can't fix. Ranges that overlap are never drift |
| Verified | At least 2 anchors agree, and at least 75% of them, on a range that puts the offset within ±15 s (so at most 30 s wide, and when 0 lies in it, 0 is at most 15 s from either edge), bounded from both sides (from below by a link, a read aloud or the media time; from above by a link, a wave or the media time), with no drift. The offset applied is the agreed one |
| Assumed | Not verified; the lengths agree within max(60 s, 2% of L); at least 50% of the anchors agree; 0 lies in the agreed range or within 30 s of it, or there is no anchor; no drift. The offset applied is 0. Its reasons say why it isn't verified: `no_anchors`, `few_anchors` (fewer than 2 agree), `weak_agreement` (under 75% agree), `wide_range` (± over 15 s), `one_direction` |
| Misaligned | Anything else. No offset is applied. Reasons: `duration_gap`, `duration_unknown` (no recording length), `anchors_disagree`, `offset_not_zero`, `drift` |
| Unavailable | No Gladia transcript (`no_transcript`) or no `started_at` (`no_clock`). The audience lanes still work: chat and questions share Livestorm's clock |
| The gate | One rule decides what may use the transcript on the session clock (`alignment_allows`): findings in seconds need verified; findings in minutes, verified or assumed; audience-only views always run. The record sends its answers (`allows`), so the page decides nothing. The Pulse's speech lanes follow the minutes rule |

### Measures (v1 Report)

The Report. No page shows them yet; the record route sends them as `record.measures` ([api.md](api.md#the-v1-record)). Every number the Report shows is a measure, computed by the server: the page renders its fields and computes nothing. Code: `livestorm_app/record/measures.py` (`REGISTRY`, `compute_measures`).

| Term | Rule |
|---|---|
| Measure | One number with how it was got: `value`, `num` and `den` (a ratio's numerator and denominator; for a median, `den` is how many values it is over), `state`, `coverage`, `excluded`, `livestorm` (Livestorm's own figure) and `rows` (a breakdown). Its id never changes meaning; its version goes up when its formula, exclusions or gates change |
| Metrics version | The version of the whole set (`METRICS_VERSION`, 6). It goes up whenever a measure's version changes, or a measure is added or removed |
| State | The first that applies. **Unavailable**: a source wasn't fetched (the session, its people, the chat, the questions, or a Gladia transcript); nothing is computed. **Unresolved**: the measure needs to know who wrote, and a source it reads has items of which not one author resolved ([Authors](#authors-v1-report)); each measure says which sources. The value, the numerator and the denominator are hidden: a count of the resolved part would read as complete. The coverage fields say what is missing. **Insufficient**: a gate isn't met; the value is hidden, the counts are shown. **Approximation**: the method itself is approximate. **Partial**: computed over incomplete inputs: the roster is partial, someone's attendance is unknown, or an author didn't resolve. **Ok** |
| Gate | The minimum a measure needs before it shows a value. Gates are provisional; changing one bumps the measure's version. When a gate hides the value, the measure says which and what the session had: `gate` is `{field, minimum, actual}` (the first gate that failed; `field` is `den` or a quantity the measure names), and null in every other state. The Report's "Method and data quality" card lists every hidden number with this reason ("5 attendees; shown from 10"), an unresolved one as "who wrote is unknown", and an unavailable one with what is missing |
| Coverage | What the measure read and how complete it was. Every measure that reads people carries the roster's coverage, its people count and Livestorm's count of the list. The include list reads partial, so these measures read partial until StormIQ syncs the full list |
| Audience-only | A measure that leaves out team members and guest speakers ([Roster](#roster-v1-report): roles after any role raised by chat or an answer), and says how many: `excluded.team`, `excluded.guestSpeakers`, and their sum `excluded.total`, the n of "excludes your team (n)". On an internal session everyone counts as audience, as in the classic app: nothing is left out (all three are 0), `excluded.internal` is true, and a measure is never 0 of 0 |
| Nothing to divide | A ratio with a denominator of 0 has no value, never 0 |
| Rows | A grouped measure's breakdown: one row per cell, channel, value or bucket, each with its own `key`. A row can have its own gate: below it, the row's `state` is insufficient and its rates are empty, its counts still shown. When the measure's state hides its value, every row's shares, rates and medians are empty too, and each row's state is the measure's. When the measure is unresolved, the row counts that depend on who wrote are empty as well. Channel and segment quality are never unresolved: when who wrote is unknown, only their `wrote` and `wroteRate` are empty |
| Other | Channels or select values too small to show on their own fold into one `(other)` row, so no small group is named |
| Rounding | Values are rounded to 4 decimal places; `num` and `den` are exact |

#### Roster coverage

`m.roster_coverage`. People in the roster ÷ Livestorm's count of the list. No gate. Partial unless the roster is complete. The include list has no count yet, so its value is empty and its state partial. Coverage also gives the source and when it was synced.

#### Audience registrants

`m.audience_registrants`. Audience members in the roster. Audience-only, over everyone registered. No gate.

#### Team count

`m.team_count`. Team members plus guest speakers. Rows: each role's people and attendees. No gate. Roles are never relabelled: on an internal session this still counts only team members and guest speakers (everyone, in an all-team meeting), while the audience-only measures count everyone.

#### Audience attendees

`m.audience_attendees`. Audience members with `attended` true. A person whose attendance is unknown is not an attendee, and makes the measure partial (`coverage.attendanceUnknown`). Audience-only, over attendees. No gate.

#### Show-up rate

`m.show_up_rate`. Audience attendees ÷ audience registrants. Gate: at least 1 audience registrant. Partial when the roster is partial or an attendance is unknown. Audience-only, over everyone registered. Needs the session payload as well as the roster. Livestorm's figure: `attendees_count ÷ registrants_count`, as sent with the session; it may count the team, and it is empty when `registrants_count` is 0 or missing.

#### Time in the room

`m.time_in_room`. The median, over audience attendees, of each one's attendance ÷ the session's length, capped at 1. The length is the [session end](#session-clock-v1-report) (`ended_at − started_at`, else `duration`). A negative attendance counts as 0. Attendees without an attendance duration are left out and counted (`coverage.durationUnknown`). `coverage.medianAttendanceS` is the same median in seconds (each attendance capped at the session's length), and `coverage.sessionLengthS` the length, so the page can say "36 of 45 min". Gates: at least 1 attendee with a duration, and a session length of at least 1 second. Always an approximation: Livestorm's attendance duration doesn't say when someone was in the room. The state stays approximation on a partial roster or with unknown durations; coverage says so. Audience-only, over attendees.

#### Wrote in chat or Q&A

`m.wrote_rate`. Audience attendees who wrote ÷ audience attendees. Wrote means a chat message or a question resolved to the person ([Authors](#authors-v1-report)), at any time: in the waiting room, live or after the end. Gate: at least 10 audience attendees; below it, the counts only. Unresolved when the chat has messages and none of their authors resolved (a chat cached before StormIQ fetched people links), or there are questions and none of their askers resolved: that source's writers are unknown, so the numerator and the denominator are hidden rather than the numerator read as nobody. Partial when the roster is partial, an attendance is unknown, or a chat message or question didn't resolve (`coverage.unresolvedItems`); the value then counts only the people who resolved. Audience-only, over attendees.

#### Questions and answers

`m.questions_qa`. Answered questions ÷ questions. Every question counts, whoever asked. A question is answered when it has a written response, a `responded_at`, or the oral flag (`response_orally`, else `responded_orally`, as in [Audience-level](#audience-level)). Rows: written (response text), oral (the oral flag, no text), marked (`responded_at` only) and open. Gate: at least 1 question. Questions the speakers covered without marking them wait for the transcript.

#### Time to answer

`m.time_to_answer`. The median of `responded_at − created_at`, in seconds, over answered questions with both times. An answer in the same second counts as 0; an answer timed before its question is left out. Gate: at least 3 such questions. Coverage: answered questions, and how many had both times.

#### Author resolution

`m.author_resolution`. Distinct authors of chat messages, questions and written answers who resolved to a roster person ÷ distinct authors. Rows: each source's [author coverage](#authors-v1-report). Gate: at least 1 author. Unresolved when the chat has messages and none of their authors resolved, since the chat's authors are then unknown and the share would count the questions and answers alone; the numerator and the denominator are hidden (the denominator would count only the authors with a people id). Partial when an item didn't resolve or the roster is partial. A diagnostic.

#### Livestorm counts

`m.livestorm_counts`. Livestorm's own counters, for provenance: the session's `registrants_count` and `attendees_count`, and the sums of each person's `messages_count`, `questions_count`, `votes_count` and `up_votes_count` (rows, with their source). No value of its own. Partial while the roster is, since the sums are over its people.

#### Chat concentration

`m.chat_concentration`. The fewest audience members whose chat messages make at least half of the audience messages, the most active first: "k people wrote half the chat". The value is k; `den` is the number of audience messages. An audience message is a chat message resolved to an audience member ([Authors](#authors-v1-report)), sent at any time: in the waiting room, live or after the end. Rows: `top` (the k authors, their messages and their share of the audience messages) and `rest`. `coverage.authors` counts the audience authors. Gate: at least 20 audience messages; below it, k and the rows' numbers are hidden and the number of messages is shown. Unresolved when the chat has messages and none of their authors resolved. Partial when the roster is partial or a message didn't resolve (`coverage.unresolvedItems`): k is then over the resolved messages only, since an unresolved author could be one person or many. Audience-only, over everyone registered. The Report shows it as the last line of "Who spoke up" in "The audience", under the list of the people who wrote (`GET /record/voices`: each one's short name, company, messages and questions, their share of what the audience wrote, and their longest message without a link; the most active 8, then the rest summed).

#### Room map

`m.room_map`. Audience attendees in four cells (rows): stayed and wrote (`stayed_wrote`), stayed and quiet (`stayed_quiet`), left and wrote (`left_wrote`), left and quiet (`left_quiet`), each with its people and their share of `den`. Stayed means an attendance of at least 75% of the session's length, capped as in [Time in the room](#time-in-the-room). Wrote means a chat message or a question resolved to the person, as in [Wrote in chat or Q&A](#wrote-in-chat-or-qa). `den` counts the audience attendees with an attendance duration; the others are left out and counted (`coverage.durationUnknown`). Gates: at least 10 such attendees, and a session length of at least 1 second (without a length nobody can be placed, so the first gate already fails); below them, the counts are shown, not the shares. Always an approximation, as time in the room is. Unresolved as wrote in chat or Q&A is, and then every cell's count is hidden. Audience-only, over attendees.

#### Channel quality

`m.channel_quality`. One row per [channel](#roster-v1-report) of the audience registrants, the largest first: registrants and their share of the audience registrants, attendees and show-up (attendees ÷ registrants), the attendees who wrote (as in [Wrote in chat or Q&A](#wrote-in-chat-or-qa)) and their share of the attendees, and the median attendance as a share of the session (`medianTimeShare`) and in seconds (`medianAttendanceS`), capped as in time in the room. A channel with fewer than 20 registrants folds into the `(other)` row (`kind` `other`, with how many `channels` it holds), whose rates show only when it reaches 20 too. Registrants with no source are a row of their own (`direct_or_unknown`, `kind` `none`), never a channel, with the same minimum of 20. A channel named like one of these two rows folds into `(other)`. Gates: at least half the audience registrants with a source (a UTM, a referrer or a registration type: `coverage.withSource`, `coverage.sourcedShare`), and at least 2 channels of 20. A session with no source at all, such as an internal meeting, fails both and has no rows: one `direct_or_unknown` row of everyone would read as "direct 100%"; `coverage.registrants` and `coverage.withSource` say why. Never unresolved: where people registered from, who came and how long they stayed don't depend on who wrote. When the chat has messages and none of their authors resolved (a chat cached before StormIQ fetched people links, which StormIQ doesn't fetch again), or there are questions and none of their askers resolved, every row's `wrote` and `wroteRate` are empty and the rest is shown; the measure is then partial, and `coverage.chatAuthors` or `coverage.questionAuthors` says unresolved. Partial also when the roster is partial, an attendance is unknown or an author didn't resolve. The median attendance is an approximation, as time in the room is; the state doesn't repeat it. Audience-only, over everyone registered.

#### Segment quality

`m.segment_quality`. One row per value of each single- or multiple-select field of the registration form, with the same columns as [Channel quality](#channel-quality); `field`, `value` and `label` say which. Text fields are never segments. A value with fewer than 10 registrants folds into its field's `(other)` row (with how many `values` it holds). A field's rates show only when at least 2 of its values have 10 registrants, and the gate is one such field. A multiple select counts a person under each value they chose, so a field's shares can add up to more than 1. Labels come from the field's options when StormIQ has them (the workspace's people attributes); until then, the label is the value as stored. `coverage.fields` gives each select field, how many audience registrants answered it, and how many of its values reach 10. With no select field (none on the form, or the event's fields weren't cached: `coverage.selectFields` is 0) the measure is insufficient and has no rows. Partial as channel quality is, and never unresolved: unresolved authors empty only `wrote` and `wroteRate`. Audience-only, over everyone registered.

#### Show-up by lead time

`m.lead_time_show_up`. Show-up (attendees ÷ registrants) per [lead-time bucket](#roster-v1-report), over the audience registrants with a lead time (`den`). The buckets before the start that have people merge while one has fewer than 20 registrants: the smallest joins its smaller neighbour (the shorter lead time on a tie), until each has 20 or one is left. A merged row's key is its first and last bucket (`4-7..15+`), and `buckets` lists them. Registering after the start is a row of its own, never merged: those people registered to join. A row with fewer than 20 registrants hides its rate. Gate: at least 100 audience registrants with a lead time. Registrants without one (no registration time, or no `started_at`) are left out and counted (`coverage.leadUnknown`), and make the measure partial, as a partial roster or an unknown attendance does. Needs the session payload. Audience-only, over everyone registered.

#### Replay catch-up

`m.replay_caught_up`. Audience no-shows who viewed the replay (`has_viewed_replay`) ÷ audience no-shows. Rows: `no_shows`, and `attendees` who watched it again, each with its people, how many viewed the replay and their share. People whose attendance or replay flag is unknown are left out and counted (`coverage.attendanceUnknown`, `coverage.replayUnknown`). Gate: at least 10 no-shows; below it, the counts are shown, not the shares, since one or two no-shows would read as 0% or 100%. Livestorm's flag only shows the views made before StormIQ read the people, so the measure is partial until they were read at least 7 days after the session ended (`coverage.settled`, with `coverage.syncedAt`). The people cached with the session have no read time, so this reads partial until StormIQ syncs them. Audience-only, over everyone registered.

#### Transcript health

`m.transcript_health`. The [mean word confidence](#transcript-health-v1-report) of the transcript, over the words that have a confidence (`den`). Gate: at least 1 such word; below it, insufficient, never 0. Partial when some words have no confidence. Unavailable without a Gladia transcript. Coverage carries the transcript's health: its state and reasons, the Gladia model, the words, the low-confidence share, the speech coverage of the recording, the substantial and expected speakers, the split risk and the parts.

#### Speaking pace

`m.speaking_pace`. Words per minute of speech: the transcript's words (`num`) over the utterances' total length in minutes (`coverage.speakingS`, recording seconds; an utterance without both times is left out). Gates: the transcript covers at least 60% of the recording ([transcript health](#transcript-health-v1-report)'s pace gate) and holds at least 60 s of speech; below either, insufficient. An approximation when the transcript's health is degraded. Shown on the Transcript tab's "Speaking stats".

#### Speaker talk time

`m.speaker_talk_time`. Talk time per speaker, as the transcript's diarization splits it: one row per speaker key (`unknown` for speech without a speaker), most talk first, with its seconds (`speakingS`), its share of all speech (`share`), its words and its words per minute (`wpm`; empty for a speaker with less than 60 s of speech, where a pace is noise). `value` is the first row's share; `den` is how many speakers. Speakers are keys, never names: the Transcript tab names them with the names saved there. Gates: speaker shares can be trusted (transcript health's speaker-share gate: at least one substantial speaker, and no risk of one speaker split across keys unless every substantial speaker is labelled) and at least 60 s of speech; below either, the rows' numbers are hidden. An approximation when a speaker may have been split.

#### Longest stretch

`m.longest_stretch`. The longest time one speaker held the floor, in seconds: their consecutive utterances joined while the gap between two is at most 2 s, from the first's start to the last's end (recording seconds). `coverage` names the speaker key, its start and end, and its utterance ids. Gated as [speaker talk time](#speaker-talk-time) is.

#### Clock alignment

`m.clock_alignment`. The [alignment probe](#alignment-probe-v1-report)'s anchors that agree ÷ the anchors found (`num` of `den`). Gate: at least 1 anchor; below it, insufficient, never 0. Its state follows the clock, through the gate: ok when it is verified, an approximation when it is assumed (offset 0 applied, not measured), and insufficient when it is misaligned (the share is hidden; the counts and the status stay). Unavailable without the session payload or a Gladia transcript. Coverage carries the probe's result: its status (`status`), where the offset comes from (`source`), the offset applied and its ± (`offsetS`, `plusminusS`), the reasons, the anchors found per kind (`link`, `wave`, `readAloud`, `mediaTime`) and the duration gap in seconds (`durationGapS`, session − recording). A session without `started_at` is insufficient, with the status `unavailable`.

#### Questions

`m.questions_total`. Every question the audience asked: the Q&A questions plus the audience's chat messages that the [chat labels](ai-outputs.md#chat-labels-v1) mark as a question about the content. Always shown split, in rows: `qa` (Q&A questions), `chat` (chat questions about the content) and `chatLogistics` (chat questions about the session itself, such as a replay or the slides), which are counted apart and are not in the value. A team message (one Livestorm flags as the team's or a guest speaker's) is left out, and `coverage.teamMessages` says how many; on an internal session every message counts. `coverage.chatMessages` counts the audience messages read. Unavailable until a session analysis has labelled this chat (`coverage.missing` is `labels`), or when the chat or the questions weren't fetched. The chat side is a model's reading, marked `coverage.labels: model`: each count opens its messages, and the page's status word for it is always "Estimated". The Report shows it in the Questions vital. "The questions" lists the questions themselves, from the question desk (`GET /record/questions`, the same as Follow-up's, read-only): still open first (with those the talk seems to answer, not confirmed), then answered, each with who asked, where (Q&A or chat), when, and its answers (the host's, Livestorm's written one, the passage of the talk that answers it); the logistics questions counted apart, the median [time to answer](#time-to-answer) when it shows, and a link to answer the open ones on Follow-up. Until the chat is labelled, the desk lists the Q&A alone and says why.

#### Chat themes

`m.chat_themes`. What the audience's chat was about. One row per label class, always in this order: `content`, `question`, `tech`, `logistics`, `social`, `laughter`, `off_topic`. Each row has its number of messages, its share of the audience's messages, its languages (each with its message count, the most frequent first; a message with no words has no language) and its messages' ids (`ids`), so the page can open them. `den` is the audience's messages. Gate: at least 30 messages; below it the shares are hidden and the counts shown. Team messages are left out as in [Questions](#questions). Unavailable until a session analysis has labelled this chat. The classes are a model's reading; laughter-only messages are labelled by a rule. The Report shows it in "The room", open by default: one line per theme that has messages (themes with none are named in a note), with its count, its share as a bar and a percentage, and its languages, always marked "Estimated". Each line opens its messages with their time on the session clock, read from `GET /api/sessions/{id}/record/chat` ([api.md](api.md#the-v1-record)) when the first line is opened; no author is shown. The card's header names the largest theme when shares show, and its chip counts the audience's messages.

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
