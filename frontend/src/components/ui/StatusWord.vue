<script setup>
import { computed } from "vue";
import Icon from "./Icon.vue";

// How sure a number is, always as icon + word (never colour alone):
// Measured (counted from the data), Estimated (derived or inferred),
// Needs your check (StormIQ isn't sure; the user confirms).
const STATUSES = {
  measured: { icon: "check", word: "Measured" },
  estimated: { icon: "info", word: "Estimated" },
  check: { icon: "shield", word: "Needs your check" },
};

const props = defineProps({
  status: {
    type: String,
    default: "measured",
    validator: (value) => ["measured", "estimated", "check"].includes(value),
  },
  // Overrides the word (keep it short); the icon and colour stay.
  label: { type: String, default: "" },
});

const meta = computed(() => STATUSES[props.status] || STATUSES.measured);
</script>

<template>
  <span class="siq-status" :class="`siq-status-${status}`">
    <Icon :name="meta.icon" :size="14" />
    <span>{{ label || meta.word }}</span>
  </span>
</template>

<style scoped>
.siq-status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  font-weight: var(--siq-weight-medium);
  white-space: nowrap;
}

.siq-status-measured {
  color: var(--siq-good);
}

.siq-status-estimated {
  color: var(--siq-muted);
}

.siq-status-check {
  color: var(--siq-fair);
  background: var(--siq-fair-tint);
  border-radius: var(--siq-radius-pill);
  padding: 2px 10px 2px 8px;
}
</style>
