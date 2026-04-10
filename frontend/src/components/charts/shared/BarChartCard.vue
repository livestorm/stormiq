<script setup>
import { computed } from "vue";

const props = defineProps({
  title: {
    type: String,
    default: "",
  },
  rows: {
    type: Array,
    default: () => [],
  },
  labelKey: {
    type: String,
    required: true,
  },
  valueKey: {
    type: String,
    required: true,
  },
  description: {
    type: String,
    default: "",
  },
});

const normalizedRows = computed(() => {
  const maxValue = Math.max(
    1,
    ...props.rows.map((row) => Number(row?.[props.valueKey]) || 0),
  );

  return props.rows.map((row) => {
    const value = Number(row?.[props.valueKey]) || 0;
    return {
      label: row?.[props.labelKey] || "Unknown",
      value,
      width: value > 0 ? `${(value / maxValue) * 100}%` : "0%",
    };
  });
});
</script>

<template>
  <section class="panel" v-if="rows.length">
    <div class="panel-heading">
      <h3>{{ title }}</h3>
      <p v-if="description">{{ description }}</p>
    </div>

    <div class="bar-chart-list">
      <div class="bar-chart-row" v-for="row in normalizedRows" :key="row.label">
        <div class="bar-chart-meta">
          <span class="bar-chart-label">{{ row.label }}</span>
          <strong class="bar-chart-value">{{ row.value }}</strong>
        </div>
        <div class="bar-chart-track" :title="`${row.label}: ${row.value}`">
          <div class="bar-chart-fill" :style="{ width: row.width }"></div>
        </div>
      </div>
    </div>
  </section>
</template>
