<script setup>
import Card from "../components/ui/Card.vue";
import Icon from "../components/ui/Icon.vue";
import IconTile from "../components/ui/IconTile.vue";
import StatCard from "../components/ui/StatCard.vue";
import UiButton from "../components/ui/UiButton.vue";
import SessionRidge from "../components/report/SessionRidge.vue";

// What a visitor who isn't signed in sees in the v1 design, on any page
// but the public ones (`App.vue`): what StormIQ does, an example of its
// report, how it works, and one way in: "Connect with Livestorm". Nothing
// here reads an account or a session; the example is made up and says so.

defineProps({
  // False when Livestorm sign-in isn't set up on this server.
  canConnect: { type: Boolean, default: true },
});
const emit = defineEmits(["connect"]);

// A made-up session's audience chat per minute, for the example's ridge.
const EXAMPLE_CHAT = [3, 6, 9, 8, 4, 3, 3, 3, 2, 2, 2, 2, 2, 2, 3, 3, 2, 2, 2, 3, 4, 5, 7, 19, 12, 6, 4, 3, 3, 4, 3, 2, 2, 3, 6, 11, 14, 12, 9, 8, 7, 5, 4, 3, 2];
const exampleMinutes = EXAMPLE_CHAT.map((audienceChat, minute) => ({ minute, audienceChat }));

const FEATURES = [
  {
    icon: "activity",
    title: "The whole session on one clock",
    text: "Chat, questions and speech minute by minute, so you see what the room reacted to.",
  },
  {
    icon: "help-circle",
    title: "Every question accounted for",
    text: "Q&A and chat questions together, with what was answered and what is still open.",
  },
  {
    icon: "users",
    title: "Who came, who stayed, who wrote",
    text: "Show-up, time in the room and participation, by channel and registration answer.",
  },
  {
    icon: "file-text",
    title: "Follow-up, ready to send",
    text: "Recaps and content drafts from the session, in English and French.",
  },
];

const STEPS = [
  { title: "Connect", text: "Sign in with your Livestorm account." },
  { title: "Pick a session", text: "Any finished session of your organization." },
  { title: "Read the report", text: "StormIQ fetches the chat, questions and recording and builds it." },
];

const TRUST = [
  { icon: "lock", title: "Your organization only", text: "You see your own organization's sessions, through your Livestorm sign-in." },
  { icon: "check", title: "Numbers you can check", text: "Each number says how it was counted, and stays hidden when there is too little data." },
  { icon: "zap", title: "AI only when you ask", text: "Nothing is sent to an AI model until you start the analysis." },
];
</script>

<template>
  <div class="l-page">
    <section class="l-hero" aria-labelledby="l-title">
      <div class="l-hero-text">
        <p class="l-eyebrow">Session analytics for Livestorm</p>
        <h1 id="l-title" class="l-title">Know what happened in every session.</h1>
        <p class="l-lead">
          StormIQ turns a finished Livestorm session into a clear report: who came and stayed, what the room reacted to, and
          which questions still need an answer.
        </p>
        <div class="l-actions">
          <UiButton v-if="canConnect" variant="primary" icon="log-in" @click="emit('connect')">Connect with Livestorm</UiButton>
          <p v-else class="l-note" role="status">Sign-in isn't available right now. Try again in a few minutes.</p>
        </div>
        <p class="l-note">Uses your Livestorm account. Nothing to install.</p>
      </div>

      <!-- A made-up example, not anyone's session. -->
      <Card as="figure" class="l-example" aria-label="An example report, with made-up numbers">
        <div class="l-example-head" aria-hidden="true">
          <span class="l-example-title">Product launch webinar</span>
          <span class="l-example-tag">Example</span>
        </div>
        <div class="l-example-ridge" aria-hidden="true">
          <SessionRidge :minutes="exampleMinutes" />
        </div>
        <div class="l-example-stats" aria-hidden="true">
          <StatCard icon="user-check" label="Show-up rate" value="41%" sub="246 of 600 registrants" :level="3" />
          <StatCard icon="message-circle" label="Wrote in chat or Q&A" value="38%" sub="94 of 246 attendees" :level="3" />
          <StatCard icon="help-circle" label="Questions" value="38" sub="12 in Q&A · 26 in the chat" :level="3" />
        </div>
        <figcaption class="l-example-caption">An example report, with made-up numbers.</figcaption>
      </Card>
    </section>

    <!-- The promo film: fetched only when played (preload="none"), never
         autoplayed. Its session is made up, and it shows some of what is
         still being built, so its caption says both. -->
    <section class="l-section" aria-labelledby="l-film">
      <h2 id="l-film" class="l-section-title">See it in a minute</h2>
      <Card as="figure" padding="flush" class="l-film">
        <video
          class="l-film-video"
          controls
          playsinline
          preload="none"
          width="1920"
          height="1080"
          poster="/media/stormiq-promo-poster.jpg"
          aria-describedby="l-film-caption"
        >
          <source src="/media/stormiq-promo.mp4" type="video/mp4" />
          <a href="/media/stormiq-promo.mp4">Download the StormIQ tour (MP4, 5 MB)</a>
        </video>
        <figcaption id="l-film-caption" class="l-film-caption">
          A one-minute tour of a made-up session. Some of what it shows is still being built.
        </figcaption>
      </Card>
    </section>

    <section class="l-section" aria-labelledby="l-features">
      <h2 id="l-features" class="l-section-title">What you get</h2>
      <ul class="l-features">
        <li v-for="item in FEATURES" :key="item.title" class="l-feature">
          <IconTile :icon="item.icon" />
          <h3 class="l-feature-title">{{ item.title }}</h3>
          <p class="l-feature-text">{{ item.text }}</p>
        </li>
      </ul>
    </section>

    <section class="l-section" aria-labelledby="l-steps">
      <h2 id="l-steps" class="l-section-title">How it works</h2>
      <ol class="l-steps">
        <li v-for="(step, index) in STEPS" :key="step.title" class="l-step">
          <span class="l-step-number" aria-hidden="true">{{ index + 1 }}</span>
          <span class="l-step-text">
            <span class="l-step-title">{{ step.title }}</span>
            <span class="l-step-detail">{{ step.text }}</span>
          </span>
        </li>
      </ol>
    </section>

    <section class="l-section" aria-labelledby="l-trust">
      <h2 id="l-trust" class="l-section-title">Built to be trusted</h2>
      <ul class="l-trust">
        <li v-for="item in TRUST" :key="item.title" class="l-trust-item">
          <Icon :name="item.icon" :size="18" />
          <span>
            <span class="l-trust-title">{{ item.title }}</span>
            <span class="l-trust-text">{{ item.text }}</span>
          </span>
        </li>
      </ul>
    </section>

    <Card v-if="canConnect" class="l-closing" padding="hero">
      <h2 class="l-closing-title">Open your first report</h2>
      <p class="l-closing-text">Connect once; your organization's sessions are then a click away.</p>
      <UiButton variant="secondary" icon="log-in" @click="emit('connect')">Connect with Livestorm</UiButton>
    </Card>
  </div>
</template>

<style scoped>
.l-page {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-16);
  padding: var(--siq-space-8) 0 var(--siq-space-12);
}

/* ── Hero ── */
.l-hero {
  display: grid;
  grid-template-columns: minmax(0, 5fr) minmax(0, 6fr);
  align-items: center;
  gap: var(--siq-space-12);
}

.l-hero-text {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--siq-space-4);
}

.l-eyebrow {
  margin: 0;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  font-weight: var(--siq-weight-medium);
  color: var(--siq-accent);
}

.l-title {
  margin: 0;
  font-size: var(--siq-text-display);
  line-height: var(--siq-text-display-lh);
  font-weight: var(--siq-weight-regular);
  letter-spacing: -0.015em;
  color: var(--siq-ink);
  text-wrap: balance;
}

.l-lead {
  margin: 0;
  max-width: 34rem;
  font-size: var(--siq-text-lead);
  line-height: var(--siq-text-lead-lh);
  color: var(--siq-ink-2);
  text-wrap: pretty;
}

.l-actions {
  margin-top: var(--siq-space-2);
}

.l-note {
  margin: 0;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-muted);
}

/* ── The example ── */
.l-example {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-4);
  margin: 0;
}

.l-example-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--siq-space-3);
}

.l-example-title {
  font-size: var(--siq-text-card-title);
  line-height: var(--siq-text-card-title-lh);
  font-weight: var(--siq-weight-medium);
  color: var(--siq-ink);
}

.l-example-tag {
  padding: 2px var(--siq-space-2);
  border-radius: var(--siq-radius-pill);
  box-shadow: inset 0 0 0 1px var(--siq-hairline-head);
  font-size: var(--siq-text-caption);
  line-height: var(--siq-text-caption-lh);
  color: var(--siq-muted);
}

.l-example-stats {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--siq-space-3);
}

/* Inside the example card the stat cards are tiles: no second card shadow,
   no icon tile, tighter padding. */
.l-example-stats :deep(.siq-stat) {
  padding: var(--siq-space-4);
  box-shadow: inset 0 0 0 1px var(--siq-hairline);
}

.l-example-stats :deep(.siq-stat-head .siq-tile) {
  display: none;
}

.l-example-stats :deep(.siq-stat-value) {
  font-size: var(--siq-text-stat);
  line-height: var(--siq-text-stat-lh);
}

.l-example-stats :deep(.siq-stat-sub) {
  white-space: normal;
}

.l-example-caption {
  font-size: var(--siq-text-caption);
  line-height: var(--siq-text-caption-lh);
  color: var(--siq-muted);
}

/* ── The film ── */
.l-film {
  margin: 0;
  overflow: hidden;
}

.l-film-video {
  display: block;
  width: 100%;
  height: auto;
  aspect-ratio: 16 / 9;
  background: var(--siq-ink);
}

.l-film-caption {
  padding: var(--siq-space-3) var(--siq-space-6);
  font-size: var(--siq-text-caption);
  line-height: var(--siq-text-caption-lh);
  color: var(--siq-muted);
}

/* ── Sections ── */
.l-section {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-6);
}

.l-section-title {
  margin: 0;
  font-size: var(--siq-text-section);
  line-height: var(--siq-text-section-lh);
  font-weight: var(--siq-weight-regular);
  color: var(--siq-ink);
}

.l-features,
.l-steps,
.l-trust {
  margin: 0;
  padding: 0;
  list-style: none;
}

.l-features {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--siq-space-4);
}

.l-feature {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-3);
  padding: var(--siq-space-6);
  border-radius: var(--siq-radius-card);
  background: var(--siq-card);
  box-shadow: var(--siq-card-shadow), inset 0 0 0 1px var(--siq-hairline);
}

.l-feature-title {
  margin: var(--siq-space-1) 0 0;
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  font-weight: var(--siq-weight-medium);
  color: var(--siq-ink);
}

.l-feature-text {
  margin: 0;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-ink-2);
}

.l-steps {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--siq-space-4);
}

.l-step {
  display: flex;
  align-items: flex-start;
  gap: var(--siq-space-3);
}

.l-step-number {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: var(--siq-radius-pill);
  background: var(--siq-selected-bg);
  color: var(--siq-selected-ink);
  font-size: var(--siq-text-ui);
  font-weight: var(--siq-weight-medium);
  font-variant-numeric: tabular-nums;
}

.l-step-text,
.l-trust-item > span {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.l-step-title,
.l-trust-title {
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  font-weight: var(--siq-weight-medium);
  color: var(--siq-ink);
}

.l-step-detail,
.l-trust-text {
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-ink-2);
}

.l-trust {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--siq-space-6);
}

.l-trust-item {
  display: flex;
  align-items: flex-start;
  gap: var(--siq-space-3);
}

.l-trust-item > :deep(svg) {
  flex: none;
  margin-top: 1px;
  color: var(--siq-good);
}

/* ── Closing ── */
.l-closing {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--siq-space-3);
}

.l-closing-title {
  margin: 0;
  font-size: var(--siq-text-section);
  line-height: var(--siq-text-section-lh);
  font-weight: var(--siq-weight-regular);
  color: var(--siq-ink);
}

.l-closing-text {
  margin: 0 0 var(--siq-space-2);
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  color: var(--siq-ink-2);
}

@media (max-width: 1100px) {
  .l-hero {
    grid-template-columns: minmax(0, 1fr);
    gap: var(--siq-space-8);
  }
  .l-features {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 720px) {
  .l-page {
    gap: var(--siq-space-12);
    padding-top: var(--siq-space-4);
  }
  .l-title {
    font-size: var(--siq-text-headline);
    line-height: var(--siq-text-headline-lh);
  }
  .l-features,
  .l-steps,
  .l-trust {
    grid-template-columns: minmax(0, 1fr);
  }
  .l-example-stats {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .l-example-stats > :last-child {
    grid-column: span 2;
  }
}

@media (forced-colors: active) {
  .l-feature,
  .l-example-stats :deep(.siq-stat) {
    border: 1px solid CanvasText;
  }
}
</style>
