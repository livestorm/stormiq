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
  labelKey: {
    type: String,
    required: true,
  },
  valueKey: {
    type: String,
    required: true,
  },
});

const chartWidth = 980;
const chartHeight = 320;
const margin = { top: 18, right: 20, bottom: 72, left: 52 };

const normalizedRows = computed(() => {
  const plotWidth = chartWidth - margin.left - margin.right;
  const plotHeight = chartHeight - margin.top - margin.bottom;
  const maxValue = Math.max(1, ...props.rows.map((row) => Number(row?.[props.valueKey]) || 0));
  const slotWidth = props.rows.length ? plotWidth / props.rows.length : plotWidth;
  const barWidth = Math.max(slotWidth * 0.55, 18);

  return props.rows.map((row, index) => {
    const label = String(row?.[props.labelKey] || "Unknown");
    const value = Number(row?.[props.valueKey]) || 0;
    const height = (value / maxValue) * plotHeight;
    const x = margin.left + index * slotWidth + (slotWidth - barWidth) / 2;
    const y = margin.top + (plotHeight - height);
    return {
      label,
      value,
      x,
      y,
      width: barWidth,
      height,
      labelX: x + barWidth / 2,
    };
  });
});

const yTicks = computed(() => {
  const plotHeight = chartHeight - margin.top - margin.bottom;
  const maxValue = Math.max(1, ...props.rows.map((row) => Number(row?.[props.valueKey]) || 0));
  const tickCount = 4;
  return Array.from({ length: tickCount + 1 }, (_, index) => {
    const value = Math.round((maxValue / tickCount) * (tickCount - index));
    const y = margin.top + (plotHeight / tickCount) * index;
    return { value, y };
  });
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
            :key="`column-grid-${tick.value}-${tick.y}`"
            :x1="margin.left"
            :x2="chartWidth - margin.right"
            :y1="tick.y"
            :y2="tick.y"
          />
        </g>

        <g class="svg-axis-labels">
          <text
            v-for="tick in yTicks"
            :key="`column-y-${tick.value}-${tick.y}`"
            :x="margin.left - 10"
            :y="tick.y + 4"
            text-anchor="end"
          >
            {{ tick.value }}
          </text>
        </g>

        <g class="svg-bars">
          <g v-for="(row, index) in normalizedRows" :key="`${row.label}-${index}`">
            <rect
              class="svg-bar svg-bar-column"
              :x="row.x"
              :y="row.y"
              :width="row.width"
              :height="row.height"
              rx="3"
            >
              <title>{{ `${row.label}: ${row.value}` }}</title>
            </rect>
            <text
              class="contributors-x-label"
              :x="row.labelX"
              :y="chartHeight - 30"
              text-anchor="middle"
            >
              {{ row.label }}
            </text>
          </g>
        </g>
      </svg>
    </div>
  </section>
</template>
