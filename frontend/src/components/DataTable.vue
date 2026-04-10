<script setup>
import { computed, ref } from "vue";

const props = defineProps({
  rows: {
    type: Array,
    default: () => [],
  },
  columnLabels: {
    type: Object,
    default: () => ({}),
  },
  columnWidths: {
    type: Object,
    default: () => ({}),
  },
  csvFilename: {
    type: String,
    default: "table.csv",
  },
  showToolbar: {
    type: Boolean,
    default: true,
  },
});

function prettifyColumn(column) {
  const value = String(column || "").trim();
  if (!value) return "";
  if (props.columnLabels[value]) return props.columnLabels[value];
  return value
    .replace(/_/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

const sortColumn = ref("");
const sortDirection = ref("asc");

const columns = computed(() => (props.rows.length ? Object.keys(props.rows[0]) : []));

const sortedRows = computed(() => {
  if (!sortColumn.value) return props.rows;

  const column = sortColumn.value;
  const direction = sortDirection.value === "asc" ? 1 : -1;

  return [...props.rows].sort((left, right) => {
    const leftValue = left?.[column];
    const rightValue = right?.[column];

    const leftNumber = Number(leftValue);
    const rightNumber = Number(rightValue);
    const bothNumeric = Number.isFinite(leftNumber) && Number.isFinite(rightNumber);
    if (bothNumeric) {
      return (leftNumber - rightNumber) * direction;
    }

    return String(leftValue ?? "").localeCompare(String(rightValue ?? ""), undefined, {
      numeric: true,
      sensitivity: "base",
    }) * direction;
  });
});

function toggleSort(column) {
  if (sortColumn.value === column) {
    sortDirection.value = sortDirection.value === "asc" ? "desc" : "asc";
    return;
  }
  sortColumn.value = column;
  sortDirection.value = "asc";
}

function downloadCsv() {
  if (!columns.value.length || !sortedRows.value.length) return;

  const escapeCell = (value) => {
    const text = String(value ?? "");
    if (/[",\n]/.test(text)) {
      return `"${text.replace(/"/g, '""')}"`;
    }
    return text;
  };

  const headerRow = columns.value.map((column) => escapeCell(prettifyColumn(column))).join(",");
  const dataRows = sortedRows.value.map((row) =>
    columns.value.map((column) => escapeCell(row?.[column])).join(",")
  );
  const csv = [headerRow, ...dataRows].join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = props.csvFilename;
  link.click();
  URL.revokeObjectURL(url);
}
</script>

<template>
  <div class="table-shell" v-if="rows.length">
    <div class="table-toolbar" v-if="showToolbar">
      <button class="table-icon-button" type="button" title="Download CSV" @click="downloadCsv">
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path
            d="M12 3v10.2l3.6-3.6 1.4 1.4-6 6-6-6 1.4-1.4 3.6 3.6V3H12zm-7 14h14v2H5v-2z"
            fill="currentColor"
          />
        </svg>
      </button>
    </div>
    <table class="data-table">
      <colgroup>
        <col
          v-for="column in columns"
          :key="column"
          :style="props.columnWidths[column] ? { width: props.columnWidths[column], minWidth: props.columnWidths[column] } : {}"
        />
      </colgroup>
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">
            <button class="table-sort-button" type="button" @click="toggleSort(column)">
              <span>{{ prettifyColumn(column) }}</span>
              <span class="table-sort-indicator" v-if="sortColumn === column">{{ sortDirection === "asc" ? "↑" : "↓" }}</span>
            </button>
          </th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(row, index) in sortedRows" :key="index">
          <td v-for="column in columns" :key="column">{{ row[column] }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
