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

const speakerColorMap = computed(() => buildSpeakerColorMap(props.rows.map((row) => row?.speaker)));

const normalizedRows = computed(() => {
  let cumulative = 0;
  return props.rows.map((row) => {
    const value = Number(row?.share_pct) || 0;
    const start = cumulative / 100;
    cumulative += value;
    const end = cumulative / 100;
    const speaker = String(row?.speaker || "Unknown");
    const midAngle = ((start + end) / 2) * 360;
    return {
      speaker,
      value,
      color: speakerColorMap.value[speaker],
      path: describePieSlice(50, 50, 36, start * 360, end * 360),
      labelPosition: polarToCartesian(50, 50, 21, midAngle),
      midAngle,
    };
  });
});

const visibleLabelRows = computed(() => {
  const candidates = normalizedRows.value
    .filter((item) => item.value >= 3)
    .sort((left, right) => left.midAngle - right.midAngle);

  const accepted = [];
  const minAngleGap = 18;
  const minYGap = 7;

  for (const candidate of candidates) {
    const overlaps = accepted.some(
      (item) =>
        Math.abs(item.midAngle - candidate.midAngle) < minAngleGap &&
        Math.abs(item.labelPosition.y - candidate.labelPosition.y) < minYGap,
    );
    if (!overlaps) {
      accepted.push(candidate);
    }
  }

  return accepted;
});

function polarToCartesian(cx, cy, r, angleDeg) {
  const angleRad = ((angleDeg - 90) * Math.PI) / 180;
  return {
    x: cx + r * Math.cos(angleRad),
    y: cy + r * Math.sin(angleRad),
  };
}

function describePieSlice(cx, cy, r, startAngle, endAngle) {
  const start = polarToCartesian(cx, cy, r, endAngle);
  const end = polarToCartesian(cx, cy, r, startAngle);
  const largeArcFlag = endAngle - startAngle <= 180 ? "0" : "1";
  return ["M", cx, cy, "L", start.x, start.y, "A", r, r, 0, largeArcFlag, 0, end.x, end.y, "Z"].join(" ");
}
</script>

<template>
  <section class="panel" v-if="rows.length">
    <div class="panel-heading">
      <h3>{{ title }}</h3>
    </div>

    <div class="speaker-share-layout">
      <div class="speaker-share-chart">
        <svg viewBox="0 0 100 100" class="speaker-share-svg" role="img" :aria-label="title">
          <path
            v-for="row in normalizedRows"
            :key="row.speaker"
            :d="row.path"
            :fill="row.color"
          >
            <title>{{ `${row.speaker}: ${row.value}%` }}</title>
          </path>
          <text
            v-for="row in visibleLabelRows"
            :key="`${row.speaker}-label`"
            :x="row.labelPosition.x"
            :y="row.labelPosition.y"
            text-anchor="middle"
            dominant-baseline="middle"
            class="speaker-share-slice-label"
          >
            {{ row.value.toFixed(row.value < 10 ? 2 : 1) }}%
          </text>
        </svg>
      </div>

      <div class="svg-chart-legend">
        <div class="svg-chart-legend-item" v-for="row in normalizedRows" :key="`${row.speaker}-legend`">
          <span class="svg-chart-legend-swatch" :style="{ background: row.color }"></span>
          <span>{{ row.speaker }}</span>
        </div>
      </div>
    </div>
  </section>
</template>
