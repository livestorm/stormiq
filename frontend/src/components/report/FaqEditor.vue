<script setup>
import { computed, onBeforeUnmount, ref } from "vue";
import Card from "../ui/Card.vue";
import CardHeader from "../ui/CardHeader.vue";
import Chip from "../ui/Chip.vue";
import Collapsible from "../ui/Collapsible.vue";
import Icon from "../ui/Icon.vue";
import UiButton from "../ui/UiButton.vue";
import MarkdownBlocks from "./MarkdownBlocks.vue";
import { DOWNLOADS, MAX_ANSWER_LENGTH, MAX_QUESTION_LENGTH, entryMarkdown, sourceChip, toMarkdown, updatesLine } from "../../followup/faq";

// The Follow-up tab's FAQ, edited in place: one entry per audience question,
// drafted by the server from the question desk (no AI), each with its
// question, its answer and where the answer came from. The user edits,
// adds, removes and reorders entries, then saves; desk answers that arrive
// later are offered, never written over an answer. Copy as Markdown here;
// the downloads (.md, CSV, PDF) are the saved FAQ, from the server. The text
// is attendees' and the host's: rendered as text, never as HTML. The data
// comes from the view's `createFaq()`.
const props = defineProps({
  faq: { type: Object, required: true },
  // The download's address for a format ("md", "csv", "pdf").
  downloadUrl: { type: Function, required: true },
  title: { type: String, default: "" },
});

const state = computed(() => props.faq.state);
const body = computed(() => state.value.body);
const entries = computed(() => state.value.entries);
const dirty = computed(() => props.faq.dirty.value);
const pending = computed(() => props.faq.pending.value);
const earlier = computed(() => body.value?.earlier?.markdown || "");
const answered = computed(() => entries.value.filter((entry) => String(entry.answer || "").trim()).length);
const subtitle = computed(() => {
  const n = entries.value.length;
  if (!body.value || !n) return "";
  const open = n - answered.value;
  return `${n} ${n === 1 ? "question" : "questions"} · ${answered.value} answered${open ? ` · ${open} still open` : ""}`;
});

// ── Copy ──
const copied = ref(""); // "all", an entry's id, or ""
let copiedTimer = null;
async function copy(text, which) {
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

// ── The download menu: a disclosure that closes on a choice or Escape ──
const downloadMenu = ref(null);
function closeMenu() {
  if (downloadMenu.value) downloadMenu.value.open = false;
}

// After a move, keep the focus on the moved entry's button.
const list = ref(null);
function move(index, delta) {
  props.faq.move(index, delta);
  const to = index + delta;
  requestAnimationFrame(() => {
    const button = list.value?.querySelector(`[data-move="${to}:${delta < 0 ? "up" : "down"}"]`);
    if (button && !button.disabled) button.focus();
    else list.value?.querySelector(`[data-move="${to}:${delta < 0 ? "down" : "up"}"]`)?.focus();
  });
}

function add() {
  props.faq.add();
  requestAnimationFrame(() => {
    const inputs = list.value?.querySelectorAll(".fe-question");
    inputs?.[inputs.length - 1]?.focus();
  });
}
</script>

<template>
  <Card class="fe" :aria-busy="state.loading || state.saving ? 'true' : 'false'">
    <CardHeader icon="help-circle" title="FAQ" :subtitle="subtitle" :level="2">
      <template v-if="body && entries.length" #right>
        <details ref="downloadMenu" class="fe-download" @keydown.esc="closeMenu">
          <summary class="fe-download-button siq-fx">
            <Icon name="download" :size="16" />
            <span>Download</span>
            <Icon name="chevron-down" :size="14" />
          </summary>
          <ul class="fe-download-menu" aria-label="Download">
            <li v-for="choice in DOWNLOADS" :key="choice.format">
              <span v-if="dirty" class="fe-download-item is-disabled" aria-disabled="true">
                <span class="fe-download-label">{{ choice.label }}</span>
                <span class="fe-download-about">Save your changes first.</span>
              </span>
              <a v-else class="fe-download-item siq-fx" :href="downloadUrl(choice.format)" download @click="closeMenu">
                <span class="fe-download-label">{{ choice.label }}</span>
                <span class="fe-download-about">{{ choice.about }}</span>
              </a>
            </li>
          </ul>
        </details>
      </template>
    </CardHeader>

    <p v-if="state.loading && !body" class="fe-muted fe-gap" role="status">Loading the FAQ…</p>

    <div v-else-if="state.error && !body" class="fe-gap" role="alert">
      <p class="fe-text">{{ state.error }}</p>
      <div class="fe-row">
        <UiButton variant="secondary" small icon="refresh-cw" @click="faq.reload()">Try again</UiButton>
      </div>
    </div>

    <template v-else-if="body">
      <div class="fe-notes">
        <p v-if="!body.saved" class="fe-note">
          <Icon name="info" :size="16" />
          <span>Draft from the question desk — edit and save. Attendees' names, emails and links are already removed.</span>
        </p>
        <p class="fe-muted">In English for now; French comes later.</p>
      </div>

      <div v-if="pending.length" class="fe-updates" role="status">
        <Icon name="refresh-cw" :size="16" />
        <span class="fe-updates-text">{{ updatesLine(pending) }}</span>
        <UiButton variant="secondary" small icon="plus" @click="faq.addUpdates()">Add them</UiButton>
      </div>

      <div class="fe-toolbar">
        <UiButton
          v-if="entries.length"
          variant="secondary"
          small
          :icon="copied === 'all' ? 'check' : 'copy'"
          @click="copy(toMarkdown(entries, title), 'all')"
        >
          {{ copied === "all" ? "Copied" : "Copy all" }}
        </UiButton>
        <UiButton variant="ghost" small icon="plus" @click="add">Add a question</UiButton>
        <template v-if="dirty">
          <span class="fe-unsaved" role="status"><Icon name="pen-line" :size="14" /> Unsaved changes</span>
          <UiButton variant="primary" small icon="check" :disabled="state.saving" @click="faq.save()">
            {{ state.saving ? "Saving…" : "Save" }}
          </UiButton>
          <UiButton variant="ghost" small :disabled="state.saving" @click="faq.discard()">Discard</UiButton>
        </template>
      </div>
      <p v-if="state.saveError" class="fe-error" role="alert">{{ state.saveError }}</p>

      <p v-if="!entries.length" class="fe-text fe-gap">No questions yet. Add one, or answer the audience on the question desk.</p>

      <ol v-else ref="list" class="fe-list">
        <li v-for="(entry, index) in entries" :key="entry.id" class="fe-entry">
          <div class="fe-entry-head">
            <span class="fe-entry-number">Question {{ index + 1 }}</span>
            <Chip :tone="sourceChip(entry).source === 'none' ? 'fair' : 'neutral'" :icon="sourceChip(entry).icon">
              {{ sourceChip(entry).label }}
            </Chip>
            <span class="fe-entry-tools">
              <UiButton
                variant="icon"
                icon="copy"
                :label="copied === entry.id ? `Question ${index + 1} copied` : `Copy question ${index + 1}`"
                @click="copy(entryMarkdown(entry), entry.id)"
              />
              <UiButton
                variant="icon"
                icon="chevron-up"
                :label="`Move question ${index + 1} up`"
                :disabled="index === 0"
                :data-move="`${index}:up`"
                @click="move(index, -1)"
              />
              <UiButton
                variant="icon"
                icon="chevron-down"
                :label="`Move question ${index + 1} down`"
                :disabled="index === entries.length - 1"
                :data-move="`${index}:down`"
                @click="move(index, 1)"
              />
              <UiButton variant="icon" icon="x" :label="`Remove question ${index + 1}`" @click="faq.remove(index)" />
            </span>
          </div>
          <label class="fe-label" :for="`fe-q-${entry.id}`">Question</label>
          <textarea
            :id="`fe-q-${entry.id}`"
            v-model="entry.question"
            class="fe-input fe-question"
            rows="2"
            :maxlength="MAX_QUESTION_LENGTH"
          />
          <label class="fe-label" :for="`fe-a-${entry.id}`">Answer <span class="fe-muted">(empty: not answered yet)</span></label>
          <textarea
            :id="`fe-a-${entry.id}`"
            :value="entry.answer"
            class="fe-input fe-answer"
            rows="3"
            :maxlength="MAX_ANSWER_LENGTH"
            @input="faq.setAnswer(index, $event.target.value)"
          />
        </li>
      </ol>

      <Collapsible v-if="earlier" variant="link" icon="file-text" title="Earlier AI version" class="fe-earlier">
        <p class="fe-muted">The FAQ StormIQ wrote with AI before it could be edited. Read-only.</p>
        <MarkdownBlocks :text="earlier" />
      </Collapsible>
    </template>
  </Card>
</template>

<style scoped>
.fe {
  min-width: 0;
}

.fe-gap {
  margin-top: var(--siq-space-4);
}

.fe-text {
  margin: 0;
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  color: var(--siq-ink-2);
}

.fe-muted {
  margin: 0;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-muted);
}

.fe-row {
  display: flex;
  flex-wrap: wrap;
  gap: var(--siq-space-2);
  margin-top: var(--siq-space-3);
}

.fe-notes {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-2);
  margin-top: var(--siq-space-4);
}

.fe-note {
  display: flex;
  gap: var(--siq-space-2);
  align-items: flex-start;
  margin: 0;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-ink-2);
}

.fe-note :deep(svg),
.fe-updates :deep(svg) {
  flex: none;
  margin-top: 2px;
  color: var(--siq-muted);
}

.fe-updates {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--siq-space-2) var(--siq-space-3);
  margin-top: var(--siq-space-4);
  padding: var(--siq-space-3) var(--siq-space-4);
  border-radius: var(--siq-radius-control);
  background: var(--siq-selected-bg);
  color: var(--siq-selected-ink);
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
}

.fe-updates-text {
  flex: 1 1 12rem;
  min-width: 0;
}

.fe-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--siq-space-2);
  margin-top: var(--siq-space-4);
}

.fe-unsaved {
  display: inline-flex;
  align-items: center;
  gap: var(--siq-space-1);
  margin-left: auto;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  font-weight: var(--siq-weight-medium);
  color: var(--siq-ink);
}

.fe-error {
  margin: var(--siq-space-2) 0 0;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-low);
}

.fe-list {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-4);
  margin: var(--siq-space-4) 0 0;
  padding: 0;
  list-style: none;
}

.fe-entry {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-2);
  min-width: 0;
  padding: var(--siq-space-3) var(--siq-space-4) var(--siq-space-4);
  border: 1px solid var(--siq-hairline);
  border-radius: var(--siq-radius-control);
  background: var(--siq-surface-2);
}

.fe-entry-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--siq-space-2);
  min-width: 0;
}

.fe-entry-number {
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  font-weight: var(--siq-weight-semibold);
  color: var(--siq-ink);
}

.fe-entry-tools {
  display: inline-flex;
  flex-wrap: wrap;
  gap: var(--siq-space-1);
  margin-left: auto;
}

.fe-label {
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  font-weight: var(--siq-weight-medium);
  color: var(--siq-ink);
}

.fe-input {
  width: 100%;
  min-width: 0;
  min-height: var(--siq-target);
  margin: 0;
  padding: var(--siq-space-2) var(--siq-space-3);
  border: 1px solid var(--siq-border);
  border-radius: var(--siq-radius-control);
  background: var(--siq-input);
  color: var(--siq-ink);
  font: inherit;
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  box-sizing: border-box;
}

.fe-question {
  resize: vertical;
}

.fe-answer {
  min-height: calc(var(--siq-target) * 2);
  resize: vertical;
}

.fe-input:focus-visible {
  outline: 2px solid var(--siq-focus);
  outline-offset: 1px;
}

.fe-earlier {
  margin-top: var(--siq-space-6);
  padding-top: var(--siq-space-4);
  box-shadow: inset 0 1px 0 var(--siq-hairline-row);
}

.fe-download {
  position: relative;
}

.fe-download-button {
  display: inline-flex;
  align-items: center;
  gap: var(--siq-space-2);
  min-height: var(--siq-target);
  padding: 0 var(--siq-space-3);
  border: 1px solid var(--siq-border);
  border-radius: var(--siq-radius-control);
  background: var(--siq-surface);
  color: var(--siq-ink);
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  font-weight: var(--siq-weight-medium);
  list-style: none;
  cursor: pointer;
  white-space: nowrap;
}

.fe-download-button::-webkit-details-marker {
  display: none;
}

.fe-download-button:hover {
  border-color: var(--siq-border-hover);
}

.fe-download-menu {
  position: absolute;
  right: 0;
  top: calc(100% + var(--siq-space-1));
  z-index: 15;
  width: min(300px, calc(100vw - 32px));
  margin: 0;
  padding: var(--siq-space-1);
  list-style: none;
  border: 1px solid var(--siq-border);
  border-radius: var(--siq-radius-control);
  background: var(--siq-surface);
  box-shadow: var(--siq-card-shadow);
}

.fe-download-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-height: var(--siq-target);
  padding: var(--siq-space-2) var(--siq-space-3);
  border-radius: calc(var(--siq-radius-control) - 4px);
  color: var(--siq-ink);
  text-decoration: none;
  box-sizing: border-box;
}

a.fe-download-item:hover {
  background: var(--siq-selected-bg);
}

.fe-download-item.is-disabled {
  color: var(--siq-muted);
}

.fe-download-label {
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  font-weight: var(--siq-weight-medium);
}

.fe-download-about {
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-muted);
}

@media (max-width: 720px) {
  .fe-unsaved {
    margin-left: 0;
    flex-basis: 100%;
  }
  .fe-entry {
    padding: var(--siq-space-3);
  }
  .fe-entry-tools {
    margin-left: 0;
  }
}

@media (forced-colors: active) {
  .fe-updates {
    border: 1px solid CanvasText;
  }
}

@media print {
  .fe-download,
  .fe-toolbar,
  .fe-entry-tools,
  .fe-updates {
    display: none !important;
  }
}
</style>
