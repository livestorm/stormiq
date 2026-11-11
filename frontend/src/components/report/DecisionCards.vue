<script setup>
import { computed, onBeforeUnmount, ref, watch } from "vue";
import Icon from "../ui/Icon.vue";
import UiButton from "../ui/UiButton.vue";
import MarkdownBlocks from "./MarkdownBlocks.vue";
import { createDecisions } from "../../followup/useDecisions";
import { decisionView, decisionsFileName } from "../../followup/decisions";

// Follow-up's "Decisions and commitments" as cards, from
// `GET /decisions?language=` (`createDecisions`): a section per kind, a card
// per item with its owner, due date and time as chips and the words said
// behind "What was said". The server names the speakers from the names saved
// on the Transcript tab when it is read, so a rename shows here without a new
// list. Copy and the download give the server's Markdown, worded the same
// way. A list saved before names could update is shown as it was saved.
// `source` is the workspace's text of the list: when a job saves a new one,
// it changes, and the cards load again. Until they load, it is shown as text.
const props = defineProps({
  sessionId: { type: String, required: true },
  language: { type: String, default: "English" },
  source: { type: String, default: "" },
});

const decisions = createDecisions();
watch(
  () => [props.sessionId, props.language, props.source],
  ([id, language, source]) => decisions.open(id, language, source),
  { immediate: true },
);
onBeforeUnmount(() => {
  decisions.close();
  clearTimeout(timer);
});

const state = computed(() => decisions.state);
const view = computed(() => decisionView(state.value.body, props.language));
const lang = computed(() => (props.language === "French" ? "fr" : "en"));
const markdown = computed(() => view.value.markdown || props.source);

const copied = ref(false);
let timer = null;
async function copy() {
  try {
    await navigator.clipboard.writeText(markdown.value);
    copied.value = true;
    clearTimeout(timer);
    timer = setTimeout(() => (copied.value = false), 2000);
  } catch {
    copied.value = false;
  }
}

function download() {
  const blob = new Blob([markdown.value], { type: "text/markdown;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = decisionsFileName(props.sessionId, props.language);
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}
</script>

<template>
  <div class="dc" :aria-busy="state.loading ? 'true' : 'false'">
    <div class="dc-actions">
      <UiButton variant="secondary" small :icon="copied ? 'check' : 'copy'" :disabled="!markdown" @click="copy">
        {{ copied ? "Copied" : "Copy" }}
      </UiButton>
      <UiButton variant="ghost" small icon="download" :disabled="!markdown" @click="download">Download (.md)</UiButton>
    </div>

    <template v-if="state.body && view.saved === 'items'">
      <p v-if="view.note" class="dc-note">
        <Icon name="timer" :size="16" />
        <span>{{ view.note }}</span>
      </p>
      <p v-if="view.empty" class="dc-empty" :lang="lang">
        <Icon name="info" :size="16" />
        <span>{{ view.emptyText }}</span>
      </p>
      <section v-for="section in view.sections" :key="section.kind" class="dc-section" :aria-labelledby="`dc-${section.kind}`">
        <h3 :id="`dc-${section.kind}`" class="dc-title" :lang="lang">
          <span class="dc-tile" aria-hidden="true"><Icon :name="section.icon" :size="16" /></span>
          {{ section.title }}
          <span class="dc-count">{{ section.count }}</span>
        </h3>
        <ul class="dc-list">
          <li v-for="item in section.items" :key="item.key" class="dc-card">
            <p class="dc-kind">
              <Icon :name="section.icon" :size="14" />
              <span>{{ section.word }}</span>
            </p>
            <p class="dc-text" :lang="lang">{{ item.text }}</p>
            <ul v-if="item.owner || item.due || item.at" class="dc-chips" aria-label="Details">
              <li v-if="item.owner" class="dc-chip dc-owner" :aria-label="item.owner.label">
                <span class="dc-avatar" aria-hidden="true">{{ item.owner.initials }}</span>
                <span class="dc-chip-text">{{ item.owner.name }}</span>
              </li>
              <li v-if="item.due" class="dc-chip" :aria-label="item.due.label">
                <Icon name="calendar" :size="14" />
                <span class="dc-chip-text" :lang="lang">{{ item.due.text }}</span>
              </li>
              <li v-if="item.at" class="dc-chip" :aria-label="item.at.label" :title="item.at.label">
                <Icon name="timer" :size="14" />
                <span class="dc-chip-text">{{ item.at.text }}</span>
              </li>
            </ul>
            <details v-if="item.quote" class="dc-said">
              <summary class="dc-said-head siq-fx">
                <Icon name="message-circle" :size="14" />
                <span>What was said</span>
                <Icon name="chevron-down" :size="14" class="dc-chevron" />
              </summary>
              <blockquote class="dc-quote">
                <p>“{{ item.quote }}”</p>
                <footer class="dc-muted">{{ item.attribution }}</footer>
              </blockquote>
            </details>
            <!-- Print shows the words said, without the toggle. -->
            <blockquote v-if="item.quote" class="dc-quote dc-print-only">
              <p>“{{ item.quote }}”</p>
              <footer class="dc-muted">{{ item.attribution }}</footer>
            </blockquote>
          </li>
        </ul>
      </section>
    </template>

    <template v-else-if="state.body && view.saved === 'markdown'">
      <p class="dc-note">
        <Icon name="info" :size="16" />
        <span>{{ view.note }}</span>
      </p>
      <MarkdownBlocks :text="view.markdown" :lang="lang" />
    </template>

    <template v-else>
      <p v-if="state.error" class="dc-note" role="alert">
        <Icon name="info" :size="16" />
        <span>{{ state.error }}</span>
        <UiButton variant="ghost" small icon="refresh-cw" @click="decisions.reload()">Try again</UiButton>
      </p>
      <p v-else-if="state.loading" class="dc-muted" role="status">Loading the cards…</p>
      <MarkdownBlocks v-if="source" :text="source" :lang="lang" />
    </template>
  </div>
</template>

<style scoped>
.dc {
  display: grid;
  gap: var(--siq-space-4);
  min-width: 0;
}

.dc-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--siq-space-2);
}

.dc-muted {
  color: var(--siq-muted);
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
}

.dc-note,
.dc-empty {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  gap: var(--siq-space-2);
  margin: 0;
  color: var(--siq-ink-2);
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
}

.dc-note > span,
.dc-empty > span {
  flex: 1 1 200px;
  min-width: 0;
}

.dc-section {
  display: grid;
  gap: var(--siq-space-3);
  min-width: 0;
}

.dc-title {
  display: flex;
  align-items: center;
  gap: var(--siq-space-2);
  margin: 0;
  color: var(--siq-ink);
  font-size: var(--siq-text-card-title);
  line-height: var(--siq-text-card-title-lh);
  font-weight: var(--siq-weight-semibold);
}

.dc-tile {
  display: inline-grid;
  place-items: center;
  width: 28px;
  height: 28px;
  border-radius: var(--siq-radius-tile);
  background: var(--siq-selected-bg);
  color: var(--siq-selected-ink);
}

.dc-count {
  min-width: 24px;
  padding: 0 var(--siq-space-2);
  border-radius: var(--siq-radius-pill);
  background: var(--siq-surface-2);
  color: var(--siq-ink-2);
  font-size: var(--siq-text-caption);
  line-height: var(--siq-text-caption-lh);
  font-weight: var(--siq-weight-medium);
  text-align: center;
}

.dc-list {
  display: grid;
  gap: var(--siq-space-3);
  margin: 0;
  padding: 0;
  list-style: none;
}

.dc-card {
  display: grid;
  gap: var(--siq-space-2);
  min-width: 0;
  padding: var(--siq-space-4);
  border: 1px solid var(--siq-border);
  border-radius: var(--siq-radius-card);
  background: var(--siq-surface);
}

.dc-kind {
  display: inline-flex;
  align-items: center;
  gap: var(--siq-space-1);
  margin: 0;
  color: var(--siq-muted);
  font-size: var(--siq-text-tag);
  line-height: var(--siq-text-tag-lh);
  font-weight: var(--siq-weight-medium);
  letter-spacing: var(--siq-label-tracking);
  text-transform: uppercase;
}

.dc-text {
  margin: 0;
  color: var(--siq-ink);
  font-size: var(--siq-text-body);
  line-height: var(--siq-text-body-lh);
  font-weight: var(--siq-weight-medium);
  overflow-wrap: anywhere;
}

.dc-chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--siq-space-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.dc-chip {
  display: inline-flex;
  align-items: center;
  gap: var(--siq-space-1);
  max-width: 100%;
  min-height: 28px;
  padding: 2px var(--siq-space-2);
  border-radius: var(--siq-radius-pill);
  box-shadow: inset 0 0 0 1px var(--siq-hairline-head);
  color: var(--siq-ink-2);
  font-size: var(--siq-text-caption);
  line-height: var(--siq-text-caption-lh);
  font-weight: var(--siq-weight-medium);
  font-variant-numeric: tabular-nums;
  box-sizing: border-box;
}

.dc-owner {
  padding-left: 2px;
}

.dc-chip-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dc-avatar {
  display: inline-grid;
  flex: none;
  place-items: center;
  width: 24px;
  height: 24px;
  border-radius: var(--siq-radius-pill);
  background: var(--siq-selected-bg);
  color: var(--siq-selected-ink);
  font-size: var(--siq-text-tag);
  line-height: 1;
  font-weight: var(--siq-weight-semibold);
}

.dc-said {
  min-width: 0;
}

.dc-said-head {
  display: inline-flex;
  align-items: center;
  gap: var(--siq-space-1);
  min-height: var(--siq-target);
  border-radius: var(--siq-radius-control);
  color: var(--siq-accent);
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  font-weight: var(--siq-weight-medium);
  cursor: pointer;
  list-style: none;
}

.dc-said-head::-webkit-details-marker {
  display: none;
}

.dc-said-head:hover {
  color: var(--siq-accent-hover);
}

.dc-said[open] .dc-chevron {
  transform: rotate(180deg);
}

.dc-quote {
  display: grid;
  gap: var(--siq-space-1);
  margin: 0;
  padding: var(--siq-space-2) var(--siq-space-3);
  border-left: 3px solid var(--siq-hairline-head);
  color: var(--siq-ink-2);
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  overflow-wrap: anywhere;
}

.dc-quote p {
  margin: 0;
  font-style: italic;
}

.dc-print-only {
  display: none;
}

@media (forced-colors: active) {
  .dc-chip,
  .dc-count {
    border: 1px solid CanvasText;
  }
}

@media print {
  .dc-actions,
  .dc-said {
    display: none;
  }

  .dc-print-only {
    display: grid;
  }

  .dc-card {
    break-inside: avoid;
  }
}
</style>
