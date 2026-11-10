<script setup>
import { computed, onMounted, ref } from "vue";
import UiButton from "../ui/UiButton.vue";
import { clockLabel } from "../../report/chartGeometry";

// The chat messages a count lists, by their `C` ids, so a reader can check
// what the analysis sorted: each message with its time on the session clock.
// The text comes from `GET /record/chat` (the report's `state.chat`), read
// once, when the first list opens; the list asks for it with `load`. No
// author is shown. A long list shows its first messages and "Show all".
const props = defineProps({
  ids: { type: Array, required: true },
  // The report's `state.chat`: {status, error, byId}.
  chat: { type: Object, required: true },
  label: { type: String, required: true },
  limit: { type: Number, default: 20 },
});
const emit = defineEmits(["load"]);

onMounted(() => {
  if (props.chat.status === "idle") emit("load");
});

const expanded = ref(false);
const messages = computed(() => props.ids.map((id) => props.chat.byId[id]).filter(Boolean));
const shown = computed(() => (expanded.value ? messages.value : messages.value.slice(0, props.limit)));
const long = computed(() => messages.value.length > props.limit);
const missing = computed(() => (props.chat.status === "ready" ? props.ids.length - messages.value.length : 0));

function time(message) {
  return message.t == null ? "—" : clockLabel(message.t);
}

function timeTitle(message) {
  if (message.t == null) return "No time on the session clock";
  return message.t < 0 ? "Before the start" : "Time since the start";
}
</script>

<template>
  <div class="r-messages">
    <p v-if="chat.status === 'loading' || chat.status === 'idle'" class="r-messages-note" role="status">Loading the messages…</p>
    <div v-else-if="chat.status === 'error'" class="r-messages-error" role="alert">
      <p class="r-messages-note">{{ chat.error }}</p>
      <UiButton variant="secondary" icon="refresh-cw" @click="emit('load')">Try again</UiButton>
    </div>
    <template v-else>
      <ol class="r-messages-list" :aria-label="label">
        <li v-for="message in shown" :key="message.id" class="r-message">
          <span class="r-message-time" :title="timeTitle(message)">{{ time(message) }}</span>
          <span class="r-message-text">{{ message.text || "(no text)" }}</span>
        </li>
      </ol>
      <p v-if="missing" class="r-messages-note">
        {{ missing === 1 ? "1 message is" : `${missing} messages are` }} no longer in the chat. Fetch the session again, then run the analysis.
      </p>
      <div v-if="long">
        <UiButton variant="ghost" :icon="expanded ? 'chevron-up' : 'chevron-down'" @click="expanded = !expanded">
          {{ expanded ? "Show fewer" : `Show all ${messages.length} messages` }}
        </UiButton>
      </div>
    </template>
  </div>
</template>

<style scoped>
.r-messages {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-2);
  padding: var(--siq-space-3) 0 var(--siq-space-4);
}

.r-messages-list {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.r-message {
  display: grid;
  grid-template-columns: 4.5rem minmax(0, 1fr);
  gap: var(--siq-space-3);
  max-width: 760px;
  padding-left: var(--siq-space-3);
  box-shadow: inset 2px 0 0 var(--siq-s1);
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-reading-lh);
}

.r-message-time {
  color: var(--siq-muted);
  font-size: var(--siq-text-small);
  font-variant-numeric: tabular-nums;
}

.r-message-text {
  color: var(--siq-ink);
  overflow-wrap: anywhere;
  white-space: pre-line;
}

.r-messages-note {
  margin: 0;
  max-width: 680px;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-muted);
}

.r-messages-error {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--siq-space-2);
}

@media (forced-colors: active) {
  .r-message {
    border-left: 2px solid CanvasText;
  }
}
</style>
