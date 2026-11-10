<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import Card from "../components/ui/Card.vue";
import CardHeader from "../components/ui/CardHeader.vue";
import Chip from "../components/ui/Chip.vue";
import Collapsible from "../components/ui/Collapsible.vue";
import Icon, { ICON_NAMES } from "../components/ui/Icon.vue";
import StatCard from "../components/ui/StatCard.vue";
import StatusWord from "../components/ui/StatusWord.vue";
import ForLivestorm from "../components/ui/ForLivestorm.vue";
import StormIqMark from "../components/ui/StormIqMark.vue";
import ThemeToggle from "../components/ui/ThemeToggle.vue";
import TipBar from "../components/ui/TipBar.vue";
import UiButton from "../components/ui/UiButton.vue";
import { useTheme } from "../design/useTheme";

// The v1 design foundation on one page: tokens, type, icons and every base
// component, in the current theme. Reached at /design.
// All sample content below is fictional.

const { theme, choice, setChoice } = useTheme();

const TOKEN_GROUPS = [
  { title: "Surfaces", tokens: ["page", "card", "ground", "surface", "surface-2", "card-hover", "topbar", "input", "notice", "selected-bg"] },
  { title: "Text", tokens: ["ink", "ink-2", "muted", "selected-ink", "accent", "accent-hover"] },
  { title: "Lines and controls", tokens: ["border", "border-hover", "hairline", "hairline-row", "hairline-head", "nontext", "focus"] },
  { title: "Actions", tokens: ["cta", "cta-hover", "cta-ink"] },
  { title: "Status", tokens: ["good", "good-fill", "good-tint", "fair", "fair-fill", "fair-tint", "low", "low-fill", "low-tint"] },
  { title: "Chart series", tokens: ["s1", "s2", "s3", "other"] },
  { title: "Mark", tokens: ["mark-fill", "mark-iq", "mark-ink"] },
];

const tokenValues = ref({});

function readTokens() {
  if (typeof window === "undefined") return;
  const styles = window.getComputedStyle(document.documentElement);
  const values = {};
  for (const group of TOKEN_GROUPS) {
    for (const token of group.tokens) values[token] = styles.getPropertyValue(`--siq-${token}`).trim();
  }
  tokenValues.value = values;
}

const TYPE_SAMPLES = [
  { name: "Display 40/48", className: "g-type-display", text: "Spring release tour" },
  { name: "Headline 28/36", className: "g-type-headline", text: "Most open questions were about pricing" },
  { name: "Section 24/32", className: "g-type-section", text: "The room" },
  { name: "Card title 18/24", className: "g-type-card", text: "When the room reacted" },
  { name: "Body 18/30", className: "g-type-body", text: "The audience wrote most during the live demo, then went quiet for nine minutes." },
  { name: "UI 14/20", className: "g-type-ui", text: "Buttons, tables and controls" },
  { name: "Caption 12/16", className: "g-type-caption", text: "Source: sample data. StormIQ analysis." },
  { name: "Label 12/16 uppercase", className: "g-type-label", text: "Finding" },
];

// Sample stat cards (fictional session).
const STATS = [
  { icon: "user-check", label: "Show-up rate", value: "41%", sub: "251 of 612 registrants", info: "Audience attendees ÷ audience registrants. Excludes your team.", status: "measured", infoOpen: true },
  { icon: "pen-line", label: "Wrote in chat or Q&A", value: "28%", sub: "70 of 251 attendees", info: "Attendees who sent at least one message or question ÷ attendees.", status: "measured" },
  { icon: "message-circle", label: "Questions", value: "38", sub: "12 in Q&A · 26 in chat", info: "Q&A questions plus chat messages StormIQ labels as questions.", status: "estimated" },
  { icon: "timer", label: "Time in the room", value: "36", unit: "of 45 min", sub: "median attendee", info: "Median minutes watched among attendees.", status: "measured" },
  { icon: "zap", label: "Unprompted moments", value: "1", sub: "at 23:40" },
];

const CALLOUTS = [
  { value: "26 of 38", label: "came through the chat" },
  { value: "11", label: "still open" },
  { value: "14", label: "replay or slides requests" },
];

const EVIDENCE = [
  { time: "23:52", text: "Is there a cheaper plan for teams under ten people?", channel: "Chat" },
  { time: "31:08", text: "Does the starter plan include the Q&A export?", channel: "Q&A" },
  { time: "38:40", text: "Can we export registrants to our CRM?", channel: "Q&A" },
];

const SECTIONS = [
  { icon: "presentation", title: "The room", takeaway: "The chat tripled within a minute of the live demo starting.", chip: "1 surge", open: true },
  { icon: "message-circle", title: "The questions", takeaway: "Pricing for small teams drew the most questions.", chip: "11 open" },
  { icon: "file-text", title: "The record", takeaway: "One figure said aloud needs your check before you share it.", chip: "1 needs your check" },
  { icon: "users", title: "The audience", takeaway: "The partner mailing brought the most active attendees.", chip: "41% of writers" },
];

const ICON_MEANINGS = {
  "message-circle": "questions",
  "help-circle": "open question",
  shield: "needs your check",
  users: "audience",
  "user-check": "show-up",
  timer: "time in the room, wait gap",
  zap: "the moment, a surge",
  activity: "the Pulse",
  "share-2": "channels",
  calendar: "lead time, sessions",
  "pie-chart": "segments",
  "pen-line": "wrote in chat or Q&A",
  presentation: "the room",
  list: "chapters",
  table: "view as table",
  "file-text": "the record",
  target: "Next time",
  play: "evidence, timestamp",
  settings: "method",
  check: "checked, Measured",
  info: "Estimated, definitions",
  download: "download",
  copy: "copy",
  star: "Surprise Me!",
};

let robotsMeta = null;
let previousTitle = "";

onMounted(() => {
  previousTitle = document.title;
  document.title = "Design foundation · StormIQ";
  robotsMeta = document.createElement("meta");
  robotsMeta.name = "robots";
  robotsMeta.content = "noindex, nofollow";
  document.head.appendChild(robotsMeta);
  readTokens();
});

onBeforeUnmount(() => {
  robotsMeta?.remove();
  document.title = previousTitle;
});

watch(theme, async () => {
  await nextTick();
  readTokens();
});
</script>

<template>
  <div class="g-page">
    <header class="g-head">
      <p class="g-label">Design foundation</p>
      <h1 class="g-title">StormIQ v1 components</h1>
      <p class="g-intro">
        Every token and base component in the current theme ({{ theme }}{{ choice ? ", chosen" : ", following the system" }}).
        All content on this page is fictional sample data.
      </p>
      <div class="g-row">
        <ThemeToggle />
        <UiButton variant="secondary" small :disabled="!choice" @click="setChoice(null)">Follow the system</UiButton>
      </div>
    </header>

    <!-- Hero card -->
    <Card padding="hero">
      <CardHeader title="In short" :level="2">
        <template #right><StatusWord status="estimated" /></template>
      </CardHeader>
      <p class="g-summary">
        A fictional 45-minute product tour: 251 people came, 70 wrote, and
        <strong class="g-emphasis">11 questions are still open</strong>.
      </p>
      <div class="g-row">
        <Chip tone="accent" icon="message-circle">38 questions</Chip>
        <Chip tone="accent" icon="help-circle">11 still open</Chip>
        <Chip tone="accent" icon="zap">1 unprompted moment</Chip>
      </div>
    </Card>

    <!-- Stat cards -->
    <section class="g-section" aria-labelledby="g-stats">
      <h2 id="g-stats" class="g-section-title">Stat cards</h2>
      <div class="g-stats">
        <StatCard v-for="stat in STATS" :key="stat.label" v-bind="stat" />
      </div>
    </section>

    <!-- Finding card -->
    <section class="g-section" aria-labelledby="g-finding">
      <h2 id="g-finding" class="g-section-title">Finding card</h2>
      <Card as="article">
        <CardHeader icon="message-circle" title="Finding 1 · Questions" :level="3">
          <template #right>
            <StatusWord status="estimated" />
          </template>
        </CardHeader>
        <p class="g-headline">Most open questions were about pricing for small teams</p>
        <dl class="g-callouts">
          <div v-for="callout in CALLOUTS" :key="callout.label" class="g-callout">
            <dt class="g-callout-label">{{ callout.label }}</dt>
            <dd class="g-callout-value">{{ callout.value }}</dd>
          </div>
        </dl>
        <hr class="g-rule" />
        <Collapsible variant="link" icon="play" :title="`Evidence (${EVIDENCE.length})`" :open="true">
          <ul class="g-evidence">
            <li v-for="item in EVIDENCE" :key="item.time" class="g-evidence-row">
              <a href="#" class="g-time siq-fx" :aria-label="`Play at ${item.time}`" @click.prevent>
                <Icon name="play" :size="12" />{{ item.time }}
              </a>
              <span class="g-quote">{{ item.text }}</span>
              <Chip>{{ item.channel }}</Chip>
            </li>
          </ul>
        </Collapsible>
        <TipBar>Post the replay link in the chat before the Q&amp;A starts.</TipBar>
        <Collapsible variant="link" icon="settings" title="How we know">
          Q&amp;A questions come from the Q&amp;A tab. Chat questions are messages StormIQ labels as questions;
          replay and slides requests are counted apart. A question stays open until it has an answer.
        </Collapsible>
      </Card>
    </section>

    <!-- Collapsible cards -->
    <section class="g-section" aria-labelledby="g-collapsibles">
      <h2 id="g-collapsibles" class="g-section-title">Collapsible cards</h2>
      <div class="g-stack">
        <Collapsible
          v-for="section in SECTIONS"
          :key="section.title"
          :icon="section.icon"
          :title="section.title"
          :takeaway="section.takeaway"
          :chip="section.chip"
          :open="Boolean(section.open)"
          :level="3"
        >
          <ul class="g-icon-bullets">
            <li><Icon name="timer" :size="16" /> You waited 3 s after each prompt; the median reply took 15 s.</li>
            <li><Icon name="list" :size="16" /> The admin chapter drew twice the session's median reaction.</li>
            <li><Icon name="check" :size="16" /> No incident: audio and video stayed up.</li>
          </ul>
        </Collapsible>
        <Card tone="fair">
          <CardHeader icon="shield" icon-tone="fair" title="Before you share" :level="3">
            <template #right><StatusWord status="check" /></template>
          </CardHeader>
          <p class="g-text">Until you answer, this figure stays out of anything you share.</p>
        </Card>
      </div>
    </section>

    <!-- Buttons, chips, status -->
    <section class="g-section" aria-labelledby="g-controls">
      <h2 id="g-controls" class="g-section-title">Buttons, chips and status words</h2>
      <Card>
        <h3 class="g-subtitle">Buttons</h3>
        <div class="g-row">
          <UiButton variant="primary" icon="plus">Add session</UiButton>
          <UiButton variant="secondary" icon="download">Download PDF</UiButton>
          <UiButton variant="ghost" icon="copy">Copy for Slack or email</UiButton>
          <UiButton variant="icon" icon="more-horizontal" label="More actions" />
          <UiButton variant="icon" icon="refresh-cw" label="Refresh" />
          <UiButton variant="secondary" disabled>Disabled</UiButton>
          <UiButton variant="ghost" href="#" icon-right="external-link" @click.prevent>Open in Livestorm</UiButton>
        </div>
        <hr class="g-rule" />
        <h3 class="g-subtitle">Chips and badges</h3>
        <div class="g-row">
          <Chip>Public</Chip>
          <Chip icon="lock">Internal</Chip>
          <Chip tone="count">11 open</Chip>
          <Chip tone="accent" icon="users">568 attendees</Chip>
          <Chip tone="good" icon="check">Ready</Chip>
          <Chip tone="fair" icon="shield">1 to check</Chip>
          <Chip tone="low" icon="x">Failed</Chip>
        </div>
        <hr class="g-rule" />
        <h3 class="g-subtitle">Status words (icon + word, never colour alone)</h3>
        <div class="g-row">
          <StatusWord status="measured" />
          <StatusWord status="estimated" />
          <StatusWord status="check" />
        </div>
      </Card>
    </section>

    <!-- Icons -->
    <section class="g-section" aria-labelledby="g-icons">
      <h2 id="g-icons" class="g-section-title">Icons (Lucide)</h2>
      <Card>
        <ul class="g-icons">
          <li v-for="name in ICON_NAMES" :key="name" class="g-icon">
            <Icon :name="name" :size="20" />
            <span class="g-icon-name">{{ name }}</span>
            <span v-if="ICON_MEANINGS[name]" class="g-icon-meaning">{{ ICON_MEANINGS[name] }}</span>
          </li>
        </ul>
      </Card>
    </section>

    <!-- Type and mark -->
    <section class="g-section" aria-labelledby="g-type">
      <h2 id="g-type" class="g-section-title">Type (Sora), the lockup and the mark</h2>
      <Card>
        <div class="g-row g-marks">
          <span class="g-row"><StormIqMark :mark="false" /><ForLivestorm /></span>
          <StormIqMark />
          <StormIqMark :size="40" :wordmark="false" />
          <StormIqMark :size="20" :wordmark="false" />
        </div>
        <hr class="g-rule" />
        <dl class="g-type-list">
          <div v-for="sample in TYPE_SAMPLES" :key="sample.name" class="g-type-row">
            <dt class="g-type-name">{{ sample.name }}</dt>
            <dd :class="sample.className">{{ sample.text }}</dd>
          </div>
        </dl>
      </Card>
    </section>

    <!-- Tokens -->
    <section class="g-section" aria-labelledby="g-tokens">
      <h2 id="g-tokens" class="g-section-title">Colour tokens ({{ theme }})</h2>
      <div class="g-token-groups">
        <Card v-for="group in TOKEN_GROUPS" :key="group.title">
          <h3 class="g-subtitle">{{ group.title }}</h3>
          <ul class="g-tokens">
            <li v-for="token in group.tokens" :key="token" class="g-token">
              <span class="g-swatch" :style="{ background: `var(--siq-${token})` }" aria-hidden="true"></span>
              <span class="g-token-text">
                <code class="g-token-name">--siq-{{ token }}</code>
                <span class="g-token-value">{{ tokenValues[token] || "…" }}</span>
              </span>
            </li>
          </ul>
        </Card>
      </div>
    </section>
  </div>
</template>

<style scoped>
.g-page {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-6);
}

.g-head {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-3);
}

.g-label {
  margin: 0;
  font-size: var(--siq-text-caption);
  line-height: var(--siq-text-caption-lh);
  font-weight: var(--siq-weight-medium);
  letter-spacing: var(--siq-label-tracking);
  text-transform: uppercase;
  color: var(--siq-muted);
}

.g-title {
  margin: 0;
  font-size: var(--siq-text-display);
  line-height: var(--siq-text-display-lh);
  font-weight: var(--siq-weight-regular);
  letter-spacing: -0.015em;
  color: var(--siq-ink);
  text-wrap: balance;
}

.g-intro,
.g-text {
  margin: 0;
  max-width: 680px;
  font-size: var(--siq-text-lead);
  line-height: var(--siq-text-lead-lh);
  color: var(--siq-ink-2);
  text-wrap: pretty;
}

.g-text {
  margin-top: var(--siq-space-3);
}

.g-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--siq-space-2) var(--siq-space-3);
}

.g-section {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-3);
}

.g-section-title {
  margin: var(--siq-space-4) 0 0;
  font-size: var(--siq-text-section);
  line-height: var(--siq-text-section-lh);
  font-weight: var(--siq-weight-regular);
  color: var(--siq-ink);
}

.g-subtitle {
  margin: 0 0 var(--siq-space-3);
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  font-weight: var(--siq-weight-medium);
  color: var(--siq-ink);
}

.g-summary {
  margin: var(--siq-space-4) 0;
  max-width: 680px;
  font-size: var(--siq-text-body);
  line-height: var(--siq-text-body-lh);
  color: var(--siq-ink-2);
  text-wrap: pretty;
}

.g-emphasis {
  font-weight: var(--siq-weight-regular);
  color: var(--siq-accent);
}

.g-stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: var(--siq-space-4);
  align-items: start;
}

.g-headline {
  margin: var(--siq-space-4) 0 0;
  max-width: 22em;
  font-size: var(--siq-text-section);
  line-height: var(--siq-text-section-lh);
  color: var(--siq-ink);
  text-wrap: balance;
}

.g-callouts {
  margin: var(--siq-space-5) 0 0;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: var(--siq-space-4);
}

.g-callout {
  display: flex;
  flex-direction: column-reverse;
}

.g-callout-label {
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-muted);
}

.g-callout-value {
  margin: 0;
  font-size: var(--siq-text-stat);
  line-height: var(--siq-text-stat-lh);
  font-variant-numeric: tabular-nums;
  color: var(--siq-ink);
}

.g-rule {
  margin: var(--siq-space-5) 0;
  border: 0;
  height: 1px;
  background: var(--siq-hairline-row);
}

.g-evidence {
  margin: 0 0 var(--siq-space-4);
  padding: 0;
  list-style: none;
}

.g-evidence-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--siq-space-2) var(--siq-space-3);
  padding: var(--siq-space-2) 0;
  box-shadow: inset 0 -1px 0 var(--siq-hairline-row);
}

.g-time {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-height: var(--siq-target);
  border-radius: var(--siq-radius-inline);
  color: var(--siq-accent);
  font-size: var(--siq-text-small);
  font-variant-numeric: tabular-nums;
  text-decoration: none;
}

.g-quote {
  flex: 1 1 260px;
  min-width: 0;
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  color: var(--siq-ink);
}

.g-stack {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-4);
}

.g-icon-bullets {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-3);
}

.g-icon-bullets li {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  color: var(--siq-ink-2);
}

.g-icon-bullets :deep(.siq-icon) {
  margin-top: 2px;
  color: var(--siq-accent);
}

.g-icons {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: var(--siq-space-4);
}

.g-icon {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-1);
  color: var(--siq-ink);
}

.g-icon-name {
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
}

.g-icon-meaning {
  font-size: var(--siq-text-caption);
  line-height: var(--siq-text-caption-lh);
  color: var(--siq-muted);
}

.g-marks {
  color: var(--siq-ink);
  gap: var(--siq-space-6);
}

.g-type-list {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-4);
}

.g-type-row {
  display: grid;
  grid-template-columns: minmax(0, 180px) minmax(0, 1fr);
  gap: var(--siq-space-4);
  align-items: baseline;
}

.g-type-row {
  color: var(--siq-ink);
}

.g-type-row dd {
  margin: 0;
}

.g-type-name {
  font-size: var(--siq-text-caption);
  line-height: var(--siq-text-caption-lh);
  color: var(--siq-muted);
}

.g-type-display {
  font-size: var(--siq-text-display);
  line-height: var(--siq-text-display-lh);
  letter-spacing: -0.015em;
}

.g-type-headline {
  font-size: var(--siq-text-headline);
  line-height: var(--siq-text-headline-lh);
}

.g-type-section {
  font-size: var(--siq-text-section);
  line-height: var(--siq-text-section-lh);
}

.g-type-card {
  font-size: var(--siq-text-card-title);
  line-height: var(--siq-text-card-title-lh);
  font-weight: var(--siq-weight-medium);
}

.g-type-body {
  font-size: var(--siq-text-body);
  line-height: var(--siq-text-body-lh);
  color: var(--siq-ink-2);
}

.g-type-ui {
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
}

.g-type-caption {
  font-size: var(--siq-text-caption);
  line-height: var(--siq-text-caption-lh);
  color: var(--siq-muted);
}

.g-type-label {
  font-size: var(--siq-text-caption);
  line-height: var(--siq-text-caption-lh);
  font-weight: var(--siq-weight-medium);
  letter-spacing: var(--siq-label-tracking);
  text-transform: uppercase;
  color: var(--siq-muted);
}

.g-token-groups {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: var(--siq-space-4);
  align-items: start;
}

.g-tokens {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-3);
}

.g-token {
  display: flex;
  align-items: center;
  gap: var(--siq-space-3);
  min-width: 0;
}

.g-swatch {
  flex: none;
  width: 36px;
  height: 36px;
  border-radius: var(--siq-radius-tile);
  box-shadow: inset 0 0 0 1px var(--siq-hairline-head);
}

.g-token-text {
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.g-token-name {
  padding: 0;
  background: none;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-ink);
}

.g-token-value {
  font-size: var(--siq-text-caption);
  line-height: var(--siq-text-caption-lh);
  color: var(--siq-muted);
  overflow-wrap: anywhere;
}

@media (max-width: 640px) {
  .g-title {
    font-size: var(--siq-text-headline);
    line-height: var(--siq-text-headline-lh);
  }

  .g-type-row {
    grid-template-columns: minmax(0, 1fr);
    gap: var(--siq-space-1);
  }
}
</style>
