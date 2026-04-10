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

const chartWidth = 860;
const chartHeight = 320;
const margin = { top: 18, right: 22, bottom: 44, left: 54 };

const HEALTHY_MIN = 130;
const HEALTHY_MAX = 160;

function getPaceColor(wpm) {
  if (wpm < 120) return "#ff9f5a";
  if (wpm < 130) return "#ffd166";
  if (wpm <= 160) return "#63d084";
  if (wpm <= 180) return "#9bd85d";
  return "#ff6b6f";
}

function roundUpTo(value, step) {
  return Math.ceil(value / step) * step;
}

const chartDomainMax = computed(() => {
  const observedMax = Math.max(1, ...props.rows.map((row) => Number(row?.segment_wpm) || 0));
  return Math.max(220, roundUpTo(Math.min(observedMax, 220), 20));
});

const timeDomainMax = computed(() => {
  const rows = props.rows;
  if (!rows.length) return 1;
  return Math.max(
    1,
    ...rows.map((row) => {
      const start = Number(row?.time_seconds) || 0;
      const duration = Math.max(Number(row?.duration_seconds) || 0, 0);
      return start + duration;
    }),
  );
});

function yForWpm(wpm) {
  const plotHeight = chartHeight - margin.top - margin.bottom;
  const safeWpm = Math.max(0, Math.min(Number(wpm) || 0, chartDomainMax.value));
  return margin.top + (plotHeight - (safeWpm / chartDomainMax.value) * plotHeight);
}

const normalizedRows = computed(() => {
  const plotWidth = chartWidth - margin.left - margin.right;
  const maxTime = timeDomainMax.value;

  return props.rows.map((row, index) => {
    const time = Number(row?.time_seconds) || 0;
    const duration = Math.max(Number(row?.duration_seconds) || 60, 1);
    const endTime = time + duration;
    const wpm = Number(row?.segment_wpm) || 0;
    const x = margin.left + (time / maxTime) * plotWidth;
    const y = yForWpm(wpm);
    const endX = margin.left + (endTime / maxTime) * plotWidth;
    const nextRow = props.rows[index + 1];
    const nextWpm = Number(nextRow?.segment_wpm);
    return {
      time,
      endTime,
      timeLabel: row?.time_label || `${time}s`,
      duration,
      wpm,
      x,
      y,
      endX,
      color: getPaceColor(wpm),
      nextX: nextRow ? margin.left + ((Number(nextRow?.time_seconds) || 0) / maxTime) * plotWidth : null,
      nextY: Number.isFinite(nextWpm) ? yForWpm(nextWpm) : null,
      nextColor: Number.isFinite(nextWpm) ? getPaceColor(nextWpm) : null,
    };
  });
});

const lineSegments = computed(() =>
  normalizedRows.value
    .filter((row) => row.nextX !== null && row.nextY !== null)
    .map((row) => ({
      x1: row.x,
      y1: row.y,
      x2: row.nextX,
      y2: row.nextY,
      color: row.nextColor || row.color,
      title: `${row.timeLabel}: ${Math.round(row.wpm)} WPM`,
    })),
);

const xTicks = computed(() => {
  const plotWidth = chartWidth - margin.left - margin.right;
  const tickCount = 4;
  const maxTime = timeDomainMax.value;
  return Array.from({ length: tickCount + 1 }, (_, index) => {
    const value = (maxTime / tickCount) * index;
    const x = margin.left + (plotWidth / tickCount) * index;
    return { value: Math.round(value), x };
  });
});

const yTicks = computed(() => {
  const plotHeight = chartHeight - margin.top - margin.bottom;
  const maxWpm = chartDomainMax.value;
  const tickCount = 5;
  return Array.from({ length: tickCount + 1 }, (_, index) => {
    const value = Math.round((maxWpm / tickCount) * (tickCount - index));
    const y = margin.top + (plotHeight / tickCount) * index;
    return { value, y };
  });
});

const healthyBand = computed(() => {
  const top = yForWpm(HEALTHY_MAX);
  const bottom = yForWpm(HEALTHY_MIN);
  return {
    y: top,
    height: bottom - top,
  };
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
            :key="`pace-grid-${tick.value}-${tick.y}`"
            :x1="margin.left"
            :x2="chartWidth - margin.right"
            :y1="tick.y"
            :y2="tick.y"
          />
        </g>

        <rect
          class="pace-zone pace-zone-healthy"
          :x="margin.left"
          :y="healthyBand.y"
          :width="chartWidth - margin.left - margin.right"
          :height="healthyBand.height"
          rx="6"
        >
          <title>Healthy comprehension zone: 130-160 WPM</title>
        </rect>

        <g class="svg-axis-labels">
          <text
            v-for="tick in yTicks"
            :key="`pace-y-${tick.value}-${tick.y}`"
            :x="margin.left - 10"
            :y="tick.y + 4"
            text-anchor="end"
          >
            {{ tick.value }}
          </text>
          <text
            v-for="tick in xTicks"
            :key="`pace-x-${tick.value}-${tick.x}`"
            :x="tick.x"
            :y="chartHeight - 12"
            text-anchor="middle"
          >
            {{ tick.value }}
          </text>
          <text class="svg-axis-title" :x="margin.left - 34" :y="chartHeight / 2" text-anchor="middle" transform="rotate(-90, 14, 160)">
            Words Per Minute
          </text>
          <text class="svg-axis-title" :x="chartWidth / 2" :y="chartHeight - 2" text-anchor="middle">
            Time (sec)
          </text>
        </g>

        <line
          v-for="(segment, index) in lineSegments"
          :key="`${segment.x1}-${segment.x2}-${index}`"
          class="pace-line-segment"
          :x1="segment.x1"
          :y1="segment.y1"
          :x2="segment.x2"
          :y2="segment.y2"
          :stroke="segment.color"
        >
          <title>{{ segment.title }}</title>
        </line>
      </svg>
    </div>

    <div class="svg-chart-legend">
      <div class="svg-chart-legend-item">
        <span class="svg-chart-legend-swatch" style="background: #63d084"></span>
        <span>Healthy pace</span>
      </div>
      <div class="svg-chart-legend-item">
        <span class="svg-chart-legend-swatch" style="background: #ffd166"></span>
        <span>Slightly energetic</span>
      </div>
      <div class="svg-chart-legend-item">
        <span class="svg-chart-legend-swatch" style="background: #ff9f5a"></span>
        <span>Too slow</span>
      </div>
      <div class="svg-chart-legend-item">
        <span class="svg-chart-legend-swatch" style="background: #ff6b6f"></span>
        <span>Too fast</span>
      </div>
    </div>
  </section>
</template>
