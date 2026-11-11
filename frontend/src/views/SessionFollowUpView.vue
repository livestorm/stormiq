<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import BeforeYouShare from "../components/report/BeforeYouShare.vue";
import ChapterNotes from "../components/report/ChapterNotes.vue";
import DecisionCards from "../components/report/DecisionCards.vue";
import EmailFollowUp from "../components/report/EmailFollowUp.vue";
import FaqEditor from "../components/report/FaqEditor.vue";
import MarkdownBlocks from "../components/report/MarkdownBlocks.vue";
import QuestionDesk from "../components/report/QuestionDesk.vue";
import SurprisePlayer from "../components/report/SurprisePlayer.vue";
import SessionTabs from "../components/report/SessionTabs.vue";
import Card from "../components/ui/Card.vue";
import Collapsible from "../components/ui/Collapsible.vue";
import CardHeader from "../components/ui/CardHeader.vue";
import Icon from "../components/ui/Icon.vue";
import ProgressSteps from "../components/ui/ProgressSteps.vue";
import StatusWord from "../components/ui/StatusWord.vue";
import UiButton from "../components/ui/UiButton.vue";
import { api } from "../api";
import {
  CORNER_OUTPUTS,
  LANGUAGES,
  OUTPUTS,
  canMake,
  earlierVersions,
  listKeyFor,
  making,
  outputFor,
  outputGroups,
  outputText,
  pdfUrl,
  tabItems,
  toggleOptions,
  versionsQuery,
} from "../followup/outputs";
import { markdownSections, plainText } from "../followup/markdown";
import { filterCounts } from "../followup/desk";
import { uncheckedNote } from "../followup/figures";
import { createChapters } from "../followup/useChapters";
import { createFaq } from "../followup/useFaq";
import { createFigureChecks } from "../followup/useFigureChecks";
import { createOutputVersions } from "../followup/useOutputVersions";
import { createQuestionDesk } from "../followup/useQuestionDesk";
import { createSurpriseShow } from "../followup/useSurpriseShow";
import { aiSteps } from "../report/progress";
import { elapsedLabel, formatLength, formatSessionStart } from "../report/format";
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
const figures = createFigureChecks();
const versions = createOutputVersions();
// The FAQ is edited in place (`FaqEditor`), drafted from the desk; no AI.
const faq = createFaq();
// Surprise me's show (`SurprisePlayer`): read while the organization has it
// on; made only from its "Make the show" click.
const surprise = createSurpriseShow();

watch(
  () => props.sessionId,
  (id) => {
    report.open(id);
    desk.open(id);
    chapters.open(id);
    figures.open(id);
    faq.open(id);
    store.loadSessionById(id).catch(() => {});
  },
  { immediate: true },
);
onBeforeUnmount(() => {
  report.close();
  desk.close();
  chapters.close();
  figures.close();
  faq.close();
  versions.close();
  surprise.close();
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
// The desk's CSV download, by scope ("open", "all", "answers").
const csvUrl = (scope) => api.getQuestionsCsvUrl(props.sessionId, scope);
// The FAQ's downloads, by format ("md", "csv", "pdf").
const faqUrl = (format) => api.getFaqDownloadUrl(props.sessionId, format);

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

// The list scrolls with the page until it reaches the top of the window and
// sticks there; from then on it is the window's height and scrolls on its
// own, and once it reaches its end the wheel carries on to the page. Never a
// small scrolling box low on the page that keeps the wheel to itself.
const navEl = ref(null);
const wide = typeof window !== "undefined" && window.matchMedia ? window.matchMedia("(min-width: 901px)") : null;
let navFrame = 0;

function fitNav() {
  navFrame = 0;
  const el = navEl.value;
  if (!el) return;
  if (!wide?.matches) {
    el.style.maxHeight = "";
    return;
  }
  const stickyTop = parseFloat(getComputedStyle(el).top) || 0;
  const stuck = el.getBoundingClientRect().top <= stickyTop + 1;
  el.style.maxHeight = stuck ? `${window.innerHeight - stickyTop - 16}px` : "";
}

function queueFitNav() {
  if (!navFrame) navFrame = requestAnimationFrame(fitNav);
}

// The page's height changes as cards above the list load or fold.
let pageObserver = null;
onMounted(() => {
  window.addEventListener("scroll", queueFitNav, { passive: true });
  window.addEventListener("resize", queueFitNav, { passive: true });
  if (typeof ResizeObserver !== "undefined") {
    pageObserver = new ResizeObserver(queueFitNav);
    pageObserver.observe(document.body);
  }
  queueFitNav();
});
onBeforeUnmount(() => {
  window.removeEventListener("scroll", queueFitNav);
  window.removeEventListener("resize", queueFitNav);
  pageObserver?.disconnect();
  cancelAnimationFrame(navFrame);
});

// The list and the output side by side: once the page is scrolled past the
// output's top, picking another one brings its top back into view.
const layoutEl = ref(null);

function toOutputTop() {
  const el = layoutEl.value;
  if (!el || el.getBoundingClientRect().top >= 0) return;
  const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  el.scrollIntoView({ block: "start", behavior: reduce ? "auto" : "smooth" });
}

function show(key, lang = language.value) {
  const french = key !== DESK && key !== CHAPTERS && lang === "French";
  router.replace({ query: { ...route.query, output: key, lang: french ? "French" : undefined } });
  toOutputTop();
}

const groups = computed(() => outputGroups(outputs.value, language.value));
// Formal and Hype (the Smart Recap): one item in the list, a toggle on the output.
const toggles = computed(() => toggleOptions(output.value));
const listKey = computed(() => listKeyFor(output.value));
// The Follow-up email's tabs (Attended, Not attended, Generic): one output each.
const emailTabs = computed(() => tabItems(output.value, outputs.value, state, language.value));
// Surprise me shows only while the organization has it on (`state.org`,
// set by a StormIQ admin; the server refuses a take when it is off).
const surpriseOn = computed(() => state.org.surpriseEnabled);
watch(
  [() => props.sessionId, surpriseOn],
  ([id, on]) => (on ? surprise.open(id) : surprise.close()),
  { immediate: true },
);
const showState = computed(() => surprise.state.body?.state || "");
const showMaking = computed(() => surprise.state.starting || showState.value === "pending" || showState.value === "running");
const corner = computed(() =>
  surpriseOn.value
    ? CORNER_OUTPUTS.map((item) => ({
        key: item.key,
        label: item.label,
        ready: showState.value === "ready",
        making: showMaking.value,
      }))
    : [],
);
const body = computed(() => outputText(outputs.value, output.value, language.value));
const busy = computed(() => making(state, output.value, language.value));
const allowed = computed(() => canMake(outputs.value, output.value, language.value));
const pdf = computed(() => (body.value ? pdfUrl(api, props.sessionId, output.value, language.value) : ""));
// The list's spinner: every output being written now, in the language shown.
const writingKeys = computed(() => new Set(OUTPUTS.filter((item) => making(state, item, language.value).busy).map((item) => item.key)));
const isWriting = (item) => (item.keys || [item.key]).some((key) => writingKeys.value.has(key));

// ── The list: groups that fold, the one on screen open ──
// "From the session": the desk and the chapters, then the outputs filed
// there (the FAQ, drafted from the desk).
const SESSION_GROUP = "From the session";
const sessionOutputs = computed(() => groups.value.find((group) => group.name === SESSION_GROUP)?.items || []);
const otherGroups = computed(() => groups.value.filter((group) => group.name !== SESSION_GROUP));
const navGroups = computed(() => [
  {
    id: "session",
    name: SESSION_GROUP,
    items: [
      { key: DESK, label: "Question desk" },
      { key: CHAPTERS, label: "Chapters and show notes" },
      ...sessionOutputs.value,
    ],
  },
  ...otherGroups.value.map((group, index) => ({ ...group, id: `g${index}` })),
].map((group) => {
  const ready = group.items.filter((item) => item.ready).length;
  const writing = group.items.some((item) => item.keys && isWriting(item));
  const badge = group.id === "session" ? (openCount.value ? `${openCount.value} open` : "") : ready ? `${ready} ready` : "";
  return { ...group, writing, badge };
}));

function isCurrent(item) {
  if (item.key === DESK) return onDesk.value;
  if (item.key === CHAPTERS) return onChapters.value;
  return !onOwn.value && (item.keys || [item.key]).includes(output.value.key);
}

const currentGroup = computed(() => navGroups.value.find((group) => group.items.some(isCurrent))?.name || "");
const openGroups = ref(new Set());
watch(
  currentGroup,
  (name) => {
    if (name && !openGroups.value.has(name)) openGroups.value = new Set([...openGroups.value, name]);
  },
  { immediate: true },
);
const isOpen = (group) => openGroups.value.has(group.name);
function toggleGroup(group) {
  const next = new Set(openGroups.value);
  if (next.has(group.name)) next.delete(group.name);
  else next.add(group.name);
  openGroups.value = next;
}
// The loader's clock, from when this page first saw the output being written.
const busyStarted = ref(null);
const busyNow = ref(Date.now());
let busyTicker = null;
watch(
  () => busy.value.busy,
  (on) => {
    clearInterval(busyTicker);
    busyTicker = null;
    busyStarted.value = on ? busyStarted.value ?? Date.now() : null;
    if (on) busyTicker = setInterval(() => (busyNow.value = Date.now()), 1000);
  },
  { immediate: true },
);
watch(() => output.value.key, () => (busyStarted.value = busy.value.busy ? Date.now() : null));
onBeforeUnmount(() => clearInterval(busyTicker));
const busyElapsed = computed(() => (busyStarted.value === null ? "" : elapsedLabel(busyStarted.value, busyNow.value)));
// Surprise me, the show (`SurprisePlayer`): first and big; the take itself
// behind "Show transcript".
const transcriptOpen = ref(false);
watch(() => output.value.key, () => (transcriptOpen.value = false));
const takeText = computed(() => (output.value.corner ? body.value || surprise.state.body?.take || "" : ""));
// The take as a Markdown file, made in the browser from the text on screen.
function downloadTake() {
  const blob = new Blob([`${takeText.value.trim()}\n`], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `stormiq-surprise-${props.sessionId}.md`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
const anyBusy = computed(() => OUTPUTS.some((item) => item.flow && state.loading[item.flow]));

// ── Earlier versions: what was saved before the output on screen ──
const versionsFor = computed(() => (onOwn.value || output.value.key === "faq" ? null : versionsQuery(output.value, language.value)));
watch([() => props.sessionId, versionsFor], ([id, query]) => versions.open(id, query), { immediate: true });
// A new version was saved: the one on screen changed, so list it again.
watch(body, (next, previous) => {
  if (next && previous && next !== previous) versions.reload();
});
const earlier = computed(() => earlierVersions(versions.state.body, output.value, body.value));
const picked = ref(null); // the id of the earlier version open, or null
watch([output, language], () => (picked.value = null));
const bodyLang = computed(() => (language.value === "French" && output.value.by === "language" ? "fr" : undefined));

function make() {
  const { flow, tone } = output.value;
  const lang = language.value;
  const run = {
    analysis: () => store.runAnalysis(lang),
    decisions: () => store.runDecisions(lang),
    teamDigest: () => store.runTeamDigest(lang),
    attendeeFollowup: () => store.runAttendeeFollowup(lang),
    noShowFollowup: () => store.runNoShowFollowup(lang),
    smartRecap: () => store.runSmartRecap(tone),
    contentRepurposing: () => store.runContentRepurposing(lang),
  }[flow];
  run?.().catch(() => {});
}

const makeLabel = computed(() => {
  if (output.value.key === "decisions") return `List the decisions${language.value === "French" ? " in French" : ""}`;
  if (output.value.key === "team-digest") return `Write the team digest${language.value === "French" ? " in French" : ""}`;
  if (output.value.makeLabel) return `${output.value.makeLabel}${language.value === "French" ? " in French" : ""}`;
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
    <SessionTabs :session-id="sessionId" current="follow-up" :title="title || ''" />

    <header class="f-head">
      <h1 class="f-title">{{ title || "Follow-up" }}</h1>
      <p v-if="start.text || length" class="f-date">
        <span v-if="start.text">{{ start.text }}<span v-if="start.zone" class="f-muted"> ({{ start.zone }})</span></span>
        <span v-if="length"> · {{ length }}</span>
      </p>
    </header>


    <BeforeYouShare :figures="figures" />

    <button
      v-for="item in corner"
      :key="item.key"
      type="button"
      class="f-surprise siq-fx"
      :class="{ 'is-current': !onOwn && item.key === output.key }"
      :aria-current="!onOwn && item.key === output.key ? 'true' : undefined"
      @click="show(item.key)"
    >
      <span class="f-surprise-icon" aria-hidden="true"><Icon name="star" :size="20" /></span>
      <span class="f-corner-text">
        <span class="f-corner-title">{{ item.label }}</span>
        <span class="f-muted">The session as a short show: an unexpected take, performed over its own numbers and chat.</span>
      </span>
      <span v-if="item.making" class="f-writing" role="status"><span class="f-spinner" aria-hidden="true" />Making it</span>
      <span v-else class="f-surprise-go">{{ item.ready ? "Watch" : "Open" }}<Icon name="chevron-right" :size="16" /></span>
    </button>

    <div ref="layoutEl" class="f-layout">
      <nav ref="navEl" class="f-nav" aria-label="Follow-up">
        <label class="f-pick">
          <span class="siq-sr-only">Show</span>
          <select class="f-input f-select" :value="ownValue || listKey" @change="show($event.target.value)">
            <optgroup label="From the session">
              <option :value="DESK">Question desk{{ openCount ? ` · ${openCount} open` : "" }}</option>
              <option :value="CHAPTERS">Chapters and show notes</option>
              <option v-for="item in sessionOutputs" :key="item.key" :value="item.key">
                {{ item.label }}{{ isWriting(item) ? " · writing" : item.ready ? " · ready" : "" }}
              </option>
            </optgroup>
            <optgroup v-for="group in otherGroups" :key="group.name" :label="group.name">
              <option v-for="item in group.items" :key="item.key" :value="item.key">
                {{ item.label }}{{ isWriting(item) ? " · writing" : item.ready ? " · ready" : "" }}
              </option>
            </optgroup>
            <optgroup v-if="corner.length" label="Just for fun">
              <option v-for="item in corner" :key="item.key" :value="item.key">
                {{ item.label }}{{ item.making ? " · making" : item.ready ? " · ready" : "" }}
              </option>
            </optgroup>
          </select>
          <Icon name="chevron-down" :size="16" class="f-select-icon" />
        </label>
        <div v-for="group in navGroups" :key="group.name" class="f-group" :class="{ 'is-open': isOpen(group) }">
          <h2 class="f-group-head">
            <button
              type="button"
              class="f-group-toggle siq-fx"
              :aria-expanded="isOpen(group) ? 'true' : 'false'"
              :aria-controls="`f-group-${group.id}`"
              @click="toggleGroup(group)"
            >
              <Icon name="chevron-down" :size="16" class="f-chevron" />
              <span class="f-group-name">{{ group.name }}</span>
              <template v-if="!isOpen(group)">
                <span v-if="group.writing" class="f-writing" role="status"><span class="f-spinner" aria-hidden="true" />Writing</span>
                <span v-else-if="group.badge" class="f-badge">{{ group.badge }}</span>
              </template>
            </button>
          </h2>
          <ul v-show="isOpen(group)" :id="`f-group-${group.id}`" class="f-items">
            <li v-for="item in group.items" :key="item.key">
              <button
                type="button"
                class="f-item siq-fx"
                :class="{ 'is-current': isCurrent(item) }"
                :aria-current="isCurrent(item) ? 'true' : undefined"
                @click="show(item.key)"
              >
                <span>{{ item.label }}</span>
                <span v-if="item.key === DESK && openCount" class="f-badge">{{ openCount }} open</span>
                <span v-else-if="isWriting(item)" class="f-writing" role="status">
                  <span class="f-spinner" aria-hidden="true" />Writing
                </span>
                <StatusWord v-else-if="item.ready" status="measured" label="Ready" class="f-ready" />
              </button>
            </li>
          </ul>
        </div>
      </nav>

      <QuestionDesk v-if="onDesk" :desk="desk" :csv-url="csvUrl" />

      <ChapterNotes v-else-if="onChapters" :chapters="chapters" />

      <FaqEditor v-else-if="output.key === 'faq'" :faq="faq" :download-url="faqUrl" :title="title" />

      <p v-else-if="loading" class="f-muted" role="status">Loading the session…</p>

      <Card v-else-if="output.corner && !surpriseOn" class="f-output">
        <CardHeader icon="star" :title="output.label" :level="2" />
        <p class="f-text">Surprise me is turned off for your organization. A StormIQ admin can turn it back on.</p>
      </Card>

      <Card v-else-if="workspace && output.corner" class="f-output">
        <CardHeader icon="star" :title="output.label" :subtitle="output.about" :level="2" />
        <SurprisePlayer
          :show="surprise"
          :session-id="sessionId"
          :can-write="hasTranscript"
        />
        <template v-if="takeText">
          <div class="f-actions">
            <UiButton
              variant="secondary"
              small
              :icon="transcriptOpen ? 'chevron-up' : 'file-text'"
              :aria-expanded="transcriptOpen ? 'true' : 'false'"
              aria-controls="f-take"
              @click="transcriptOpen = !transcriptOpen"
            >
              {{ transcriptOpen ? "Hide transcript" : "Show transcript" }}
            </UiButton>
            <span class="f-muted f-ai">Written by AI from this session. Check it before you share it.</span>
          </div>
          <div v-if="transcriptOpen" id="f-take" class="f-take">
            <div class="f-actions">
              <UiButton variant="secondary" small :icon="copied === 'all' ? 'check' : 'copy'" @click="copy(takeText)">
                {{ copied === "all" ? "Copied" : "Copy" }}
              </UiButton>
              <UiButton variant="ghost" small icon="download" @click="downloadTake">Download (.md)</UiButton>
            </div>
            <MarkdownBlocks :text="takeText" />
            <Collapsible v-if="earlier.length" variant="link" icon="refresh-cw" :title="`Earlier takes (${earlier.length})`" class="f-versions">
              <p class="f-muted">Kept as they were saved, newest first. Read-only.</p>
              <ul class="f-version-list">
                <li v-for="version in earlier" :key="version.id" class="f-version">
                  <button
                    type="button"
                    class="f-version-head siq-fx"
                    :aria-expanded="picked === version.id ? 'true' : 'false'"
                    @click="picked = picked === version.id ? null : version.id"
                  >
                    <span class="f-version-when">{{ version.when }}</span>
                    <span v-if="version.models" class="f-muted">{{ version.models }}</span>
                    <Icon :name="picked === version.id ? 'chevron-up' : 'chevron-down'" :size="14" class="f-version-chevron" />
                  </button>
                  <div v-if="picked === version.id" class="f-version-body">
                    <MarkdownBlocks :text="version.text" />
                  </div>
                </li>
              </ul>
            </Collapsible>
          </div>
        </template>
      </Card>

      <Card v-else-if="workspace && !hasTranscript && !output.archived" class="f-output">
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
        <EmailFollowUp v-if="emailTabs.length" :tabs="emailTabs" @pick="show" />

        <template v-if="body">
          <div class="f-actions">
            <!-- The decisions' cards have their own Copy, worded with the names saved now. -->
            <UiButton
              v-if="output.key !== 'decisions'"
              variant="secondary"
              small
              :icon="copied === 'all' ? 'check' : 'copy'"
              @click="copy(body)"
            >
              {{ copied === "all" ? "Copied" : sections.length > 1 ? "Copy all" : "Copy" }}
            </UiButton>
            <UiButton v-if="pdf" variant="ghost" small icon="download" :href="pdf">PDF</UiButton>
            <span class="f-muted f-ai">Written by AI from this session. Check it before you share it.</span>
            <span v-if="uncheckedNote(figures.state.body)" class="f-unchecked">
              <Icon name="shield" :size="14" /> {{ uncheckedNote(figures.state.body) }}
            </span>
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
              <MarkdownBlocks :text="section.text" :top="4" :lang="bodyLang" />
            </section>
          </div>
          <DecisionCards v-else-if="output.key === 'decisions'" :session-id="sessionId" :language="language" :source="body" />
          <MarkdownBlocks v-else :text="body" :lang="bodyLang" />

          <Collapsible v-if="earlier.length" variant="link" icon="refresh-cw" :title="`Earlier versions (${earlier.length})`" class="f-versions">
            <p class="f-muted">Kept as they were saved, newest first. Read-only.</p>
            <ul class="f-version-list">
              <li v-for="version in earlier" :key="version.id" class="f-version">
                <button
                  type="button"
                  class="f-version-head siq-fx"
                  :aria-expanded="picked === version.id ? 'true' : 'false'"
                  @click="picked = picked === version.id ? null : version.id"
                >
                  <span class="f-version-when">{{ version.when }}</span>
                  <span v-if="version.models" class="f-muted">{{ version.models }}</span>
                  <Icon :name="picked === version.id ? 'chevron-up' : 'chevron-down'" :size="14" class="f-version-chevron" />
                </button>
                <div v-if="picked === version.id" class="f-version-body">
                  <div class="f-actions">
                    <UiButton
                      variant="secondary"
                      small
                      :icon="copied === `v${version.id}` ? 'check' : 'copy'"
                      @click="copy(version.text, `v${version.id}`)"
                    >
                      {{ copied === `v${version.id}` ? "Copied" : "Copy this version" }}
                    </UiButton>
                  </div>
                  <MarkdownBlocks :text="version.text" :lang="bodyLang" />
                </div>
              </li>
            </ul>
          </Collapsible>
          <p v-else-if="versions.state.error" class="f-muted">{{ versions.state.error }}</p>
        </template>

        <div v-else-if="busy.busy" class="f-empty" role="status">
          <ProgressSteps :steps="aiSteps(busy.stage)" :percent="busy.progress" :label="`Writing the ${output.label}`" :elapsed="busyElapsed" />
        </div>

        <div v-else-if="output.archived" class="f-empty">
          <p class="f-text">
            No {{ output.label.toLowerCase() }} was saved for this session{{ language === "French" ? " in French" : "" }}.
            StormIQ no longer makes it.
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
          <p v-else-if="output.key === 'decisions'" class="f-text">
            Not listed yet. It runs only when you ask, with AI, from the transcript: what was decided on stage and who
            promised what, each with the words said and when. Only what the transcript says word for word is kept.
          </p>
          <p v-else-if="output.key === 'team-digest'" class="f-text">
            Not written yet. It runs only when you ask, with AI: about 100 words for the team's channel, marked internal,
            from the Report's numbers, the key moments, the open questions and the decisions if you listed them. A digest
            with a number or a time StormIQ didn't measure is never saved. Nobody is named.
          </p>
          <p v-else-if="output.notYet" class="f-text">{{ output.notYet }}</p>
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
  scroll-margin-top: var(--siq-space-4);
}

/* The list sticks at the top of the window; once stuck it is the window's
   height (`fitNav`) and scrolls on its own, the wheel carrying on to the page
   at its ends. */
.f-nav {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-1);
  position: sticky;
  top: var(--siq-space-6);
  overflow-y: auto;
  scrollbar-width: thin;
}

.f-pick {
  display: none;
  position: relative;
  align-items: center;
}

.f-group-head {
  margin: 0;
  font: inherit;
}

.f-group-toggle {
  display: flex;
  align-items: center;
  gap: var(--siq-space-2);
  width: 100%;
  min-height: var(--siq-target);
  margin: 0;
  padding: 0 var(--siq-space-3);
  border: 0;
  border-radius: var(--siq-radius-control);
  background: transparent;
  color: var(--siq-muted);
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition-property: background-color, color;
  transition-duration: var(--siq-duration);
  transition-timing-function: var(--siq-ease);
}

.f-group-toggle:hover {
  background: var(--siq-card-hover);
  color: var(--siq-ink);
}

.f-group-toggle.siq-fx:focus-visible {
  outline-offset: -3px;
}

.f-group-name {
  flex: 1;
  min-width: 0;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  font-weight: var(--siq-weight-medium);
}

.f-chevron {
  flex: none;
  transform: rotate(-90deg);
  transition-property: transform;
  transition-duration: var(--siq-duration);
  transition-timing-function: var(--siq-ease);
}

.f-group.is-open .f-chevron {
  transform: none;
}

.f-items {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-1);
  margin: 0 0 var(--siq-space-2);
  padding: 0 0 0 var(--siq-space-4);
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

/* An output being written: a small spinner in the list, in place of "Ready". */
.f-writing {
  display: inline-flex;
  flex: none;
  align-items: center;
  gap: var(--siq-space-1);
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-accent);
}

.f-spinner {
  display: block;
  width: 10px;
  height: 10px;
  border: 2px solid currentColor;
  border-right-color: transparent;
  border-radius: 50%;
  animation: f-spin 0.9s linear infinite;
}

@keyframes f-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .f-spinner {
    animation: none;
  }
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

/* Surprise me's take, behind "Show transcript". */
.f-take {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-3);
  margin-top: var(--siq-space-3);
}

/* Surprise me: its own card above the list and the output. */
.f-surprise {
  display: flex;
  align-items: center;
  gap: var(--siq-space-3);
  width: 100%;
  margin: 0 0 var(--siq-space-4);
  padding: var(--siq-space-3) var(--siq-space-4);
  border: 1px solid var(--siq-border);
  border-radius: var(--siq-radius-card);
  background: var(--siq-card);
  color: var(--siq-ink-2);
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition-property: background-color, border-color, color;
  transition-duration: var(--siq-duration);
  transition-timing-function: var(--siq-ease);
}

.f-surprise:hover {
  background: var(--siq-card-hover);
  color: var(--siq-ink);
}

.f-surprise.is-current {
  border-color: var(--siq-accent);
  background: var(--siq-selected-bg);
  color: var(--siq-selected-ink);
}

.f-surprise-icon {
  display: grid;
  flex: none;
  place-items: center;
  width: var(--siq-target);
  height: var(--siq-target);
  border-radius: var(--siq-radius-control);
  background: var(--siq-surface-2);
  color: var(--siq-accent);
}

.f-surprise .f-corner-text {
  flex: 1;
  min-width: 0;
}

.f-surprise-go {
  display: inline-flex;
  flex: none;
  align-items: center;
  gap: var(--siq-space-1);
  font-size: var(--siq-text-ui);
  font-weight: var(--siq-weight-medium);
  color: var(--siq-accent);
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
    max-height: none;
    overflow: visible;
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
  .f-surprise.is-current,
  .f-seg.is-on {
    outline: 2px solid CanvasText;
  }
}

@media (prefers-reduced-motion: reduce) {
  .f-item,
  .f-group-toggle,
  .f-chevron,
  .f-surprise {
    transition: none;
  }
}

@media print {
  .f-nav,
  .f-surprise,
  .f-actions {
    display: none !important;
  }
  .f-layout {
    grid-template-columns: minmax(0, 1fr);
  }
}

/* A post or an email list: one block per part, each with its own Copy. */
.f-versions {
  margin-top: var(--siq-space-6);
  padding-top: var(--siq-space-4);
  box-shadow: inset 0 1px 0 var(--siq-hairline-row);
}

.f-version-list {
  margin: var(--siq-space-3) 0 0;
  padding: 0;
  list-style: none;
}

.f-version + .f-version {
  box-shadow: inset 0 1px 0 var(--siq-hairline-row);
}

.f-version-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: var(--siq-space-1) var(--siq-space-3);
  width: 100%;
  min-height: var(--siq-target);
  margin: 0;
  padding: var(--siq-space-2) 0;
  border: 0;
  background: transparent;
  color: var(--siq-ink);
  font: inherit;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  text-align: left;
  cursor: pointer;
}

.f-version-when {
  font-weight: var(--siq-weight-medium);
  font-variant-numeric: tabular-nums;
}

.f-version-chevron {
  margin-left: auto;
  align-self: center;
  color: var(--siq-muted);
}

.f-version-body {
  margin: 0 0 var(--siq-space-4);
  padding: var(--siq-space-4);
  border-radius: var(--siq-radius-tile);
  background: var(--siq-surface-2);
}

.f-version-body .f-actions {
  margin-top: 0;
}

/* The version's own lists, not nested under the version list's items. */
.f-version-body :deep(ul) {
  list-style-type: disc;
}

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

.f-unchecked {
  display: inline-flex;
  align-items: center;
  gap: var(--siq-space-1);
  flex-basis: 100%;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-ink-2);
}
</style>
