<script setup>
import Icon from "./Icon.vue";

// The "Next time" tip bar: one actionable line on the notice background.
defineProps({
  label: { type: String, default: "Next time" },
  icon: { type: String, default: "target" },
});
</script>

<template>
  <p class="siq-tip">
    <Icon :name="icon" :size="16" class="siq-tip-icon" />
    <span><span class="siq-tip-label">{{ label }}</span> · <slot /></span>
  </p>
</template>

<style scoped>
.siq-tip {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin: 0;
  padding: var(--siq-space-3) var(--siq-space-4);
  border-radius: var(--siq-radius-control);
  background: var(--siq-notice);
  color: var(--siq-ink);
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  text-wrap: pretty;
}

.siq-tip-icon {
  margin-top: 2px;
  color: var(--siq-accent);
}

.siq-tip-label {
  font-weight: var(--siq-weight-medium);
  color: var(--siq-ink-2);
}
</style>
