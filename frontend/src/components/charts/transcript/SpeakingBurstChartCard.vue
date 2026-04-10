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
  const maxDuration = Math.max(1, ...props.rows.map((row) => Number(row?.duration_seconds) || 0));
  const barWidth = props.rows.length ? Math.max((plotWidth / props.rows.length) * 0.72, 3) : 0;

  return props.rows.map((row, index) => {
    const duration = Number(row?.duration_seconds) || 0;
    const x = margin.left + (index / maxIndex) * plotWidth - barWidth / 2;
    const height = maxDuration > 0 ? (duration / maxDuration) * plotHeight : 0;
    const y = margin.top + (plotHeight - height);
    return {
      label: String(row?.start_label || "Unknown"),
      duration,
      x,
      y,
      width: barWidth,
      height,
    };
  });
});

const xTicks = computed(() => {
  const tickCount = Math.min(8, Math.max(2, props.rows.length));
  if (!props.rows.length) return [];
  return Array.from({ length: tickCount }, (_, index) => {
    const rowIndex = Math.min(
      props.rows.length - 1,
      Math.round((index / Math.max(tickCount - 1, 1)) * (props.rows.length - 1)),
    );
    const row = normalizedRows.value[rowIndex];
    return {
      x: row ? row.x + row.width / 2 : margin.left,
      value: props.rows[rowIndex]?.start_label || "",
    };
  });
});

const yTicks = computed(() => {
  const plotHeight = chartHeight - margin.top - margin.bottom;
  const maxDuration = Math.max(1, ...props.rows.map((row) => Number(row?.duration_seconds) || 0));
  const tickCount = 4;
  return Array.from({ length: tickCount + 1 }, (_, index) => {
    const value = Math.round((maxDuration / tickCount) * (tickCount - index));
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
        <g class="svg-grid">
          <line
            v-for="tick in yTicks"
            :key="`burst-grid-${tick.value}-${tick.y}`"
            :x1="margin.left"
            :x2="chartWidth - margin.right"
            :y1="tick.y"
            :y2="tick.y"
          />
        </g>

        <g class="svg-axis-labels">
          <text
            v-for="tick in yTicks"
            :key="`burst-y-${tick.value}-${tick.y}`"
            :x="margin.left - 10"
            :y="tick.y + 4"
            text-anchor="end"
          >
            {{ tick.value }}
          </text>
          <text
            v-for="(tick, index) in xTicks"
            :key="`burst-x-${index}`"
            :x="tick.x"
            :y="chartHeight - 12"
            text-anchor="middle"
          >
            {{ tick.value }}
          </text>
          <text class="svg-axis-title" :x="18" :y="chartHeight / 2" text-anchor="middle" transform="rotate(-90, 18, 150)">
            Duration (sec)
          </text>
          <text class="svg-axis-title" :x="chartWidth / 2" :y="chartHeight - 2" text-anchor="middle">
            Burst start
          </text>
        </g>

        <g class="svg-bars">
          <rect
            v-for="(row, index) in normalizedRows"
            :key="`${row.label}-${row.duration}-${index}`"
            class="svg-bar"
            :x="row.x"
            :y="row.y"
            :width="row.width"
            :height="row.height"
            rx="2"
          >
            <title>{{ `${row.duration}s at ${row.label}` }}</title>
          </rect>
        </g>
      </svg>
    </div>
  </section>
</template>
