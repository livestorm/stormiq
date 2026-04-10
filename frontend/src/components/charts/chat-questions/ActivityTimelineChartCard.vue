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
const chartHeight = 320;
const margin = { top: 18, right: 22, bottom: 56, left: 52 };
const messageColor = "#8eddf0";
const questionColor = "#ffc247";

const normalizedRows = computed(() => {
  const plotWidth = chartWidth - margin.left - margin.right;
  const plotHeight = chartHeight - margin.top - margin.bottom;
  const maxValue = Math.max(
    1,
    ...props.rows.flatMap((row) => [Number(row?.messages) || 0, Number(row?.questions) || 0])
  );
  const pointsCount = Math.max(props.rows.length - 1, 1);

  return props.rows.map((row, index) => {
    const x = margin.left + (index / pointsCount) * plotWidth;
    const messages = Number(row?.messages) || 0;
    const questions = Number(row?.questions) || 0;
    return {
      label: String(row?.label || ""),
      x,
      messages,
      questions,
      messagesY: margin.top + (plotHeight - (messages / maxValue) * plotHeight),
      questionsY: margin.top + (plotHeight - (questions / maxValue) * plotHeight),
    };
  });
});

const messagePath = computed(() => {
  if (!normalizedRows.value.length) return "";
  return normalizedRows.value
    .map((row, index) => `${index === 0 ? "M" : "L"} ${row.x.toFixed(2)} ${row.messagesY.toFixed(2)}`)
    .join(" ");
});

const questionPath = computed(() => {
  if (!normalizedRows.value.length) return "";
  return normalizedRows.value
    .map((row, index) => `${index === 0 ? "M" : "L"} ${row.x.toFixed(2)} ${row.questionsY.toFixed(2)}`)
    .join(" ");
});

const xTicks = computed(() => {
  if (!normalizedRows.value.length) return [];
  const desiredTicks = 6;
  const step = Math.max(1, Math.ceil(normalizedRows.value.length / desiredTicks));
  return normalizedRows.value.filter((_, index) => index % step === 0 || index === normalizedRows.value.length - 1);
});

const yTicks = computed(() => {
  const plotHeight = chartHeight - margin.top - margin.bottom;
  const maxValue = Math.max(
    1,
    ...props.rows.flatMap((row) => [Number(row?.messages) || 0, Number(row?.questions) || 0])
  );
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
            :key="`activity-grid-${tick.value}-${tick.y}`"
            :x1="margin.left"
            :x2="chartWidth - margin.right"
            :y1="tick.y"
            :y2="tick.y"
          />
        </g>

        <g class="svg-axis-labels">
          <text
            v-for="tick in yTicks"
            :key="`activity-y-${tick.value}-${tick.y}`"
            :x="margin.left - 10"
            :y="tick.y + 4"
            text-anchor="end"
          >
            {{ tick.value }}
          </text>
          <text
            v-for="tick in xTicks"
            :key="`activity-x-${tick.label}-${tick.x}`"
            :x="tick.x"
            :y="chartHeight - 12"
            text-anchor="middle"
          >
            {{ tick.label }}
          </text>
          <text class="svg-axis-title" :x="20" :y="chartHeight / 2" text-anchor="middle" transform="rotate(-90, 20, 160)">
            Count
          </text>
          <text class="svg-axis-title" :x="chartWidth / 2" :y="chartHeight - 2" text-anchor="middle">
            Time (UTC)
          </text>
        </g>

        <path v-if="messagePath" class="activity-chart-line activity-chart-line-messages" :d="messagePath">
          <title>Messages over time. Hover points to see the exact count for each time slot.</title>
        </path>
        <path v-if="questionPath" class="activity-chart-line activity-chart-line-questions" :d="questionPath">
          <title>Questions over time. Hover points to see the exact count for each time slot.</title>
        </path>

        <circle
          v-for="(row, index) in normalizedRows"
          :key="`messages-${row.label}-${index}`"
          class="activity-chart-point activity-chart-point-messages"
          :cx="row.x"
          :cy="row.messagesY"
          r="3"
        >
          <title>{{ `${row.label}: ${row.messages} messages` }}</title>
        </circle>

        <circle
          v-for="(row, index) in normalizedRows"
          :key="`questions-${row.label}-${index}`"
          class="activity-chart-point activity-chart-point-questions"
          :cx="row.x"
          :cy="row.questionsY"
          r="3"
        >
          <title>{{ `${row.label}: ${row.questions} questions` }}</title>
        </circle>
      </svg>
    </div>

    <div class="svg-chart-legend">
      <div class="svg-chart-legend-item">
        <span class="svg-chart-legend-swatch" :style="{ background: messageColor }"></span>
        <span>Messages</span>
      </div>
      <div class="svg-chart-legend-item">
        <span class="svg-chart-legend-swatch" :style="{ background: questionColor }"></span>
        <span>Questions</span>
      </div>
    </div>
  </section>
</template>
