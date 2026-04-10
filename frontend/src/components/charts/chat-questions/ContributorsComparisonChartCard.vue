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

const chartWidth = 980;
const chartHeight = 340;
const margin = { top: 18, right: 22, bottom: 92, left: 52 };
const seriesColors = {
  Messages: "var(--color-data-blue)",
  Questions: "var(--color-data-peach)",
};

function formatContributorLabel(value) {
  const label = String(value || "Unknown").trim();
  if (!label) return "Unknown";
  if (label.length <= 14) return label;
  if (/^[0-9a-f-]{16,}$/i.test(label)) {
    return `${label.slice(0, 6)}...${label.slice(-4)}`;
  }
  return `${label.slice(0, 12)}...`;
}

const normalizedRows = computed(() => {
  const plotWidth = chartWidth - margin.left - margin.right;
  const plotHeight = chartHeight - margin.top - margin.bottom;
  const maxValue = Math.max(1, ...props.rows.map((row) => Number(row?.count) || 0));
  const barSlot = props.rows.length ? plotWidth / props.rows.length : 0;
  const barWidth = Math.max(barSlot * 0.55, 10);

  return props.rows.map((row, index) => {
    const count = Number(row?.count) || 0;
    const height = maxValue > 0 ? (count / maxValue) * plotHeight : 0;
    const x = margin.left + index * barSlot + (barSlot - barWidth) / 2;
    const y = margin.top + (plotHeight - height);
    return {
      label: String(row?.contributor || "Unknown"),
      displayLabel: formatContributorLabel(row?.contributor),
      count,
      kind: String(row?.kind || "Messages"),
      x,
      y,
      width: barWidth,
      height,
      color: seriesColors[String(row?.kind || "Messages")] || "var(--color-data-blue)",
      labelX: x + barWidth / 2,
    };
  });
});

const yTicks = computed(() => {
  const plotHeight = chartHeight - margin.top - margin.bottom;
  const maxValue = Math.max(1, ...props.rows.map((row) => Number(row?.count) || 0));
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
    </div>

    <div class="svg-chart-shell">
      <svg :viewBox="`0 0 ${chartWidth} ${chartHeight}`" class="svg-chart" role="img" :aria-label="title">
        <g class="svg-grid">
          <line
            v-for="tick in yTicks"
            :key="`contributors-grid-${tick.value}-${tick.y}`"
            :x1="margin.left"
            :x2="chartWidth - margin.right"
            :y1="tick.y"
            :y2="tick.y"
          />
        </g>

        <g class="svg-axis-labels">
          <text
            v-for="tick in yTicks"
            :key="`contributors-y-${tick.value}-${tick.y}`"
            :x="margin.left - 10"
            :y="tick.y + 4"
            text-anchor="end"
          >
            {{ tick.value }}
          </text>
          <text class="svg-axis-title" :x="20" :y="chartHeight / 2" text-anchor="middle" transform="rotate(-90, 20, 160)">
            Count
          </text>
          <text class="svg-axis-title" :x="chartWidth / 2" :y="chartHeight - 10" text-anchor="middle">
            Author / Asker ID
          </text>
        </g>

        <g class="svg-bars">
          <g v-for="(row, index) in normalizedRows" :key="`${row.label}-${row.kind}-${index}`">
            <rect
              :x="row.x"
              :y="row.y"
              :width="row.width"
              :height="row.height"
              rx="2"
              :fill="row.color"
            >
              <title>{{ `${row.label}: ${row.count} ${row.kind}` }}</title>
            </rect>
            <text
              class="contributors-x-label"
              :x="row.labelX"
              :y="chartHeight - 48"
              text-anchor="middle"
              :transform="`rotate(20 ${row.labelX} ${chartHeight - 48})`"
            >
              {{ row.displayLabel }}
            </text>
          </g>
        </g>
      </svg>
    </div>

    <div class="svg-chart-legend">
      <div class="svg-chart-legend-item" v-for="(color, label) in seriesColors" :key="label">
        <span class="svg-chart-legend-swatch" :style="{ background: color }"></span>
        <span>{{ label }}</span>
      </div>
    </div>
  </section>
</template>
