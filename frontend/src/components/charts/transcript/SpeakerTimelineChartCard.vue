<script setup>
import { computed } from "vue";
import { buildSpeakerColorMap } from "./speakerColors";

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

const chartWidth = 620;
const chartHeight = 300;
const margin = { top: 20, right: 18, bottom: 40, left: 82 };
const speakerOrder = computed(() => {
  return Object.keys(buildSpeakerColorMap(props.rows.map((row) => row?.speaker)));
});

const speakerColorMap = computed(() => buildSpeakerColorMap(props.rows.map((row) => row?.speaker)));

const normalizedRows = computed(() => {
  const plotWidth = chartWidth - margin.left - margin.right;
  const plotHeight = chartHeight - margin.top - margin.bottom;
  const maxTime = Math.max(1, ...props.rows.map((row) => Number(row?.end_seconds) || Number(row?.start_seconds) || 0));
  const laneHeight = speakerOrder.value.length ? plotHeight / speakerOrder.value.length : plotHeight;

  return props.rows.map((row) => {
    const start = Number(row?.start_seconds) || 0;
    const end = Number(row?.end_seconds) || start;
    const duration = Math.max(end - start, 0);
    const speaker = String(row?.speaker || "Unknown");
    const laneIndex = speakerOrder.value.indexOf(speaker);
    const x = margin.left + (start / maxTime) * plotWidth;
    const width = Math.max((duration / maxTime) * plotWidth, 2);
    const y = margin.top + laneIndex * laneHeight + 8;
    return {
      speaker,
      start,
      end,
      duration,
      x,
      y,
      width,
      height: Math.max(laneHeight - 16, 10),
      color: speakerColorMap.value[speaker],
    };
  });
});

const xTicks = computed(() => {
  const plotWidth = chartWidth - margin.left - margin.right;
  const maxTime = Math.max(1, ...props.rows.map((row) => Number(row?.end_seconds) || Number(row?.start_seconds) || 0));
  const tickCount = 4;
  return Array.from({ length: tickCount + 1 }, (_, index) => {
    const value = (maxTime / tickCount) * index;
    const x = margin.left + (plotWidth / tickCount) * index;
    return { value: Math.round(value), x };
  });
});

const laneLabels = computed(() => {
  const plotHeight = chartHeight - margin.top - margin.bottom;
  const laneHeight = speakerOrder.value.length ? plotHeight / speakerOrder.value.length : plotHeight;
  return speakerOrder.value.map((speaker, index) => ({
    speaker,
    y: margin.top + index * laneHeight + laneHeight / 2 + 4,
  }));
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
            v-for="label in laneLabels"
            :key="`${label.speaker}-grid`"
            :x1="margin.left"
            :x2="chartWidth - margin.right"
            :y1="label.y"
            :y2="label.y"
          />
        </g>

        <g class="svg-axis-labels">
          <text
            v-for="tick in xTicks"
            :key="`speaker-x-${tick.value}-${tick.x}`"
            :x="tick.x"
            :y="chartHeight - 12"
            text-anchor="middle"
          >
            {{ tick.value }}
          </text>
          <text
            v-for="label in laneLabels"
            :key="`${label.speaker}-label`"
            :x="margin.left - 12"
            :y="label.y + 4"
            text-anchor="end"
          >
            {{ label.speaker }}
          </text>
          <text class="svg-axis-title" :x="chartWidth / 2" :y="chartHeight - 2" text-anchor="middle">
            Transcript time (sec)
          </text>
        </g>

        <g class="svg-speaker-timeline">
          <rect
            v-for="(row, index) in normalizedRows"
            :key="`${row.speaker}-${row.start}-${row.end}-${index}`"
            :x="row.x"
            :y="row.y"
            :width="row.width"
            :height="row.height"
            rx="2"
            :fill="row.color"
          >
            <title>{{ `${row.speaker}: ${row.start}s-${row.end}s (${row.duration.toFixed(2)}s)` }}</title>
          </rect>
        </g>
      </svg>
    </div>

    <div class="svg-chart-legend">
      <div class="svg-chart-legend-item" v-for="speaker in speakerOrder" :key="`${speaker}-legend`">
        <span class="svg-chart-legend-swatch" :style="{ background: speakerColorMap[speaker] }"></span>
        <span>{{ speaker }}</span>
      </div>
    </div>
  </section>
</template>
