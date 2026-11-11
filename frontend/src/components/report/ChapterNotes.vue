<script setup>
import { computed, onBeforeUnmount, ref } from "vue";
import Card from "../ui/Card.vue";
import CardHeader from "../ui/CardHeader.vue";
import Icon from "../ui/Icon.vue";
import UiButton from "../ui/UiButton.vue";
import { emptyReason, noteRows, youtubeNote } from "../../followup/chapters";

// The Follow-up tab's "Chapters and show notes": the chapters the session
// analysis found, as lines for a replay page or a YouTube description, on
// the recording's clock (the server writes the text). No model call. The
// data comes from the view's `createChapters()`.
const props = defineProps({
  chapters: { type: Object, required: true },
});

const state = computed(() => props.chapters.state);
const body = computed(() => state.value.body);
const rows = computed(() => noteRows(body.value));

const copied = ref(false);
let timer = null;
async function copy() {
  try {
    await navigator.clipboard.writeText(body.value?.showNotes?.text || "");
    copied.value = true;
    clearTimeout(timer);
    timer = setTimeout(() => (copied.value = false), 2000);
  } catch {
    copied.value = false;
  }
}
onBeforeUnmount(() => clearTimeout(timer));
</script>

<template>
  <Card class="cn" :aria-busy="state.loading ? 'true' : 'false'">
    <CardHeader
      icon="list"
      title="Chapters and show notes"
      subtitle="For a replay page or a YouTube description. Times are the recording's."
      :level="2"
    />

    <p v-if="state.loading && !body" class="cn-muted cn-gap" role="status">Loading the chapters…</p>

    <div v-else-if="state.error && !body" class="cn-gap" role="alert">
      <p class="cn-text">{{ state.error }}</p>
      <div class="cn-row">
        <UiButton variant="secondary" small icon="refresh-cw" @click="chapters.reload()">Try again</UiButton>
      </div>
    </div>

    <template v-else-if="body">
      <p v-if="emptyReason(body)" class="cn-note cn-gap">
        <Icon name="info" :size="16" />
        <span>{{ emptyReason(body) }}</span>
      </p>

      <template v-else>
        <div class="cn-row">
          <UiButton variant="secondary" small :icon="copied ? 'check' : 'copy'" @click="copy">
            {{ copied ? "Copied" : "Copy" }}
          </UiButton>
          <span class="cn-muted">Found by AI in the transcript. Check the titles before you share them.</span>
        </div>
        <ol class="cn-list">
          <li v-for="(row, index) in rows" :key="index" class="cn-item">
            <span class="cn-time">{{ row.time }}</span>
            <span class="cn-title">{{ row.title }}</span>
          </li>
        </ol>
        <p v-if="youtubeNote(body)" class="cn-note">
          <Icon name="info" :size="16" />
          <span>{{ youtubeNote(body) }}</span>
        </p>
      </template>
    </template>
  </Card>
</template>

<style scoped>
.cn {
  min-width: 0;
}

.cn-gap {
  margin-top: var(--siq-space-4);
}

.cn-text,
.cn-muted,
.cn-note {
  margin: 0;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
}

.cn-text {
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  color: var(--siq-ink-2);
}

.cn-muted {
  color: var(--siq-muted);
}

.cn-note {
  display: flex;
  gap: var(--siq-space-2);
  align-items: flex-start;
  margin-top: var(--siq-space-4);
  color: var(--siq-ink-2);
}

.cn-note :deep(svg) {
  flex: none;
  margin-top: 2px;
  color: var(--siq-muted);
}

.cn-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--siq-space-2) var(--siq-space-3);
  margin-top: var(--siq-space-4);
}

.cn-list {
  margin: var(--siq-space-4) 0 0;
  padding: 0;
  list-style: none;
}

.cn-item {
  display: grid;
  grid-template-columns: 4.5rem minmax(0, 1fr);
  gap: var(--siq-space-3);
  padding: var(--siq-space-2) 0;
  box-shadow: inset 0 1px 0 var(--siq-hairline-row);
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
}

.cn-time {
  color: var(--siq-muted);
  font-variant-numeric: tabular-nums;
}

.cn-title {
  color: var(--siq-ink);
  overflow-wrap: anywhere;
}

@media (forced-colors: active) {
  .cn-item {
    border-top: 1px solid CanvasText;
  }
}
</style>
