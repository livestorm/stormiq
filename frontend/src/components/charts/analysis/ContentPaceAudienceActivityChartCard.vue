<script setup>
import { computed } from "vue";

const props = defineProps({
  title: {
    type: String,
    default: "",
  },
  isFrench: {
    type: Boolean,
    default: false,
  },
  rows: {
    type: Array,
    default: () => [],
  },
});

const chartWidth = 980;
const chartHeight = 360;
const margin = { top: 18, right: 54, bottom: 52, left: 58 };

const normalizedRows = computed(() => {
  const plotWidth = chartWidth - margin.left - margin.right;
  const plotHeight = chartHeight - margin.top - margin.bottom;
  const maxChat = Math.max(1, ...props.rows.map((row) => Number(row?.chat_messages) || 0));
  const maxQuestions = Math.max(1, ...props.rows.map((row) => Number(row?.question_count) || 0));
  const maxWpm = Math.max(1, ...props.rows.map((row) => Number(row?.transcript_wpm) || 0));
  const slotWidth = props.rows.length ? plotWidth / props.rows.length : 0;
  const barWidth = Math.max(slotWidth * 0.52, 12);

  return props.rows.map((row, index) => {
    const chatMessages = Number(row?.chat_messages) || 0;
    const questionCount = Number(row?.question_count) || 0;
    const transcriptWpm = Number(row?.transcript_wpm) || 0;
    const bucketStart = Number(row?.bucket_start_pct) || 0;
    const xCenter = margin.left + index * slotWidth + slotWidth / 2;
    return {
      label: String(row?.progress_window || `${bucketStart}%`),
      bucketStart,
      chatMessages,
      questionCount,
      transcriptWpm,
      barX: xCenter - barWidth / 2,
      barWidth,
      barHeight: (chatMessages / maxChat) * plotHeight,
      chatY: margin.top + (plotHeight - (chatMessages / maxChat) * plotHeight),
      wpmX: xCenter,
      wpmY: margin.top + (plotHeight - (transcriptWpm / maxWpm) * plotHeight),
      questionX: xCenter,
      questionY: margin.top + (plotHeight - (questionCount / maxQuestions) * plotHeight),
    };
  });
});

const transcriptPath = computed(() => {
  if (!normalizedRows.value.length) return "";
  return normalizedRows.value
    .map((row, index) => `${index === 0 ? "M" : "L"} ${row.wpmX.toFixed(2)} ${row.wpmY.toFixed(2)}`)
    .join(" ");
});

const questionPath = computed(() => {
  if (!normalizedRows.value.length) return "";
  return normalizedRows.value
    .map((row, index) => `${index === 0 ? "M" : "L"} ${row.questionX.toFixed(2)} ${row.questionY.toFixed(2)}`)
    .join(" ");
});

const leftTicks = computed(() => {
  const plotHeight = chartHeight - margin.top - margin.bottom;
  const maxWpm = Math.max(1, ...props.rows.map((row) => Number(row?.transcript_wpm) || 0));
  const tickCount = 4;
  return Array.from({ length: tickCount + 1 }, (_, index) => {
    const value = Math.round((maxWpm / tickCount) * (tickCount - index));
    const y = margin.top + (plotHeight / tickCount) * index;
    return { value, y };
  });
});

const rightTicks = computed(() => {
  const plotHeight = chartHeight - margin.top - margin.bottom;
  const maxQuestions = Math.max(1, ...props.rows.map((row) => Number(row?.question_count) || 0));
  const tickCount = 4;
  return Array.from({ length: tickCount + 1 }, (_, index) => {
    const value = ((maxQuestions / tickCount) * (tickCount - index)).toFixed(1).replace(/\.0$/, "");
    const y = margin.top + (plotHeight / tickCount) * index;
    return { value, y };
  });
});

const xTicks = computed(() => {
  if (!normalizedRows.value.length) return [];
  return normalizedRows.value.map((row) => ({
    x: row.wpmX,
    value: row.bucketStart,
  }));
});

const chartText = computed(() =>
  props.isFrench
    ? {
        leftAxis: "Rythme de transcription (MPM)",
        rightAxis: "Questions",
        bottomAxis: "Chronologie de la session",
        chatMessages: "Messages chat",
        transcriptPace: "Rythme de transcription",
        questions: "Questions",
      }
    : {
        leftAxis: "Transcript pace (WPM)",
        rightAxis: "Questions",
        bottomAxis: "Session timeline",
        chatMessages: "Chat messages",
        transcriptPace: "Transcript pace",
        questions: "Questions",
      }
);
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
            v-for="tick in leftTicks"
            :key="`content-pace-grid-${tick.value}-${tick.y}`"
            :x1="margin.left"
            :x2="chartWidth - margin.right"
            :y1="tick.y"
            :y2="tick.y"
          />
        </g>

        <g class="svg-axis-labels">
          <text
            v-for="tick in leftTicks"
            :key="`content-pace-left-${tick.value}-${tick.y}`"
            :x="margin.left - 10"
            :y="tick.y + 4"
            text-anchor="end"
          >
            {{ tick.value }}
          </text>
          <text
            v-for="tick in rightTicks"
            :key="`content-pace-right-${tick.value}-${tick.y}`"
            :x="chartWidth - margin.right + 10"
            :y="tick.y + 4"
            text-anchor="start"
          >
            {{ tick.value }}
          </text>
          <text
            v-for="tick in xTicks"
            :key="`content-pace-x-${tick.x}-${tick.value}`"
            :x="tick.x"
            :y="chartHeight - 18"
            text-anchor="middle"
          >
            {{ tick.value }}
          </text>
          <text class="svg-axis-title" :x="18" :y="chartHeight / 2" text-anchor="middle" transform="rotate(-90, 18, 180)">
            {{ chartText.leftAxis }}
          </text>
          <text class="svg-axis-title" :x="chartWidth - 10" :y="chartHeight / 2" text-anchor="middle" transform="rotate(90, 970, 180)">
            {{ chartText.rightAxis }}
          </text>
          <text class="svg-axis-title" :x="chartWidth / 2" :y="chartHeight - 2" text-anchor="middle">
            {{ chartText.bottomAxis }}
          </text>
        </g>

        <g class="svg-bars">
          <rect
            v-for="(row, index) in normalizedRows"
            :key="`chat-bar-${index}`"
            :x="row.barX"
            :y="row.chatY"
            :width="row.barWidth"
            :height="row.barHeight"
            rx="2"
            fill="var(--color-surface-warning-alpha-300)"
          >
            <title>{{ `${row.label}: ${row.chatMessages} ${chartText.chatMessages.toLowerCase()}` }}</title>
          </rect>
        </g>

        <path v-if="transcriptPath" class="content-pace-line content-pace-line-wpm" :d="transcriptPath">
          <title>
            {{
              isFrench
                ? "Rythme de transcription au fil de la session. Survolez les points pour voir la valeur exacte par segment."
                : "Transcript pace over the session. Hover the points to see the exact value for each segment."
            }}
          </title>
        </path>
        <path v-if="questionPath" class="content-pace-line content-pace-line-questions" :d="questionPath">
          <title>
            {{
              isFrench
                ? "Questions au fil de la session. Survolez les points pour voir le volume exact par segment."
                : "Questions over the session. Hover the points to see the exact volume for each segment."
            }}
          </title>
        </path>

        <circle
          v-for="(row, index) in normalizedRows"
          :key="`wpm-point-${index}`"
          class="content-pace-point content-pace-point-wpm"
          :cx="row.wpmX"
          :cy="row.wpmY"
          r="3"
        >
          <title>{{ `${row.label}: ${row.transcriptWpm} ${chartText.transcriptPace.toLowerCase()}` }}</title>
        </circle>

        <polygon
          v-for="(row, index) in normalizedRows"
          :key="`question-point-${index}`"
          class="content-pace-diamond"
          :points="`${row.questionX},${row.questionY - 4} ${row.questionX + 4},${row.questionY} ${row.questionX},${row.questionY + 4} ${row.questionX - 4},${row.questionY}`"
        >
          <title>{{ `${row.label}: ${row.questionCount} ${chartText.questions.toLowerCase()}` }}</title>
        </polygon>
      </svg>
    </div>

    <div class="svg-chart-legend">
      <div class="svg-chart-legend-item">
        <span class="svg-chart-legend-swatch" style="background: var(--color-surface-warning-alpha-300)"></span>
        <span>{{ chartText.chatMessages }}</span>
      </div>
      <div class="svg-chart-legend-item">
        <span class="svg-chart-legend-swatch" style="background: var(--color-data-blue)"></span>
        <span>{{ chartText.transcriptPace }}</span>
      </div>
      <div class="svg-chart-legend-item">
        <span class="svg-chart-legend-swatch" style="background: var(--color-data-peach)"></span>
        <span>{{ chartText.questions }}</span>
      </div>
    </div>
  </section>
</template>
