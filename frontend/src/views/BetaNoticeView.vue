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
        <p><strong>Anthropic Claude</strong> is used for all analytical and generative writing tasks — overall analysis, deep analysis, smart recap, and content repurposing.</p>
        <p><strong>OpenAI Images API</strong> is used exclusively for AI-generated session cover images.</p>
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
          <span>Speech-to-text, diarization, sentences, NER, subtitle generation (srt, vtt).</span>
        </div>
        <div class="beta-model-row">
          <span>Transcript page</span>
          <span><code>gladia-v2-pre-recorded</code> output</span>
          <span>Displays the transcript, subtitle files, speaker timeline data, and timing metrics.</span>
        </div>
        <div class="beta-model-row">
          <span>Overall Analysis</span>
          <span><code>claude-haiku-4-5</code></span>
          <span>Generates the 5-section overall analysis from transcript, chat, and question signals.</span>
        </div>
        <div class="beta-model-row">
          <span>Deep Analysis</span>
          <span><code>claude-haiku-4-5</code></span>
          <span>Generates the 10-section host-facing diagnostic using transcript, chat, questions, and session context.</span>
        </div>
        <div class="beta-model-row">
          <span>Content Repurposing</span>
          <span><code>claude-haiku-4-5</code></span>
          <span>Creates summary, blog post, follow-up email, and social media copy from session content.</span>
        </div>
        <div class="beta-model-row">
          <span>Smart Recap</span>
          <span><code>claude-sonnet-4-6</code></span>
          <span>Produces tone-specific recap variants (professional, hype, surprise). Uses a stronger model intentionally.</span>
        </div>
        <div class="beta-model-row">
          <span>Language switching</span>
          <span><code>claude-haiku-4-5</code></span>
          <span>Translates generated outputs between English and French when the missing language is requested.</span>
        </div>
        <div class="beta-model-row">
          <span>Session cover image</span>
          <span><code>gpt-image-2</code> — OpenAI Images API</span>
          <span>Generates a 16:9 AI cover image for each session card, derived from the Professional Smart Recap.</span>
        </div>
      </div>
    </section>

    <section class="panel beta-notice-card">
      <div class="panel-heading">
        <h3>Infrastructure &amp; data residency</h3>
      </div>
      <p>
        The application, background worker, and database are hosted on <strong>Render</strong> in the
        <strong>Frankfurt (EU Central)</strong> region. Session data, transcripts, and generated outputs are stored in a
        Render-managed PostgreSQL database located in Frankfurt. No data is persisted outside the EU.
      </p>
      <p>
        AI inference requests are sent to Anthropic&apos;s API (Claude) for text generation and to OpenAI&apos;s API for
        cover image generation. Both providers process data transiently and do not retain request content for training
        purposes under their standard API terms.
      </p>
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
