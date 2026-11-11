<script setup>
import { computed, ref, useId, watch } from "vue";
import { DATE_PRESETS, LENGTH_PRESETS, TRANSCRIPT_OPTIONS, parseMinimum } from "../../sessions/list";
import OptionList from "./OptionList.vue";

// The controls of one column's filter on the Sessions page (`column`: date,
// session, attendees, transcript or added), shown in that column's popover
// and in the phone's "Sort & filter" sheet. It sets one key of the page's
// filters (`set`), and says `done` after a choice, which closes a popover.
const props = defineProps({
  column: { type: String, required: true },
  filters: { type: Object, required: true },
  teammates: { type: Array, default: () => [] },
  // Put the cursor in the "at least" field on open (not in the phone's sheet).
  autofocus: { type: Boolean, default: true },
});
const emit = defineEmits(["set", "done"]);

const CHOICES = {
  date: { key: "date", label: "Session date", options: [{ value: "", label: "Any time" }, ...DATE_PRESETS] },
  session: { key: "length", label: "Length", options: [{ value: "", label: "Any length" }, ...LENGTH_PRESETS] },
  transcript: { key: "transcript", label: "Transcript", options: [{ value: "", label: "Either" }, ...TRANSCRIPT_OPTIONS] },
};

const choice = computed(() => {
  if (props.column === "added") {
    return { key: "addedBy", label: "Added by", options: [{ value: "", label: "Anyone" }, ...props.teammates] };
  }
  return CHOICES[props.column] || null;
});

function choose(value) {
  emit("set", choice.value.key, value);
  emit("done");
}

// "At least n attendees": set as it's typed; Enter closes.
const minimum = ref(props.filters.attendeesMin ?? "");
watch(
  () => props.filters.attendeesMin,
  (value) => {
    if (parseMinimum(value) !== parseMinimum(minimum.value)) minimum.value = value ?? "";
  },
);
const fieldId = `cf-min-${useId()}`;
function onMinimum(event) {
  minimum.value = event.target.value;
  emit("set", "attendeesMin", parseMinimum(minimum.value));
}
</script>

<template>
  <div class="cf">
    <OptionList
      v-if="choice"
      :options="choice.options"
      :model-value="filters[choice.key] || ''"
      :label="choice.label"
      @choose="choose"
    />
    <form v-else-if="column === 'attendees'" class="cf-min" @submit.prevent="emit('done')">
      <label :for="fieldId" class="cf-min-label">At least</label>
      <input
        :id="fieldId"
        class="cf-input"
        type="number"
        inputmode="numeric"
        min="1"
        step="1"
        placeholder="Any"
        :data-autofocus="autofocus ? '' : undefined"
        :value="minimum"
        @input="onMinimum"
      />
      <span class="cf-min-label" aria-hidden="true">attendees</span>
      <span class="cf-hint">Livestorm's count, your team included.</span>
    </form>
  </div>
</template>

<style scoped>
.cf-min {
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: center;
  gap: var(--siq-space-2);
  padding: var(--siq-space-2);
}

.cf-min-label {
  color: var(--siq-ink-2);
}

.cf-hint {
  grid-column: 1 / -1;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-muted);
}

.cf-input {
  width: 100%;
  min-width: 5rem;
  height: var(--siq-target);
  margin: 0;
  padding: 0 var(--siq-space-3);
  border: 1px solid var(--siq-border);
  border-radius: var(--siq-radius-control);
  background: var(--siq-input);
  color: var(--siq-ink);
  font: inherit;
  font-variant-numeric: tabular-nums;
  box-sizing: border-box;
}

.cf-input:hover {
  border-color: var(--siq-border-hover);
}

.cf-input:focus-visible {
  outline: 2px solid var(--siq-focus);
  outline-offset: 1px;
}

.cf-input::placeholder {
  color: var(--siq-muted);
}
</style>
