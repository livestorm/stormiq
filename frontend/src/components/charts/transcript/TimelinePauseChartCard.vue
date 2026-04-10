<script setup>
import { computed } from "vue";

const props = defineProps({
  title: {
    type: String,
    default: "",
  },
  description: {
    type: String,
    default: "",
  },
  rows: {
    type: Array,
    default: () => [],
  },
});

const chartWidth = 560;
const chartHeight = 250;
const margin = { top: 20, right: 16, bottom: 40, left: 44 };

const pauseColors = {
  "Strong silence": "#8eddf0",
  "Thinking pause": "#ffc857",
  Hesitation: "#ff7d8a",
  "Natural flow": "#5ed1a8",
};

const normalizedRows = computed(() => {
  const plotWidth = chartWidth - margin.left - margin.right;
  const plotHeight = chartHeight - margin.top - margin.bottom;
  const maxTime = Math.max(1, ...props.rows.map((row) => Number(row?.time_seconds) || 0));
  const maxPause = Math.max(1, ...props.rows.map((row) => Number(row?.pause_seconds) || 0));

  return props.rows.map((row) => {
    const time = Number(row?.time_seconds) || 0;
    const pause = Number(row?.pause_seconds) || 0;
    return {
      time,
      pause,
      pauseType: String(row?.pause_type || "Pause"),
      x: margin.left + (time / maxTime) * plotWidth,
      y: margin.top + (plotHeight - (pause / maxPause) * plotHeight),
      lineY2: margin.top + plotHeight,
      color: pauseColors[String(row?.pause_type || "")] || "#8eddf0",
    };
  });
});

const xTicks = computed(() => {
  const plotWidth = chartWidth - margin.left - margin.right;
  const maxTime = Math.max(1, ...props.rows.map((row) => Number(row?.time_seconds) || 0));
  const tickCount = 4;
  return Array.from({ length: tickCount + 1 }, (_, index) => {
    const value = (maxTime / tickCount) * index;
    const x = margin.left + (plotWidth / tickCount) * index;
    return { value: Math.round(value), x };
  });
});

const yTicks = computed(() => {
  const plotHeight = chartHeight - margin.top - margin.bottom;
  const maxPause = Math.max(1, ...props.rows.map((row) => Number(row?.pause_seconds) || 0));
  const tickCount = 3;
  return Array.from({ length: tickCount + 1 }, (_, index) => {
    const value = ((maxPause / tickCount) * (tickCount - index)).toFixed(1);
    const y = margin.top + (plotHeight / tickCount) * index;
    return { value, y };
  });
});

const legendItems = computed(() => {
  const seen = new Set();
  return props.rows
    .map((row) => String(row?.pause_type || "Pause"))
    .filter((type) => {
      if (seen.has(type)) return false;
      seen.add(type);
      return true;
    })
    .map((type) => ({
      label: type,
      color: pauseColors[type] || "#8eddf0",
    }));
});
</script>

<template>
  <section class="panel" v-if="rows.length">
    <div class="panel-heading">
      <h3>{{ title }}</h3>
      <p v-if="description">{{ description }}</p>
    </div>

    <div class="svg-chart-shell">
      <svg :viewBox="`0 0 ${chartWidth} ${chartHeight}`" class="svg-chart" role="img" :aria-label="title">
        <g class="svg-grid">
          <line
            v-for="tick in yTicks"
            :key="`grid-${tick.value}-${tick.y}`"
            :x1="margin.left"
            :x2="chartWidth - margin.right"
            :y1="tick.y"
            :y2="tick.y"
          />
        </g>

        <g class="svg-axis-labels">
          <text
            v-for="tick in yTicks"
            :key="`ylabel-${tick.value}-${tick.y}`"
            :x="margin.left - 10"
            :y="tick.y + 4"
            text-anchor="end"
          >
            {{ tick.value }}
          </text>
          <text
            v-for="tick in xTicks"
            :key="`xlabel-${tick.value}-${tick.x}`"
            :x="tick.x"
            :y="chartHeight - 12"
            text-anchor="middle"
          >
            {{ tick.value }}
          </text>
        </g>

        <g class="svg-timeline-series">
          <line
            v-for="(row, index) in normalizedRows"
            :key="`${row.time}-${row.pause}-${index}`"
            class="svg-timeline-line"
            :x1="row.x"
            :x2="row.x"
            :y1="row.y"
            :y2="row.lineY2"
            :style="{ stroke: row.color }"
          >
            <title>{{ `${row.pauseType}: ${row.pause}s at ${row.time}s` }}</title>
          </line>
        </g>
      </svg>
    </div>

    <div class="svg-chart-legend" v-if="legendItems.length">
      <div class="svg-chart-legend-item" v-for="item in legendItems" :key="item.label">
        <span class="svg-chart-legend-swatch" :style="{ background: item.color }"></span>
        <span>{{ item.label }}</span>
      </div>
    </div>
  </section>
</template>
