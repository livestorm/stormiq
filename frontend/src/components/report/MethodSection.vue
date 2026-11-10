<script setup>
import { computed } from "vue";
import Chip from "../ui/Chip.vue";
import Collapsible from "../ui/Collapsible.vue";
import AudienceBlock from "./AudienceBlock.vue";
import { methodRows, methodSummary, notShown } from "../../report/method";

// The Report's last card, closed by default: "Method and data quality".
// What the numbers rest on (the transcript, the clock, the people list, who
// wrote, the chat sorting), each with a status chip (icon and word, never
// colour alone), then "Not shown, and why": every number the page hides,
// with the server's reason. `report/method.js` words them.

const props = defineProps({
  // The record route's whole response: {meta, record}.
  body: { type: Object, required: true },
});

const TONE_ICONS = { good: "check", fair: "info", low: "info", neutral: "" };

const summary = computed(() => methodSummary(props.body));
const rows = computed(() => methodRows(props.body));
const hidden = computed(() => notShown(props.body?.record?.measures || {}));
</script>

<template>
  <Collapsible title="Method and data quality" icon="settings" :takeaway="summary.takeaway" :chip="summary.chip">
    <AudienceBlock icon="shield" title="What the numbers rest on">
      <dl class="r-method">
        <div v-for="row in rows" :key="row.key" class="r-method-row">
          <dt class="r-method-label">{{ row.label }}</dt>
          <dd class="r-method-value">
            <Chip :tone="row.tone" :icon="TONE_ICONS[row.tone] || ''">{{ row.value }}</Chip>
            <span v-for="detail in row.details" :key="detail" class="r-method-detail">{{ detail }}</span>
          </dd>
        </div>
      </dl>
    </AudienceBlock>

    <AudienceBlock icon="info" title="Not shown, and why">
      <ul v-if="hidden.length" class="r-hidden">
        <li v-for="item in hidden" :key="item.id" class="r-hidden-row">
          <span class="r-hidden-label">{{ item.label }}</span>
          <span class="r-hidden-why">{{ item.why }}</span>
        </li>
      </ul>
      <p v-else class="r-note">Every number on this page is shown.</p>
      <p class="r-note">A number is hidden rather than shown on too little data: each one has its minimum.</p>
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

.r-method,
.r-hidden {
  display: flex;
  flex-direction: column;
  margin: 0;
  padding: 0;
  list-style: none;
  max-width: 880px;
}

.r-method-row,
.r-hidden-row {
  display: grid;
  grid-template-columns: minmax(8rem, 12rem) minmax(0, 1fr);
  gap: var(--siq-space-4);
  padding: var(--siq-space-3) 0;
  box-shadow: inset 0 -1px 0 var(--siq-hairline-row);
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-reading-lh);
}

.r-method-row:last-child,
.r-hidden-row:last-child {
  box-shadow: none;
}

.r-method-label,
.r-hidden-label {
  color: var(--siq-ink);
  font-weight: var(--siq-weight-medium);
}

.r-method-value {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--siq-space-1);
  margin: 0;
}

.r-method-detail,
.r-hidden-why {
  color: var(--siq-ink-2);
}

@media (max-width: 640px) {
  .r-method-row,
  .r-hidden-row {
    grid-template-columns: minmax(0, 1fr);
    gap: var(--siq-space-1);
  }
}

@media (forced-colors: active) {
  .r-method-row,
  .r-hidden-row {
    border-bottom: 1px solid CanvasText;
  }
  .r-method-row:last-child,
  .r-hidden-row:last-child {
    border-bottom: none;
  }
}
</style>
