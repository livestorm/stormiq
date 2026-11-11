<script setup>
import { computed, onBeforeUnmount, ref, watch } from "vue";
import Icon from "../ui/Icon.vue";
import UiButton from "../ui/UiButton.vue";
import { countUp, surpriseBeats } from "../../followup/surpriseBeats";

// Surprise me, played: the take one beat at a time in large type, read aloud
// by the browser's own voice (`speechSynthesis`; nothing is sent anywhere,
// nothing is paid). Numbers count up, key words get a marker, punchlines pop
// after a beat of silence. Without a voice (switched off, or a browser that
// has none) each beat stays on screen for a time set by its length. With
// "reduce motion", the beats change without animation.
const props = defineProps({
  take: { type: String, required: true },
  chapterTitles: { type: Array, default: () => [] },
  // BCP 47 language of the take, for the voice ("en").
  lang: { type: String, default: "en" },
});

const beats = computed(() => surpriseBeats(props.take, props.chapterTitles));
const index = ref(-1); // the beat on screen, -1 before the start
const playing = ref(false);
const speech = typeof window !== "undefined" && "speechSynthesis" in window ? window.speechSynthesis : null;
const voiceOn = ref(Boolean(speech));
const reduced = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
const counted = ref({}); // part index → the number shown while it counts up

let timer = null;
let frame = null;
let run = 0; // bumps on every stop, so a late "end" of an old beat is ignored

const beat = computed(() => (index.value >= 0 ? beats.value[index.value] : null));
const finished = computed(() => index.value >= beats.value.length - 1 && !playing.value && index.value >= 0);

function voice() {
  const voices = speech?.getVoices?.() || [];
  const want = props.lang.toLowerCase();
  return voices.find((v) => v.lang?.toLowerCase().startsWith(want) && v.default) || voices.find((v) => v.lang?.toLowerCase().startsWith(want)) || null;
}

function clearTimers() {
  clearTimeout(timer);
  cancelAnimationFrame(frame);
  timer = null;
  frame = null;
}

function animateNumbers() {
  counted.value = {};
  if (reduced || !beat.value) return;
  const targets = beat.value.parts
    .map((part, i) => (part.kind === "number" ? { i, count: countUp(part.text) } : null))
    .filter((target) => target?.count);
  if (!targets.length) return;
  const start = performance.now();
  const step = (now) => {
    const t = Math.min(1, (now - start) / 700);
    const eased = 1 - (1 - t) ** 3;
    const next = {};
    for (const { i, count } of targets) next[i] = count.format(count.value * eased);
    counted.value = next;
    if (t < 1) frame = requestAnimationFrame(step);
    else counted.value = {};
  };
  frame = requestAnimationFrame(step);
}

function readMs(text) {
  return Math.max(1800, text.split(/\s+/).length * 380);
}

function show(i, mine) {
  if (mine !== run) return;
  if (i >= beats.value.length) {
    playing.value = false;
    return;
  }
  index.value = i;
  animateNumbers();
  const current = beats.value[i];
  const next = () => {
    if (mine !== run) return;
    // A beat of silence before a punchline, a breath after it.
    const pause = beats.value[i + 1]?.kind === "punch" ? 650 : current.kind === "punch" ? 500 : 200;
    timer = setTimeout(() => show(i + 1, mine), pause);
  };
  if (voiceOn.value && speech) {
    const utterance = new SpeechSynthesisUtterance(current.text);
    utterance.lang = props.lang;
    const chosen = voice();
    if (chosen) utterance.voice = chosen;
    utterance.rate = current.kind === "punch" ? 0.95 : 1.02;
    utterance.onend = next;
    utterance.onerror = next;
    speech.speak(utterance);
  } else {
    timer = setTimeout(next, readMs(current.text));
  }
}

function stop() {
  run += 1;
  clearTimers();
  speech?.cancel();
  playing.value = false;
}

function play() {
  if (!beats.value.length) return;
  stop();
  const from = finished.value || index.value < 0 ? 0 : index.value;
  playing.value = true;
  show(from, run);
}

function replay() {
  stop();
  index.value = -1;
  play();
}

function toggleVoice() {
  const wasPlaying = playing.value;
  stop();
  voiceOn.value = !voiceOn.value;
  if (wasPlaying) play();
}

watch(() => props.take, () => {
  stop();
  index.value = -1;
});
onBeforeUnmount(stop);
</script>

<template>
  <section class="sp" aria-label="Surprise me, played">
    <div class="sp-stage" :class="beat ? `is-${beat.kind}` : 'is-idle'">
      <Transition :name="reduced ? '' : 'sp-beat'" mode="out-in">
        <p v-if="beat" :key="index" class="sp-beat" :class="`is-${beat.kind}`" :lang="lang">
          <template v-for="(part, i) in beat.parts" :key="i">
            <span v-if="part.kind === 'number'" class="sp-number">{{ counted[i] ?? part.text }}</span>
            <mark v-else-if="part.kind === 'keyword'" class="sp-keyword">{{ part.text }}</mark>
            <template v-else>{{ part.text }}</template>
          </template>
        </p>
        <p v-else class="sp-beat is-idle">Press play: the take, read aloud, one beat at a time.</p>
      </Transition>
    </div>
    <div class="sp-bar">
      <div class="sp-controls">
        <UiButton v-if="!playing" variant="primary" small icon="play" @click="play">
          {{ finished ? "Play again" : index >= 0 ? "Resume" : "Play" }}
        </UiButton>
        <UiButton v-else variant="secondary" small @click="stop">Pause</UiButton>
        <UiButton v-if="index >= 0" variant="ghost" small icon="refresh-cw" @click="replay">From the start</UiButton>
        <UiButton
          v-if="speech"
          variant="ghost"
          small
          icon="message-circle"
          :aria-pressed="voiceOn ? 'true' : 'false'"
          @click="toggleVoice"
        >
          {{ voiceOn ? "Voice on" : "Voice off" }}
        </UiButton>
      </div>
      <ol class="sp-dots" aria-hidden="true">
        <li v-for="(item, i) in beats" :key="i" class="sp-dot" :class="{ 'is-done': i < index, 'is-now': i === index, 'is-punch': item.kind === 'punch' }" />
      </ol>
    </div>
    <p class="sp-note">
      <Icon name="info" :size="14" /> Read by your browser's own voice; it sounds different from one computer to another.
    </p>
  </section>
</template>

<style scoped>
.sp {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-3);
  margin-bottom: var(--siq-space-6);
}

.sp-stage {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 14rem;
  padding: var(--siq-space-8) var(--siq-space-6);
  border-radius: var(--siq-radius-card);
  background: var(--siq-surface-2);
  box-shadow: inset 0 0 0 1px var(--siq-hairline);
  overflow: hidden;
  text-align: center;
}

.sp-beat {
  max-width: 34ch;
  margin: 0;
  font-size: var(--siq-text-section);
  line-height: 1.35;
  font-weight: var(--siq-weight-regular);
  color: var(--siq-ink);
  text-wrap: balance;
}

.sp-beat.is-title {
  font-size: var(--siq-text-display);
  line-height: var(--siq-text-display-lh);
  letter-spacing: -0.015em;
}

.sp-beat.is-punch {
  font-weight: var(--siq-weight-semibold);
  color: var(--siq-accent);
}

.sp-beat.is-idle {
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  color: var(--siq-muted);
}

.sp-number {
  font-weight: var(--siq-weight-semibold);
  font-variant-numeric: tabular-nums;
  color: var(--siq-accent);
}

/* A marker that sweeps under the word. */
.sp-keyword {
  padding: 0 0.15em;
  border-radius: 0.2em;
  color: inherit;
  background: linear-gradient(transparent 55%, color-mix(in srgb, var(--siq-accent) 28%, transparent) 55%) no-repeat;
  background-size: 100% 100%;
  animation: sp-sweep 0.6s var(--siq-ease) 0.25s both;
}

@keyframes sp-sweep {
  from {
    background-size: 0% 100%;
  }
  to {
    background-size: 100% 100%;
  }
}

.sp-beat-enter-active,
.sp-beat-leave-active {
  transition-property: opacity, transform;
  transition-duration: 0.28s;
  transition-timing-function: var(--siq-ease);
}

.sp-beat-enter-from {
  opacity: 0;
  transform: translateY(10px);
}

.sp-beat-leave-to {
  opacity: 0;
  transform: translateY(-8px);
}

.sp-beat-enter-active.is-punch {
  transition-duration: 0.36s;
}

.sp-beat-enter-from.is-punch {
  opacity: 0;
  transform: scale(0.92);
}

.sp-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--siq-space-3);
}

.sp-controls {
  display: flex;
  flex-wrap: wrap;
  gap: var(--siq-space-2);
}

.sp-dots {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.sp-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--siq-border);
}

.sp-dot.is-punch {
  width: 14px;
  border-radius: 4px;
}

.sp-dot.is-done {
  background: color-mix(in srgb, var(--siq-accent) 45%, var(--siq-border));
}

.sp-dot.is-now {
  background: var(--siq-accent);
}

.sp-note {
  display: flex;
  align-items: center;
  gap: var(--siq-space-1);
  margin: 0;
  font-size: var(--siq-text-caption);
  line-height: var(--siq-text-caption-lh);
  color: var(--siq-muted);
}

@media (max-width: 640px) {
  .sp-stage {
    min-height: 11rem;
    padding: var(--siq-space-6) var(--siq-space-4);
  }
  .sp-beat {
    font-size: var(--siq-text-card-title);
  }
  .sp-beat.is-title {
    font-size: var(--siq-text-section);
    line-height: var(--siq-text-section-lh);
  }
}

@media (prefers-reduced-motion: reduce) {
  .sp-keyword {
    animation: none;
  }
}

@media (forced-colors: active) {
  .sp-stage {
    border: 1px solid CanvasText;
  }
  .sp-keyword {
    background: none;
    text-decoration: underline;
  }
}
</style>
