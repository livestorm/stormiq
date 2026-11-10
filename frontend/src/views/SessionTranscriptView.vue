<script setup>
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { RouterLink } from "vue-router";
import SessionTabs from "../components/report/SessionTabs.vue";
import TranscriptLine from "../components/report/TranscriptLine.vue";
import Card from "../components/ui/Card.vue";
import CardHeader from "../components/ui/CardHeader.vue";
import Icon from "../components/ui/Icon.vue";
import UiButton from "../components/ui/UiButton.vue";
import { api } from "../api";
import { formatLength, formatSessionStart } from "../report/format";
import { createSessionReport } from "../report/useSessionReport";
import { VIEWS, speakersOf, subtitles, transcriptLines } from "../transcript/lines";
import { createTranscriptView } from "../transcript/useTranscript";

// The Transcript tab: what was said and what the room wrote, on one clock
// when the server could place the speech on it (`clock.interleave`), apart
// otherwise. Data: `GET /record/transcript` (`transcript/useTranscript.js`);
// the header reads the record, as the Report does. Wording and order:
// `transcript/lines.js`. Chat and questions are attendees' text: rendered as
// text, never as HTML.

const props = defineProps({
  sessionId: { type: String, required: true },
});

const transcript = createTranscriptView();
const report = createSessionReport();
const { state } = transcript;

watch(
  () => props.sessionId,
  (id) => {
    transcript.open(id);
    report.open(id);
  },
  { immediate: true },
);
onBeforeUnmount(() => {
  transcript.close();
  report.close();
});

// ── The header ──
const record = computed(() => report.state.record);
const clock = computed(() => record.value?.meta?.clock || {});
const title = computed(() => record.value?.meta?.session?.title || "");
const start = computed(() => formatSessionStart(clock.value.startedAtUnix, clock.value.timezone));
const length = computed(() => formatLength(clock.value.endS));

// ── Filters ──
const query = ref("");
const view = ref("all");
const speaker = ref(undefined);

const body = computed(() => state.body);
const speakers = computed(() => speakersOf(body.value));
const hasSpeech = computed(() => Boolean(body.value?.speech?.length));
const shown = computed(() => transcriptLines(body.value, { view: view.value, query: query.value, speaker: speaker.value }));
const nothingMatches = computed(
  () => Boolean(body.value) && !shown.value.lines.length && !shown.value.speech.length && !shown.value.room.length,
);
const filtered = computed(() => Boolean(query.value.trim()) || view.value !== "all" || speaker.value !== undefined);

function pickSpeaker(key) {
  speaker.value = speaker.value === key ? undefined : key;
  if (speaker.value !== undefined && view.value === "room") view.value = "all";
}

function clearFilters() {
  query.value = "";
  view.value = "all";
  speaker.value = undefined;
}

const countLabel = computed(() => {
  const { speech, room } = shown.value.counts;
  const parts = [];
  if (view.value !== "room" && hasSpeech.value) parts.push(`${speech} ${speech === 1 ? "line" : "lines"} of speech`);
  if (view.value !== "speech" && speaker.value === undefined) parts.push(`${room} from the room`);
  return parts.join(" · ");
});

// ── Naming the speakers ──
const naming = ref(false);
const draft = ref({});

function startNaming() {
  draft.value = Object.fromEntries(speakers.value.filter((s) => s.key !== null).map((s) => [s.key, s.named ? s.name : ""]));
  naming.value = true;
}

async function saveNames() {
  await transcript.saveNames({ ...draft.value });
  if (!state.saveError) naming.value = false;
}

// ── Downloads ──
function downloadSubtitles() {
  const blob = new Blob([subtitles(body.value)], { type: "application/x-subrip;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${(title.value || "transcript").replace(/[^\w\- ]+/g, "").trim() || "transcript"}.srt`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
const rawUrl = computed(() => api.getTranscriptJsonUrl(props.sessionId));

// ── Without a transcript ──
const job = computed(() => state.job);
const jobBusy = computed(() => ["pending", "running", "starting", "checking"].includes(job.value.status));
</script>

<template>
  <div class="t-page">
    <nav class="t-crumbs" aria-label="Breadcrumb">
      <RouterLink class="t-crumb siq-fx" to="/sessions">Sessions</RouterLink>
      <Icon name="chevron-right" :size="14" />
      <RouterLink class="t-crumb siq-fx" :to="`/sessions/${encodeURIComponent(sessionId)}/report`">
        {{ title || "Session" }}
      </RouterLink>
      <Icon name="chevron-right" :size="14" />
      <span aria-current="page">Transcript</span>
    </nav>

    <header class="t-head">
      <h1 class="t-title">{{ title || "Transcript" }}</h1>
      <p v-if="start.text || length" class="t-date">
        <span v-if="start.text">{{ start.text }}<span v-if="start.zone" class="t-muted"> ({{ start.zone }})</span></span>
        <span v-if="length"> · {{ length }}</span>
      </p>
    </header>

    <SessionTabs :session-id="sessionId" current="transcript" />

    <p v-if="state.loading && !body" class="t-muted" role="status">Loading the transcript…</p>

    <Card v-else-if="state.error && !body" tone="fair">
      <CardHeader icon="info" title="The transcript could not be loaded" :level="2" />
      <p class="t-text">{{ state.error }}</p>
      <div class="t-row">
        <UiButton variant="secondary" icon="refresh-cw" @click="transcript.reload()">Try again</UiButton>
      </div>
    </Card>

    <template v-else-if="body">
      <!-- No speech yet: the transcript job's state, and the one paid action, on a click. -->
      <Card v-if="!hasSpeech" :tone="job.status === 'error' ? 'fair' : 'default'" :aria-busy="jobBusy ? 'true' : 'false'">
        <template v-if="job.status === 'pending' || job.status === 'running' || job.status === 'starting'">
          <CardHeader icon="refresh-cw" title="The transcript is being made" :level="2" />
          <p class="t-text" role="status">
            {{ job.progress != null ? `${job.progress}% done.` : "It has started." }} A long recording takes a few minutes; this
            page updates by itself. The chat and questions are below meanwhile.
          </p>
        </template>
        <template v-else-if="job.status === 'error'">
          <CardHeader icon="info" title="The transcript could not be made" :level="2" />
          <p class="t-text" role="alert">{{ job.error }}</p>
          <div class="t-row">
            <UiButton variant="primary" icon="refresh-cw" @click="transcript.makeTranscript({ force: true })">Try again</UiButton>
          </div>
        </template>
        <template v-else-if="job.status === 'checking'">
          <p class="t-muted" role="status">Checking the transcript…</p>
        </template>
        <template v-else>
          <CardHeader icon="file-text" title="No transcript yet" :level="2" />
          <p class="t-text">
            StormIQ transcribes the session's recording, then shows what was said next to the chat. It runs only when you ask.
          </p>
          <div class="t-row">
            <UiButton variant="primary" icon="file-text" @click="transcript.makeTranscript()">Make the transcript</UiButton>
          </div>
        </template>
      </Card>

      <Card padding="flush" class="t-tools">
        <div class="t-toolbar" role="search">
          <label class="t-field t-search">
            <span class="siq-sr-only">Search the transcript</span>
            <Icon name="search" :size="16" class="t-field-icon" />
            <input v-model="query" type="search" class="t-input" placeholder="Search the transcript" />
          </label>
          <div v-if="hasSpeech" class="t-segmented" role="group" aria-label="Show">
            <button
              v-for="option in VIEWS"
              :key="option.value"
              type="button"
              class="t-seg siq-fx"
              :class="{ 'is-on': view === option.value }"
              :aria-pressed="view === option.value ? 'true' : 'false'"
              @click="view = option.value"
            >
              {{ option.label }}
            </button>
          </div>
        </div>

        <div v-if="speakers.length" class="t-speakers">
          <span class="t-muted">Speakers</span>
          <button
            v-for="(item, index) in speakers"
            :key="item.key ?? `none-${index}`"
            type="button"
            class="t-chip siq-fx"
            :class="{ 'is-on': speaker === item.key }"
            :aria-pressed="speaker === item.key ? 'true' : 'false'"
            @click="pickSpeaker(item.key)"
          >
            {{ item.name }}
          </button>
          <UiButton variant="ghost" small icon="pen-line" @click="naming ? (naming = false) : startNaming()">
            {{ naming ? "Close" : speakers.some((s) => s.named) ? "Edit names" : "Name speakers" }}
          </UiButton>
          <span class="t-spacer" />
          <UiButton variant="secondary" small icon="download" @click="downloadSubtitles">Subtitles (SRT)</UiButton>
          <UiButton variant="ghost" small icon="download" :href="rawUrl">Raw (JSON)</UiButton>
        </div>

        <form v-if="naming" class="t-naming" @submit.prevent="saveNames">
          <p class="t-muted">Names show in this transcript, its subtitles and the analyses made after you save them.</p>
          <div class="t-naming-grid">
            <label v-for="(item, index) in speakers.filter((s) => s.key !== null)" :key="item.key" class="t-naming-row">
              <span class="t-label">Speaker {{ index + 1 }}</span>
              <input v-model="draft[item.key]" type="text" class="t-input" :placeholder="`Speaker ${index + 1}`" maxlength="80" />
            </label>
          </div>
          <p v-if="state.saveError" class="t-problem" role="alert">{{ state.saveError }}</p>
          <div class="t-row">
            <UiButton type="submit" variant="primary" icon="check" :disabled="state.saving">
              {{ state.saving ? "Saving…" : "Save names" }}
            </UiButton>
            <UiButton variant="ghost" @click="naming = false">Cancel</UiButton>
          </div>
        </form>

        <p class="t-status">
          <span role="status">{{ countLabel }}</span>
          <UiButton v-if="filtered" variant="ghost" small icon="x" @click="clearFilters">Clear</UiButton>
        </p>
      </Card>

      <p v-if="hasSpeech && !shown.interleaved && !nothingMatches" class="t-note">
        <Icon name="info" :size="16" />
        <span>
          Speech and the chat are shown apart: the recording couldn't be matched to the session's clock to the second. Speech
          times are the recording's.
        </span>
      </p>

      <p v-if="nothingMatches" class="t-muted">Nothing matches.</p>

      <Card v-if="shown.interleaved && shown.lines.length" padding="flush">
        <h2 class="siq-sr-only">The session, line by line</h2>
        <ol class="t-lines">
          <TranscriptLine v-for="line in shown.lines" :key="line.id" :line="line" />
        </ol>
      </Card>

      <template v-else>
        <Card v-if="shown.speech.length" padding="flush">
          <div class="t-section-head"><CardHeader icon="presentation" title="What was said" :level="2" /></div>
          <ol class="t-lines">
            <TranscriptLine v-for="line in shown.speech" :key="line.id" :line="line" />
          </ol>
        </Card>
        <Card v-if="shown.room.length" padding="flush">
          <div class="t-section-head"><CardHeader icon="message-circle" title="Chat and questions" :level="2" /></div>
          <ol class="t-lines">
            <TranscriptLine v-for="line in shown.room" :key="line.id" :line="line" flat />
          </ol>
        </Card>
      </template>
    </template>
  </div>
</template>

<style scoped>
.t-page {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-6);
}

.t-crumbs {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--siq-space-2);
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-muted);
}

.t-crumb {
  color: var(--siq-accent);
  text-decoration: none;
  border-radius: var(--siq-radius-inline);
}

.t-crumb:hover {
  color: var(--siq-accent-hover);
}

.t-head {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-2);
}

.t-title {
  margin: 0;
  font-size: var(--siq-text-display);
  line-height: var(--siq-text-display-lh);
  font-weight: var(--siq-weight-regular);
  letter-spacing: -0.015em;
  color: var(--siq-ink);
  overflow-wrap: anywhere;
}

.t-date,
.t-text {
  margin: 0;
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  color: var(--siq-ink-2);
}

.t-muted,
.t-status {
  margin: 0;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-muted);
}

.t-row {
  display: flex;
  flex-wrap: wrap;
  gap: var(--siq-space-2);
  margin-top: var(--siq-space-4);
}

/* ── Tools ── */
.t-toolbar,
.t-speakers,
.t-naming,
.t-status {
  padding: var(--siq-space-3) var(--siq-space-6);
}

.t-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--siq-space-3);
  padding-top: var(--siq-space-4);
}

.t-speakers {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--siq-space-2);
  box-shadow: inset 0 1px 0 var(--siq-hairline-row);
}

.t-spacer {
  flex: 1 1 auto;
}

.t-status {
  display: flex;
  align-items: center;
  gap: var(--siq-space-2);
  min-height: 48px;
  box-shadow: inset 0 1px 0 var(--siq-hairline-row);
}

.t-field {
  position: relative;
  display: inline-flex;
  align-items: center;
}

.t-search {
  flex: 1 1 18rem;
}

/* v1 controls set their own font, colours, border and height: the classic
   element styles still load under v1. */
.t-input {
  width: 100%;
  height: var(--siq-target);
  margin: 0;
  padding: 0 var(--siq-space-3);
  border: 1px solid var(--siq-border);
  border-radius: var(--siq-radius-control);
  background: var(--siq-input);
  color: var(--siq-ink);
  font: inherit;
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  box-shadow: none;
  transition-property: border-color;
  transition-duration: var(--siq-duration);
  transition-timing-function: var(--siq-ease);
}

.t-input:hover {
  border-color: var(--siq-border-hover);
}

.t-input:focus-visible {
  outline: 2px solid var(--siq-focus);
  outline-offset: 1px;
}

.t-input::placeholder {
  color: var(--siq-muted);
}

.t-search .t-input {
  padding-left: 36px;
}

.t-field-icon {
  position: absolute;
  left: var(--siq-space-3);
  color: var(--siq-muted);
  pointer-events: none;
}

.t-segmented {
  display: inline-flex;
  padding: 2px;
  border: 1px solid var(--siq-border);
  border-radius: var(--siq-radius-control);
  background: var(--siq-input);
}

.t-seg,
.t-chip {
  margin: 0;
  border: 0;
  font: inherit;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  font-weight: var(--siq-weight-medium);
  color: var(--siq-ink-2);
  background: transparent;
  cursor: pointer;
  transition-property: background-color, color;
  transition-duration: var(--siq-duration);
  transition-timing-function: var(--siq-ease);
}

.t-seg {
  min-height: calc(var(--siq-target) - 6px);
  padding: 0 var(--siq-space-3);
  border-radius: calc(var(--siq-radius-control) - 2px);
}

.t-seg.is-on {
  background: var(--siq-selected-bg);
  color: var(--siq-selected-ink);
}

.t-chip {
  min-height: 32px;
  padding: 0 var(--siq-space-3);
  border: 1px solid var(--siq-border);
  border-radius: 999px;
}

.t-chip:hover,
.t-seg:not(.is-on):hover {
  color: var(--siq-ink);
}

.t-chip.is-on {
  border-color: transparent;
  background: var(--siq-selected-bg);
  color: var(--siq-selected-ink);
}

.t-naming {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-3);
  box-shadow: inset 0 1px 0 var(--siq-hairline-row);
}

.t-naming .t-row {
  margin-top: 0;
}

.t-naming-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(14rem, 1fr));
  gap: var(--siq-space-3);
}

.t-naming-row {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-1);
}

.t-label {
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  font-weight: var(--siq-weight-medium);
  color: var(--siq-ink-2);
}

.t-problem {
  margin: 0;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-ink);
}

.t-note {
  display: flex;
  align-items: flex-start;
  gap: var(--siq-space-2);
  margin: 0;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-ink-2);
}

.t-note svg {
  flex: none;
  margin-top: 2px;
  color: var(--siq-muted);
}

.t-section-head {
  padding: var(--siq-space-6) var(--siq-space-6) var(--siq-space-3);
}

.t-lines {
  margin: 0;
  padding: var(--siq-space-2) 0;
  list-style: none;
}

@media (max-width: 720px) {
  .t-title {
    font-size: var(--siq-text-headline);
    line-height: var(--siq-text-headline-lh);
  }
  .t-toolbar,
  .t-speakers,
  .t-naming,
  .t-status {
    padding-left: var(--siq-space-4);
    padding-right: var(--siq-space-4);
  }
  .t-segmented {
    width: 100%;
  }
  .t-seg {
    flex: 1 1 0;
    padding: 0 var(--siq-space-2);
  }
  .t-section-head {
    padding: var(--siq-space-4) var(--siq-space-4) var(--siq-space-2);
  }
}

@media (forced-colors: active) {
  .t-seg.is-on,
  .t-chip.is-on {
    outline: 2px solid CanvasText;
  }
}

@media (prefers-reduced-motion: reduce) {
  .t-input,
  .t-seg,
  .t-chip {
    transition: none;
  }
}

@media print {
  .t-tools,
  .t-crumbs {
    display: none !important;
  }
}
</style>
