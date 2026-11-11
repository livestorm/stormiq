<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import SessionTabs from "../components/report/SessionTabs.vue";
import SpeakingStats from "../components/report/SpeakingStats.vue";
import TranscriptLine from "../components/report/TranscriptLine.vue";
import Card from "../components/ui/Card.vue";
import CardHeader from "../components/ui/CardHeader.vue";
import Icon from "../components/ui/Icon.vue";
import ProgressSteps from "../components/ui/ProgressSteps.vue";
import UiButton from "../components/ui/UiButton.vue";
import { api } from "../api";
import { elapsedLabel, formatLength, formatSessionStart } from "../report/format";
import { transcriptSteps } from "../report/progress";
import { createSessionReport } from "../report/useSessionReport";
import { ROOM_KINDS, VIEWS, authorsOf, speakersOf, subtitles, transcriptLines } from "../transcript/lines";
import { createTranscriptView } from "../transcript/useTranscript";
import { refreshOf } from "../report/sessionRefresh";

// The Transcript tab: what was said and what the room wrote, on one clock
// when the server could place the speech on it (`clock.interleave`), apart
// otherwise. Data: `GET /record/transcript` (`transcript/useTranscript.js`);
// the header reads the record, as the Report does. Wording and order:
// `transcript/lines.js`. Chat and questions are attendees' text: rendered as
// text, never as HTML.
//
// Layout: the tools (search, view, speakers, author) sit right above the
// lines and stay pinned under the session's tabs. When speech and the room
// are apart, the chat is a column beside the speech on a wide screen, with
// its own scroll, search and author filter, so it is never far down the page.

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

// "Fetch again from Livestorm" (here or on another tab): read the lines
// again once it lands. Offered on the chat when it has nothing to show, or
// when its authors couldn't be matched to people.
watch(
  () => refreshOf(props.sessionId).version,
  (version, before) => {
    if (version > before) transcript.reload();
  },
);
const refetchStatus = computed(() => report.state.refetch.status);
const authorsUnknown = computed(() => record.value?.measures?.["m.author_resolution"]?.state === "unresolved");
const chatNeedsFetch = computed(() => Boolean(state.body) && (!(state.body.room || []).length || authorsUnknown.value));

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
const author = ref(undefined); // one author of the room, or undefined
const roomQuery = ref(""); // the chat column's own search
const roomKind = ref("all"); // the room's chat, its questions, or both (ROOM_KINDS)

const body = computed(() => state.body);
const speakers = computed(() => speakersOf(body.value));
const hasSpeech = computed(() => Boolean(body.value?.speech?.length));
const authors = computed(() => authorsOf(body.value));
const hasRoom = computed(() => Boolean(body.value?.room?.length));
// Questions asked in the chat are found by the session analysis: until it
// has read this chat, "Questions" shows the Q&A's only, and says so.
const chatQuestionsUnknown = computed(
  () => roomKind.value === "questions" && (body.value?.room || []).some((line) => line.kind === "chat") && !(body.value?.room || []).some((line) => line.asks),
);
const shown = computed(() =>
  transcriptLines(body.value, {
    view: view.value,
    query: query.value,
    speaker: speaker.value,
    author: author.value,
    roomQuery: roomQuery.value,
    roomKind: roomKind.value,
  }),
);
const nothingMatches = computed(
  () => Boolean(body.value) && !shown.value.lines.length && !shown.value.speech.length && !shown.value.room.length,
);
const filtered = computed(
  () =>
    Boolean(query.value.trim()) ||
    Boolean(roomQuery.value.trim()) ||
    view.value !== "all" ||
    roomKind.value !== "all" ||
    speaker.value !== undefined ||
    author.value !== undefined,
);
// Speech and the room apart: the room gets its own column, with its own tools.
const split = computed(() => hasSpeech.value && !shown.value.interleaved && view.value === "all");

function pickSpeaker(key) {
  speaker.value = speaker.value === key ? undefined : key;
  if (speaker.value !== undefined) author.value = undefined;
  if (speaker.value !== undefined && view.value === "room") view.value = "all";
}

function pickAuthor(name) {
  author.value = name || undefined;
  if (author.value !== undefined) speaker.value = undefined;
}

function clearFilters() {
  query.value = "";
  roomQuery.value = "";
  view.value = "all";
  roomKind.value = "all";
  speaker.value = undefined;
  author.value = undefined;
}

// The pinned tools' height, so the chat column pins right under them.
const page = ref(null);
const tools = ref(null);
let resize = null;
onMounted(() => {
  if (typeof ResizeObserver === "undefined") return;
  resize = new ResizeObserver(([entry]) => {
    page.value?.style.setProperty("--t-tools-h", `${Math.ceil(entry.target.getBoundingClientRect().height)}px`);
  });
  watch(
    tools,
    async (el, old) => {
      if (old) resize.unobserve(old);
      await nextTick();
      if (el) resize.observe(el);
    },
    { immediate: true },
  );
});
onBeforeUnmount(() => resize?.disconnect());

const countLabel = computed(() => {
  const { speech, room } = shown.value.counts;
  const parts = [];
  if (view.value !== "room" && hasSpeech.value) parts.push(`${speech} ${speech === 1 ? "line" : "lines"} of speech`);
  const what = { chat: ["chat message", "chat messages"], questions: ["question or answer", "questions and answers"] }[
    roomKind.value
  ];
  const roomWords = what ? `${room} ${room === 1 ? what[0] : what[1]}` : `${room} from the room`;
  if (author.value !== undefined && view.value !== "speech") return `${what ? roomWords : room} from ${author.value}`;
  if (view.value !== "speech" && speaker.value === undefined) parts.push(roomWords);
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
// The loader's clock: from when this page first saw the transcript being made.
const jobStarted = ref(null);
const clockNow = ref(Date.now());
let jobTicker = null;
watch(
  jobBusy,
  (busy) => {
    clearInterval(jobTicker);
    jobTicker = null;
    if (busy) {
      if (jobStarted.value === null) jobStarted.value = Date.now();
      jobTicker = setInterval(() => (clockNow.value = Date.now()), 1000);
    } else {
      jobStarted.value = null;
    }
  },
  { immediate: true },
);
onBeforeUnmount(() => clearInterval(jobTicker));
const jobElapsed = computed(() => (jobStarted.value === null ? "" : elapsedLabel(jobStarted.value, clockNow.value)));
</script>

<template>
  <div ref="page" class="t-page">
    <SessionTabs :session-id="sessionId" current="transcript" :title="title || ''" />

    <header class="t-head">
      <h1 class="t-title">{{ title || "Transcript" }}</h1>
      <p v-if="start.text || length" class="t-date">
        <span v-if="start.text">{{ start.text }}<span v-if="start.zone" class="t-muted"> ({{ start.zone }})</span></span>
        <span v-if="length"> · {{ length }}</span>
      </p>
    </header>


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
            A long recording takes a few minutes; this page updates by itself. The chat and questions are below meanwhile.
          </p>
          <ProgressSteps
            :steps="transcriptSteps({ stage: job.stage, message: job.message })"
            :percent="job.progress ?? null"
            label="Transcript progress"
            :elapsed="jobElapsed"
          />
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

      <!-- What you do once: name the speakers, take the subtitles or the raw file. -->
      <div v-if="hasSpeech" class="t-actions">
        <UiButton variant="ghost" small icon="pen-line" @click="naming ? (naming = false) : startNaming()">
          {{ naming ? "Close" : speakers.some((s) => s.named) ? "Edit speaker names" : "Name speakers" }}
        </UiButton>
        <UiButton variant="ghost" small icon="download" @click="downloadSubtitles">Subtitles (SRT)</UiButton>
        <UiButton variant="ghost" small icon="download" :href="rawUrl">Raw (JSON)</UiButton>
      </div>

      <Card v-if="naming">
        <form class="t-naming" @submit.prevent="saveNames">
          <p class="t-muted">Names show in this transcript, its subtitles and the analyses made after you save them.</p>
          <div class="t-naming-grid">
            <label v-for="(item, index) in speakers.filter((s) => s.key !== null)" :key="item.key" class="t-naming-row">
              <span class="t-label">
                <span class="t-dot" :style="{ background: `var(--siq-speaker-${item.tone})` }" aria-hidden="true" />
                Speaker {{ index + 1 }}
              </span>
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
      </Card>

      <SpeakingStats v-if="hasSpeech && record?.record?.measures" :measures="record.record.measures" :speakers="speakers" />

      <!-- The tools, right above the lines, pinned under the tabs while reading. -->
      <div ref="tools" class="t-tools" role="search">
        <div class="t-toolbar">
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
        <div class="t-filters">
          <div v-if="speakers.length && view !== 'room'" class="t-chips" role="group" aria-label="Speakers">
            <button
              v-for="(item, index) in speakers"
              :key="item.key ?? `none-${index}`"
              type="button"
              class="t-chip siq-fx"
              :class="{ 'is-on': speaker === item.key }"
              :aria-pressed="speaker === item.key ? 'true' : 'false'"
              @click="pickSpeaker(item.key)"
            >
              <span class="t-dot" :style="{ background: `var(--siq-speaker-${item.tone})` }" aria-hidden="true" />
              {{ item.name }}
            </button>
          </div>
<div v-if="!split && hasRoom && view !== 'speech'" class="t-segmented t-kinds" role="group" aria-label="Chat or questions">
            <button
              v-for="option in ROOM_KINDS"
              :key="option.value"
              type="button"
              class="t-seg siq-fx"
              :class="{ 'is-on': roomKind === option.value }"
              :aria-pressed="roomKind === option.value ? 'true' : 'false'"
              @click="roomKind = option.value"
            >
              {{ option.label }}
            </button>
          </div>
          <label v-if="!split && authors.length && view !== 'speech'" class="t-field t-author">
            <span class="siq-sr-only">Who wrote</span>
            <select class="t-input t-select" :value="author ?? ''" @change="pickAuthor($event.target.value)">
              <option value="">Everyone in the chat</option>
              <option v-for="item in authors" :key="item.name" :value="item.name">{{ item.name }} · {{ item.count }}</option>
            </select>
            <Icon name="chevron-down" :size="16" class="t-select-icon" />
          </label>
          <span class="t-spacer" />
          <span class="t-count" role="status">{{ countLabel }}</span>
          <UiButton v-if="filtered" variant="ghost" small icon="x" @click="clearFilters">Clear</UiButton>
        </div>
      </div>

      <p v-if="hasSpeech && !shown.interleaved && !nothingMatches" class="t-note">
        <Icon name="info" :size="16" />
        <span>
          Speech and the chat are shown apart: the recording couldn't be matched to the session's clock to the second. Speech
          times are the recording's.
        </span>
      </p>

      <div v-if="chatNeedsFetch" class="t-refetch" role="status">
        <Icon name="message-circle" :size="16" />
        <span>
          {{
            refetchStatus === "running"
              ? "Fetching the chat again from Livestorm…"
              : !(state.body.room || []).length
                ? "No chat or questions saved for this session. If it had some, fetch them again from Livestorm."
                : "The chat's authors couldn't be matched to people: this chat was saved before StormIQ read who wrote each message."
          }}
        </span>
        <UiButton v-if="refetchStatus !== 'running'" variant="secondary" small icon="refresh-cw" @click="report.refetch()">
          Fetch the chat again (free)
        </UiButton>
        <span v-if="refetchStatus === 'error'" role="alert">{{ report.state.refetch.error }}</span>
      </div>

      <p v-if="chatQuestionsUnknown" class="t-muted t-kinds-note">
        Questions from the Q&amp;A tab only: questions asked in the chat show here once the session is analysed.
      </p>
      <p v-if="nothingMatches && !split" class="t-muted">Nothing matches.</p>

      <Card v-if="shown.interleaved && shown.lines.length" padding="flush">
        <h2 class="siq-sr-only">The session, line by line</h2>
        <ol class="t-lines">
          <TranscriptLine v-for="line in shown.lines" :key="line.id" :line="line" />
        </ol>
      </Card>

      <div v-else-if="split" class="t-split">
        <Card padding="flush" class="t-speech-col">
          <div class="t-section-head"><CardHeader icon="presentation" title="What was said" :level="2" /></div>
          <ol v-if="shown.speech.length" class="t-lines">
            <TranscriptLine v-for="line in shown.speech" :key="line.id" :line="line" />
          </ol>
          <p v-else class="t-muted t-empty">
            {{ author !== undefined ? "An author is picked: their messages only." : "Nothing matches." }}
          </p>
        </Card>
        <aside class="t-room-col" aria-label="Chat and questions">
          <Card padding="flush" class="t-room-card">
            <div class="t-room-head">
              <CardHeader icon="message-circle" title="Chat and questions" :level="2" />
              <div class="t-room-tools">
                <div class="t-segmented t-kinds" role="group" aria-label="Chat or questions">
                  <button
                  v-for="option in ROOM_KINDS"
                  :key="option.value"
                  type="button"
                  class="t-seg siq-fx"
                  :class="{ 'is-on': roomKind === option.value }"
                  :aria-pressed="roomKind === option.value ? 'true' : 'false'"
                  @click="roomKind = option.value"
                >
                  {{ option.label }}
                </button>
              </div>
                <label class="t-field t-search">
                  <span class="siq-sr-only">Search the chat</span>
                  <Icon name="search" :size="16" class="t-field-icon" />
                  <input v-model="roomQuery" type="search" class="t-input" placeholder="Search the chat" />
                </label>
                <label v-if="authors.length" class="t-field t-author">
                  <span class="siq-sr-only">Who wrote</span>
                  <select class="t-input t-select" :value="author ?? ''" @change="pickAuthor($event.target.value)">
                    <option value="">Everyone</option>
                    <option v-for="item in authors" :key="item.name" :value="item.name">{{ item.name }} · {{ item.count }}</option>
                  </select>
                  <Icon name="chevron-down" :size="16" class="t-select-icon" />
                </label>
              </div>
            </div>
            <ol v-if="shown.room.length" class="t-lines t-room-lines">
              <TranscriptLine v-for="line in shown.room" :key="line.id" :line="line" flat />
            </ol>
            <p v-else-if="(state.body?.room || []).length" class="t-muted t-empty">
              {{ speaker !== undefined ? "A speaker is picked: their speech only." : "Nothing matches." }}
            </p>
          </Card>
        </aside>
      </div>

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
.t-refetch {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--siq-space-2) var(--siq-space-3);
  padding: var(--siq-space-3) var(--siq-space-4);
  border-radius: var(--siq-radius-tile);
  background: var(--siq-surface-2);
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-ink-2);
}

.t-page {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-6);
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

.t-muted {
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

/* ── Tools ──
   Right above the lines, pinned under the session's tabs (which pin at the
   top, --siq-target high) on a wide screen. */
.t-actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--siq-space-1) var(--siq-space-2);
  margin-bottom: calc(var(--siq-space-3) * -1);
}

.t-tools {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-3);
  padding: var(--siq-space-3) 0;
  background: var(--siq-page);
  box-shadow: inset 0 -1px 0 var(--siq-hairline);
}

@media (min-width: 721px) {
  .t-tools {
    position: sticky;
    top: var(--siq-target);
    z-index: 9;
  }
}

.t-toolbar,
.t-filters {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--siq-space-2) var(--siq-space-3);
}

.t-chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--siq-space-2);
}

.t-dot {
  display: inline-block;
  flex: none;
  width: 10px;
  height: 10px;
  border-radius: 50%;
}

.t-chip {
  display: inline-flex;
  align-items: center;
  gap: var(--siq-space-2);
}

.t-spacer {
  flex: 1 1 auto;
}

.t-count {
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-muted);
}

.t-author {
  flex: 0 1 16rem;
  min-width: 11rem;
}

.t-select {
  appearance: none;
  padding-right: calc(var(--siq-space-3) + 20px);
  cursor: pointer;
}

.t-select-icon {
  position: absolute;
  right: var(--siq-space-3);
  color: var(--siq-muted);
  pointer-events: none;
}

/* ── Speech and the room apart: two columns on a wide screen; the chat
   column pins under the tools and scrolls on its own. ── */
.t-split {
  display: grid;
  gap: var(--siq-space-4);
  align-items: start;
}

.t-room-card {
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.t-room-head {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-3);
  padding: var(--siq-space-5) var(--siq-space-5) var(--siq-space-3);
  box-shadow: inset 0 -1px 0 var(--siq-hairline-row);
}

.t-room-tools {
  display: flex;
  flex-wrap: wrap;
  gap: var(--siq-space-2);
}

.t-room-tools .t-kinds {
  flex: 1 1 100%;
}

.t-kinds .t-seg {
  flex: 1 1 0;
}

.t-room-tools .t-search,
.t-room-tools .t-author {
  flex: 1 1 10rem;
}

.t-empty {
  margin: 0;
  padding: var(--siq-space-4) var(--siq-space-6) var(--siq-space-6);
}

@media (min-width: 1024px) {
  .t-split {
    grid-template-columns: minmax(0, 1.65fr) minmax(300px, 1fr);
  }
  .t-room-col {
    position: sticky;
    top: calc(var(--siq-target) + var(--t-tools-h, 0px) + var(--siq-space-3));
  }
  .t-room-card {
    max-height: calc(100vh - var(--siq-target) - var(--t-tools-h, 0px) - var(--siq-space-6));
  }
  .t-room-lines {
    overflow-y: auto;
    overscroll-behavior: contain;
  }
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
  .t-actions,
  .t-room-tools {
    display: none !important;
  }
}
</style>
