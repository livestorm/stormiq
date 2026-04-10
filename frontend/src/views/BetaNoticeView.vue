<template>
  <section class="page-section beta-notice-page">
    <div class="panel beta-notice-hero">
      <p class="beta-notice-kicker">Beta Notice</p>
      <h2>How this assistant works</h2>
      <p class="page-description beta-notice-description">
        This tool is a beta companion designed to help customers get started faster with post-event review,
        transcript exploration, and AI-assisted content outputs.
      </p>
      <p class="beta-notice-copy">
        It is not part of Livestorm&apos;s official product workflow or support process. Outputs may be incomplete,
        approximate, or require human review before operational use. Livestorm does not assume responsibility for the
        full downstream usage, validation, or long-term maintenance of this tool.
      </p>
    </div>

    <div class="beta-notice-grid">
      <article class="panel beta-notice-card">
        <h3>What the app does</h3>
        <p>
          The app fetches session data from Livestorm, transcribes the recording, structures transcript insights,
          analyzes engagement signals, and generates optional summaries, analyses, and repurposed content.
        </p>
      </article>

      <article class="panel beta-notice-card">
        <h3>Core providers</h3>
        <p><strong>Livestorm</strong> is used for session, event, chat, question, and recording retrieval.</p>
        <p><strong>Gladia</strong> is used for transcription, diarization, sentence extraction, NER, and subtitles.</p>
        <p><strong>OpenAI GPT models</strong> are used for analytical and generative writing tasks.</p>
      </article>
    </div>

    <section class="panel beta-notice-card">
      <div class="panel-heading">
        <h3>Model usage by section</h3>
      </div>
      <div class="beta-model-table">
        <div class="beta-model-row beta-model-row-head">
          <span>Section / Step</span>
          <span>Provider / Model</span>
          <span>Purpose</span>
        </div>
        <div class="beta-model-row">
          <span>Transcript fetch</span>
          <span><code>gladia-v2-pre-recorded</code></span>
          <span>Speech-to-text, diarization, sentences, NER, subtitle generation (`srt`, `vtt`).</span>
        </div>
        <div class="beta-model-row">
          <span>Transcript page</span>
          <span><code>gladia-v2-pre-recorded</code> output</span>
          <span>Displays the transcript, subtitle files, speaker timeline data, and timing metrics.</span>
        </div>
        <div class="beta-model-row">
          <span>Overall Analysis</span>
          <span><code>gpt-4o-mini</code></span>
          <span>Generates the transcript-based overall analysis.</span>
        </div>
        <div class="beta-model-row">
          <span>Deep Analysis</span>
          <span><code>gpt-4o-mini</code></span>
          <span>Generates the deeper diagnostic analysis using transcript, chat, questions, and session context.</span>
        </div>
        <div class="beta-model-row">
          <span>Content Repurposing</span>
          <span><code>gpt-4o-mini</code></span>
          <span>Creates summary, blog, email, and social copy variants from session content.</span>
        </div>
        <div class="beta-model-row">
          <span>Smart Recap</span>
          <span><code>gpt-5.4-mini</code></span>
          <span>Produces tone-specific recap variants such as professional, hype, or surprise.</span>
        </div>
        <div class="beta-model-row">
          <span>Translations</span>
          <span><code>gpt-4o-mini</code></span>
          <span>Translates generated outputs between supported languages when needed.</span>
        </div>
      </div>
    </section>

    <section class="panel beta-notice-card">
      <div class="panel-heading">
        <h3>Compliance-oriented note</h3>
      </div>
      <p>
        Customers should review outputs before sharing them externally, relying on them for decision-making, or using
        them as a source of record. Transcript quality depends on source audio, and LLM-generated outputs can contain
        omissions, formatting issues, or reasoning errors.
      </p>
      <p>
        This page is informational and intended to clarify the current beta implementation, not to create product,
        legal, or support commitments.
      </p>
    </section>
  </section>
</template>
