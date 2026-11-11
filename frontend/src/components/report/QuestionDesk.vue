<script setup>
import { computed, ref } from "vue";
import Card from "../ui/Card.vue";
import CardHeader from "../ui/CardHeader.vue";
import Icon from "../ui/Icon.vue";
import StatusWord from "../ui/StatusWord.vue";
import UiButton from "../ui/UiButton.vue";
import {
  FILTERS,
  canMark,
  chatNotice,
  csvChoices,
  filterCounts,
  filterQuestions,
  locatedActions,
  locatedLabel,
  locatedNotice,
  logisticsLine,
  metaLine,
  statusOf,
  summaryLine,
} from "../../followup/desk";

// The Follow-up tab's question desk: every audience question with its
// status (decided by the server), a filter, and for an open one a reply and
// "Mark answered", saved on the server. A question the analysis found
// answered in the talk shows that passage, with "Confirm" and "Not this".
// The text is attendees' and the talk's: rendered as text, never as HTML.
// The desk's data comes from the view's `createQuestionDesk()`.
const props = defineProps({
  desk: { type: Object, required: true },
  // The CSV's address for a scope ("open", "all", "answers"), or null for no download.
  csvUrl: { type: Function, default: null },
});

const state = computed(() => props.desk.state);
const body = computed(() => state.value.body);
const summary = computed(() => body.value?.summary || {});
const counts = computed(() => filterCounts(summary.value));

const filter = ref("open");

// The download menu: a disclosure that closes on a choice or Escape.
const downloadMenu = ref(null);
const choices = computed(() => csvChoices(counts.value));
function closeMenu() {
  if (downloadMenu.value) downloadMenu.value.open = false;
}
const shown = computed(() => filterQuestions(body.value?.questions || [], filter.value));

// The one question being answered: its id and the draft.
const editing = ref("");
const draft = ref("");

function startEdit(question) {
  editing.value = question.id;
  draft.value = question.reply || "";
}

function cancelEdit() {
  editing.value = "";
  draft.value = "";
}

async function save(question, answered) {
  const ok = await props.desk.mark(question.id, { answered, reply: answered ? draft.value : "" });
  if (ok) cancelEdit();
}

function decide(question, decision) {
  props.desk.decide(question.id, decision);
}

const EMPTY = {
  open: "Nothing still open.",
  answered: "No answered questions yet.",
  all: "No audience questions in the Q&A or the chat.",
};
</script>

<template>
  <Card class="qd" :aria-busy="state.loading ? 'true' : 'false'">
    <CardHeader icon="help-circle" title="Question desk" :subtitle="body ? summaryLine(summary) : ''" :level="2">
      <template v-if="csvUrl && counts.all" #right>
        <details ref="downloadMenu" class="qd-download" @keydown.esc="closeMenu">
          <summary class="qd-download-button siq-fx">
            <Icon name="download" :size="16" />
            <span>Download (CSV)</span>
            <Icon name="chevron-down" :size="14" />
          </summary>
          <ul class="qd-download-menu" aria-label="Download">
            <li v-for="choice in choices" :key="choice.scope">
              <span v-if="choice.disabled" class="qd-download-item is-disabled" aria-disabled="true">
                <span class="qd-download-label">{{ choice.label }}</span>
                <span class="qd-download-about">None yet</span>
              </span>
              <a v-else class="qd-download-item siq-fx" :href="csvUrl(choice.scope)" download @click="closeMenu">
                <span class="qd-download-label">{{ choice.label }}</span>
                <span class="qd-download-about">{{ choice.about }}</span>
              </a>
            </li>
          </ul>
        </details>
      </template>
    </CardHeader>

    <p v-if="state.loading && !body" class="qd-muted qd-gap" role="status">Loading the questions…</p>

    <div v-else-if="state.error && !body" class="qd-gap" role="alert">
      <p class="qd-text">{{ state.error }}</p>
      <div class="qd-row">
        <UiButton variant="secondary" small icon="refresh-cw" @click="desk.reload()">Try again</UiButton>
      </div>
    </div>

    <template v-else-if="body">
      <div class="qd-notes">
        <p v-if="csvUrl && counts.all" class="qd-muted">
          The CSV names who asked each question, on every session. It stays inside your organization.
        </p>
        <p v-if="logisticsLine(summary)" class="qd-muted">{{ logisticsLine(summary) }}</p>
        <p v-if="chatNotice(summary.chatQuestions)" class="qd-note">
          <Icon name="info" :size="16" />
          <span>{{ chatNotice(summary.chatQuestions) }}</span>
        </p>
        <p v-if="locatedNotice(summary)" class="qd-note">
          <Icon name="info" :size="16" />
          <span>{{ locatedNotice(summary) }}</span>
        </p>
      </div>

      <div v-if="counts.all" class="qd-filters" role="group" aria-label="Show">
        <button
          v-for="option in FILTERS"
          :key="option.key"
          type="button"
          class="qd-seg siq-fx"
          :class="{ 'is-on': filter === option.key }"
          :aria-pressed="filter === option.key ? 'true' : 'false'"
          @click="filter = option.key"
        >
          {{ option.label }} <span class="qd-count">{{ counts[option.key] }}</span>
        </button>
      </div>

      <p v-if="!shown.length" class="qd-muted qd-gap">{{ EMPTY[filter] }}</p>

      <ul v-else class="qd-list">
        <li v-for="question in shown" :key="question.id" class="qd-item">
          <span class="qd-icon" aria-hidden="true">
            <Icon :name="question.source === 'chat' ? 'message-circle' : 'help-circle'" :size="16" />
          </span>
          <div class="qd-main">
            <p class="qd-question">{{ question.text }}</p>
            <p class="qd-meta">
              <span>{{ metaLine(question) }}</span>
              <StatusWord :status="statusOf(question).status" :label="statusOf(question).label" />
            </p>

            <p v-if="question.answer" class="qd-answer"><span class="qd-answer-label">Answer:</span> {{ question.answer }}</p>
            <figure v-if="question.located" class="qd-located">
              <figcaption class="qd-located-label">
                <Icon name="play" :size="14" />
                <span>{{ locatedLabel(question) }}</span>
              </figcaption>
              <blockquote class="qd-quote">“{{ question.located.quote }}”</blockquote>
            </figure>
            <p v-if="question.reply && editing !== question.id" class="qd-answer">
              <span class="qd-answer-label">Your answer:</span> {{ question.reply }}
            </p>

            <form v-if="editing === question.id" class="qd-editor" @submit.prevent="save(question, true)">
              <label class="qd-label" :for="`qd-reply-${question.id}`">Your answer <span class="qd-muted">(optional)</span></label>
              <textarea
                :id="`qd-reply-${question.id}`"
                v-model="draft"
                class="qd-input"
                rows="3"
                maxlength="4000"
              />
              <div class="qd-row">
                <UiButton type="submit" variant="primary" small icon="check" :disabled="state.saving === question.id">
                  {{ state.saving === question.id ? "Saving…" : "Mark answered" }}
                </UiButton>
                <UiButton variant="ghost" small :disabled="state.saving === question.id" @click="cancelEdit">Cancel</UiButton>
              </div>
            </form>

            <div v-else-if="locatedActions(question).reject" class="qd-row qd-actions">
              <UiButton
                v-if="locatedActions(question).confirm"
                variant="secondary"
                small
                icon="check"
                :disabled="Boolean(state.saving)"
                @click="decide(question, 'confirm')"
              >
                {{ state.saving === question.id ? "Saving…" : "Confirm" }}
              </UiButton>
              <UiButton variant="ghost" small icon="x" :disabled="Boolean(state.saving)" @click="decide(question, 'reject')">
                Not this
              </UiButton>
            </div>

            <div v-else-if="canMark(question)" class="qd-row qd-actions">
              <UiButton
                v-if="question.status === 'open'"
                variant="secondary"
                small
                icon="check"
                :disabled="Boolean(state.saving)"
                @click="startEdit(question)"
              >
                Mark answered
              </UiButton>
              <template v-else>
                <UiButton variant="ghost" small icon="pen-line" :disabled="Boolean(state.saving)" @click="startEdit(question)">
                  Edit answer
                </UiButton>
                <UiButton variant="ghost" small :disabled="Boolean(state.saving)" @click="save(question, false)">
                  {{ state.saving === question.id ? "Saving…" : "Mark as open" }}
                </UiButton>
              </template>
            </div>

            <p v-if="state.saveError.id === question.id" class="qd-error" role="alert">{{ state.saveError.message }}</p>
          </div>
        </li>
      </ul>
    </template>
  </Card>
</template>

<style scoped>
.qd {
  min-width: 0;
}

.qd-gap {
  margin-top: var(--siq-space-4);
}

.qd-text,
.qd-muted,
.qd-note,
.qd-error {
  margin: 0;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
}

.qd-text {
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  color: var(--siq-ink-2);
}

.qd-muted {
  color: var(--siq-muted);
}

.qd-error {
  margin-top: var(--siq-space-2);
  color: var(--siq-low);
}

.qd-notes {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-2);
  margin-top: var(--siq-space-3);
}

.qd-notes:empty {
  display: none;
}

.qd-note {
  display: flex;
  gap: var(--siq-space-2);
  align-items: flex-start;
  color: var(--siq-ink-2);
}

.qd-note :deep(svg) {
  flex: none;
  margin-top: 2px;
  color: var(--siq-muted);
}

.qd-row {
  display: flex;
  flex-wrap: wrap;
  gap: var(--siq-space-2);
  margin-top: var(--siq-space-3);
}

/* ── The filter ── */
.qd-filters {
  display: inline-flex;
  flex-wrap: wrap;
  margin-top: var(--siq-space-4);
  padding: 2px;
  border: 1px solid var(--siq-border);
  border-radius: var(--siq-radius-control);
  background: var(--siq-input);
}

.qd-seg {
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

.qd-seg.is-on {
  background: var(--siq-selected-bg);
  color: var(--siq-selected-ink);
}

.qd-count {
  margin-left: 4px;
  font-variant-numeric: tabular-nums;
  color: var(--siq-muted);
}

.qd-seg.is-on .qd-count {
  color: inherit;
}

/* ── The list ── */
.qd-list {
  margin: var(--siq-space-4) 0 0;
  padding: 0;
  list-style: none;
}

.qd-item {
  display: flex;
  gap: var(--siq-space-3);
  padding: var(--siq-space-4) 0;
  box-shadow: inset 0 1px 0 var(--siq-hairline-row);
}

.qd-icon {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: var(--siq-radius-inline);
  background: var(--siq-surface-2);
  color: var(--siq-ink-2);
}

.qd-main {
  flex: 1;
  min-width: 0;
}

.qd-question {
  margin: 0;
  font-size: var(--siq-text-body);
  line-height: var(--siq-text-body-lh);
  color: var(--siq-ink);
  overflow-wrap: anywhere;
  white-space: pre-line;
}

.qd-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--siq-space-1) var(--siq-space-3);
  margin: var(--siq-space-1) 0 0;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-muted);
}

.qd-answer {
  margin: var(--siq-space-2) 0 0;
  padding-left: var(--siq-space-3);
  box-shadow: inset 2px 0 0 var(--siq-hairline);
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  color: var(--siq-ink-2);
  overflow-wrap: anywhere;
  white-space: pre-line;
}

.qd-located {
  margin: var(--siq-space-2) 0 0;
  padding-left: var(--siq-space-3);
  box-shadow: inset 2px 0 0 var(--siq-hairline);
}

.qd-located-label {
  display: inline-flex;
  align-items: center;
  gap: var(--siq-space-1);
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  font-weight: var(--siq-weight-medium);
  color: var(--siq-ink);
}

.qd-located-label :deep(svg) {
  flex: none;
  color: var(--siq-muted);
}

.qd-quote {
  margin: var(--siq-space-1) 0 0;
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  color: var(--siq-ink-2);
  overflow-wrap: anywhere;
}

.qd-answer-label {
  font-weight: var(--siq-weight-medium);
  color: var(--siq-ink);
}

.qd-actions {
  margin-top: var(--siq-space-2);
}

.qd-editor {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-2);
  margin-top: var(--siq-space-3);
}

.qd-label {
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  font-weight: var(--siq-weight-medium);
  color: var(--siq-ink);
}

.qd-input {
  width: 100%;
  min-height: calc(var(--siq-target) * 2);
  margin: 0;
  padding: var(--siq-space-2) var(--siq-space-3);
  border: 1px solid var(--siq-border);
  border-radius: var(--siq-radius-control);
  background: var(--siq-input);
  color: var(--siq-ink);
  font: inherit;
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  resize: vertical;
}

.qd-input:focus-visible {
  outline: 2px solid var(--siq-focus);
  outline-offset: 1px;
}

.qd-editor .qd-row {
  margin-top: 0;
}

/* ── The download menu ── */
.qd-download {
  position: relative;
}

.qd-download-button {
  display: inline-flex;
  align-items: center;
  gap: var(--siq-space-2);
  min-height: calc(var(--siq-target) - 4px);
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

.qd-download-button::-webkit-details-marker {
  display: none;
}

.qd-download-button:hover {
  border-color: var(--siq-border-hover);
}

.qd-download-menu {
  position: absolute;
  right: 0;
  top: calc(100% + var(--siq-space-1));
  z-index: 15;
  width: min(320px, calc(100vw - 32px));
  margin: 0;
  padding: var(--siq-space-1);
  list-style: none;
  border: 1px solid var(--siq-border);
  border-radius: var(--siq-radius-control);
  background: var(--siq-surface);
  box-shadow: var(--siq-card-shadow);
}

.qd-download-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: var(--siq-space-2) var(--siq-space-3);
  border-radius: calc(var(--siq-radius-control) - 4px);
  color: var(--siq-ink);
  text-decoration: none;
}

a.qd-download-item:hover {
  background: var(--siq-selected-bg);
}

.qd-download-item.is-disabled {
  color: var(--siq-muted);
}

.qd-download-label {
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  font-weight: var(--siq-weight-medium);
}

.qd-download-about {
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-muted);
}

@media (forced-colors: active) {
  .qd-seg.is-on {
    outline: 2px solid CanvasText;
  }
  .qd-item {
    border-top: 1px solid CanvasText;
  }
  .qd-answer,
  .qd-located {
    border-left: 2px solid CanvasText;
  }
}

@media print {
  .qd-download,
  .qd-filters,
  .qd-actions,
  .qd-editor {
    display: none !important;
  }
}
</style>
