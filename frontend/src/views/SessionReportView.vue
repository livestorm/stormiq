<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { RouterLink } from "vue-router";
import Card from "../components/ui/Card.vue";
import CardHeader from "../components/ui/CardHeader.vue";
import Chip from "../components/ui/Chip.vue";
import Icon from "../components/ui/Icon.vue";
import AudienceSection from "../components/report/AudienceSection.vue";
import MethodSection from "../components/report/MethodSection.vue";
import PulseChart from "../components/report/PulseChart.vue";
import QuestionsSection from "../components/report/QuestionsSection.vue";
import RoomSection from "../components/report/RoomSection.vue";
import VitalsRow from "../components/report/VitalsRow.vue";
import { inShort } from "../report/vitals";
import SessionRidge from "../components/report/SessionRidge.vue";
import UiButton from "../components/ui/UiButton.vue";
import { analysisSummary, formatLength, formatSessionStart } from "../report/format";
import { createSessionReport } from "../report/useSessionReport";

// The v1 Report: the session page of the new design (v1 only, behind the
// switch). This first part holds the session header, the analysis the user
// starts, and the tabs; the Report's sections follow in their own PRs.
// It renders what `GET /api/sessions/{id}/record` sends and computes nothing.

const props = defineProps({
  sessionId: { type: String, required: true },
});

const report = createSessionReport();
const { state } = report;

watch(
  () => props.sessionId,
  (id) => report.open(id),
  { immediate: true },
);
onBeforeUnmount(() => report.close());

// Printing: a closed card prints only its header, so every closed section
// opens for the print and closes again after it (the cards and their "How
// we know" panels). The print stylesheet below hides what only works on
// screen.
const page = ref(null);
let openedForPrint = [];
function openForPrint() {
  openedForPrint = [...(page.value?.querySelectorAll("details:not([open])") || [])];
  for (const details of openedForPrint) details.open = true;
}
function closeAfterPrint() {
  for (const details of openedForPrint) details.open = false;
  openedForPrint = [];
}
onMounted(() => {
  window.addEventListener("beforeprint", openForPrint);
  window.addEventListener("afterprint", closeAfterPrint);
});
onBeforeUnmount(() => {
  window.removeEventListener("beforeprint", openForPrint);
  window.removeEventListener("afterprint", closeAfterPrint);
});

const body = computed(() => state.record);
const record = computed(() => body.value?.record || null);
const clock = computed(() => body.value?.meta?.clock || {});

const title = computed(() => body.value?.meta?.session?.title || "Untitled session");
const start = computed(() => formatSessionStart(clock.value.startedAtUnix, clock.value.timezone));
const length = computed(() => formatLength(clock.value.endS));
const teamExcluded = computed(() => {
  const excluded = record.value?.measures?.["m.show_up_rate"]?.excluded;
  return excluded && !excluded.internal && excluded.total > 0 ? excluded.total : 0;
});

const ROSTER_WORDS = { complete: "complete", partial: "partial" };
const HEALTH_WORDS = { ok: "good", degraded: "usable", poor: "poor", unavailable: "none yet" };
const sources = computed(() => {
  if (!record.value) return [];
  const roster = record.value.roster || {};
  const health = record.value.transcriptHealth?.state;
  return [
    `People: ${roster.peopleCount ?? 0} (${ROSTER_WORDS[roster.coverage] || "unknown"} list)`,
    `Transcript: ${HEALTH_WORDS[health] || "none yet"}`,
  ];
});

const summary = computed(() => analysisSummary(record.value?.chatLabels, state.analysis));
const short = computed(() => inShort(record.value?.measures || {}));

const base = computed(() => `/single-analysis/${encodeURIComponent(props.sessionId)}`);
const tabs = computed(() => [
  { key: "report", label: "Report", to: `${base.value}/report`, current: true },
  { key: "transcript", label: "Transcript", to: `${base.value}/transcript`, current: false },
  { key: "follow-up", label: "Follow-up", to: `${base.value}/content-repurposing`, current: false },
]);
</script>

<template>
  <div ref="page" class="r-page">
    <nav class="r-crumbs" aria-label="Breadcrumb">
      <RouterLink class="r-crumb siq-fx" to="/single-analysis">Sessions</RouterLink>
      <Icon name="chevron-right" :size="14" />
      <span aria-current="page">{{ body ? title : "Session" }}</span>
    </nav>

    <p v-if="state.loading && !body" class="r-status" role="status">Loading the session…</p>

    <Card v-else-if="state.error && !body" tone="fair">
      <CardHeader icon="info" title="This session could not be loaded" :level="1" />
      <p class="r-text">{{ state.error }}</p>
      <div class="r-row">
        <UiButton variant="secondary" icon="refresh-cw" @click="report.open(sessionId)">Try again</UiButton>
        <UiButton variant="ghost" to="/single-analysis">Back to sessions</UiButton>
      </div>
    </Card>

    <template v-else-if="body">
      <SessionRidge :minutes="record?.pulse?.minutes || []" />
      <header class="r-head">
        <h1 class="r-title">{{ title }}</h1>
        <p class="r-date">
          <span v-if="start.text">{{ start.text }}<span v-if="start.zone" class="r-muted"> ({{ start.zone }})</span></span>
          <span v-if="length"> · {{ length }}</span>
          <span v-if="teamExcluded" class="r-muted"> · excludes your team: {{ teamExcluded }}</span>
        </p>
        <p v-if="sources.length" class="r-sources">{{ sources.join(" · ") }}</p>
      </header>

      <Card class="r-analysis" :aria-busy="summary.busy ? 'true' : 'false'">
        <CardHeader icon="zap" :title="summary.title" :level="2">
          <template #right>
            <Chip v-if="summary.tone === 'good'" tone="good" icon="check">Up to date</Chip>
            <Chip v-else-if="summary.tone === 'fair'" tone="fair" icon="refresh-cw">Out of date</Chip>
            <Chip v-else-if="summary.tone === 'low'" tone="low" icon="info">Failed</Chip>
          </template>
        </CardHeader>
        <p class="r-text" :role="summary.tone === 'low' ? 'alert' : 'status'">{{ summary.detail }}</p>
        <div v-if="summary.action" class="r-row">
          <UiButton
            :variant="summary.busy ? 'secondary' : 'primary'"
            :icon="summary.busy ? 'refresh-cw' : 'zap'"
            :disabled="summary.busy"
            @click="report.startAnalysis()"
          >
            {{ summary.action }}
          </UiButton>
        </div>
      </Card>

      <nav class="r-tabs" aria-label="Session sections">
        <RouterLink
          v-for="tab in tabs"
          :key="tab.key"
          :to="tab.to"
          class="r-tab siq-fx"
          :class="{ 'is-current': tab.current }"
          :aria-current="tab.current ? 'page' : undefined"
        >
          {{ tab.label }}
        </RouterLink>
      </nav>

      <Card v-if="short.sentence || short.chips.length" padding="hero">
        <CardHeader icon="star" title="In short" :level="2" />
        <p v-if="short.sentence" class="r-lead">{{ short.sentence }}</p>
        <div v-if="short.chips.length" class="r-row">
          <Chip v-for="chip in short.chips" :key="chip.text" tone="accent" :icon="chip.icon">{{ chip.text }}</Chip>
        </div>
      </Card>

      <VitalsRow :measures="record.measures || {}" />

      <Card>
        <PulseChart :pulse="record.pulse" :alignment="clock.alignment || null" />
      </Card>

      <RoomSection :measures="record.measures || {}" :chat="state.chat" @load-chat="report.loadChat()" />

      <QuestionsSection :measures="record.measures || {}" :chat="state.chat" @load-chat="report.loadChat()" />

      <AudienceSection :measures="record.measures || {}" />

      <MethodSection :body="body" />
    </template>
  </div>
</template>

<style scoped>
.r-page {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-6);
}

.r-crumbs {
  display: flex;
  align-items: center;
  gap: var(--siq-space-2);
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-muted);
}

.r-crumb {
  color: var(--siq-accent);
  text-decoration: none;
  border-radius: var(--siq-radius-inline);
}

.r-crumb:hover {
  color: var(--siq-accent-hover);
}

.r-head {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-2);
}

.r-title {
  margin: 0;
  font-size: var(--siq-text-display);
  line-height: var(--siq-text-display-lh);
  font-weight: var(--siq-weight-regular);
  letter-spacing: -0.015em;
  color: var(--siq-ink);
  text-wrap: balance;
}

.r-date {
  margin: 0;
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  color: var(--siq-ink-2);
  font-variant-numeric: tabular-nums;
}

.r-muted,
.r-sources,
.r-status {
  color: var(--siq-muted);
}

.r-sources,
.r-status {
  margin: 0;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
}

.r-text {
  margin: 0;
  max-width: 680px;
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  color: var(--siq-ink-2);
}

.r-lead {
  margin: var(--siq-space-3) 0 0;
  max-width: 680px;
  font-size: var(--siq-text-lead);
  line-height: var(--siq-text-lead-lh);
  color: var(--siq-ink-2);
  text-wrap: pretty;
}

.r-row {
  display: flex;
  flex-wrap: wrap;
  gap: var(--siq-space-3);
  margin-top: var(--siq-space-4);
}

.r-analysis .r-text {
  margin-top: var(--siq-space-2);
}

.r-tabs {
  display: flex;
  gap: var(--siq-space-2);
  overflow-x: auto;
  box-shadow: inset 0 -1px 0 var(--siq-hairline);
}

.r-tab {
  display: inline-flex;
  align-items: center;
  min-height: var(--siq-target);
  padding: 0 var(--siq-space-3);
  font-size: var(--siq-text-nav);
  line-height: var(--siq-text-nav-lh);
  font-weight: var(--siq-weight-medium);
  color: var(--siq-ink-2);
  text-decoration: none;
  white-space: nowrap;
  transition-property: color, box-shadow;
  transition-duration: var(--siq-duration);
  transition-timing-function: var(--siq-ease);
}

.r-tab:hover {
  color: var(--siq-ink);
}

.r-tab.is-current {
  color: var(--siq-ink);
  box-shadow: inset 0 -2px 0 var(--siq-accent);
}

.r-tab.siq-fx:focus-visible {
  outline-offset: -3px;
}

@media (max-width: 640px) {
  .r-title {
    font-size: var(--siq-text-headline);
    line-height: var(--siq-text-headline-lh);
  }
}

@media (forced-colors: active) {
  .r-tab.is-current {
    border-bottom: 2px solid CanvasText;
  }
}

@media (prefers-reduced-motion: reduce) {
  .r-tab {
    transition: none;
  }
}

/* The printed Report: the computed page, on paper. Print is always light
   (tokens.css). Controls that only work on screen go: the tabs, the
   analysis action, every button (view as table, ⓘ, show all, a theme row's
   chevron); the cards stay whole across pages where they fit. */
@media print {
  .r-page {
    gap: var(--siq-space-4);
  }

  .r-tabs,
  .r-analysis,
  .r-page :deep(.siq-stat-head .siq-tile),
  .r-page :deep(.siq-btn),
  .r-page :deep(.siq-stat-info),
  .r-page :deep(.siq-summary-chevron),
  .r-page :deep(.siq-collapsible-link > .siq-summary > svg:last-child),
  .r-page :deep(.r-theme-chevron) {
    display: none !important;
  }

  .r-page :deep(.siq-card),
  .r-page :deep(.siq-collapsible-card),
  .r-page :deep(.siq-stat) {
    box-shadow: inset 0 0 0 1px var(--siq-hairline);
  }

  .r-page :deep(.siq-stat),
  .r-page :deep(.r-block),
  .r-page :deep(.r-tile),
  .r-page :deep(.r-answer),
  .r-page :deep(.r-pulse),
  .r-page :deep(tr) {
    break-inside: avoid;
  }

  .r-page :deep(.siq-summary) {
    break-after: avoid;
  }

  /* The vitals: five across on an A4 page, without their icon tiles, and
     the sub-line wraps instead of being cut. */
  .r-page :deep(.r-vitals) {
    grid-template-columns: repeat(5, minmax(0, 1fr));
    gap: var(--siq-space-3);
  }

  .r-page :deep(.siq-stat) {
    padding: var(--siq-space-4);
  }

  .r-page :deep(.siq-stat-sub) {
    white-space: normal;
  }

  /* A scrolling table prints whole. */
  .r-page :deep(.r-table-wrap) {
    overflow: visible;
  }
}
</style>
