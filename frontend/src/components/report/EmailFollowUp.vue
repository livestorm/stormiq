<script setup>
import { computed, ref } from "vue";
import Icon from "../ui/Icon.vue";

// The Follow-up email's tabs: Attended, Not attended and Generic, each one
// output of its own (`followup/outputs.js` `tabItems`), with the line that
// says who it is for. Picking a tab only changes `?output=`: the page shows
// that output's text, button, loader and versions below, as for any output.
const props = defineProps({
  // `tabItems(...)`: {key, label, purpose, current, ready, writing}.
  tabs: { type: Array, required: true },
});
const emit = defineEmits(["pick"]);

const current = computed(() => props.tabs.find((tab) => tab.current) || props.tabs[0] || null);
const list = ref(null);

// Arrow keys, Home and End move between the tabs and pick one.
function onKey(event, index) {
  const last = props.tabs.length - 1;
  const next = {
    ArrowRight: index === last ? 0 : index + 1,
    ArrowLeft: index === 0 ? last : index - 1,
    Home: 0,
    End: last,
  }[event.key];
  if (next === undefined) return;
  event.preventDefault();
  list.value?.querySelectorAll("[role=tab]")[next]?.focus();
  emit("pick", props.tabs[next].key);
}
</script>

<template>
  <div class="ef">
    <div ref="list" class="ef-tabs" role="tablist" aria-label="Who the email is for">
      <button
        v-for="(tab, index) in tabs"
        :id="`ef-tab-${tab.key}`"
        :key="tab.key"
        type="button"
        role="tab"
        class="ef-tab siq-fx"
        :class="{ 'is-current': tab.current }"
        :aria-selected="tab.current ? 'true' : 'false'"
        :tabindex="tab.current ? 0 : -1"
        @click="emit('pick', tab.key)"
        @keydown="onKey($event, index)"
      >
        <span>{{ tab.label }}</span>
        <span v-if="tab.writing" class="ef-state">
          <span class="ef-spinner" aria-hidden="true" /><span class="siq-sr-only">(writing)</span>
        </span>
        <span v-else-if="tab.ready" class="ef-state ef-ready">
          <Icon name="check" :size="14" /><span class="siq-sr-only">(ready)</span>
        </span>
      </button>
    </div>
    <p v-if="current?.purpose" class="ef-purpose">{{ current.purpose }}</p>
  </div>
</template>

<style scoped>
.ef {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-3);
  position: relative;
  margin-bottom: var(--siq-space-4);
}

.ef-tabs {
  display: flex;
  gap: var(--siq-space-1);
  overflow-x: auto;
  scrollbar-width: thin;
  box-shadow: inset 0 -1px 0 var(--siq-hairline);
}

.ef-tab {
  display: inline-flex;
  flex: none;
  align-items: center;
  gap: var(--siq-space-2);
  min-height: var(--siq-target);
  margin: 0;
  padding: 0 var(--siq-space-3);
  border: 0;
  border-radius: var(--siq-radius-control) var(--siq-radius-control) 0 0;
  background: transparent;
  color: var(--siq-ink-2);
  font: inherit;
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  font-weight: var(--siq-weight-medium);
  white-space: nowrap;
  cursor: pointer;
  transition-property: color, background-color, box-shadow;
  transition-duration: var(--siq-duration);
  transition-timing-function: var(--siq-ease);
}

.ef-tab:hover {
  background: var(--siq-card-hover);
  color: var(--siq-ink);
}

.ef-tab.is-current {
  color: var(--siq-ink);
  box-shadow: inset 0 -2px 0 var(--siq-accent);
}

.ef-tab.siq-fx:focus-visible {
  outline-offset: -3px;
}

.ef-state {
  display: inline-flex;
  align-items: center;
  color: var(--siq-muted);
}

.ef-ready {
  color: var(--siq-good);
}

.ef-spinner {
  width: 12px;
  height: 12px;
  border: 2px solid var(--siq-border);
  border-top-color: var(--siq-accent);
  border-radius: var(--siq-radius-pill);
  animation: ef-spin 0.8s linear infinite;
}

.ef-purpose {
  margin: 0;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-ink-2);
}

@keyframes ef-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .ef-tab {
    transition: none;
  }
  .ef-spinner {
    animation: none;
  }
}

@media (forced-colors: active) {
  .ef-tab.is-current {
    border-bottom: 2px solid CanvasText;
  }
}

@media print {
  .ef-tabs {
    display: none;
  }
}
</style>
