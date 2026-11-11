<script setup>
import { computed, onBeforeUnmount, onMounted, ref, useId } from "vue";
import Icon from "../ui/Icon.vue";
import SessionTimeAxis from "./SessionTimeAxis.vue";
import { busiestMinute, ridgePaths } from "../../report/chartGeometry";
import { coverModel } from "../../report/cover";
import { minuteFromKey, minuteFromX, minuteStory, momentMarks, storyIndex } from "../../report/coverStory";

// The ridge: the session's shape under the Report's title. The audience's
// chat messages per live minute (`record.pulse.minutes`, lane
// `audienceChat`), its busiest minute marked, the minutes with questions
// dotted under it, the chapters underneath when the session analysis found
// them and the server could place them on the session clock
// (`GET /record/chapters`, `placed`), the time along the bottom, and what
// stands out written below it (`report/cover.js`).
//
// It replays the session: moving the mouse, a finger or the arrow keys
// across it snaps a guide to each minute, lights up its chapter and shows a
// card with the minute's chapter, counts, what was being said and a few
// messages by name (`report/coverStory.js`, from `GET /record/transcript`
// when the page passes it). The key moments (`GET /record/moments`) sit on
// the ridge as numbered pins; a pin tells its moment and leads to its card
// under the Pulse. For screen readers the chart is a slider whose value is
// the minute's card in one line.

const props = defineProps({
  // `record.pulse.minutes`: one row per live minute, zero-filled.
  minutes: { type: Array, required: true },
  // `GET /record/chapters`, or null while there is none.
  chapters: { type: Object, default: null },
  // `GET /record/transcript`, or null (the landing's example has none).
  transcript: { type: Object, default: null },
  // `GET /record/moments`, or null.
  moments: { type: Object, default: null },
  // Say why there are no chapters (the Report; not the landing's example).
  explain: { type: Boolean, default: false },
});

const uid = useId();
const values = computed(() => props.minutes.map((row) => row?.audienceChat ?? 0));
const paths = computed(() => ridgePaths(values.value));
const model = computed(() => coverModel(props.minutes, props.chapters));
const story = computed(() => storyIndex(props.transcript, props.minutes.length));
const marks = computed(() => momentMarks(props.moments, props.minutes));
const peakMinute = computed(() => busiestMinute(props.minutes, "audienceChat")?.minute ?? null);
const topValue = computed(() => Math.max(0, ...values.value));

const hint = computed(() => {
  if (!props.explain || model.value.segments.length || !props.chapters) return "";
  if (props.chapters.state === "ready" && !props.chapters.placed && props.chapters.chapters?.length) {
    return "The chapters aren't shown here: the recording's clock couldn't be matched to the session's.";
  }
  return "";
});

// Labels lean away from the edges so they stay inside the chart.
const align = (left) => (left < 14 ? "start" : left > 86 ? "end" : "middle");
const peakAlign = computed(() => align(model.value.peak?.left ?? 50));

// --- Replay --------------------------------------------------------------

const stage = ref(null);
const active = ref(null); // the minute index under the guide, or null
const pin = ref(null); // the key moment whose pin is hovered or focused

const current = computed(() =>
  active.value === null
    ? null
    : minuteStory(active.value, {
        minutes: props.minutes,
        chapters: props.chapters,
        story: story.value,
        marks: marks.value,
        peakMinute: peakMinute.value,
      }),
);
const shownMark = computed(() => marks.value.find((mark) => mark.n === pin.value) || null);

// Where the guide and the card stand: the pin's minute wins over the guide.
const focusLeft = computed(() => shownMark.value?.left ?? current.value?.left ?? null);
const guideTop = computed(() => {
  const index = shownMark.value?.minute ?? active.value;
  if (index === null || !(topValue.value > 0)) return 99;
  return 99 - ((values.value[index] || 0) / topValue.value) * 93;
});
const activeSegment = computed(() => {
  const left = focusLeft.value;
  if (left === null) return null;
  return [...model.value.segments].reverse().find((segment) => segment.left <= left) || null;
});
// The hovered chapter's full name covers its truncated one: from the
// chapter's start, or back from its end in the right part of the chart.
const flagStyle = computed(() => {
  const segment = activeSegment.value;
  if (!segment) return {};
  const end = segment.left + segment.width;
  return segment.left > 55
    ? { right: `${100 - end}%`, minWidth: `${segment.width}%`, textAlign: "right" }
    : { left: `${segment.left}%`, minWidth: `${segment.width}%` };
});
// The peak's label gives way to a key moment's pin on the same minute.
const peakPinned = computed(() => marks.value.some((mark) => mark.left === model.value.peak?.left));

const cardStyle = computed(() => {
  const left = focusLeft.value ?? 0;
  return left < 50
    ? { left: `calc(${left}% + 14px)`, maxWidth: `calc(${100 - left}% - 22px)` }
    : { right: `calc(${100 - left}% + 14px)`, maxWidth: `calc(${left}% - 22px)` };
});

function moveTo(event) {
  const box = stage.value?.getBoundingClientRect();
  if (!box) return;
  pin.value = null;
  active.value = minuteFromX(event.clientX - box.left, box.width, props.minutes.length);
}

function onPointerDown(event) {
  moveTo(event);
  if (event.pointerType === "mouse") return;
  try {
    // A finger keeps scrubbing even when it slides off the chart.
    stage.value?.setPointerCapture?.(event.pointerId);
  } catch {
    // The pointer is already gone: nothing to follow.
  }
}

function onPointerLeave(event) {
  // A finger leaves when it lifts: the card stays until the next touch.
  if (event.pointerType === "mouse") active.value = null;
}

function onKey(event) {
  if (event.key === "Escape") {
    active.value = null;
    return;
  }
  const next = minuteFromKey(event.key, active.value, props.minutes.length);
  if (next === null) return;
  event.preventDefault();
  pin.value = null;
  active.value = next;
}

function openMoment(mark) {
  const target = document.getElementById(`key-moment-${mark.n}`);
  if (!target) return;
  const still = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  target.scrollIntoView({ behavior: still ? "auto" : "smooth", block: "center" });
}

// A tap outside the chart puts the card away on a touch screen.
const figure = ref(null);
function onOutside(event) {
  if (figure.value && !figure.value.contains(event.target)) {
    active.value = null;
    pin.value = null;
  }
}
onMounted(() => document.addEventListener("pointerdown", onOutside));
onBeforeUnmount(() => document.removeEventListener("pointerdown", onOutside));
</script>

<template>
  <figure v-if="minutes.length" ref="figure" class="r-cover">
    <figcaption class="r-cover-head">
      <span class="r-cover-title">Audience chat per minute</span>
      <span class="r-cover-keys">
        <span v-if="marks.length" class="r-cover-key">
          <span class="r-cover-pin-key" aria-hidden="true">1</span>Key moments
        </span>
        <span v-if="model.questions.length" class="r-cover-key">
          <span class="r-cover-swatch" aria-hidden="true" />Questions asked
        </span>
      </span>
    </figcaption>
    <p :id="`${uid}-desc`" class="siq-sr-only">{{ model.description }}</p>

    <div class="r-cover-frame">
      <div
        ref="stage"
        class="r-cover-chart"
        :class="{ 'is-scrubbing': focusLeft !== null }"
        role="slider"
        tabindex="0"
        aria-label="Replay the session minute by minute"
        :aria-describedby="`${uid}-desc`"
        aria-valuemin="0"
        :aria-valuemax="minutes.length - 1"
        :aria-valuenow="active ?? 0"
        :aria-valuetext="current ? current.text : 'Use the arrow keys to move through the session, one minute at a time'"
        @pointerdown="onPointerDown"
        @pointermove="moveTo"
        @pointerleave="onPointerLeave"
        @keydown="onKey"
        @blur="active = null"
      >
        <div class="r-cover-plot" aria-hidden="true">
          <span
            v-if="activeSegment"
            class="r-cover-band"
            :style="{ left: `${activeSegment.left}%`, width: `${activeSegment.width}%` }"
          />
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
            <span
              v-if="!peakPinned"
              class="r-cover-peak-label"
              :class="`is-${peakAlign}`" :style="{ left: `${model.peak.left}%` }">
              {{ model.peak.count }} at {{ model.peak.time }}
            </span>
          </template>
          <template v-if="focusLeft !== null">
            <span class="r-cover-guide" :style="{ left: `${focusLeft}%` }" />
            <span class="r-cover-guide-dot" :style="{ left: `${focusLeft}%`, top: `${guideTop}%` }" />
          </template>
        </div>

        <div v-if="model.questions.length" class="r-cover-questions" aria-hidden="true">
          <span
            v-for="mark in model.questions"
            :key="mark.left"
            class="r-cover-question"
            :class="{ 'is-many': mark.count > 1, 'is-active': focusLeft === mark.left }"
            :style="{ left: `${mark.left}%` }"
          />
        </div>

        <div v-if="model.segments.length" class="r-cover-chapters" aria-hidden="true">
          <span
            v-for="segment in model.segments"
            :key="segment.key"
            class="r-cover-chapter"
            :class="{ 'is-lively': segment.key === model.lively, 'is-active': segment === activeSegment }"
            :style="{ left: `${segment.left}%`, width: `${segment.width}%` }"
          >
            <span class="r-cover-chapter-name">{{ segment.title }}</span>
          </span>
          <span v-if="activeSegment" class="r-cover-flag" :style="flagStyle">
            {{ activeSegment.title }}
          </span>
        </div>

        <SessionTimeAxis :count="model.count" />
      </div>

      <div v-if="marks.length" class="r-cover-pins">
        <button
          v-for="(mark, order) in marks"
          :key="mark.n"
          type="button"
          class="r-cover-pin siq-fx"
          :class="{ 'is-on': pin === mark.n || (current?.moment?.n === mark.n && pin === null) }"
          :style="{ left: `${mark.left}%`, top: `${mark.top}%`, '--order': order }"
          :aria-label="`${mark.label}. Show its story`"
          @pointerenter="pin = mark.n"
          @pointerleave="pin = null"
          @focus="pin = mark.n"
          @blur="pin = null"
          @click="openMoment(mark)"
        >
          {{ mark.n }}
        </button>
      </div>

      <Transition name="r-card">
        <aside
          v-if="shownMark"
          :key="`m-${shownMark.n}`"
          class="r-cover-card"
          :style="cardStyle"
          aria-live="polite"
        >
          <p class="r-card-top">
            <span class="r-card-badge is-moment">Key moment {{ shownMark.n }}</span>
            <span class="r-card-when">{{ shownMark.view.when }}</span>
          </p>
          <p class="r-card-kind"><Icon :name="shownMark.view.icon" :size="14" />{{ shownMark.view.kind }}</p>
          <p class="r-card-muted">{{ shownMark.view.takeaway }}</p>
          <p class="r-card-counts">{{ shownMark.view.facts }}</p>
          <div v-if="shownMark.view.prompt" class="r-card-part">
            <p class="r-card-label"><Icon name="presentation" :size="13" />At {{ shownMark.view.prompt.time }}, the stage asked</p>
            <blockquote class="r-card-quote">“{{ shownMark.view.prompt.text }}”</blockquote>
          </div>
          <div v-else-if="shownMark.view.said" class="r-card-part">
            <p class="r-card-label"><Icon name="presentation" :size="13" />{{ shownMark.view.said.label }}</p>
            <blockquote class="r-card-quote">“{{ shownMark.view.said.text }}”</blockquote>
          </div>
          <ul v-if="shownMark.view.written.length" class="r-card-lines">
            <li v-for="item in shownMark.view.written.slice(0, 2)" :key="item.id">
              <strong>{{ item.author }}</strong> {{ item.text }}
            </li>
          </ul>
          <p class="r-card-more">Select the pin to read the whole moment</p>
        </aside>

        <aside
          v-else-if="current"
          :key="`t-${current.index}`"
          class="r-cover-card"
          :style="cardStyle"
          aria-hidden="true"
        >
          <p class="r-card-top">
            <span class="r-card-when">{{ current.range }}</span>
            <span v-if="current.peak" class="r-card-badge is-peak">Busiest minute</span>
            <span v-if="current.moment" class="r-card-badge is-moment">Key moment {{ current.moment.n }}</span>
          </p>
          <p v-if="current.chapter" class="r-card-chapter">
            <span class="r-card-muted">Chapter {{ current.chapter.number }} of {{ current.chapter.of }}</span>
            {{ current.chapter.title }}
          </p>
          <p class="r-card-counts">
            <template v-if="current.counts"><Icon name="message-circle" :size="14" />{{ current.counts }}</template>
            <template v-else>A quiet minute: nobody wrote.</template>
          </p>
          <div v-if="current.said" class="r-card-part">
            <p class="r-card-label"><Icon name="presentation" :size="13" />{{ current.said.speaker || "On stage" }}</p>
            <blockquote class="r-card-quote">“{{ current.said.text }}”</blockquote>
          </div>
          <ul v-if="current.written.length" class="r-card-lines">
            <li v-for="item in current.written" :key="item.id"><strong>{{ item.author }}</strong> {{ item.text }}</li>
          </ul>
          <ul v-if="current.asked.length" class="r-card-lines is-asked">
            <li v-for="item in current.asked" :key="item.id">
              <Icon name="help-circle" :size="13" /><strong>{{ item.author }}</strong> {{ item.text }}
            </li>
          </ul>
        </aside>
      </Transition>
    </div>

    <p class="r-cover-try" aria-hidden="true">
      <Icon name="play" :size="12" />
      <span class="r-try-fine">Move across the chart to replay the session, minute by minute</span>
      <span class="r-try-touch">Touch and drag across the chart to replay the session</span>
    </p>

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

.r-cover-keys {
  display: inline-flex;
  flex-wrap: wrap;
  gap: var(--siq-space-1) var(--siq-space-4);
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

.r-cover-pin-key {
  display: inline-grid;
  place-items: center;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: var(--siq-accent);
  color: var(--siq-page);
  font-size: 10px;
  font-weight: var(--siq-weight-semibold);
}

.r-cover-frame {
  position: relative;
}

.r-cover-chart {
  position: relative;
  cursor: crosshair;
  touch-action: pan-y;
  border-radius: var(--siq-radius-inline);
  outline: none;
}

.r-cover-chart:focus-visible {
  box-shadow: 0 0 0 2px var(--siq-focus);
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
  animation: r-reveal 1.2s var(--siq-ease) both;
}

@keyframes r-reveal {
  from {
    clip-path: inset(0 100% 0 0);
  }
  to {
    clip-path: inset(0 0 0 0);
  }
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

.r-cover-band {
  position: absolute;
  top: 0;
  bottom: 0;
  background: var(--siq-selected-bg);
  border-radius: var(--siq-radius-inline) var(--siq-radius-inline) 0 0;
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
  transition: opacity var(--siq-duration) var(--siq-ease);
}

.r-cover-peak-label.is-start {
  transform: translateX(-4px);
}

.r-cover-peak-label.is-end {
  transform: translateX(calc(-100% + 4px));
}

.is-scrubbing .r-cover-peak-label {
  opacity: 0.35;
}

.r-cover-guide {
  position: absolute;
  top: 0;
  bottom: calc(-1 * (10px + var(--siq-text-caption-lh) + 10px));
  border-left: 1px solid var(--siq-ink-2);
  pointer-events: none;
}

.r-cover-guide-dot {
  position: absolute;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--siq-accent);
  box-shadow: 0 0 0 3px var(--siq-page);
  transform: translate(-50%, -50%);
  pointer-events: none;
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
  transition: transform var(--siq-duration) var(--siq-ease);
}

.r-cover-question.is-many {
  box-shadow: 0 0 0 2px var(--siq-page), 0 0 0 3px var(--siq-s2);
}

.r-cover-question.is-active {
  transform: translateX(-50%) scale(1.5);
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
  transition: border-top-color var(--siq-duration) var(--siq-ease);
}

.r-cover-chapter.is-lively {
  border-top-color: var(--siq-accent);
}

.r-cover-chapter.is-active {
  border-top-color: var(--siq-ink);
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

/* The hovered chapter's full name, over the truncated ones. */
.r-cover-flag {
  position: absolute;
  top: 3px;
  box-sizing: border-box;
  max-width: 100%;
  padding: 3px var(--siq-space-1) 0 0;
  background: var(--siq-page);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: var(--siq-text-caption);
  line-height: var(--siq-text-caption-lh);
  font-weight: var(--siq-weight-semibold);
  color: var(--siq-ink);
  pointer-events: none;
}

/* The key moments, numbered on the ridge (a layer over the plot). */
.r-cover-pins {
  position: absolute;
  top: var(--siq-text-caption-lh);
  left: 0;
  right: 0;
  height: 104px;
  pointer-events: none;
}

.r-cover-pin {
  position: absolute;
  display: grid;
  place-items: center;
  width: 22px;
  height: 22px;
  padding: 0;
  border: 2px solid var(--siq-page);
  border-radius: 50%;
  background: var(--siq-accent);
  color: var(--siq-page);
  font: inherit;
  font-size: 11px;
  font-weight: var(--siq-weight-semibold);
  line-height: 1;
  cursor: pointer;
  pointer-events: auto;
  transform: translate(-50%, calc(-100% - 4px));
  animation: r-pop 420ms var(--siq-ease) both;
  animation-delay: calc(900ms + var(--order) * 140ms);
  transition-property: transform, background-color;
}

/* A larger, invisible target around each pin. */
.r-cover-pin::after {
  content: "";
  position: absolute;
  inset: -9px;
  border-radius: 50%;
}

.r-cover-pin:hover,
.r-cover-pin.is-on {
  background: var(--siq-accent-hover);
  transform: translate(-50%, calc(-100% - 4px)) scale(1.2);
}

@keyframes r-pop {
  from {
    opacity: 0;
    transform: translate(-50%, -40%) scale(0.4);
  }
  to {
    opacity: 1;
    transform: translate(-50%, calc(-100% - 4px)) scale(1);
  }
}

/* The card that follows the guide. */
.r-cover-card {
  position: absolute;
  top: 0;
  z-index: 3;
  box-sizing: border-box;
  width: 320px;
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-2);
  padding: var(--siq-space-3) var(--siq-space-4);
  border: 1px solid var(--siq-hairline-head);
  border-radius: var(--siq-radius-control);
  background: var(--siq-card);
  box-shadow: var(--siq-card-shadow);
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-ink-2);
  pointer-events: none;
}

.r-cover-card p,
.r-cover-card ul,
.r-cover-card blockquote {
  margin: 0;
}

.r-card-top {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--siq-space-1) var(--siq-space-2);
}

.r-card-when {
  font-weight: var(--siq-weight-semibold);
  color: var(--siq-ink);
  font-variant-numeric: tabular-nums;
}

.r-card-badge {
  padding: 0 var(--siq-space-2);
  border-radius: var(--siq-radius-pill);
  font-size: var(--siq-text-caption);
  line-height: var(--siq-text-caption-lh);
  font-weight: var(--siq-weight-semibold);
  background: var(--siq-surface-2);
  color: var(--siq-ink);
}

.r-card-badge.is-moment {
  background: var(--siq-accent);
  color: var(--siq-page);
}

.r-card-chapter {
  display: flex;
  flex-direction: column;
  font-weight: var(--siq-weight-semibold);
  color: var(--siq-ink);
}

.r-card-chapter .r-card-muted {
  font-weight: var(--siq-weight-regular, 400);
  font-size: var(--siq-text-caption);
  line-height: var(--siq-text-caption-lh);
}

.r-card-muted {
  color: var(--siq-muted);
}

.r-card-kind,
.r-card-counts,
.r-card-label {
  display: flex;
  align-items: center;
  gap: var(--siq-space-1);
}

.r-card-kind {
  font-weight: var(--siq-weight-semibold);
  color: var(--siq-ink);
}

.r-card-counts {
  font-weight: var(--siq-weight-medium);
  color: var(--siq-ink);
}

.r-card-label {
  font-size: var(--siq-text-caption);
  line-height: var(--siq-text-caption-lh);
  color: var(--siq-muted);
}

.r-card-quote {
  padding-left: var(--siq-space-2);
  border-left: 2px solid var(--siq-accent);
  color: var(--siq-ink);
}

.r-card-lines {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-1);
  padding: var(--siq-space-2) 0 0;
  border-top: 1px solid var(--siq-hairline);
  list-style: none;
}

.r-card-lines strong {
  font-weight: var(--siq-weight-semibold);
  color: var(--siq-ink);
}

.r-card-lines.is-asked li {
  color: var(--siq-ink);
}

.r-card-lines.is-asked :deep(svg) {
  margin-right: var(--siq-space-1);
  color: var(--siq-s2);
  vertical-align: -2px;
}

.r-card-more {
  font-size: var(--siq-text-caption);
  line-height: var(--siq-text-caption-lh);
  color: var(--siq-muted);
}

.r-card-enter-active,
.r-card-leave-active {
  transition: opacity 120ms var(--siq-ease);
}

.r-card-leave-active {
  position: absolute;
}

.r-card-enter-from,
.r-card-leave-to {
  opacity: 0;
}

.r-cover-try {
  display: flex;
  align-items: center;
  gap: var(--siq-space-1);
  margin: 0;
  font-size: var(--siq-text-caption);
  line-height: var(--siq-text-caption-lh);
  color: var(--siq-muted);
}

.r-try-touch {
  display: none;
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

/* A phone, or a screen without hover: the card sits under the chart. */
@media (max-width: 640px), (hover: none) {
  .r-cover-card {
    position: static;
    width: auto;
    max-width: none !important;
    margin-top: var(--siq-space-3);
  }

  .r-try-fine {
    display: none;
  }

  .r-try-touch {
    display: inline;
  }
}

@media (max-width: 640px) {
  .r-cover-plot,
  .r-cover-pins {
    height: 64px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .r-cover-plot svg,
  .r-cover-pin {
    animation: none;
  }

  .r-cover-pin,
  .r-cover-question,
  .r-cover-chapter,
  .r-cover-peak-label,
  .r-card-enter-active,
  .r-card-leave-active {
    transition-duration: 0ms;
  }
}

@media print {
  .r-cover-pins,
  .r-cover-card,
  .r-cover-try {
    display: none;
  }
}

@media (forced-colors: active) {
  .r-ridge-line {
    stroke: CanvasText;
  }

  .r-cover-peak,
  .r-cover-question,
  .r-cover-guide-dot {
    background: CanvasText;
  }

  .r-cover-chapter {
    border-top-color: CanvasText;
  }

  .r-cover-chapter.is-lively,
  .r-cover-chapter.is-active {
    border-top-color: Highlight;
  }

  .r-cover-pin {
    border: 2px solid ButtonText;
    background: ButtonFace;
    color: ButtonText;
  }

  .r-cover-card {
    border: 1px solid CanvasText;
  }

  .r-cover-chart:focus-visible {
    outline: 2px solid Highlight;
  }
}
</style>
