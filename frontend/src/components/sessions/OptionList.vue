<script setup>
import Icon from "../ui/Icon.vue";

// One choice among a few, as a column of 40px buttons: the current one is
// pressed and ticked (icon and state, never colour alone). Used by the
// Sessions page's column filters and its event picker. An option with a
// `count` shows it as a badge on the right, and its `title` (else `label`)
// on at most two lines, the whole title in its tooltip.
defineProps({
  options: { type: Array, required: true },
  modelValue: { type: [String, Number], default: "" },
  label: { type: String, required: true },
});
const emit = defineEmits(["choose"]);
</script>

<template>
  <div class="ol" role="group" :aria-label="label">
    <button
      v-for="option in options"
      :key="option.value"
      type="button"
      class="ol-option siq-fx"
      :aria-pressed="option.value === modelValue ? 'true' : 'false'"
      :aria-label="option.label"
      :title="option.title || option.label"
      @click="emit('choose', option.value)"
    >
      <Icon name="check" :size="16" class="ol-tick" />
      <span class="ol-text">{{ option.title || option.label }}</span>
      <span v-if="option.count !== undefined" class="ol-count" aria-hidden="true">{{ option.count }}</span>
    </button>
  </div>
</template>

<style scoped>
.ol {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.ol-option {
  display: flex;
  align-items: center;
  gap: var(--siq-space-2);
  width: 100%;
  min-height: var(--siq-target);
  margin: 0;
  padding: var(--siq-space-2) var(--siq-space-3) var(--siq-space-2) var(--siq-space-2);
  border: 0;
  border-radius: var(--siq-radius-inline);
  background: transparent;
  color: var(--siq-ink);
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition-property: background-color, color;
  transition-duration: var(--siq-duration);
  transition-timing-function: var(--siq-ease);
}

.ol-option:hover {
  background: var(--siq-card-hover);
}

.ol-option[aria-pressed="true"] {
  background: var(--siq-selected-bg);
  color: var(--siq-selected-ink);
  font-weight: var(--siq-weight-medium);
}

.ol-tick {
  visibility: hidden;
}

.ol-option[aria-pressed="true"] .ol-tick {
  visibility: visible;
}

.ol-tick {
  flex: none;
}

/* A long title wraps to two lines at most, then ends in "…". */
.ol-text {
  flex: 1;
  min-width: 0;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  overflow: hidden;
  overflow-wrap: anywhere;
  line-height: var(--siq-text-ui-lh);
}

.ol-count {
  flex: none;
  align-self: center;
  min-width: 1.5rem;
  padding: 0 var(--siq-space-2);
  border-radius: var(--siq-radius-pill);
  background: var(--siq-surface-2);
  color: var(--siq-muted);
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  font-variant-numeric: tabular-nums;
  text-align: center;
}

.ol-option.siq-fx:focus-visible {
  outline: 2px solid var(--siq-focus);
  outline-offset: -2px;
}

@media (forced-colors: active) {
  .ol-option[aria-pressed="true"] {
    border: 1px solid Highlight;
  }
}

@media (prefers-reduced-motion: reduce) {
  .ol-option {
    transition: none;
  }
}
</style>
