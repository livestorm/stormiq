<script setup>
import { computed } from "vue";
import SessionTimeAxis from "./SessionTimeAxis.vue";
import { ridgePaths } from "../../report/chartGeometry";
import { coverModel } from "../../report/cover";

// The ridge: the session's shape under the Report's title. The audience's
// chat messages per live minute (`record.pulse.minutes`, lane
// `audienceChat`), its busiest minute marked, the minutes with questions
// dotted under it, the chapters underneath when the session analysis found
// them and the server could place them on the session clock
// (`GET /record/chapters`, `placed`), the time along the bottom, and what
// stands out written below it (`report/cover.js`). Spoken as one sentence.

const props = defineProps({
  // `record.pulse.minutes`: one row per live minute, zero-filled.
  minutes: { type: Array, required: true },
  // `GET /record/chapters`, or null while there is none.
  chapters: { type: Object, default: null },
  // Say why there are no chapters (the Report; not the landing's example).
  explain: { type: Boolean, default: false },
});

const values = computed(() => props.minutes.map((row) => row?.audienceChat ?? 0));
const paths = computed(() => ridgePaths(values.value));
const model = computed(() => coverModel(props.minutes, props.chapters));

const hint = computed(() => {
  if (!props.explain || model.value.segments.length || !props.chapters) return "";
  if (props.chapters.state === "ready" && !props.chapters.placed && props.chapters.chapters?.length) {
    return "The chapters aren't shown here: the recording's clock couldn't be matched to the session's.";
  }
  return "";
});

// The peak's label leans away from the edges so it stays inside the chart.
const peakAlign = computed(() => {
  const left = model.value.peak?.left ?? 50;
  return left < 12 ? "start" : left > 88 ? "end" : "middle";
});
</script>

<template>
  <figure v-if="minutes.length" class="r-cover">
    <figcaption class="r-cover-head">
      <span class="r-cover-title">Audience chat per minute</span>
      <span v-if="model.questions.length" class="r-cover-key">
        <span class="r-cover-swatch" aria-hidden="true" />Questions asked
      </span>
    </figcaption>

    <div class="r-cover-chart" role="img" :aria-label="model.description">
      <div class="r-cover-plot" aria-hidden="true">
        <svg viewBox="0 0 1000 100" preserveAspectRatio="none" focusable="false">
          <path class="r-ridge-area" :d="paths.area" />
          <path class="r-ridge-line" :d="paths.line" />
        </svg>
        <span
          v-for="segment in model.segments.slice(1)"
          :key="`d-${segment.key}`"
          class="r-cover-divider"
          :style="{ left: `${segment.left}%` }"
        />
        <template v-if="model.peak">
          <span class="r-cover-peak" :style="{ left: `${model.peak.left}%` }" />
          <span class="r-cover-peak-label" :class="`is-${peakAlign}`" :style="{ left: `${model.peak.left}%` }">
            {{ model.peak.count }} at {{ model.peak.time }}
          </span>
        </template>
      </div>

      <div v-if="model.questions.length" class="r-cover-questions" aria-hidden="true">
        <span
          v-for="mark in model.questions"
          :key="mark.left"
          class="r-cover-question"
          :class="{ 'is-many': mark.count > 1 }"
          :style="{ left: `${mark.left}%` }"
          :title="mark.title"
        />
      </div>

      <div v-if="model.segments.length" class="r-cover-chapters" aria-hidden="true">
        <span
          v-for="segment in model.segments"
          :key="segment.key"
          class="r-cover-chapter"
          :class="{ 'is-lively': segment.key === model.lively }"
          :style="{ left: `${segment.left}%`, width: `${segment.width}%` }"
          :title="`${segment.start} · ${segment.title}`"
        >
          <span class="r-cover-chapter-name">{{ segment.title }}</span>
        </span>
      </div>

      <SessionTimeAxis :count="model.count" />
    </div>

    <dl v-if="model.glance.length" class="r-cover-glance">
      <div v-for="item in model.glance" :key="item.label" class="r-cover-fact">
        <dt>{{ item.label }}</dt>
        <dd>
          <span class="r-cover-value">{{ item.value }}</span>
          <span class="r-cover-detail">{{ item.detail }}</span>
        </dd>
      </div>
    </dl>
    <p v-if="hint" class="r-cover-hint">{{ hint }}</p>
  </figure>
</template>

<style scoped>
.r-cover {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-2);
}

.r-cover-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--siq-space-2) var(--siq-space-4);
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
}

.r-cover-title {
  font-weight: var(--siq-weight-semibold);
  color: var(--siq-ink-2);
}

.r-cover-key {
  display: inline-flex;
  align-items: center;
  gap: var(--siq-space-1);
  color: var(--siq-muted);
}

.r-cover-swatch {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--siq-s2);
}

.r-cover-plot {
  position: relative;
  height: 104px;
  margin-top: var(--siq-text-caption-lh);
}

.r-cover-plot svg {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  display: block;
}

.r-ridge-area {
  fill: var(--siq-ridge-fill);
}

.r-ridge-line {
  fill: none;
  stroke: var(--siq-ink-2);
  stroke-width: 1px;
  vector-effect: non-scaling-stroke;
}

.r-cover-divider {
  position: absolute;
  top: 0;
  bottom: 0;
  border-left: 1px dashed var(--siq-hairline);
}

.r-cover-peak {
  position: absolute;
  top: 6%;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--siq-ink);
  box-shadow: 0 0 0 2px var(--siq-page);
  transform: translate(-50%, -50%);
}

.r-cover-peak-label {
  position: absolute;
  bottom: calc(94% + var(--siq-space-2));
  white-space: nowrap;
  font-size: var(--siq-text-caption);
  line-height: var(--siq-text-caption-lh);
  font-weight: var(--siq-weight-semibold);
  color: var(--siq-ink);
  font-variant-numeric: tabular-nums;
  transform: translateX(-50%);
}

.r-cover-peak-label.is-start {
  transform: translateX(-4px);
}

.r-cover-peak-label.is-end {
  transform: translateX(calc(-100% + 4px));
}

.r-cover-questions {
  position: relative;
  height: 10px;
}

.r-cover-question {
  position: absolute;
  top: 1px;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--siq-s2);
  transform: translateX(-50%);
}

.r-cover-question.is-many {
  box-shadow: 0 0 0 2px var(--siq-page), 0 0 0 3px var(--siq-s2);
}

.r-cover-chapters {
  position: relative;
  height: calc(var(--siq-text-caption-lh) + 10px);
}

.r-cover-chapter {
  position: absolute;
  top: 0;
  bottom: 0;
  box-sizing: border-box;
  padding: 6px var(--siq-space-1) 0 0;
  border-top: 3px solid var(--siq-hairline);
  box-shadow: inset 1px 0 0 var(--siq-page);
  overflow: hidden;
}

.r-cover-chapter.is-lively {
  border-top-color: var(--siq-accent);
}

.r-cover-chapter-name {
  display: block;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: var(--siq-text-caption);
  line-height: var(--siq-text-caption-lh);
  color: var(--siq-muted);
}

.r-cover-chapter.is-lively .r-cover-chapter-name {
  color: var(--siq-ink-2);
  font-weight: var(--siq-weight-medium);
}

.r-cover-glance {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: var(--siq-space-3) var(--siq-space-6);
  margin: var(--siq-space-2) 0 0;
}

.r-cover-fact dt {
  font-size: var(--siq-text-caption);
  line-height: var(--siq-text-caption-lh);
  color: var(--siq-muted);
}

.r-cover-fact dd {
  display: flex;
  flex-direction: column;
  margin: 0;
}

.r-cover-value {
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  font-weight: var(--siq-weight-semibold);
  color: var(--siq-ink);
  overflow-wrap: anywhere;
}

.r-cover-detail,
.r-cover-hint {
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-muted);
}

.r-cover-hint {
  margin: 0;
}

@media (max-width: 640px) {
  .r-cover-plot {
    height: 64px;
  }
}

@media (forced-colors: active) {
  .r-ridge-line {
    stroke: CanvasText;
  }

  .r-cover-peak,
  .r-cover-question {
    background: CanvasText;
  }

  .r-cover-chapter {
    border-top-color: CanvasText;
  }

  .r-cover-chapter.is-lively {
    border-top-color: Highlight;
  }
}
</style>
