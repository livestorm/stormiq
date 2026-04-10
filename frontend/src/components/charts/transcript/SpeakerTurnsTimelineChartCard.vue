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

const chartWidth = 600;
const chartHeight = 240;
const margin = { top: 18, right: 18, bottom: 38, left: 78 };

const speakerOrder = computed(() => {
  return Object.keys(buildSpeakerColorMap(props.rows.map((row) => row?.speaker)));
});

const speakerColorMap = computed(() => buildSpeakerColorMap(props.rows.map((row) => row?.speaker)));

const normalizedRows = computed(() => {
  const plotWidth = chartWidth - margin.left - margin.right;
  const plotHeight = chartHeight - margin.top - margin.bottom;
  const maxTime = Math.max(1, ...props.rows.map((row) => Number(row?.time_seconds) || 0));
  const laneHeight = speakerOrder.value.length ? plotHeight / speakerOrder.value.length : plotHeight;

  return props.rows.map((row) => {
    const speaker = String(row?.speaker || "Unknown");
    const time = Number(row?.time_seconds) || 0;
    const laneIndex = speakerOrder.value.indexOf(speaker);
    return {
      speaker,
      time,
      x: margin.left + (time / maxTime) * plotWidth,
      y: margin.top + laneIndex * laneHeight + laneHeight / 2,
      color: speakerColorMap.value[speaker],
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
            :key="`turns-x-${tick.value}-${tick.x}`"
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
            Time (sec)
          </text>
          <text class="svg-axis-title" :x="20" :y="chartHeight / 2" text-anchor="middle" transform="rotate(-90, 20, 150)">
            Speaker
          </text>
        </g>

        <g class="svg-turn-series">
          <circle
            v-for="(row, index) in normalizedRows"
            :key="`${row.speaker}-${row.time}-${index}`"
            :cx="row.x"
            :cy="row.y"
            r="4"
            :fill="row.color"
          >
            <title>{{ `${row.speaker} turn at ${row.time}s` }}</title>
          </circle>
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
