<script setup>
import { computed, ref } from "vue";
import Collapsible from "../ui/Collapsible.vue";
import UiButton from "../ui/UiButton.vue";
import AudienceBlock from "./AudienceBlock.vue";
import ChatMessageList from "./ChatMessageList.vue";
import { qaAnswers, questionCount, questionsSummary } from "../../report/questions";

// The Report's "The questions" section, first part: how many questions the
// audience asked, in the Q&A and in the chat, and how the Q&A's were
// answered. The chat's questions open their messages. The question ledger,
// with each question's status, comes with 1e.

const props = defineProps({
  measures: { type: Object, required: true },
  chat: { type: Object, required: true },
});
const emit = defineEmits(["load-chat"]);

const summary = computed(() => questionsSummary(props.measures));
const count = computed(() => questionCount(props.measures));
const answers = computed(() => qaAnswers(props.measures));
const chatIds = computed(() => {
  const row = (props.measures["m.chat_themes"]?.rows || []).find((item) => item.key === "question");
  return row?.ids || [];
});
const showChat = ref(false);
</script>

<template>
  <Collapsible title="The questions" icon="help-circle" :takeaway="summary.takeaway" :chip="summary.chip">
    <AudienceBlock v-if="count" icon="help-circle" title="How many questions" :status="count.status" :info="count.info">
      <p class="r-callout">
        <span class="r-callout-number">{{ count.value }}</span>
        <span>{{ count.parts.join(" · ") }}</span>
      </p>
      <p v-if="count.apart" class="r-note">{{ count.apart }}</p>
      <p v-if="count.note" class="r-note">{{ count.note }}</p>
      <div v-if="chatIds.length">
        <UiButton
          variant="ghost"
          :icon="showChat ? 'chevron-up' : 'chevron-down'"
          :aria-expanded="showChat ? 'true' : 'false'"
          :aria-controls="showChat ? 'r-chat-questions' : undefined"
          @click="showChat = !showChat"
        >
          {{ showChat ? "Hide the chat's questions" : `Read the chat's ${chatIds.length} questions` }}
        </UiButton>
        <div v-if="showChat" id="r-chat-questions">
          <ChatMessageList
            :ids="chatIds"
            :chat="chat"
            label="Chat messages sorted as questions"
            @load="emit('load-chat')"
          />
        </div>
      </div>
    </AudienceBlock>

    <AudienceBlock v-if="answers" icon="check" title="How the Q&A was answered" :status="answers.status" :info="answers.info">
      <dl v-if="answers.rows.length" class="r-answers">
        <div v-for="row in answers.rows" :key="row.key" class="r-answer" :class="{ 'is-open': row.open }">
          <dt>{{ row.label }}</dt>
          <dd>{{ row.questions }}</dd>
        </div>
      </dl>
      <p v-if="answers.rate" class="r-note">Answered: {{ answers.rate }} of the Q&A's questions.</p>
      <p v-if="answers.delay" class="r-note">Median time to a written answer: {{ answers.delay }}.</p>
      <p v-if="answers.note" class="r-note">{{ answers.note }}</p>
    </AudienceBlock>
  </Collapsible>
</template>

<style scoped>
.r-note {
  margin: 0;
  max-width: 680px;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-muted);
}

.r-callout {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: var(--siq-space-2);
  margin: 0;
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  color: var(--siq-ink-2);
}

.r-callout-number {
  font-size: var(--siq-text-headline);
  line-height: var(--siq-text-headline-lh);
  color: var(--siq-ink);
  font-variant-numeric: tabular-nums;
}

.r-answers {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr));
  gap: var(--siq-space-3);
  max-width: 640px;
  margin: 0;
}

/* The number first, then its label: the number is in row 1 so every tile's
   number lines up, whether or not its label wraps. */
.r-answer {
  display: grid;
  grid-template-rows: auto 1fr;
  align-content: start;
  gap: 2px;
  padding: var(--siq-space-3) var(--siq-space-4);
  border-radius: var(--siq-radius-control);
  box-shadow: inset 0 0 0 1px var(--siq-hairline);
}

/* Still open: a question-coloured edge, as on the Pulse's questions lane. */
.r-answer.is-open {
  box-shadow:
    inset 4px 0 0 var(--siq-s2),
    inset 0 0 0 1px var(--siq-hairline);
}

.r-answer dt {
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-ink-2);
}

.r-answer dt {
  grid-row: 2;
}

.r-answer dd {
  grid-row: 1;
  margin: 0;
  font-size: var(--siq-text-card-title);
  line-height: var(--siq-text-card-title-lh);
  color: var(--siq-ink);
  font-variant-numeric: tabular-nums;
}

@media (forced-colors: active) {
  .r-answer {
    border: 1px solid CanvasText;
  }
  .r-answer.is-open {
    border-left-width: 4px;
  }
}
</style>
