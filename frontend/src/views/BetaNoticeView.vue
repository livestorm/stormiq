<script setup>
import Card from "../components/ui/Card.vue";
import CardHeader from "../components/ui/CardHeader.vue";

// The beta notice (`/beta-info`, public, linked from the footer): what
// StormIQ is, the providers it calls and where data lives. Informational;
// keep it in step with docs/providers.md and docs/operations.md.

const MODELS = [
  { step: "Transcript", model: "gladia-v2-pre-recorded", purpose: "Speech to text, speakers, sentences and named entities." },
  { step: "Transcript tab", model: "gladia-v2-pre-recorded output", purpose: "What was said next to the chat, the speakers' names, the subtitles (SRT) and the speaking stats." },
  { step: "Session analysis", model: "claude-sonnet-5-5", purpose: "What happened, what landed with the audience, the questions and what to do next, from what StormIQ measured and the transcript, never with a name." },
  { step: "Repurposing", model: "claude-haiku-4-5", purpose: "A summary, a blog post, a follow-up email and social posts." },
  { step: "Smart Recap", model: "claude-sonnet-4-6", purpose: "A recap in three tones (professional, hype, surprise), with a stronger model on purpose." },
  { step: "Languages", model: "claude-haiku-4-5", purpose: "Translates an output between English and French when the other language is asked for." },
  { step: "Session cover", model: "gpt-image-2 (OpenAI Images API)", purpose: "A cover image for the session, from its professional Smart Recap." },
];
</script>

<template>
  <div class="b-page">
    <header class="b-head">
      <p class="b-kicker">Beta notice</p>
      <h1 class="b-title">How StormIQ works</h1>
      <p class="b-lead">
        StormIQ is a beta companion that helps you get started faster with reviewing a session after it ends: its
        transcript, its audience and AI-written follow-up.
      </p>
      <p class="b-text">
        It is not part of Livestorm's official product workflow or support process. Outputs may be incomplete,
        approximate, or require human review before operational use. Livestorm does not assume responsibility for the full
        downstream usage, validation, or long-term maintenance of this tool.
      </p>
    </header>

    <div class="b-grid">
      <Card>
        <CardHeader icon="activity" title="What it does" :level="2" />
        <p class="b-text b-body">
          It fetches a session's data from Livestorm, transcribes the recording, measures how the audience took part, and,
          when you ask, writes summaries, analyses and repurposed content.
        </p>
      </Card>
      <Card>
        <CardHeader icon="settings" title="Core providers" :level="2" />
        <ul class="b-list">
          <li><strong>Livestorm</strong> for the session, the event, the chat, the questions and the recording.</li>
          <li><strong>Gladia</strong> for transcription, speakers, sentences and named entities.</li>
          <li><strong>Anthropic Claude</strong>, by default, for the analyses, Smart Recap and repurposed content (an admin can choose OpenAI instead).</li>
          <li><strong>OpenAI Images API</strong> only for session cover images.</li>
        </ul>
      </Card>
    </div>

    <Card padding="flush">
      <div class="b-section-head">
        <CardHeader icon="list" title="Models by step" subtitle="The defaults; an admin can change the provider and the models." :level="2" />
      </div>
      <div class="b-table-wrap">
        <table class="b-table">
          <thead>
            <tr>
              <th scope="col">Step</th>
              <th scope="col">Provider and model</th>
              <th scope="col">What it does</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in MODELS" :key="row.step">
              <th scope="row">{{ row.step }}</th>
              <td><code>{{ row.model }}</code></td>
              <td>{{ row.purpose }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </Card>

    <Card>
      <CardHeader icon="shield" title="Infrastructure and data residency" :level="2" />
      <div class="b-body">
        <p class="b-text">
          The application, background worker, and database are hosted on <strong>Render</strong> in the
          <strong>Frankfurt (EU Central)</strong> region. Session data, transcripts, and generated outputs are stored in a
          Render-managed PostgreSQL database located in Frankfurt. No data is persisted outside the EU.
        </p>
        <p class="b-text">
          AI inference requests are sent to Anthropic's API (Claude) for text generation and to OpenAI's API for cover
          image generation. Both providers process data transiently and do not retain request content for training purposes
          under their standard API terms.
        </p>
      </div>
    </Card>

    <Card>
      <CardHeader icon="info" title="Compliance-oriented note" :level="2" />
      <div class="b-body">
        <p class="b-text">
          Customers should review outputs before sharing them externally, relying on them for decision-making, or using them
          as a source of record. Transcript quality depends on source audio, and LLM-generated outputs can contain omissions,
          formatting issues, or reasoning errors.
        </p>
        <p class="b-text">
          This page is informational and intended to clarify the current beta implementation, not to create product, legal,
          or support commitments.
        </p>
      </div>
    </Card>
  </div>
</template>

<style scoped>
.b-page {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-6);
  max-width: 960px;
}

.b-head {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-3);
}

.b-kicker {
  margin: 0;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  font-weight: var(--siq-weight-medium);
  color: var(--siq-accent);
}

.b-title {
  margin: 0;
  font-size: var(--siq-text-display);
  line-height: var(--siq-text-display-lh);
  font-weight: var(--siq-weight-regular);
  letter-spacing: -0.015em;
  color: var(--siq-ink);
}

.b-lead {
  margin: 0;
  max-width: 68ch;
  font-size: var(--siq-text-lead);
  line-height: var(--siq-text-lead-lh);
  color: var(--siq-ink);
}

.b-text {
  margin: 0;
  max-width: 72ch;
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  color: var(--siq-ink-2);
}

.b-body {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-3);
  margin-top: var(--siq-space-4);
}

.b-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--siq-space-4);
}

.b-list {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-2);
  margin: var(--siq-space-4) 0 0;
  padding-left: var(--siq-space-6);
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  color: var(--siq-ink-2);
}

.b-list strong,
.b-text strong {
  color: var(--siq-ink);
  font-weight: var(--siq-weight-medium);
}

.b-section-head {
  padding: var(--siq-space-6) var(--siq-space-6) var(--siq-space-2);
}

.b-table-wrap {
  overflow-x: auto;
}

.b-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  color: var(--siq-ink-2);
}

.b-table th,
.b-table td {
  padding: var(--siq-space-3) var(--siq-space-4);
  text-align: left;
  vertical-align: top;
  box-shadow: inset 0 -1px 0 var(--siq-hairline-row);
}

.b-table th:first-child,
.b-table td:first-child {
  padding-left: var(--siq-space-6);
}

.b-table th:last-child,
.b-table td:last-child {
  padding-right: var(--siq-space-6);
}

.b-table thead th {
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  font-weight: var(--siq-weight-medium);
  color: var(--siq-muted);
}

.b-table tbody th {
  font-weight: var(--siq-weight-medium);
  color: var(--siq-ink);
  white-space: nowrap;
}

.b-table tbody tr:last-child th,
.b-table tbody tr:last-child td {
  box-shadow: none;
}

.b-table code {
  font-size: var(--siq-text-small);
  white-space: nowrap;
}

@media (max-width: 720px) {
  .b-title {
    font-size: var(--siq-text-headline);
    line-height: var(--siq-text-headline-lh);
  }
  .b-grid {
    grid-template-columns: minmax(0, 1fr);
  }
  .b-section-head {
    padding: var(--siq-space-4) var(--siq-space-4) var(--siq-space-2);
  }
}

@media (forced-colors: active) {
  .b-table th,
  .b-table td {
    border-bottom: 1px solid CanvasText;
  }
}
</style>
