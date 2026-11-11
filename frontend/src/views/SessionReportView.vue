<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import Card from "../components/ui/Card.vue";
import CardHeader from "../components/ui/CardHeader.vue";
import Chip from "../components/ui/Chip.vue";
import Icon from "../components/ui/Icon.vue";
import ProgressSteps from "../components/ui/ProgressSteps.vue";
import AudienceSection from "../components/report/AudienceSection.vue";
import MethodSection from "../components/report/MethodSection.vue";
import PulseChart from "../components/report/PulseChart.vue";
import QuestionsSection from "../components/report/QuestionsSection.vue";
import RoomSection from "../components/report/RoomSection.vue";
import VitalsRow from "../components/report/VitalsRow.vue";
import { inShort } from "../report/vitals";
import SessionRidge from "../components/report/SessionRidge.vue";
import SessionTabs from "../components/report/SessionTabs.vue";
import UiButton from "../components/ui/UiButton.vue";
import { analysisSummary, elapsedLabel, formatLength, formatSessionStart, preparationView } from "../report/format";
import { createSessionReport } from "../report/useSessionReport";
import { useWorkspace } from "../store/workspace";
import { createChapters } from "../followup/useChapters";
import { createFindings, createMoments, createTranscriptLines, createVoices } from "../report/useFindings";
import { momentPins } from "../report/moments";
import { shortPoints } from "../report/summary";
import KeyMoments from "../components/report/KeyMoments.vue";
import { createQuestionDesk } from "../followup/useQuestionDesk";
import { pulseMarks } from "../report/findings";
import FindingsSection from "../components/report/FindingsSection.vue";

// The Report: a session's page. It holds the session header, the analysis
// the user starts, the tabs, and the Report's sections.
// It renders what `GET /api/sessions/{id}/record` sends and computes nothing.

const props = defineProps({
  sessionId: { type: String, required: true },
});

const report = createSessionReport();
const { state } = report;
// "Add" opens this page as it starts fetching the session (the store's
// `loadSessionById`): when that fetch fails, the page stops waiting and says why.
const workspaceStore = useWorkspace();
watch(
  () => [workspaceStore.state.sessionId, workspaceStore.state.loading.sessionFetch, workspaceStore.state.error],
  ([id, fetching, error]) => {
    if (id === props.sessionId && !fetching && error) report.fetchFailed(error);
  },
);
// The chapters (`GET /record/chapters`): the cover chart draws them, and
// their state says whether the session itself has been read.
const chapters = createChapters();
// The findings (`GET /record/findings`): "What stood out".
const findings = createFindings();
// "The questions" (Follow-up's desk, `GET /record/questions`, read-only here)
// and "Who spoke up" (`GET /record/voices`).
const desk = createQuestionDesk();
const voices = createVoices();
// The Pulse's key moments, told (`GET /record/moments`).
const moments = createMoments();
// The cover chart's replay: what was said and written each minute (`GET /record/transcript`).
const lines = createTranscriptLines();

const route = useRoute();
const router = useRouter();

watch(
  () => props.sessionId,
  (id) => {
    // `?prepare=1`: just added by "Add"; the page shows the preparation.
    report.open(id, { prepare: route.query.prepare === "1" });
    chapters.open(id);
    findings.open(id);
    desk.open(id);
    voices.open(id);
    moments.open(id);
    lines.open(id);
  },
  { immediate: true },
);
onBeforeUnmount(() => {
  report.close();
  chapters.close();
  findings.close();
  desk.close();
  voices.close();
  moments.close();
  lines.close();
});

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

const hasTranscript = computed(() => {
  const health = record.value?.transcriptHealth?.state;
  return Boolean(health) && health !== "unavailable";
});
// The chat has messages and none of their authors resolved (the measure says
// `unresolved`): typically a chat saved before messages carried their authors.
const authorsUnknown = computed(() => record.value?.measures?.["m.author_resolution"]?.state === "unresolved");
const summary = computed(() =>
  analysisSummary(record.value?.chatLabels, state.analysis, {
    transcript: hasTranscript.value,
    reading: chapters.state.body?.state || "",
  }),
);
// A run that ends brings new chapters, or none: read them again.
watch(
  () => summary.value.busy,
  (busy, wasBusy) => {
    if (wasBusy && !busy) {
      chapters.reload();
      findings.reload();
      desk.reload();
      voices.reload();
      moments.reload();
      lines.reload();
    }
  },
);

// The session fetched again from Livestorm (a new `updatedAt` on the same
// session): what is read beside the record is read again too.
watch(
  () => [state.record?.sessionId, state.record?.updatedAt],
  ([id, updated], [wasId, wasUpdated] = []) => {
    if (!id || id !== wasId || !wasUpdated || updated === wasUpdated) return;
    findings.reload();
    desk.reload();
    voices.reload();
    moments.reload();
    lines.reload();
  },
);

// The run's elapsed time, ticking once a second while it is in flight.
const now = ref(Date.now());
let ticker = null;
watch(
  () => summary.value.busy,
  (busy) => {
    clearInterval(ticker);
    ticker = busy ? setInterval(() => (now.value = Date.now()), 1000) : null;
  },
  { immediate: true },
);
onBeforeUnmount(() => clearInterval(ticker));
const elapsed = computed(() => (summary.value.busy ? elapsedLabel(summary.value.startedAtMs, now.value) : ""));

// Just added: the transcript and the first analysis are being made. The
// page shows their progress in place of the Report until they are done,
// then drops `?prepare=1` so a reload opens the Report at once.
const preparation = computed(() => preparationView(state.transcript, state.analysis, { fetching: state.fetching }));
const prepStarted = Date.now();
const prepElapsed = computed(() => (state.preparing ? elapsedLabel(prepStarted, now.value) : ""));
let prepTicker = null;
// Immediate: `report.open` sets `preparing` before this watch exists, and
// without it the clock never started ("Running for 0:00" forever).
watch(
  () => state.preparing,
  (preparing, was) => {
    clearInterval(prepTicker);
    prepTicker = preparing ? setInterval(() => (now.value = Date.now()), 1000) : null;
    if (was && !preparing && route.query.prepare) {
      router.replace({ query: { ...route.query, prepare: undefined } });
      chapters.reload();
      findings.reload();
      desk.reload();
      voices.reload();
      moments.reload();
      lines.reload();
    }
  },
  { immediate: true },
);
onBeforeUnmount(() => clearInterval(prepTicker));
// The analysis card: only when there is something to do, a run in flight or
// a failure; an analysed session shows none.
const showAnalysis = computed(() => Boolean(summary.value.action) || summary.value.busy || summary.value.tone === "low");
const short = computed(() => inShort(record.value?.measures || {}));
// What the rest of the page found, said first: the strongest findings, the
// moment the room reacted most and the questions still open.
const points = computed(() =>
  shortPoints({ findings: findings.state.body, moments: moments.state.body, desk: desk.state.body }),
);

</script>

<template>
  <div ref="page" class="r-page">
    <SessionTabs :session-id="sessionId" current="report" :title="body ? title : ''" />

    <p v-if="state.loading && !body" class="r-status" role="status">Loading the session…</p>

    <Card v-else-if="state.error && !body" tone="fair">
      <CardHeader icon="info" title="This session could not be loaded" :level="1" />
      <p class="r-text">{{ state.error }}</p>
      <div class="r-row">
        <UiButton variant="secondary" icon="refresh-cw" @click="report.open(sessionId)">Try again</UiButton>
        <UiButton variant="ghost" to="/sessions">Back to sessions</UiButton>
      </div>
    </Card>

    <template v-else-if="body">
      <header class="r-head">
        <h1 class="r-title">{{ title }}</h1>
        <p class="r-date">
          <span v-if="start.text">{{ start.text }}<span v-if="start.zone" class="r-muted"> ({{ start.zone }})</span></span>
          <span v-if="length"> · {{ length }}</span>
          <span v-if="teamExcluded" class="r-muted"> · excludes your team: {{ teamExcluded }}</span>
        </p>
        <p v-if="sources.length" class="r-sources">{{ sources.join(" · ") }}</p>
        <div v-if="authorsUnknown || state.refetch.status === 'running'" class="r-hint">
          <Icon name="info" :size="16" />
          <span>
            {{
              state.refetch.status === "running"
                ? "Fetching the session again from Livestorm…"
                : "The chat's authors couldn't be matched to people: this chat was saved before StormIQ read who wrote each message."
            }}
          </span>
          <UiButton
            v-if="state.refetch.status !== 'running'"
            variant="secondary"
            small
            icon="refresh-cw"
            @click="report.refetch()"
          >
            Fetch the chat again (free)
          </UiButton>
        </div>
        <p v-if="state.refetch.status === 'error'" class="r-hint" role="alert">{{ state.refetch.error }}</p>
      </header>

      <SessionRidge
        v-if="!state.preparing"
        :minutes="record?.pulse?.minutes || []"
        :chapters="chapters.state.body"
        :transcript="lines.state.body"
        :moments="moments.state.body"
        explain
      />

      <Card v-if="state.preparing" class="r-prepare" aria-busy="true">
        <CardHeader icon="zap" :title="preparation.title" :level="2" />
        <p class="r-text" role="status">{{ preparation.detail }}</p>
        <ProgressSteps :steps="preparation.steps" :percent="preparation.percent" label="Preparing the report" :elapsed="prepElapsed" />
      </Card>

      <Card v-else-if="showAnalysis" class="r-analysis" :aria-busy="summary.busy ? 'true' : 'false'">
        <CardHeader icon="zap" :title="summary.title" :level="2">
          <template #right>
            <Chip v-if="summary.tone === 'good'" tone="good" icon="check">Up to date</Chip>
            <Chip v-else-if="summary.tone === 'fair'" tone="fair" icon="refresh-cw">Out of date</Chip>
            <Chip v-else-if="summary.tone === 'low'" tone="low" icon="info">Failed</Chip>
          </template>
        </CardHeader>
        <p class="r-text" :role="summary.tone === 'low' ? 'alert' : 'status'">{{ summary.detail }}</p>
        <template v-if="summary.busy">
          <ProgressSteps :steps="summary.steps" :percent="summary.percent" label="Analysis progress" :elapsed="elapsed" />
        </template>
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

      <template v-if="!state.preparing">
      <Card v-if="short.sentence || short.chips.length || points.length" padding="hero">
        <CardHeader icon="star" title="In short" :level="2" />
        <p v-if="short.sentence" class="r-lead">{{ short.sentence }}</p>
        <ul v-if="points.length" class="r-points">
          <li v-for="point in points" :key="point.text" class="r-point">
            <Icon :name="point.icon" :size="18" />
            <span>{{ point.text }}</span>
          </li>
        </ul>
        <div v-else-if="short.chips.length" class="r-row">
          <Chip v-for="chip in short.chips" :key="chip.text" tone="accent" :icon="chip.icon">{{ chip.text }}</Chip>
        </div>
      </Card>

      <VitalsRow :measures="record.measures || {}" />

      <FindingsSection :body="findings.state.body" />

      <Card>
        <PulseChart
          :pulse="record.pulse"
          :alignment="clock.alignment || null"
          :marks="pulseMarks(findings.state.body, record.pulse?.minutes?.length || 0, moments.state.body ? momentPins(moments.state.body) : null)"
        />
        <KeyMoments :moments="moments.state" @retry="moments.reload()" />
      </Card>

      <RoomSection :measures="record.measures || {}" :chat="state.chat" @load-chat="report.loadChat()" />

      <QuestionsSection :desk="desk.state" :measures="record.measures || {}" :session-id="sessionId" @retry="desk.reload()" />

      <AudienceSection :measures="record.measures || {}" :voices="voices.state" @retry-voices="voices.reload()" />

      <MethodSection :body="body" :findings="findings.state.body" />
      </template>
    </template>
  </div>
</template>

<style scoped>
.r-page {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-6);
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

.r-sources {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--siq-space-1) var(--siq-space-3);
}

.r-hint {
  display: flex;
  flex-wrap: wrap;
  gap: var(--siq-space-2);
  align-items: center;
  max-width: 72ch;
  margin: var(--siq-space-2) 0 0;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-ink-2);
}

.r-hint :deep(svg) {
  flex: none;
  margin-top: 1px;
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

.r-points {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-2);
  max-width: 760px;
  margin: var(--siq-space-4) 0 0;
  padding: 0;
  list-style: none;
}

.r-point {
  display: flex;
  gap: var(--siq-space-3);
  align-items: flex-start;
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  color: var(--siq-ink);
}

.r-point :deep(svg) {
  flex: none;
  margin-top: 3px;
  color: var(--siq-accent);
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

.r-small {
  margin: var(--siq-space-2) 0 0;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
}

@media (max-width: 640px) {
  .r-title {
    font-size: var(--siq-text-headline);
    line-height: var(--siq-text-headline-lh);
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
