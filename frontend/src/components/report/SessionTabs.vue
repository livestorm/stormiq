<script setup>
import { computed } from "vue";
import { RouterLink } from "vue-router";
import Icon from "../ui/Icon.vue";

// A session's navigation bar, the first thing on each of its pages (Report,
// Transcript, Follow-up) and pinned to the top of the window: "Sessions",
// the session's title on the left, its three pages on the right. The same
// component in the same place on every page, so moving between them never
// moves the bar. On a phone the title gives way to the tabs.
const props = defineProps({
  sessionId: { type: String, required: true },
  current: {
    type: String,
    required: true,
    validator: (value) => ["report", "transcript", "follow-up"].includes(value),
  },
  // The session's title, or "" while it loads.
  title: { type: String, default: "" },
});

const tabs = computed(() => {
  const base = `/sessions/${encodeURIComponent(props.sessionId)}`;
  return [
    { key: "report", label: "Report", icon: "star", to: `${base}/report` },
    { key: "transcript", label: "Transcript", icon: "file-text", to: `${base}/transcript` },
    { key: "follow-up", label: "Follow-up", icon: "share-2", to: `${base}/follow-up` },
  ];
});
</script>

<template>
  <div class="st-bar">
    <nav class="st-crumbs" aria-label="Breadcrumb">
      <RouterLink class="st-back siq-fx" to="/sessions">
        <Icon name="chevron-left" :size="16" />
        <span>Sessions</span>
      </RouterLink>
      <span v-if="title" class="st-title" :title="title">{{ title }}</span>
    </nav>
    <nav class="st-tabs" aria-label="Session pages">
      <RouterLink
        v-for="tab in tabs"
        :key="tab.key"
        :to="tab.to"
        class="st-tab siq-fx"
        :class="{ 'is-current': tab.key === current }"
        :aria-current="tab.key === current ? 'page' : undefined"
      >
        <Icon :name="tab.icon" :size="16" />
        <span>{{ tab.label }}</span>
      </RouterLink>
    </nav>
  </div>
</template>

<style scoped>
.st-bar {
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--siq-space-3);
  min-width: 0;
  padding: var(--siq-space-2) 0;
  background: var(--siq-page);
  box-shadow: 0 1px 0 var(--siq-hairline);
}

.st-crumbs {
  display: flex;
  align-items: center;
  gap: var(--siq-space-2);
  min-width: 0;
}

.st-back {
  display: inline-flex;
  flex: none;
  align-items: center;
  gap: 2px;
  min-height: var(--siq-target);
  padding: 0 var(--siq-space-2) 0 0;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-muted);
  text-decoration: none;
}

.st-back:hover {
  color: var(--siq-ink);
}

.st-title {
  min-width: 0;
  overflow: hidden;
  padding-left: var(--siq-space-3);
  box-shadow: inset 1px 0 0 var(--siq-hairline);
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  font-weight: var(--siq-weight-medium);
  color: var(--siq-ink);
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* A segmented control: the three pages, the current one filled. */
.st-tabs {
  display: inline-flex;
  flex: none;
  padding: 2px;
  border: 1px solid var(--siq-border);
  border-radius: var(--siq-radius-control);
  background: var(--siq-input);
}

.st-tab {
  display: inline-flex;
  align-items: center;
  gap: var(--siq-space-2);
  min-height: calc(var(--siq-target) - 6px);
  padding: 0 var(--siq-space-3);
  border-radius: calc(var(--siq-radius-control) - 2px);
  font-size: var(--siq-text-nav);
  line-height: var(--siq-text-nav-lh);
  font-weight: var(--siq-weight-medium);
  color: var(--siq-ink-2);
  text-decoration: none;
  white-space: nowrap;
  transition-property: color, background-color;
  transition-duration: var(--siq-duration);
  transition-timing-function: var(--siq-ease);
}

.st-tab:hover {
  color: var(--siq-ink);
}

.st-tab.is-current {
  background: var(--siq-selected-bg);
  color: var(--siq-selected-ink);
}

.st-tab.siq-fx:focus-visible,
.st-back.siq-fx:focus-visible {
  outline-offset: -3px;
}

@media (max-width: 640px) {
  .st-title {
    display: none;
  }
  .st-tab {
    padding: 0 var(--siq-space-2);
  }
}

@media (max-width: 380px) {
  .st-tab :deep(svg) {
    display: none;
  }
}

@media (forced-colors: active) {
  .st-bar {
    border-bottom: 1px solid CanvasText;
  }
  .st-tab.is-current {
    outline: 2px solid CanvasText;
  }
}

@media (prefers-reduced-motion: reduce) {
  .st-tab {
    transition: none;
  }
}

@media print {
  .st-bar {
    display: none !important;
  }
}
</style>
