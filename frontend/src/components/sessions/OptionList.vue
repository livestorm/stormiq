<script setup>
import Icon from "../ui/Icon.vue";

// One choice among a few, as a column of 40px buttons: the current one is
// pressed and ticked (icon and state, never colour alone). Used by the
// Sessions page's column filters and its event picker.
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
      @click="emit('choose', option.value)"
    >
      <Icon name="check" :size="16" class="ol-tick" />
      <span class="ol-text">{{ option.label }}</span>
    </button>
  </div>
</template>

<style scoped>
.ol {
  display: flex;
  flex-direction: column;
}

.ol-option {
  display: flex;
  align-items: center;
  gap: var(--siq-space-2);
  width: 100%;
  min-height: var(--siq-target);
  margin: 0;
  padding: 0 var(--siq-space-3) 0 var(--siq-space-2);
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

.ol-text {
  min-width: 0;
  overflow-wrap: anywhere;
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
