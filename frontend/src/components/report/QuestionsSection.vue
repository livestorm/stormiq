<script setup>
import { computed, ref } from "vue";
import { RouterLink } from "vue-router";
import Collapsible from "../ui/Collapsible.vue";
import Icon from "../ui/Icon.vue";
import StatusWord from "../ui/StatusWord.vue";
import UiButton from "../ui/UiButton.vue";
import { answerDelay, questionGroups, questionNotes, questionsHeadline } from "../../report/questions";

// The Report's "The questions": every audience question, from the Q&A and
// the chat, as two lists, still open then answered. Each says who asked and
// when, and shows its answer: the host's, Livestorm's written one, or the
// passage of the talk that answers it. The data is Follow-up's question
// desk (`GET /record/questions`), read-only here; answering happens there.
// Attendees' text, rendered as text, never as HTML.

const props = defineProps({
  // The view's `createQuestionDesk().state`: {loading, error, body}.
  desk: { type: Object, required: true },
  measures: { type: Object, required: true },
  sessionId: { type: String, required: true },
});
const emit = defineEmits(["retry"]);

// A long list shows its first questions and "Show all".
const LIMIT = 8;

const body = computed(() => props.desk.body);
const headline = computed(() => questionsHeadline(body.value, props.measures));
const groups = computed(() => questionGroups(body.value));
const notes = computed(() => questionNotes(body.value));
const delay = computed(() => answerDelay(props.measures));
const followUp = computed(() => `/sessions/${encodeURIComponent(props.sessionId)}/follow-up`);
const hasOpen = computed(() => groups.value.some((group) => group.key === "open"));

const expanded = ref({});
function shown(group) {
  return expanded.value[group.key] ? group.items : group.items.slice(0, LIMIT);
}
</script>

<template>
  <Collapsible title="The questions" icon="help-circle" :takeaway="headline.takeaway" :chip="headline.chip">
    <p v-if="desk.loading && !body" class="q-note" role="status">Loading the questions…</p>

    <div v-else-if="desk.error && !body" class="q-error" role="alert">
      <p class="q-note">{{ desk.error }}</p>
      <UiButton variant="secondary" small icon="refresh-cw" @click="emit('retry')">Try again</UiButton>
    </div>

    <template v-else-if="body">
      <p v-if="!groups.length" class="q-note">No audience question in the Q&A or the chat.</p>

      <section v-for="group in groups" :key="group.key" class="q-group" :aria-labelledby="`q-group-${group.key}`">
        <h3 :id="`q-group-${group.key}`" class="q-group-title" :class="`is-${group.key}`">
          <Icon :name="group.key === 'open' ? 'help-circle' : 'check'" :size="18" />
          <span>{{ group.title }}</span>
          <span class="q-group-count">{{ group.count }}</span>
        </h3>

        <ul class="q-list">
          <li v-for="item in shown(group)" :key="item.id" class="q-item" :class="`is-${group.key}`">
            <span class="q-icon" aria-hidden="true">
              <Icon :name="item.source === 'chat' ? 'message-circle' : 'help-circle'" :size="16" />
            </span>
            <div class="q-main">
              <p class="q-text">{{ item.text }}</p>
              <p class="q-meta">
                <span>{{ item.who }}</span>
                <StatusWord :status="item.status.status" :label="item.status.label" />
              </p>
              <div v-for="answer in item.answers" :key="answer.label" class="q-answer" :class="{ 'is-quote': answer.quote }">
                <span class="q-answer-label">
                  <Icon v-if="answer.quote" name="play" :size="14" />
                  {{ answer.label }}
                </span>
                <span v-if="answer.quote" class="q-answer-text">“{{ answer.text }}”</span>
                <span v-else class="q-answer-text">{{ answer.text }}</span>
              </div>
            </div>
          </li>
        </ul>

        <UiButton
          v-if="group.items.length > LIMIT"
          variant="ghost"
          small
          :icon="expanded[group.key] ? 'chevron-up' : 'chevron-down'"
          @click="expanded = { ...expanded, [group.key]: !expanded[group.key] }"
        >
          {{ expanded[group.key] ? "Show fewer" : `Show all ${group.items.length}` }}
        </UiButton>
      </section>

      <div v-if="notes.length || delay || hasOpen" class="q-foot">
        <p v-if="delay" class="q-note">{{ delay }}</p>
        <p v-for="note in notes" :key="note" class="q-note">{{ note }}</p>
        <RouterLink v-if="hasOpen" :to="followUp" class="q-link siq-fx">
          <span>Answer the open ones, or download them, on Follow-up</span>
          <Icon name="chevron-right" :size="16" />
        </RouterLink>
      </div>
    </template>
  </Collapsible>
</template>

<style scoped>
.q-note {
  margin: 0;
  max-width: 680px;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-muted);
}

.q-error {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--siq-space-2);
}

.q-group {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--siq-space-3);
  padding: var(--siq-space-4) 0;
  box-shadow: inset 0 -1px 0 var(--siq-hairline-row);
}

.q-group:first-child {
  padding-top: var(--siq-space-2);
}

.q-group-title {
  display: flex;
  align-items: center;
  gap: var(--siq-space-2);
  margin: 0;
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  font-weight: var(--siq-weight-medium);
  color: var(--siq-ink);
}

.q-group-title.is-open :deep(svg) {
  color: var(--siq-s2);
}

.q-group-count {
  min-width: 1.5rem;
  padding: 0 var(--siq-space-2);
  border-radius: var(--siq-radius-pill);
  background: var(--siq-input);
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  text-align: center;
  color: var(--siq-ink-2);
  font-variant-numeric: tabular-nums;
}

.q-list {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-2);
  width: 100%;
  margin: 0;
  padding: 0;
  list-style: none;
}

.q-item {
  display: flex;
  gap: var(--siq-space-3);
  padding: var(--siq-space-3) var(--siq-space-4);
  border-radius: var(--siq-radius-control);
  box-shadow: inset 0 0 0 1px var(--siq-hairline);
}

/* Still open: a question-coloured edge, as on the Pulse's questions lane. */
.q-item.is-open {
  box-shadow:
    inset 4px 0 0 var(--siq-s2),
    inset 0 0 0 1px var(--siq-hairline);
}

.q-icon {
  flex: none;
  margin-top: 3px;
  color: var(--siq-muted);
}

.q-main {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-1);
  min-width: 0;
}

.q-text {
  margin: 0;
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  color: var(--siq-ink);
  overflow-wrap: anywhere;
}

.q-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--siq-space-2) var(--siq-space-3);
  margin: 0;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-muted);
}

.q-answer {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-top: var(--siq-space-1);
  padding-left: var(--siq-space-3);
  box-shadow: inset 2px 0 0 var(--siq-hairline);
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
}

.q-answer-label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--siq-muted);
}

.q-answer-text {
  color: var(--siq-ink-2);
  overflow-wrap: anywhere;
}

.q-answer.is-quote .q-answer-text {
  font-style: italic;
}

.q-foot {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-2);
  padding-top: var(--siq-space-4);
}

.q-link {
  display: inline-flex;
  align-items: center;
  gap: var(--siq-space-2);
  align-self: flex-start;
  min-height: var(--siq-target);
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  font-weight: var(--siq-weight-medium);
  color: var(--siq-accent);
  text-decoration: none;
}

.q-link:hover {
  text-decoration: underline;
}

@media (forced-colors: active) {
  .q-item {
    border: 1px solid CanvasText;
  }
  .q-item.is-open {
    border-left-width: 4px;
  }
  .q-answer {
    border-left: 2px solid CanvasText;
  }
}

@media print {
  .q-link {
    display: none;
  }
}
</style>
