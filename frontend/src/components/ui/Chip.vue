<script setup>
import Icon from "./Icon.vue";

// A small, non-interactive pill. Tones:
//   neutral  outlined label ("Public", "Q&A", "Beta")
//   count    a count badge on surface-2 ("11 open")
//   accent   selected-blue fill, for key numbers in a summary
//   good / fair / low  status tints; pair them with an icon and a word
defineProps({
  tone: {
    type: String,
    default: "neutral",
    validator: (value) => ["neutral", "count", "accent", "good", "fair", "low"].includes(value),
  },
  icon: { type: String, default: "" },
});
</script>

<template>
  <span class="siq-chip" :class="`siq-chip-${tone}`">
    <Icon v-if="icon" :name="icon" :size="14" />
    <span class="siq-chip-text"><slot /></span>
  </span>
</template>

<style scoped>
.siq-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 100%;
  min-height: 24px;
  padding: 2px 10px;
  border-radius: var(--siq-radius-pill);
  font-size: var(--siq-text-caption);
  line-height: var(--siq-text-caption-lh);
  font-weight: var(--siq-weight-medium);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  box-sizing: border-box;
}

.siq-chip-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.siq-chip-neutral {
  color: var(--siq-ink-2);
  box-shadow: inset 0 0 0 1px var(--siq-hairline-head);
}

.siq-chip-count {
  color: var(--siq-ink);
  background: var(--siq-surface-2);
}

.siq-chip-accent {
  color: var(--siq-selected-ink);
  background: var(--siq-selected-bg);
}

.siq-chip-good {
  color: var(--siq-good);
  background: var(--siq-good-tint);
}

.siq-chip-fair {
  color: var(--siq-fair);
  background: var(--siq-fair-tint);
}

.siq-chip-low {
  color: var(--siq-low);
  background: var(--siq-low-tint);
}

/* Windows High Contrast drops backgrounds and box-shadows: keep the pill shape. */
@media (forced-colors: active) {
  .siq-chip {
    border: 1px solid CanvasText;
  }
}
</style>
