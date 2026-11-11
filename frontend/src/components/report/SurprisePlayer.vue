<script setup>
import { computed, onBeforeUnmount, ref, watch } from "vue";
import Icon from "../ui/Icon.vue";
import ProgressSteps from "../ui/ProgressSteps.vue";
import UiButton from "../ui/UiButton.vue";
import { api } from "../../api";
import { beatsWithParts, countUp, surpriseBeats } from "../../followup/surpriseBeats";
import { beatMs, captionWords, curveGeometry, revealedChars, sceneFor } from "../../followup/surpriseScenes";
import { createSurpriseVoice } from "../../followup/useSurpriseVoice";
import { voiceSteps } from "../../report/progress";

// Surprise me, played: one beat at a time, each read by its own OpenAI clip
// (made in the worker the first time someone presses Play, then kept), each
// with a scene drawn from the session's real data (`surpriseScenes.js`):
// the audience chat's curve for a time or a peak, a ring for a percentage, a
// number counting up, a bold card for a punchline, key words otherwise. The
// caption is revealed word by word in step with the clip (its share of the
// text at the clip's share of time). "Sound off" plays the captions alone, at
// each clip's measured length or a reading pace, and needs no clip at all.
// With "reduce motion" nothing moves: the scenes and captions show whole.
const props = defineProps({
  sessionId: { type: String, required: true },
  take: { type: String, required: true },
  chapterTitles: { type: Array, default: () => [] },
  // The Pulse's rows (`record.pulse.minutes`): the audience chat per minute.
  minutes: { type: Array, default: () => [] },
  lang: { type: String, default: "en" },
});

const voice = createSurpriseVoice();
const reduced = typeof window !== "undefined" && Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)").matches);

// ── The beats: the server's once it answered (its clips are cut from the
// saved take the same way), else the page's own cut of the same text. ──
const beats = computed(() => {
  const server = voice.state.body?.beats;
  return server?.length ? beatsWithParts(server, props.chapterTitles) : surpriseBeats(props.take, props.chapterTitles);
});

// ── The voice ──
const voiceState = computed(() => voice.state.body?.state || "");
const voiceReady = computed(() => voiceState.value === "ready");
const making = computed(() => voice.state.starting || voiceState.value === "pending" || voiceState.value === "running");
const failure = computed(
  () => voice.state.error || (voiceState.value === "error" ? voice.state.body?.error || "The voice could not be made." : ""),
);
const steps = computed(() => voiceSteps(voice.state.body?.progress));
const percent = computed(() => {
  const value = voice.state.body?.progress?.percent;
  return typeof value === "number" ? value : null;
});

// ── Playback ──
const index = ref(-1); // the beat on screen, -1 before the start
const playing = ref(false);
const fraction = ref(0); // how far into the beat on screen, 0 to 1
const muted = ref(false); // captions only, no sound
const waiting = ref(false); // Play pressed while the voice is being made
const counted = ref(null); // the number shown while it counts up

let audio = null;
let preloader = null;
let loadedUrl = "";
let mode = "silent"; // how the beat on screen plays: "voice" | "silent"
let frame = null;
let timer = null;
let counter = null;
let silentStart = 0;
let silentMs = 0;
let run = 0; // bumps on every stop, so a late event of an old beat is ignored

const beat = computed(() => (index.value >= 0 ? beats.value[index.value] || null : null));
const finished = computed(() => index.value === beats.value.length - 1 && !playing.value && fraction.value >= 1);
const scene = computed(() => (beat.value ? sceneFor(beat.value, { minutes: props.minutes }) : null));
const curve = computed(() => (scene.value?.type === "curve" ? curveGeometry(props.minutes, scene.value.minute) : null));
const shownType = computed(() => (scene.value?.type === "curve" && !curve.value ? "words" : scene.value?.type || "idle"));
const words = computed(() => captionWords(beat.value?.parts || []));
const reveal = computed(() => (reduced ? Infinity : revealedChars(beat.value?.text.length || 0, fraction.value)));
const bigCaption = computed(() => ["punch", "words"].includes(shownType.value));

function clipUrl(item) {
  return item?.clip ? api.getSurpriseVoiceClipUrl(props.sessionId, item.clip) : "";
}

function withVoice(item) {
  return !muted.value && voiceReady.value && Boolean(item?.clip);
}

function clearTimers() {
  cancelAnimationFrame(frame);
  cancelAnimationFrame(counter);
  clearTimeout(timer);
  frame = counter = timer = null;
}

function stop() {
  run += 1;
  clearTimers();
  audio?.pause();
  playing.value = false;
}

// A count's number counts up to the one said, then stays. A ring fills to its
// percentage with the percentage itself shown from the start.
function countNumber(item, ms) {
  counted.value = null;
  const shown = sceneFor(item, { minutes: props.minutes });
  const target = shown.type === "count" ? countUp(shown.label) : null;
  if (reduced || !target || !ms) return;
  const start = performance.now();
  const length = Math.min(1100, Math.max(500, ms * 0.6));
  const tickCount = (now) => {
    const t = Math.min(1, (now - start) / length);
    counted.value = target.format(target.value * (1 - (1 - t) ** 3));
    if (t < 1) counter = requestAnimationFrame(tickCount);
    else counted.value = null;
  };
  counter = requestAnimationFrame(tickCount);
}

function finishBeat(i, mine) {
  if (mine !== run) return;
  fraction.value = 1;
  cancelAnimationFrame(frame);
  if (i + 1 >= beats.value.length) {
    playing.value = false;
    return;
  }
  // A beat of silence before a punchline, a breath after it.
  const pause = beats.value[i + 1]?.kind === "punch" ? 450 : beats.value[i].kind === "punch" ? 400 : 150;
  timer = setTimeout(() => startBeat(i + 1, mine, 0), pause);
}

function tick(i, mine) {
  if (mine !== run) return;
  if (mode === "voice") {
    const length = Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : beats.value[i]?.durationS;
    fraction.value = length ? Math.min(1, audio.currentTime / length) : 0;
  } else {
    fraction.value = Math.min(1, (performance.now() - silentStart) / silentMs);
    if (fraction.value >= 1) {
      finishBeat(i, mine);
      return;
    }
  }
  frame = requestAnimationFrame(() => tick(i, mine));
}

function playSilent(i, mine, from) {
  if (mine !== run) return;
  mode = "silent";
  audio?.pause();
  silentMs = beatMs(beats.value[i]);
  silentStart = performance.now() - from * silentMs;
  cancelAnimationFrame(frame);
  frame = requestAnimationFrame(() => tick(i, mine));
}

function preloadNext(i) {
  const next = beats.value[i + 1];
  if (!withVoice(next)) return;
  preloader ||= new Audio();
  preloader.preload = "auto";
  preloader.src = clipUrl(next);
}

function startBeat(i, mine, from = 0) {
  if (mine !== run) return;
  const item = beats.value[i];
  if (!item) return;
  if (from === 0) countNumber(item, (item.durationS || 0) * 1000 || beatMs(item));
  index.value = i;
  fraction.value = from;
  if (!withVoice(item)) {
    playSilent(i, mine, from);
    return;
  }
  mode = "voice";
  audio ||= new Audio();
  const url = clipUrl(item);
  if (loadedUrl !== url) {
    audio.src = url;
    loadedUrl = url;
    fraction.value = 0;
  } else if (from === 0) {
    audio.currentTime = 0;
  }
  audio.onended = () => finishBeat(i, mine);
  // A clip that can't load: this beat plays silently, the next tries its own.
  audio.onerror = () => {
    if (mine === run && mode === "voice") playSilent(i, mine, fraction.value);
  };
  audio.play().then(
    () => {
      if (mine === run && mode === "voice") frame = requestAnimationFrame(() => tick(i, mine));
    },
    (error) => {
      if (mine !== run || error?.name === "AbortError") return;
      if (error?.name === "NotAllowedError") {
        // The browser wants a click before it plays sound: wait for Play.
        run += 1;
        playing.value = false;
        return;
      }
      if (mode === "voice") playSilent(i, mine, fraction.value);
    },
  );
  preloadNext(i);
}

function begin(i, from = 0) {
  if (!beats.value.length) return;
  stop();
  waiting.value = false;
  playing.value = true;
  startBeat(Math.max(0, Math.min(i, beats.value.length - 1)), run, from);
}

function play() {
  if (!beats.value.length) return;
  if (!muted.value && !voiceReady.value) {
    // The first play records the voice (paid, in the worker), then starts.
    waiting.value = true;
    if (!making.value) voice.start();
    return;
  }
  if (finished.value || index.value < 0) begin(0);
  else begin(index.value, fraction.value >= 1 ? 0 : fraction.value);
}

function playSilently() {
  muted.value = true;
  begin(0);
}

function retryVoice() {
  muted.value = false;
  waiting.value = true;
  voice.start();
}

function step(by) {
  const target = Math.max(0, Math.min(beats.value.length - 1, (index.value < 0 ? 0 : index.value) + by));
  if (playing.value) {
    begin(target);
    return;
  }
  stop();
  counted.value = null;
  index.value = target;
  fraction.value = 1;
}

function toggleSound() {
  const was = playing.value;
  stop();
  muted.value = !muted.value;
  if (!was) return;
  if (!muted.value && !voiceReady.value) play();
  else begin(index.value);
}

function segment(i) {
  if (i < index.value) return "100%";
  if (i === index.value) return `${Math.round(fraction.value * 100)}%`;
  return "0%";
}

// The voice is made: start if Play is waiting for it. It failed: stop waiting.
watch(voiceReady, (ready) => {
  if (ready && waiting.value) begin(0);
});
watch(failure, (message) => {
  if (message) waiting.value = false;
});
watch(
  () => props.sessionId,
  (id) => voice.open(id),
  { immediate: true },
);
// A new take: its beats and clips are new.
watch(
  () => props.take,
  () => {
    stop();
    index.value = -1;
    fraction.value = 0;
    voice.reload();
  },
);
onBeforeUnmount(() => {
  stop();
  voice.close();
  if (audio) audio.removeAttribute("src");
  if (preloader) preloader.removeAttribute("src");
});
</script>

<template>
  <section class="sp" aria-label="Surprise me, played" :lang="lang">
    <div class="sp-stage" :class="`is-${shownType}`">
      <div v-if="making && waiting" class="sp-making" role="status">
        <ProgressSteps :steps="steps" :percent="percent" label="Recording the voice" note="The voice is kept: the next play starts at once." />
        <UiButton variant="ghost" small icon="volume-x" @click="playSilently">Play with captions only</UiButton>
      </div>

      <div v-else-if="!beat" class="sp-scene is-idle">
        <div class="sp-glow" aria-hidden="true" />
        <p class="sp-idle-text">Press play: the take, read aloud, one beat at a time, over the session's own data.</p>
      </div>

      <Transition v-else :name="reduced ? '' : 'sp-scene'" mode="out-in">
        <div :key="index" class="sp-scene" :class="[`is-${shownType}`, { 'is-punchline': scene.punch }]">
          <div v-if="shownType === 'words' || shownType === 'punch'" class="sp-glow" aria-hidden="true" />

          <div v-if="shownType === 'curve'" class="sp-curve" aria-hidden="true">
            <svg class="sp-curve-svg" viewBox="0 0 1000 200" preserveAspectRatio="none">
              <path :d="curve.area" class="sp-curve-area" />
              <path :d="curve.line" class="sp-curve-line" pathLength="1" vector-effect="non-scaling-stroke" />
            </svg>
            <span class="sp-curve-rule" :style="{ left: `${curve.x / 10}%` }" />
            <span class="sp-curve-dot" :style="{ left: `${curve.x / 10}%`, top: `${curve.y / 2}%` }" />
            <span class="sp-curve-tag" :style="{ left: `${Math.min(88, Math.max(12, curve.x / 10))}%` }">
              {{ scene.label || "The peak" }}
            </span>
            <span class="sp-curve-axis">The audience chat, minute by minute</span>
          </div>

          <div v-else-if="shownType === 'ring'" class="sp-ring" aria-hidden="true">
            <svg viewBox="0 0 120 120" class="sp-ring-svg">
              <circle cx="60" cy="60" r="50" class="sp-ring-track" pathLength="100" />
              <circle cx="60" cy="60" r="50" class="sp-ring-fill" pathLength="100" :style="{ strokeDashoffset: 100 - scene.value }" />
            </svg>
            <span class="sp-ring-value">{{ scene.label }}</span>
          </div>

          <div v-else-if="shownType === 'count'" class="sp-count" aria-hidden="true">{{ counted ?? scene.label }}</div>

          <p class="sp-caption" :class="{ 'is-big': bigCaption, 'is-title': scene.title }" aria-hidden="true">
            <span
              v-for="(word, i) in words"
              :key="i"
              class="sp-word"
              :class="[`is-${word.kind}`, { 'is-on': word.start < reveal }]"
            >{{ word.text }}</span>
          </p>
        </div>
      </Transition>
      <p v-if="beat" class="siq-sr-only" aria-live="polite">{{ beat.text }}</p>
    </div>

    <div class="sp-track" aria-hidden="true">
      <span v-for="(item, i) in beats" :key="i" class="sp-segment" :class="{ 'is-punch': item.kind === 'punch' }">
        <span class="sp-segment-fill" :style="{ width: segment(i) }" />
      </span>
    </div>

    <div class="sp-bar">
      <div class="sp-controls">
        <UiButton variant="icon" icon="chevron-left" label="Previous beat" :disabled="index <= 0" @click="step(-1)" />
        <UiButton
          v-if="!playing"
          variant="primary"
          small
          :icon="making && waiting ? 'timer' : 'play'"
          :disabled="making && waiting"
          @click="play"
        >
          {{ making && waiting ? "Recording…" : finished ? "Play again" : index >= 0 ? "Resume" : "Play" }}
        </UiButton>
        <UiButton v-else variant="secondary" small icon="pause" @click="stop">Pause</UiButton>
        <UiButton variant="icon" icon="chevron-right" label="Next beat" :disabled="index >= beats.length - 1" @click="step(1)" />
        <UiButton v-if="index >= 0" variant="ghost" small icon="refresh-cw" @click="begin(0)">From the start</UiButton>
        <UiButton variant="ghost" small :icon="muted ? 'volume-x' : 'volume-2'" :aria-pressed="muted ? 'true' : 'false'" @click="toggleSound">
          {{ muted ? "Sound off" : "Sound on" }}
        </UiButton>
      </div>
      <span class="sp-position">{{ index >= 0 ? `Beat ${index + 1} of ${beats.length}` : `${beats.length} beats` }}</span>
    </div>

    <div v-if="failure" class="sp-failure" role="alert">
      <p class="sp-note"><Icon name="info" :size="14" /> {{ failure }}</p>
      <div class="sp-controls">
        <UiButton variant="secondary" small icon="refresh-cw" :disabled="making" @click="retryVoice">Try the voice again</UiButton>
        <UiButton variant="ghost" small icon="volume-x" @click="playSilently">Play with captions only</UiButton>
      </div>
    </div>
    <p v-else class="sp-note">
      <Icon name="info" :size="14" />
      {{
        voiceReady
          ? "Read by an AI voice (OpenAI). The scenes show this session's own data."
          : "The first play records an AI voice (OpenAI) from the take's text alone, in a minute or so. Then it is kept."
      }}
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
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 17rem;
  padding: var(--siq-space-6);
  border-radius: var(--siq-radius-card);
  background: var(--siq-surface-2);
  box-shadow: inset 0 0 0 1px var(--siq-hairline);
  overflow: hidden;
  text-align: center;
}

.sp-making {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--siq-space-3);
  width: min(100%, 28rem);
  text-align: left;
}

.sp-scene {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--siq-space-4);
  width: 100%;
}

.sp-idle-text {
  position: relative;
  max-width: 34ch;
  margin: 0;
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  color: var(--siq-muted);
}

/* ── The soft background of the words and punch scenes ── */
.sp-glow {
  position: absolute;
  inset: -40%;
  background:
    radial-gradient(closest-side, color-mix(in srgb, var(--siq-s1) 22%, transparent), transparent) 20% 30% / 55% 55% no-repeat,
    radial-gradient(closest-side, color-mix(in srgb, var(--siq-s2) 20%, transparent), transparent) 80% 70% / 60% 60% no-repeat;
  animation: sp-drift 9s var(--siq-ease) infinite alternate;
  pointer-events: none;
}

@keyframes sp-drift {
  from {
    transform: translate3d(-4%, -2%, 0) rotate(0deg);
  }
  to {
    transform: translate3d(4%, 3%, 0) rotate(8deg);
  }
}

/* ── The caption, revealed word by word ── */
.sp-caption {
  position: relative;
  max-width: 34ch;
  margin: 0;
  font-size: var(--siq-text-card-title);
  line-height: 1.4;
  color: var(--siq-ink);
  text-wrap: balance;
}

.sp-caption.is-big {
  font-size: var(--siq-text-section);
  line-height: 1.35;
}

.sp-caption.is-title {
  font-size: var(--siq-text-display);
  line-height: var(--siq-text-display-lh);
  letter-spacing: -0.015em;
}

.sp-scene.is-punchline .sp-caption {
  font-weight: var(--siq-weight-semibold);
}

.sp-word {
  opacity: 0.18;
  transition-property: opacity;
  transition-duration: 0.18s;
  transition-timing-function: var(--siq-ease);
}

.sp-word.is-on {
  opacity: 1;
}

.sp-word.is-number {
  font-weight: var(--siq-weight-semibold);
  font-variant-numeric: tabular-nums;
  color: var(--siq-accent);
}

.sp-word.is-keyword {
  padding: 0 0.1em;
  border-radius: 0.2em;
  background: linear-gradient(transparent 55%, color-mix(in srgb, var(--siq-accent) 28%, transparent) 55%) no-repeat;
  background-size: 0% 100%;
  transition-property: opacity, background-size;
  transition-duration: 0.18s, 0.5s;
}

.sp-word.is-keyword.is-on {
  background-size: 100% 100%;
}

/* ── Punchline: a bold card with a flash of colour ── */
.sp-scene.is-punch {
  padding: var(--siq-space-6) var(--siq-space-5);
  border-radius: var(--siq-radius-card);
  background: var(--siq-card);
  box-shadow: var(--siq-card-shadow);
  overflow: hidden;
  animation: sp-flash 0.9s var(--siq-ease) both;
}

@keyframes sp-flash {
  0% {
    transform: scale(0.94);
    box-shadow: 0 0 0 0 color-mix(in srgb, var(--siq-accent) 55%, transparent), var(--siq-card-shadow);
  }
  45% {
    transform: scale(1.02);
    box-shadow: 0 0 0 14px color-mix(in srgb, var(--siq-accent) 0%, transparent), var(--siq-card-shadow);
  }
  100% {
    transform: scale(1);
    box-shadow: var(--siq-card-shadow);
  }
}

/* ── Curve: the audience chat draws in, the minute lights up ── */
.sp-scene.is-curve {
  padding-top: var(--siq-space-6);
}

.sp-curve {
  position: relative;
  width: 100%;
  height: 9rem;
  margin-bottom: var(--siq-space-4);
}

.sp-curve-svg {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  overflow: visible;
}

.sp-curve-area {
  fill: var(--siq-ridge-fill);
  animation: sp-fade 0.8s var(--siq-ease) 0.4s both;
}

.sp-curve-line {
  fill: none;
  stroke: var(--siq-s1);
  stroke-width: 2.5;
  stroke-linejoin: round;
  stroke-dasharray: 1;
  stroke-dashoffset: 0;
  animation: sp-draw 1.1s var(--siq-ease) both;
}

@keyframes sp-draw {
  from {
    stroke-dashoffset: 1;
  }
}

@keyframes sp-fade {
  from {
    opacity: 0;
  }
}

.sp-curve-rule {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 0;
  border-left: 1px dashed var(--siq-accent);
  animation: sp-fade 0.4s var(--siq-ease) 1s both;
}

.sp-curve-dot {
  position: absolute;
  width: 14px;
  height: 14px;
  margin: -7px 0 0 -7px;
  border-radius: 50%;
  background: var(--siq-accent);
  animation: sp-light 1.6s var(--siq-ease) 1s both;
}

@keyframes sp-light {
  0% {
    transform: scale(0);
    box-shadow: 0 0 0 0 color-mix(in srgb, var(--siq-accent) 50%, transparent);
  }
  30% {
    transform: scale(1.3);
    box-shadow: 0 0 0 0 color-mix(in srgb, var(--siq-accent) 50%, transparent);
  }
  100% {
    transform: scale(1);
    box-shadow: 0 0 0 16px color-mix(in srgb, var(--siq-accent) 0%, transparent);
  }
}

.sp-curve-tag {
  position: absolute;
  top: -0.25rem;
  transform: translate(-50%, -100%);
  padding: 2px var(--siq-space-2);
  border-radius: var(--siq-radius-pill);
  background: var(--siq-cta);
  color: var(--siq-cta-ink);
  font-size: var(--siq-text-tag);
  line-height: var(--siq-text-tag-lh);
  font-weight: var(--siq-weight-semibold);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  animation: sp-fade 0.4s var(--siq-ease) 1.1s both;
}

.sp-curve-axis {
  position: absolute;
  right: 0;
  bottom: -1.5rem;
  font-size: var(--siq-text-caption);
  line-height: var(--siq-text-caption-lh);
  color: var(--siq-muted);
}

/* ── Ring: fills to the percentage said ── */
.sp-ring {
  position: relative;
  display: grid;
  place-items: center;
  width: 9rem;
  height: 9rem;
}

.sp-ring-svg {
  position: absolute;
  inset: 0;
  transform: rotate(-90deg);
}

.sp-ring-track,
.sp-ring-fill {
  fill: none;
  stroke-width: 10;
}

.sp-ring-track {
  stroke: var(--siq-border);
}

.sp-ring-fill {
  stroke: var(--siq-accent);
  stroke-linecap: round;
  stroke-dasharray: 100;
  animation: sp-ring 1.1s var(--siq-ease) both;
}

@keyframes sp-ring {
  from {
    stroke-dashoffset: 100;
  }
}

.sp-ring-value,
.sp-count {
  font-size: var(--siq-text-display);
  line-height: var(--siq-text-display-lh);
  font-weight: var(--siq-weight-semibold);
  font-variant-numeric: tabular-nums;
  color: var(--siq-accent);
}

.sp-count {
  font-size: calc(var(--siq-text-display) * 1.6);
  letter-spacing: -0.02em;
}

/* ── Changing beats ── */
.sp-scene-enter-active,
.sp-scene-leave-active {
  transition-property: opacity, transform;
  transition-duration: 0.26s;
  transition-timing-function: var(--siq-ease);
}

.sp-scene-enter-from {
  opacity: 0;
  transform: translateY(10px);
}

.sp-scene-leave-to {
  opacity: 0;
  transform: translateY(-8px);
}

/* ── Progress, one segment a beat ── */
.sp-track {
  display: flex;
  gap: 3px;
  height: 6px;
}

.sp-segment {
  position: relative;
  flex: 1 1 0;
  min-width: 2px;
  overflow: hidden;
  border-radius: var(--siq-radius-pill);
  background: var(--siq-border);
}

.sp-segment.is-punch {
  flex-grow: 1.6;
}

.sp-segment-fill {
  position: absolute;
  inset: 0 auto 0 0;
  background: var(--siq-accent);
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
  align-items: center;
  gap: var(--siq-space-2);
}

.sp-position {
  font-size: var(--siq-text-caption);
  line-height: var(--siq-text-caption-lh);
  font-variant-numeric: tabular-nums;
  color: var(--siq-muted);
}

.sp-failure {
  display: flex;
  flex-direction: column;
  gap: var(--siq-space-2);
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
    min-height: 15rem;
    padding: var(--siq-space-5) var(--siq-space-4);
  }
  .sp-caption,
  .sp-caption.is-big {
    font-size: var(--siq-text-card-title);
  }
  .sp-caption.is-title {
    font-size: var(--siq-text-section);
    line-height: var(--siq-text-section-lh);
  }
  .sp-curve {
    height: 7rem;
  }
  .sp-ring {
    width: 7rem;
    height: 7rem;
  }
  .sp-ring-value {
    font-size: var(--siq-text-section);
  }
  .sp-count {
    font-size: var(--siq-text-display);
  }
}

@media (prefers-reduced-motion: reduce) {
  .sp-glow,
  .sp-curve-area,
  .sp-curve-line,
  .sp-curve-rule,
  .sp-curve-dot,
  .sp-curve-tag,
  .sp-ring-fill,
  .sp-scene.is-punch {
    animation: none;
  }
  .sp-word,
  .sp-word.is-keyword {
    transition: none;
  }
}

@media (forced-colors: active) {
  .sp-stage,
  .sp-scene.is-punch {
    border: 1px solid CanvasText;
  }
  .sp-word.is-keyword {
    background: none;
    text-decoration: underline;
  }
  .sp-segment {
    border: 1px solid CanvasText;
  }
}
</style>
