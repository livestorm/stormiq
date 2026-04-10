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

const chartWidth = 520;
const chartHeight = 250;
const margin = { top: 20, right: 12, bottom: 42, left: 42 };

const normalizedRows = computed(() => {
  const maxValue = Math.max(1, ...props.rows.map((row) => Number(row?.[props.valueKey]) || 0));
  const plotWidth = chartWidth - margin.left - margin.right;
  const plotHeight = chartHeight - margin.top - margin.bottom;
  const barGap = 18;
  const barWidth = props.rows.length ? Math.max((plotWidth - barGap * (props.rows.length - 1)) / props.rows.length, 16) : 0;

  return props.rows.map((row, index) => {
    const value = Number(row?.[props.valueKey]) || 0;
    const barHeight = maxValue > 0 ? (value / maxValue) * plotHeight : 0;
    const x = margin.left + index * (barWidth + barGap);
    const y = margin.top + (plotHeight - barHeight);
    return {
      label: String(row?.[props.labelKey] || "Unknown"),
      value,
      x,
      y,
      width: barWidth,
      height: barHeight,
      labelX: x + barWidth / 2,
    };
  });
});

const yTicks = computed(() => {
  const maxValue = Math.max(1, ...props.rows.map((row) => Number(row?.[props.valueKey]) || 0));
  const plotHeight = chartHeight - margin.top - margin.bottom;
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
            :key="`label-${tick.value}-${tick.y}`"
            :x="margin.left - 10"
            :y="tick.y + 4"
            text-anchor="end"
          >
            {{ tick.value }}
          </text>
        </g>

        <g class="svg-bars">
          <g v-for="row in normalizedRows" :key="row.label">
            <rect
              class="svg-bar"
              :x="row.x"
              :y="row.y"
              :width="row.width"
              :height="row.height"
              rx="8"
            >
              <title>{{ `${row.label}: ${row.value}` }}</title>
            </rect>
            <text class="svg-bar-value" :x="row.labelX" :y="row.y - 8" text-anchor="middle">
              {{ row.value }}
            </text>
            <text class="svg-x-label" :x="row.labelX" :y="chartHeight - 12" text-anchor="middle">
              {{ row.label }}
            </text>
          </g>
        </g>
      </svg>
    </div>
  </section>
</template>
