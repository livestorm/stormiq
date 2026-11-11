<script setup>
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";
import ChapterNotes from "../components/report/ChapterNotes.vue";
import MarkdownBlocks from "../components/report/MarkdownBlocks.vue";
import QuestionDesk from "../components/report/QuestionDesk.vue";
import SessionTabs from "../components/report/SessionTabs.vue";
import Card from "../components/ui/Card.vue";
import CardHeader from "../components/ui/CardHeader.vue";
import Icon from "../components/ui/Icon.vue";
import StatusWord from "../components/ui/StatusWord.vue";
import UiButton from "../components/ui/UiButton.vue";
import { api } from "../api";
import {
  CORNER_OUTPUTS,
  LANGUAGES,
  OUTPUTS,
  canMake,
  making,
  outputFor,
  outputGroups,
  outputReady,
  outputText,
  pdfUrl,
  toggleOptions,
} from "../followup/outputs";
import { markdownSections, plainText } from "../followup/markdown";
import { filterCounts } from "../followup/desk";
import { createChapters } from "../followup/useChapters";
import { createQuestionDesk } from "../followup/useQuestionDesk";
import { formatLength, formatSessionStart } from "../report/format";
import { createSessionReport } from "../report/useSessionReport";
import { useWorkspace } from "../store/workspace";

// The Follow-up tab. It opens on the question desk (every audience question,
// marked answered here, the open ones as a CSV), which reads the record's
// routes and needs no transcript; next to it, the chapters and their show
// notes, from what the session analysis stored. Then the session's AI outputs (Analysis,
// Smart Recap, Repurposing, and Surprise me in its corner), each written only
// when the user clicks. They live in the classic workspace, so the page loads
// it through the store (`loadSessionById`) and makes them with the store's AI
// flows, which keep their scope, poll and save rules. The header reads the
// record, as the Report does. Model text is rendered as text (`MarkdownBlocks`).

const props = defineProps({
  sessionId: { type: String, required: true },
});

const route = useRoute();
const router = useRouter();
const store = useWorkspace();
const { state } = store;
const report = createSessionReport();
const desk = createQuestionDesk();
const chapters = createChapters();

watch(
  () => props.sessionId,
  (id) => {
    report.open(id);
    desk.open(id);
    chapters.open(id);
    store.loadSessionById(id).catch(() => {});
  },
  { immediate: true },
);
onBeforeUnmount(() => {
  report.close();
  desk.close();
  chapters.close();
});

const DESK = "desk";
const CHAPTERS = "chapters";
const onDesk = computed(() => !route.query.output || route.query.output === DESK);
const onChapters = computed(() => route.query.output === CHAPTERS);
// One of the session's own views (the desk, the chapters), not an AI output.
const onOwn = computed(() => onDesk.value || onChapters.value);
const ownValue = computed(() => (onDesk.value ? DESK : onChapters.value ? CHAPTERS : ""));
// Still open for the host: open, or covered in the talk but not confirmed.
const openCount = computed(() => (desk.state.body ? filterCounts(desk.state.body.summary).open : null));
const csvUrl = computed(() => api.getOpenQuestionsCsvUrl(props.sessionId));

// ── The header ──
const record = computed(() => report.state.record);
const clock = computed(() => record.value?.meta?.clock || {});
const title = computed(() => record.value?.meta?.session?.title || "");
const start = computed(() => formatSessionStart(clock.value.startedAtUnix, clock.value.timezone));
const length = computed(() => formatLength(clock.value.endS));

// ── The workspace ──
const workspace = computed(() => (state.workspace?.sessionId === props.sessionId ? state.workspace : null));
const outputs = computed(() => workspace.value?.outputs || {});
const loading = computed(() => !workspace.value && state.loading.sessionFetch);
const hasTranscript = computed(() => Boolean(workspace.value) && store.hasTranscriptData.value);

// ── The output on screen: `?output=` and `?lang=` keep it in the URL ──
const output = computed(() => outputFor(String(route.query.output || "")));
const language = computed(() => (route.query.lang === "French" ? "French" : "English"));

function show(key, lang = language.value) {
  const french = key !== DESK && key !== CHAPTERS && lang === "French";
  router.replace({ query: { ...route.query, output: key, lang: french ? "French" : undefined } });
}

const groups = computed(() => outputGroups(outputs.value, language.value));
// Formal and Hype (the Smart Recap): one item in the list, a toggle on the output.
const toggles = computed(() => toggleOptions(output.value));
const listKey = computed(() => toggles.value[0]?.key || output.value.key);
const corner = computed(() =>
  CORNER_OUTPUTS.map((item) => ({ key: item.key, label: item.label, ready: outputReady(outputs.value, item, language.value) })),
);
const body = computed(() => outputText(outputs.value, output.value, language.value));
const busy = computed(() => making(state, output.value, language.value));
const allowed = computed(() => canMake(outputs.value, output.value, language.value));
const pdf = computed(() => (body.value ? pdfUrl(api, props.sessionId, output.value, language.value) : ""));
const anyBusy = computed(() => OUTPUTS.some((item) => state.loading[item.flow]));

function make() {
  const { flow, tone } = output.value;
  const lang = language.value;
  const run = {
    analysis: () => store.runAnalysis(lang),
    smartRecap: () => store.runSmartRecap(tone),
    contentRepurposing: () => store.runContentRepurposing(lang),
  }[flow];
  run?.().catch(() => {});
}

const makeLabel = computed(() => {
  if (output.value.corner) return "Write a surprise take";
  if (output.value.flow === "contentRepurposing") return `Write the repurposed content${language.value === "French" ? " in French" : ""}`;
  return `Write the ${output.value.label.toLowerCase()}${output.value.by === "language" && language.value === "French" ? " in French" : ""}`;
});

// ── Copy: the whole output, or one section of it (a post, an email) ──
// Each section of a post or an email list is copied apart, as plain text.
const sections = computed(() => (output.value.sections && body.value ? markdownSections(body.value) : []));
const copied = ref(""); // "all", a section's index, or ""
let copiedTimer = null;
async function copy(text, which = "all") {
  try {
    await navigator.clipboard.writeText(text);
    copied.value = which;
    clearTimeout(copiedTimer);
    copiedTimer = setTimeout(() => (copied.value = ""), 2000);
  } catch {
    copied.value = "";
  }
}
onBeforeUnmount(() => clearTimeout(copiedTimer));
</script>

<template>
  <div class="f-page">
    <nav class="f-crumbs" aria-label="Breadcrumb">
      <RouterLink class="f-crumb siq-fx" to="/sessions">Sessions</RouterLink>
      <Icon name="chevron-right" :size="14" />
      <RouterLink class="f-crumb siq-fx" :to="`/sessions/${encodeURIComponent(sessionId)}/report`">
        {{ title || "Session" }}
      </RouterLink>
      <Icon name="chevron-right" :size="14" />
      <span aria-current="page">Follow-up</span>
    </nav>

    <header class="f-head">
      <h1 class="f-title">{{ title || "Follow-up" }}</h1>
      <p v-if="start.text || length" class="f-date">
        <span v-if="start.text">{{ start.text }}<span v-if="start.zone" class="f-muted"> ({{ start.zone }})</span></span>
        <span v-if="length"> · {{ length }}</span>
      </p>
    </header>

    <SessionTabs :session-id="sessionId" current="follow-up" />

    <div class="f-layout">
      <nav class="f-nav" aria-label="Follow-up">
        <label class="f-pick">
          <span class="siq-sr-only">Show</span>
          <select class="f-input f-select" :value="ownValue || listKey" @change="show($event.target.value)">
            <optgroup label="From the session">
              <option :value="DESK">Question desk{{ openCount ? ` · ${openCount} open` : "" }}</option>
              <option :value="CHAPTERS">Chapters and show notes</option>
            </optgroup>
            <optgroup v-for="group in groups" :key="group.name" :label="group.name">
              <option v-for="item in group.items" :key="item.key" :value="item.key">
                {{ item.label }}{{ item.ready ? " · ready" : "" }}
              </option>
            </optgroup>
            <optgroup label="Just for fun">
              <option v-for="item in corner" :key="item.key" :value="item.key">
                {{ item.label }}{{ item.ready ? " · ready" : "" }}
              </option>
            </optgroup>
          </select>
          <Icon name="chevron-down" :size="16" class="f-select-icon" />
        </label>
        <div class="f-group">
          <h2 class="f-group-name">From the session</h2>
          <ul class="f-items">
            <li>
              <button
                type="button"
                class="f-item siq-fx"
                :class="{ 'is-current': onDesk }"
                :aria-current="onDesk ? 'true' : undefined"
                @click="show(DESK)"
              >
                <span>Question desk</span>
                <span v-if="openCount" class="f-badge">{{ openCount }} open</span>
              </button>
            </li>
            <li>
              <button
                type="button"
                class="f-item siq-fx"
                :class="{ 'is-current': onChapters }"
                :aria-current="onChapters ? 'true' : undefined"
                @click="show(CHAPTERS)"
              >
                <span>Chapters and show notes</span>
              </button>
            </li>
          </ul>
        </div>
        <div v-for="group in groups" :key="group.name" class="f-group">
          <h2 class="f-group-name">{{ group.name }}</h2>
          <ul class="f-items">
            <li v-for="item in group.items" :key="item.key">
              <button
                type="button"
                class="f-item siq-fx"
                :class="{ 'is-current': !onOwn && item.keys.includes(output.key) }"
                :aria-current="!onOwn && item.keys.includes(output.key) ? 'true' : undefined"
                @click="show(item.key)"
              >
                <span>{{ item.label }}</span>
                <StatusWord v-if="item.ready" status="measured" label="Ready" class="f-ready" />
              </button>
            </li>
          </ul>
        </div>
        <div v-for="item in corner" :key="item.key" class="f-group f-corner">
          <button
            type="button"
            class="f-corner-button siq-fx"
            :class="{ 'is-current': !onOwn && item.key === output.key }"
            :aria-current="!onOwn && item.key === output.key ? 'true' : undefined"
            @click="show(item.key)"
          >
            <Icon name="star" :size="16" />
            <span class="f-corner-text">
              <span class="f-corner-title">{{ item.label }}</span>
              <span class="f-muted">An unexpected take on the session. Opt-in.</span>
            </span>
          </button>
        </div>
      </nav>

      <QuestionDesk v-if="onDesk" :desk="desk" :csv-url="csvUrl" />

      <ChapterNotes v-else-if="onChapters" :chapters="chapters" />

      <p v-else-if="loading" class="f-muted" role="status">Loading the session…</p>

      <Card v-else-if="workspace && !hasTranscript" class="f-output">
        <CardHeader icon="file-text" title="This one is written from the transcript" :level="2" />
        <p class="f-text">This session has no transcript yet. Make it on the Transcript tab, then come back to write it.</p>
        <div class="f-row">
          <UiButton variant="primary" icon-right="chevron-right" :to="`/sessions/${encodeURIComponent(sessionId)}/transcript`">
            Open the Transcript
          </UiButton>
        </div>
      </Card>

      <Card v-else-if="workspace" class="f-output" :aria-busy="busy.busy ? 'true' : 'false'">
        <CardHeader :title="output.label" :subtitle="output.about" :level="2">
          <template v-if="toggles.length" #right>
            <div class="f-segmented" role="group" aria-label="Tone">
              <button
                v-for="option in toggles"
                :key="option.key"
                type="button"
                class="f-seg siq-fx"
                :class="{ 'is-on': option.key === output.key }"
                :aria-pressed="option.key === output.key ? 'true' : 'false'"
                @click="show(option.key)"
              >
                {{ option.toggleLabel }}
              </button>
            </div>
          </template>
          <template v-else-if="output.by === 'language'" #right>
            <div class="f-segmented" role="group" aria-label="Language">
              <button
                v-for="option in LANGUAGES"
                :key="option.value"
                type="button"
                class="f-seg siq-fx"
                :class="{ 'is-on': language === option.value }"
                :aria-pressed="language === option.value ? 'true' : 'false'"
                :lang="option.value === 'French' ? 'fr' : 'en'"
                @click="show(output.key, option.value)"
              >
                {{ option.label }}
              </button>
            </div>
          </template>
        </CardHeader>

        <template v-if="body">
          <div class="f-actions">
            <UiButton variant="secondary" small :icon="copied === 'all' ? 'check' : 'copy'" @click="copy(body)">
              {{ copied === "all" ? "Copied" : sections.length > 1 ? "Copy all" : "Copy" }}
            </UiButton>
            <UiButton v-if="pdf" variant="ghost" small icon="download" :href="pdf">PDF</UiButton>
            <span class="f-muted f-ai">Written by AI from this session. Check it before you share it.</span>
          </div>
          <div v-if="sections.length > 1" class="f-sections">
            <section v-for="(section, index) in sections" :key="index" class="f-section">
              <div class="f-section-head">
                <h3 class="f-section-title">{{ section.title || "Notes" }}</h3>
                <UiButton
                  variant="ghost"
                  small
                  :icon="copied === index ? 'check' : 'copy'"
                  :aria-label="`Copy ${section.title || 'this part'}`"
                  @click="copy(plainText(section.text), index)"
                >
                  {{ copied === index ? "Copied" : "Copy" }}
                </UiButton>
              </div>
              <MarkdownBlocks
                :text="section.text"
                :top="4"
                :lang="language === 'French' && output.by === 'language' ? 'fr' : undefined"
              />
            </section>
          </div>
          <MarkdownBlocks v-else :text="body" :lang="language === 'French' && output.by === 'language' ? 'fr' : undefined" />
        </template>

        <div v-else-if="busy.busy" class="f-empty" role="status">
          <p class="f-text">
            Writing{{ busy.progress != null ? `: ${busy.progress}%` : "…" }} This takes a minute or two; it keeps going if you
            leave the page.
          </p>
        </div>

        <div v-else-if="!allowed" class="f-empty">
          <p class="f-text">The French version is written from the English one. Write the English one first.</p>
          <div class="f-row">
            <UiButton variant="secondary" @click="show(output.key, 'English')">Show the English one</UiButton>
          </div>
        </div>

        <div v-else class="f-empty">
          <p v-if="output.key === 'overall'" class="f-text">
            Not written yet. It runs only when you ask, with AI, from what "Analyse session" found and the transcript, so run
            "Analyse session" on the Report first.
          </p>
          <p v-else class="f-text">Not written yet. It runs only when you ask, with AI, from the transcript and the chat.</p>
          <p v-if="output.flow === 'contentRepurposing'" class="f-muted">
            The summary, blog post, follow-up email and social posts are written together.
          </p>
          <div class="f-row">
            <UiButton variant="primary" icon="zap" :disabled="anyBusy" @click="make">{{ makeLabel }}</UiButton>
          </div>
        </div>
      </Card>
    </div>
  </div>
</template>

<style scoped>
.f-page {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-6);
}

.f-crumbs {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--siq-space-2);
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-muted);
}

.f-crumb {
  color: var(--siq-accent);
  text-decoration: none;
  border-radius: var(--siq-radius-inline);
}

.f-crumb:hover {
  color: var(--siq-accent-hover);
}

.f-head {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-2);
}

.f-title {
  margin: 0;
  font-size: var(--siq-text-display);
  line-height: var(--siq-text-display-lh);
  font-weight: var(--siq-weight-regular);
  letter-spacing: -0.015em;
  color: var(--siq-ink);
  overflow-wrap: anywhere;
}

.f-date,
.f-text {
  margin: 0;
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  color: var(--siq-ink-2);
}

.f-muted {
  margin: 0;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-muted);
}

.f-row {
  display: flex;
  flex-wrap: wrap;
  gap: var(--siq-space-2);
  margin-top: var(--siq-space-4);
}

/* ── Layout: the outputs on the left, the one on screen on the right ── */
.f-layout {
  display: grid;
  grid-template-columns: 15rem minmax(0, 1fr);
  gap: var(--siq-space-6);
  align-items: start;
}

.f-nav {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-4);
  position: sticky;
  top: var(--siq-space-6);
}

.f-pick {
  display: none;
  position: relative;
  align-items: center;
}

.f-group-name {
  margin: 0 0 var(--siq-space-1);
  padding: 0 var(--siq-space-3);
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  font-weight: var(--siq-weight-medium);
  color: var(--siq-muted);
}

.f-items {
  margin: 0;
  padding: 0;
  list-style: none;
}

.f-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--siq-space-2);
  width: 100%;
  min-height: var(--siq-target);
  margin: 0;
  padding: 0 var(--siq-space-3);
  border: 0;
  border-radius: var(--siq-radius-control);
  background: transparent;
  color: var(--siq-ink-2);
  font: inherit;
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  text-align: left;
  cursor: pointer;
  transition-property: background-color, color;
  transition-duration: var(--siq-duration);
  transition-timing-function: var(--siq-ease);
}

.f-item:hover {
  background: var(--siq-card-hover);
  color: var(--siq-ink);
}

.f-item.is-current {
  background: var(--siq-selected-bg);
  color: var(--siq-selected-ink);
  font-weight: var(--siq-weight-medium);
}

.f-item.siq-fx:focus-visible {
  outline-offset: -3px;
}

.f-ready {
  flex: none;
  font-size: var(--siq-text-small);
}

.f-badge {
  flex: none;
  padding: 0 var(--siq-space-2);
  border-radius: var(--siq-radius-pill);
  background: var(--siq-surface-2);
  color: var(--siq-ink-2);
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  font-weight: var(--siq-weight-medium);
  font-variant-numeric: tabular-nums;
}

/* Surprise me: small and quiet, at the bottom of the list. */
.f-corner {
  margin-top: var(--siq-space-4);
}

.f-corner-button {
  display: flex;
  gap: var(--siq-space-2);
  align-items: flex-start;
  width: 100%;
  margin: 0;
  padding: var(--siq-space-3);
  border: 1px dashed var(--siq-border);
  border-radius: var(--siq-radius-control);
  background: transparent;
  color: var(--siq-ink-2);
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition-property: background-color, border-color, color;
  transition-duration: var(--siq-duration);
  transition-timing-function: var(--siq-ease);
}

.f-corner-button :deep(svg) {
  flex: none;
  margin-top: 2px;
  color: var(--siq-muted);
}

.f-corner-button:hover {
  background: var(--siq-card-hover);
  color: var(--siq-ink);
}

.f-corner-button.is-current {
  border-style: solid;
  background: var(--siq-selected-bg);
  color: var(--siq-selected-ink);
}

.f-corner-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.f-corner-title {
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  font-weight: var(--siq-weight-medium);
}

.f-output {
  min-width: 0;
}

.f-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--siq-space-2);
  margin: var(--siq-space-4) 0 var(--siq-space-6);
}

.f-ai {
  margin-left: auto;
}

.f-empty {
  margin-top: var(--siq-space-4);
}

.f-segmented {
  display: inline-flex;
  padding: 2px;
  border: 1px solid var(--siq-border);
  border-radius: var(--siq-radius-control);
  background: var(--siq-input);
}

.f-seg {
  min-height: calc(var(--siq-target) - 6px);
  margin: 0;
  padding: 0 var(--siq-space-3);
  border: 0;
  border-radius: calc(var(--siq-radius-control) - 2px);
  background: transparent;
  color: var(--siq-ink-2);
  font: inherit;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  font-weight: var(--siq-weight-medium);
  cursor: pointer;
}

.f-seg.is-on {
  background: var(--siq-selected-bg);
  color: var(--siq-selected-ink);
}

/* v1 controls set their own font, colours, border and height: the classic
   element styles still load under v1. */
.f-input {
  width: 100%;
  height: var(--siq-target);
  margin: 0;
  padding: 0 36px 0 var(--siq-space-3);
  border: 1px solid var(--siq-border);
  border-radius: var(--siq-radius-control);
  background: var(--siq-input);
  color: var(--siq-ink);
  font: inherit;
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  appearance: none;
  cursor: pointer;
}

.f-input:focus-visible {
  outline: 2px solid var(--siq-focus);
  outline-offset: 1px;
}

.f-select-icon {
  position: absolute;
  right: var(--siq-space-3);
  color: var(--siq-muted);
  pointer-events: none;
}

@media (max-width: 900px) {
  .f-layout {
    grid-template-columns: minmax(0, 1fr);
    gap: var(--siq-space-4);
  }
  .f-nav {
    position: static;
  }
  .f-pick {
    display: flex;
  }
  .f-group {
    display: none;
  }
}

@media (max-width: 720px) {
  .f-title {
    font-size: var(--siq-text-headline);
    line-height: var(--siq-text-headline-lh);
  }
  .f-ai {
    margin-left: 0;
    flex-basis: 100%;
  }
}

@media (forced-colors: active) {
  .f-item.is-current,
  .f-corner-button.is-current,
  .f-seg.is-on {
    outline: 2px solid CanvasText;
  }
}

@media (prefers-reduced-motion: reduce) {
  .f-item,
  .f-corner-button {
    transition: none;
  }
}

@media print {
  .f-nav,
  .f-crumbs,
  .f-actions {
    display: none !important;
  }
  .f-layout {
    grid-template-columns: minmax(0, 1fr);
  }
}

/* A post or an email list: one block per part, each with its own Copy. */
.f-sections {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-4);
}

.f-section {
  padding: var(--siq-space-4) var(--siq-space-5) var(--siq-space-5);
  border: 1px solid var(--siq-hairline);
  border-radius: var(--siq-radius-control);
  background: var(--siq-surface-2);
}

.f-section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--siq-space-3);
  margin-bottom: var(--siq-space-3);
}

.f-section-title {
  margin: 0;
  font-size: var(--siq-text-card-title);
  line-height: var(--siq-text-card-title-lh);
  font-weight: var(--siq-weight-semibold);
  color: var(--siq-ink);
}
</style>
