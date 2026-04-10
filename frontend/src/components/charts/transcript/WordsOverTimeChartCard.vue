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
});

const chartWidth = 860;
const chartHeight = 300;
const margin = { top: 18, right: 18, bottom: 44, left: 48 };

const normalizedRows = computed(() => {
  const plotWidth = chartWidth - margin.left - margin.right;
  const plotHeight = chartHeight - margin.top - margin.bottom;
  const maxIndex = Math.max(1, props.rows.length - 1);
  const maxWords = Math.max(1, ...props.rows.map((row) => Number(row?.word_count) || 0));

  return props.rows.map((row, index) => {
    const wordCount = Number(row?.word_count) || 0;
    const x = margin.left + (index / maxIndex) * plotWidth;
    const y = margin.top + (plotHeight - (wordCount / maxWords) * plotHeight);
    return {
      x,
      y,
      label: String(row?.minute_label || row?.time_label || "Unknown"),
      wordCount,
    };
  });
});

const linePath = computed(() => {
  if (!normalizedRows.value.length) return "";
  return normalizedRows.value
    .map((row, index) => `${index === 0 ? "M" : "L"} ${row.x.toFixed(2)} ${row.y.toFixed(2)}`)
    .join(" ");
});

const xTicks = computed(() => {
  const tickCount = Math.min(6, Math.max(2, props.rows.length));
  if (!props.rows.length) return [];
  return Array.from({ length: tickCount }, (_, index) => {
    const rowIndex = Math.min(
      props.rows.length - 1,
      Math.round((index / Math.max(tickCount - 1, 1)) * (props.rows.length - 1)),
    );
    const plotWidth = chartWidth - margin.left - margin.right;
    const x = margin.left + (rowIndex / Math.max(props.rows.length - 1, 1)) * plotWidth;
    const row = props.rows[rowIndex] || {};
    return {
      x,
      value: String(row.minute_label || row.time_label || ""),
    };
  });
});

const yTicks = computed(() => {
  const plotHeight = chartHeight - margin.top - margin.bottom;
  const maxWords = Math.max(1, ...props.rows.map((row) => Number(row?.word_count) || 0));
  const tickCount = 4;
  return Array.from({ length: tickCount + 1 }, (_, index) => {
    const value = Math.round((maxWords / tickCount) * (tickCount - index));
    const y = margin.top + (plotHeight / tickCount) * index;
    return { value, y };
  });
});
</script>

<template>
  <section class="panel" v-if="rows.length">
    <div class="panel-heading">
      <h3>{{ title }}</h3>
    </div>

    <div class="svg-chart-shell">
      <svg :viewBox="`0 0 ${chartWidth} ${chartHeight}`" class="svg-chart" role="img" :aria-label="title">
        <defs>
          <linearGradient id="words-line-gradient" x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" stop-color="#8eddf0" />
            <stop offset="55%" stop-color="#6ec5d9" />
            <stop offset="100%" stop-color="#ffffff" />
          </linearGradient>
        </defs>

        <g class="svg-grid">
          <line
            v-for="tick in yTicks"
            :key="`words-grid-${tick.value}-${tick.y}`"
            :x1="margin.left"
            :x2="chartWidth - margin.right"
            :y1="tick.y"
            :y2="tick.y"
          />
        </g>

        <g class="svg-axis-labels">
          <text
            v-for="tick in yTicks"
            :key="`words-y-${tick.value}-${tick.y}`"
            :x="margin.left - 10"
            :y="tick.y + 4"
            text-anchor="end"
          >
            {{ tick.value }}
          </text>
          <text
            v-for="(tick, index) in xTicks"
            :key="`words-x-${index}`"
            :x="tick.x"
            :y="chartHeight - 12"
            text-anchor="middle"
          >
            {{ tick.value }}
          </text>
          <text class="svg-axis-title" :x="18" :y="chartHeight / 2" text-anchor="middle" transform="rotate(-90, 18, 150)">
            Words
          </text>
          <text class="svg-axis-title" :x="chartWidth / 2" :y="chartHeight - 2" text-anchor="middle">
            Time
          </text>
        </g>

        <path v-if="linePath" class="words-chart-line" :d="linePath" />
        <circle
          v-for="(row, index) in normalizedRows"
          :key="`${row.label}-${row.wordCount}-${index}`"
          class="words-chart-point"
          :cx="row.x"
          :cy="row.y"
          r="3"
        >
          <title>{{ `${row.wordCount} words at ${row.label}` }}</title>
        </circle>
      </svg>
    </div>
  </section>
</template>
