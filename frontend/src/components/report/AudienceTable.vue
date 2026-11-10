<script setup>
import { computed, ref } from "vue";
import UiButton from "../ui/UiButton.vue";

// A table of an audience measure's rows, already worded by `report/audience.js`
// (labels and formatted cells; "—" for a value the server left empty). The
// first column is the row's label; a fold row ("Other", "No source") is
// muted, as it is a label and never a group to compare. Scrolls sideways on
// its own on a narrow screen, never the page. A long table (a multiple
// choice with many answers) shows its first rows and a "Show all" button.
const props = defineProps({
  caption: { type: String, required: true },
  labelHeading: { type: String, required: true },
  // [{ key, label }] for the value columns, in order.
  columns: { type: Array, required: true },
  rows: { type: Array, required: true },
  limit: { type: Number, default: 8 },
  // The rows' noun for the button: "Show all 31 answers".
  noun: { type: String, default: "rows" },
});

const expanded = ref(false);
// A fold row ("Other") stays visible: it is how the table adds up.
const long = computed(() => props.rows.length > props.limit + 1);
const shown = computed(() => {
  if (!long.value || expanded.value) return props.rows;
  const head = props.rows.slice(0, props.limit);
  const folds = props.rows.slice(props.limit).filter((row) => row.fold);
  return [...head, ...folds];
});
</script>

<template>
  <div class="r-table-wrap" tabindex="0" role="region" :aria-label="caption">
    <table class="r-table">
      <caption class="siq-sr-only">{{ caption }}</caption>
      <thead>
        <tr>
          <th scope="col">{{ labelHeading }}</th>
          <th v-for="column in columns" :key="column.key" scope="col">{{ column.label }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in shown" :key="row.key" :class="{ 'is-fold': row.fold }">
          <th scope="row">{{ row.label }}</th>
          <td v-for="column in columns" :key="column.key">{{ row[column.key] }}</td>
        </tr>
      </tbody>
    </table>
  </div>
  <div v-if="long" class="r-table-more">
    <UiButton variant="ghost" :icon="expanded ? 'chevron-up' : 'chevron-down'" @click="expanded = !expanded">
      {{ expanded ? "Show fewer" : `Show all ${rows.length} ${noun}` }}
    </UiButton>
  </div>
</template>

<style scoped>
.r-table-wrap {
  max-width: 100%;
  overflow-x: auto;
  border-radius: var(--siq-radius-inline);
}

.r-table-wrap:focus-visible {
  outline: 2px solid var(--siq-accent);
  outline-offset: 2px;
}

.r-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  font-variant-numeric: tabular-nums;
  color: var(--siq-ink);
}

.r-table th,
.r-table td {
  padding: var(--siq-space-2) var(--siq-space-3);
  text-align: right;
  white-space: nowrap;
  box-shadow: inset 0 -1px 0 var(--siq-hairline-row);
}

.r-table thead th {
  color: var(--siq-muted);
  font-weight: var(--siq-weight-medium);
  box-shadow: inset 0 -1px 0 var(--siq-hairline-head);
}

.r-table th[scope="row"],
.r-table thead th:first-child {
  padding-left: 0;
  text-align: left;
  font-weight: var(--siq-weight-regular);
  white-space: normal;
  min-width: 9rem;
  /* The label stays in view while a narrow screen scrolls the numbers. */
  position: sticky;
  left: 0;
  z-index: 1;
  background: var(--siq-card);
}

.r-table-more {
  margin-top: calc(-1 * var(--siq-space-2));
}

.r-table .is-fold th[scope="row"] {
  color: var(--siq-ink-2);
  font-style: italic;
}

@media (forced-colors: active) {
  .r-table th,
  .r-table td {
    border-bottom: 1px solid CanvasText;
  }
}
</style>
