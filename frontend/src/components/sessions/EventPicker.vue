<script setup>
import { computed, ref, useId } from "vue";
import { searchEvents } from "../../sessions/list";
import Icon from "../ui/Icon.vue";
import FloatingPanel from "./FloatingPanel.vue";
import OptionList from "./OptionList.vue";

// The Sessions page's event filter, as one compact button in the toolbar
// ("All events ▾", or the chosen event's title) that opens a short list,
// with its own search once there are more than a handful of events.
// `events` is `eventOptions()`'s list.
const props = defineProps({
  events: { type: Array, required: true },
  modelValue: { type: String, default: "" },
});
const emit = defineEmits(["update:modelValue"]);

const SEARCH_FROM = 7;
const open = ref(false);
const button = ref(null);
const query = ref("");
const searchId = `ep-search-${useId()}`;

const current = computed(() => props.events.find((option) => option.value === props.modelValue) || null);
const options = computed(() => [
  ...(query.value.trim() ? [] : [{ value: "", label: `All events (${props.events.reduce((sum, e) => sum + e.count, 0)})` }]),
  ...searchEvents(props.events, query.value),
]);

function toggle() {
  open.value = !open.value;
  if (open.value) query.value = "";
}

function choose(value) {
  emit("update:modelValue", value);
  open.value = false;
}
</script>

<template>
  <button
    ref="button"
    type="button"
    class="ep-button siq-fx"
    :class="{ 'ep-on': current }"
    aria-haspopup="dialog"
    :aria-expanded="open ? 'true' : 'false'"
    @click="toggle"
  >
    <Icon name="calendar" :size="16" class="ep-icon" />
    <span class="siq-sr-only">Event: </span>
    <span class="ep-label">{{ current ? current.title : "All events" }}</span>
    <Icon name="chevron-down" :size="16" class="ep-icon" />
  </button>
  <FloatingPanel :open="open" :anchor="button" label="Choose an event" @close="open = false">
    <div v-if="events.length >= SEARCH_FROM" class="ep-search">
      <label :for="searchId" class="siq-sr-only">Search events</label>
      <Icon name="search" :size="16" class="ep-search-icon" />
      <input
        :id="searchId"
        v-model="query"
        class="ep-input"
        type="search"
        placeholder="Search events"
        autocomplete="off"
        data-autofocus
      />
    </div>
    <OptionList v-if="options.length" :options="options" :model-value="modelValue" label="Events" @choose="choose" />
    <p v-else class="ep-none">No event matches.</p>
  </FloatingPanel>
</template>

<style scoped>
.ep-button {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: var(--siq-space-2);
  max-width: 16rem;
  height: var(--siq-target);
  margin: 0;
  padding: 0 var(--siq-space-3);
  border: 1px solid var(--siq-border);
  border-radius: var(--siq-radius-control);
  background: var(--siq-input);
  color: var(--siq-ink);
  font: inherit;
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  cursor: pointer;
  transition-property: border-color, background-color;
  transition-duration: var(--siq-duration);
  transition-timing-function: var(--siq-ease);
}

.ep-button:hover {
  border-color: var(--siq-border-hover);
}

.ep-button.ep-on {
  border-color: var(--siq-accent);
  background: var(--siq-selected-bg);
  color: var(--siq-selected-ink);
}

.ep-icon {
  color: currentColor;
  opacity: 0.8;
}

.ep-label {
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.ep-search {
  position: relative;
  display: flex;
  align-items: center;
  margin-bottom: var(--siq-space-2);
}

.ep-search-icon {
  position: absolute;
  left: var(--siq-space-3);
  color: var(--siq-muted);
  pointer-events: none;
}

.ep-input {
  width: 100%;
  height: var(--siq-target);
  margin: 0;
  padding: 0 var(--siq-space-3) 0 36px;
  border: 1px solid var(--siq-border);
  border-radius: var(--siq-radius-control);
  background: var(--siq-input);
  color: var(--siq-ink);
  font: inherit;
  box-sizing: border-box;
}

.ep-input:focus-visible {
  outline: 2px solid var(--siq-focus);
  outline-offset: 1px;
}

.ep-input::placeholder {
  color: var(--siq-muted);
}

.ep-none {
  margin: 0;
  padding: var(--siq-space-2) var(--siq-space-3);
  color: var(--siq-muted);
}

@media (forced-colors: active) {
  .ep-button.ep-on {
    border-color: Highlight;
  }
}

@media (prefers-reduced-motion: reduce) {
  .ep-button {
    transition: none;
  }
}
</style>
