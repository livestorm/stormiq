<script setup>
import { computed } from "vue";
import { RouterLink } from "vue-router";

// A session's tabs: Report · Transcript · Follow-up. Shared by the Report and
// the Transcript pages; Follow-up still opens the older Repurposing view.
const props = defineProps({
  sessionId: { type: String, required: true },
  current: {
    type: String,
    required: true,
    validator: (value) => ["report", "transcript", "follow-up"].includes(value),
  },
});

const tabs = computed(() => {
  const base = `/sessions/${encodeURIComponent(props.sessionId)}`;
  return [
    { key: "report", label: "Report", to: `${base}/report` },
    { key: "transcript", label: "Transcript", to: `${base}/transcript` },
    { key: "follow-up", label: "Follow-up", to: `${base}/content-repurposing` },
  ];
});
</script>

<template>
  <nav class="st-tabs" aria-label="Session sections">
    <RouterLink
      v-for="tab in tabs"
      :key="tab.key"
      :to="tab.to"
      class="st-tab siq-fx"
      :class="{ 'is-current': tab.key === current }"
      :aria-current="tab.key === current ? 'page' : undefined"
    >
      {{ tab.label }}
    </RouterLink>
  </nav>
</template>

<style scoped>
.st-tabs {
  display: flex;
  gap: var(--siq-space-2);
  overflow-x: auto;
  box-shadow: inset 0 -1px 0 var(--siq-hairline);
}

.st-tab {
  display: inline-flex;
  align-items: center;
  min-height: var(--siq-target);
  padding: 0 var(--siq-space-3);
  font-size: var(--siq-text-nav);
  line-height: var(--siq-text-nav-lh);
  font-weight: var(--siq-weight-medium);
  color: var(--siq-ink-2);
  text-decoration: none;
  white-space: nowrap;
  transition-property: color, box-shadow;
  transition-duration: var(--siq-duration);
  transition-timing-function: var(--siq-ease);
}

.st-tab:hover {
  color: var(--siq-ink);
}

.st-tab.is-current {
  color: var(--siq-ink);
  box-shadow: inset 0 -2px 0 var(--siq-accent);
}

.st-tab.siq-fx:focus-visible {
  outline-offset: -3px;
}

@media (forced-colors: active) {
  .st-tab.is-current {
    border-bottom: 2px solid CanvasText;
  }
}

@media (prefers-reduced-motion: reduce) {
  .st-tab {
    transition: none;
  }
}

@media print {
  .st-tabs {
    display: none !important;
  }
}
</style>
