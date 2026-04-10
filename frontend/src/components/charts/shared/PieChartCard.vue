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

const palette = ["#8eddf0", "#ffc247", "#f46a6f", "#63d084", "#8b9bff", "#d989ff", "#5fd5bb"];

function polarToCartesian(cx, cy, r, angleDeg) {
  const angleRad = ((angleDeg - 90) * Math.PI) / 180;
  return {
    x: cx + r * Math.cos(angleRad),
    y: cy + r * Math.sin(angleRad),
  };
}

function describePieSlice(cx, cy, r, startAngle, endAngle) {
  const sweep = endAngle - startAngle;
  if (sweep >= 359.999) {
    return "";
  }
  const start = polarToCartesian(cx, cy, r, endAngle);
  const end = polarToCartesian(cx, cy, r, startAngle);
  const largeArcFlag = sweep <= 180 ? "0" : "1";
  return ["M", cx, cy, "L", start.x, start.y, "A", r, r, 0, largeArcFlag, 0, end.x, end.y, "Z"].join(" ");
}

const normalizedRows = computed(() => {
  const total = props.rows.reduce((sum, row) => sum + (Number(row?.[props.valueKey]) || 0), 0);
  let cumulative = 0;

  return props.rows
    .map((row, index) => {
      const label = String(row?.[props.labelKey] || "Unknown");
      const value = Number(row?.[props.valueKey]) || 0;
      const sharePct = total > 0 ? (value / total) * 100 : 0;
      const start = total > 0 ? cumulative / total : 0;
      cumulative += value;
      const end = total > 0 ? cumulative / total : 0;
      const midAngle = ((start + end) / 2) * 360;
      return {
        label,
        value,
        sharePct,
        color: palette[index % palette.length],
        isFullCircle: sharePct >= 99.999,
        path: describePieSlice(50, 50, 36, start * 360, end * 360),
        labelPosition: polarToCartesian(50, 50, 21, midAngle),
      };
    })
    .filter((row) => row.value > 0);
});
</script>

<template>
  <section class="panel" v-if="rows.length">
    <div class="panel-heading">
      <h3>{{ title }}</h3>
      <p v-if="description">{{ description }}</p>
    </div>

    <div class="speaker-share-layout">
      <div class="speaker-share-chart">
        <svg viewBox="0 0 100 100" class="speaker-share-svg" role="img" :aria-label="title">
          <circle
            v-for="row in normalizedRows.filter((item) => item.isFullCircle)"
            :key="`${row.label}-full`"
            class="pie-chart-slice"
            cx="50"
            cy="50"
            r="36"
            :style="{ fill: row.color }"
          >
            <title>{{ `${row.label}: ${row.value} (${row.sharePct.toFixed(1)}%)` }}</title>
          </circle>
          <path
            v-for="row in normalizedRows.filter((item) => !item.isFullCircle)"
            :key="row.label"
            class="pie-chart-slice"
            :d="row.path"
            :style="{ fill: row.color }"
          >
            <title>{{ `${row.label}: ${row.value} (${row.sharePct.toFixed(1)}%)` }}</title>
          </path>
          <text
            v-for="row in normalizedRows.filter((item) => item.sharePct >= 5)"
            :key="`${row.label}-label`"
            :x="row.labelPosition.x"
            :y="row.labelPosition.y"
            text-anchor="middle"
            dominant-baseline="middle"
            class="speaker-share-slice-label"
          >
            {{ row.sharePct.toFixed(1) }}%
          </text>
        </svg>
      </div>

      <div class="svg-chart-legend">
        <div class="svg-chart-legend-item" v-for="row in normalizedRows" :key="`${row.label}-legend`">
          <span class="svg-chart-legend-swatch" :style="{ background: row.color }"></span>
          <span>{{ row.label }}</span>
        </div>
      </div>
    </div>
  </section>
</template>
