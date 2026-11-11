<script setup>
import { computed } from "vue";
import Icon from "../ui/Icon.vue";
import UiButton from "../ui/UiButton.vue";
import { momentsView } from "../../report/moments";

// Under the Pulse: each key moment told, numbered as the chart's pins. What
// kind of reaction it was, what was being said, what people wrote and the
// questions asked then (`GET /record/moments`, worded by
// `src/report/moments.js`). Attendees' and speakers' words, rendered as
// text, never as HTML.

const props = defineProps({
  // The view's `createMoments().state`: {loading, error, body}.
  moments: { type: Object, required: true },
});
const emit = defineEmits(["retry"]);

const view = computed(() => momentsView(props.moments.body));
</script>

<template>
  <section class="km" aria-labelledby="km-title">
    <h3 id="km-title" class="km-title">What happened at each moment</h3>

    <p v-if="moments.loading && !view" class="km-note" role="status">Loading the moments…</p>
    <div v-else-if="moments.error && !view" class="km-error" role="alert">
      <p class="km-note">{{ moments.error }}</p>
      <UiButton variant="secondary" small icon="refresh-cw" @click="emit('retry')">Try again</UiButton>
    </div>

    <template v-else-if="view">
      <p v-if="view.intro" class="km-note">{{ view.intro }}</p>
      <p v-if="view.empty" class="km-note">{{ view.empty }}</p>

      <ol v-if="view.moments.length" class="km-list">
        <li v-for="moment in view.moments" :id="`key-moment-${moment.n}`" :key="moment.n" class="km-item">
          <div class="km-head">
            <span class="km-n" aria-hidden="true">{{ moment.n }}</span>
            <span class="km-when">{{ moment.when }}</span>
            <span class="km-kind"><Icon :name="moment.icon" :size="14" />{{ moment.kind }}</span>
            <span class="km-facts">{{ moment.facts }}</span>
          </div>
          <p class="km-takeaway">{{ moment.takeaway }}</p>

          <div v-if="moment.prompt" class="km-part">
            <p class="km-label">At {{ moment.prompt.time }}, the stage asked the room:</p>
            <blockquote class="km-quote">“{{ moment.prompt.text }}”</blockquote>
          </div>

          <div v-if="moment.said" class="km-part">
            <p class="km-label">{{ moment.said.label }}</p>
            <blockquote class="km-quote">“{{ moment.said.text }}”</blockquote>
          </div>

          <div v-if="moment.written.length" class="km-part">
            <p class="km-label">What people wrote</p>
            <ul class="km-lines">
              <li v-for="item in moment.written" :key="item.id" class="km-line">
                <span class="km-time">{{ item.time }}</span>
                <span class="km-author">{{ item.author }}</span>
                <span class="km-text">{{ item.text }}</span>
              </li>
            </ul>
          </div>

          <div v-if="moment.asked.length" class="km-part">
            <p class="km-label"><Icon name="help-circle" :size="14" />Questions asked then</p>
            <ul class="km-lines">
              <li v-for="item in moment.asked" :key="item.id" class="km-line">
                <span class="km-time">{{ item.time }}</span>
                <span class="km-author">{{ item.author }}</span>
                <span class="km-text">{{ item.text }}</span>
              </li>
            </ul>
          </div>

          <p v-if="moment.themes" class="km-note">{{ moment.themes }}</p>
        </li>
      </ol>
    </template>
  </section>
</template>

<style scoped>
.km {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-3);
  margin-top: var(--siq-space-5);
  padding-top: var(--siq-space-5);
  box-shadow: inset 0 1px 0 var(--siq-hairline-row);
}

.km-title {
  margin: 0;
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  font-weight: var(--siq-weight-medium);
  color: var(--siq-ink);
}

.km-note {
  margin: 0;
  max-width: 720px;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-muted);
}

.km-error {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--siq-space-2);
}

.km-list {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-3);
  margin: 0;
  padding: 0;
  list-style: none;
}

.km-item {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-2);
  padding: var(--siq-space-4);
  border-radius: var(--siq-radius-control);
  box-shadow: inset 0 0 0 1px var(--siq-hairline);
}

.km-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--siq-space-2) var(--siq-space-3);
}

/* The chart's pin, repeated: the same number in the same colour. */
.km-n {
  display: inline-grid;
  place-items: center;
  width: 22px;
  height: 22px;
  border-radius: var(--siq-radius-pill);
  background: var(--siq-accent);
  color: var(--siq-page);
  font-size: var(--siq-text-caption);
  line-height: 1;
  font-weight: var(--siq-weight-medium);
  font-variant-numeric: tabular-nums;
}

.km-when {
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  font-weight: var(--siq-weight-medium);
  color: var(--siq-ink);
  font-variant-numeric: tabular-nums;
}

.km-kind {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 0 var(--siq-space-2);
  border-radius: var(--siq-radius-pill);
  background: var(--siq-input);
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-ink-2);
}

.km-facts {
  margin-left: auto;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-muted);
  font-variant-numeric: tabular-nums;
}

.km-takeaway {
  margin: 0;
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  color: var(--siq-ink-2);
}

.km-part {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.km-label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin: 0;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-muted);
}

.km-quote {
  margin: 0;
  padding-left: var(--siq-space-3);
  box-shadow: inset 2px 0 0 var(--siq-hairline);
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  font-style: italic;
  color: var(--siq-ink-2);
  overflow-wrap: anywhere;
}

.km-lines {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.km-line {
  display: flex;
  flex-wrap: wrap;
  gap: 0 var(--siq-space-2);
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
}

.km-time {
  color: var(--siq-muted);
  font-variant-numeric: tabular-nums;
}

.km-author {
  font-weight: var(--siq-weight-medium);
  color: var(--siq-ink);
}

.km-text {
  flex: 1 1 16rem;
  min-width: 0;
  color: var(--siq-ink-2);
  overflow-wrap: anywhere;
}

@media (forced-colors: active) {
  .km-item {
    border: 1px solid CanvasText;
  }
  .km-quote {
    border-left: 2px solid CanvasText;
  }
}
</style>
