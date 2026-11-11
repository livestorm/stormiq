<script setup>
import { computed, onBeforeUnmount, ref, watch } from "vue";
import Icon from "../ui/Icon.vue";
import ProgressSteps from "../ui/ProgressSteps.vue";
import UiButton from "../ui/UiButton.vue";
import { api } from "../../api";
import { beatsWithParts, countUp, surpriseBeats } from "../../followup/surpriseBeats";
import { beatMs, clockLabel, curveGeometry, headlineWords, percentValue, storyScene } from "../../followup/surpriseScenes";
import { createSurpriseVoice } from "../../followup/useSurpriseVoice";
import { voiceSteps } from "../../report/progress";

// Surprise me, played: one beat at a time, each read by its own OpenAI clip
// (made in the worker the first time someone presses Play, then kept), each
// shown as a storyboard scene (`surpriseScenes.js` `storyScene`): the voice
// tells the sentence, the screen tells the story, never by writing the
// sentence out. A scene is one short headline lifted from the beat (slammed
// in, stamped on, popped in word by word, or over a flash of colour for a
// punchline), a number it says counting up (a ring for a percentage), one of
// the room's own lines flying in as a chat bubble, or the audience chat's
// curve drawing in to its minute. The server's storyboard (Claude, checked
// word for word) when it sent one; else the page's own scenes. "CC" shows
// the sentence as a subtitle, off by default; screen readers always get it.
// "Sound off" plays the scenes alone, at each clip's measured length or a
// reading pace, and needs no clip at all. With "reduce motion" nothing
// moves: each scene shows whole.
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
const muted = ref(false); // the scenes only, no sound
const captions = ref(false); // "CC": the sentence as a subtitle, off by default
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
function sceneAt(i) {
  const item = beats.value[i];
  return item ? storyScene(item, { minutes: props.minutes, index: i }) : null;
}

const scene = computed(() => (index.value >= 0 ? sceneAt(index.value) : null));
const curve = computed(() => (scene.value?.effect === "pulse" ? curveGeometry(props.minutes, scene.value.minute) : null));
// A pulse whose minute can't be drawn slams its headline in instead.
const effect = computed(() => (scene.value?.effect === "pulse" && !curve.value ? "slam" : scene.value?.effect || "idle"));
const headline = computed(() => headlineWords(scene.value?.headline));
const ring = computed(() => (effect.value === "count" ? percentValue(scene.value?.number) : null));
const bubbleTime = computed(() => clockLabel(scene.value?.quote?.t));

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
// percentage while the percentage counts up with it.
function countNumber(i, ms) {
  counted.value = null;
  const shown = sceneAt(i);
  const target = shown?.effect === "count" ? countUp(shown.number) : null;
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
  // (The deadpan pause the voice leaves before a punchline.)
  const pause = beats.value[i + 1]?.kind === "punch" ? 550 : beats.value[i].kind === "punch" ? 400 : 150;
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
  if (from === 0) countNumber(i, (item.durationS || 0) * 1000 || beatMs(item));
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
    <div class="sp-stage" :class="`is-${effect}`">
      <div v-if="making && waiting" class="sp-making" role="status">
        <ProgressSteps :steps="steps" :percent="percent" label="Recording the voice" note="The voice is kept: the next play starts at once." />
        <UiButton variant="ghost" small icon="volume-x" @click="playSilently">Play without sound</UiButton>
      </div>

      <div v-else-if="!beat" class="sp-scene is-idle">
        <div class="sp-glow" aria-hidden="true" />
        <p class="sp-idle-text">Press play: the take, read aloud, one beat at a time, as a storyboard of the session's own words, numbers and chat.</p>
      </div>

      <Transition v-else :name="reduced ? '' : 'sp-scene'" mode="out-in">
        <div :key="index" class="sp-scene" :class="[`is-${effect}`, { 'is-punchline': scene.punch }]" aria-hidden="true">
          <div v-if="effect === 'flash' || scene.punch" class="sp-flash" />
          <div v-if="effect === 'pop' || effect === 'slam'" class="sp-glow" />

          <!-- The audience chat's curve draws in to the minute. -->
          <div v-if="effect === 'pulse'" class="sp-curve">
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

          <!-- A number the beat says counts up; a percentage fills a ring. -->
          <div v-else-if="effect === 'count'" class="sp-count" :class="{ 'has-ring': ring !== null }">
            <svg v-if="ring !== null" viewBox="0 0 120 120" class="sp-ring-svg">
              <circle cx="60" cy="60" r="52" class="sp-ring-track" pathLength="100" />
              <circle cx="60" cy="60" r="52" class="sp-ring-fill" pathLength="100" :style="{ strokeDashoffset: 100 - ring }" />
            </svg>
            <span class="sp-count-value">{{ counted ?? scene.number }}</span>
          </div>

          <!-- One of the room's own lines flies in as a chat bubble. -->
          <figure v-else-if="effect === 'chat'" class="sp-bubble" :class="`is-${scene.quote.kind}`">
            <figcaption class="sp-bubble-head">
              <Icon :name="scene.quote.kind === 'question' ? 'help-circle' : 'message-circle'" :size="14" />
              {{ scene.quote.kind === "question" ? "Asked in the Q&A" : "In the chat" }}<template v-if="bubbleTime"> · {{ bubbleTime }}</template>
            </figcaption>
            <blockquote class="sp-bubble-text">{{ scene.quote.text }}</blockquote>
          </figure>

          <!-- The headline: words of the beat, never the whole sentence. -->
          <p
            v-if="scene.headline"
            class="sp-headline"
            :class="[`is-${effect}`, { 'is-small': ['count', 'chat', 'pulse'].includes(effect) }]"
          >
            <template v-if="effect === 'pop'">
              <span v-for="(word, i) in headline" :key="i" class="sp-pop" :style="{ animationDelay: `${i * 140}ms` }">{{ word }}</span>
            </template>
            <template v-else>{{ scene.headline }}</template>
          </p>
        </div>
      </Transition>
      <p v-if="beat && captions && !(making && waiting)" class="sp-cc">{{ beat.text }}</p>
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
        <UiButton variant="ghost" small icon="captions" :aria-pressed="captions ? 'true' : 'false'" @click="captions = !captions">
          CC<span class="siq-sr-only"> (subtitles {{ captions ? "on" : "off" }})</span>
        </UiButton>
      </div>
      <span class="sp-position">{{ index >= 0 ? `Beat ${index + 1} of ${beats.length}` : `${beats.length} beats` }}</span>
    </div>

    <div v-if="failure" class="sp-failure" role="alert">
      <p class="sp-note"><Icon name="info" :size="14" /> {{ failure }}</p>
      <div class="sp-controls">
        <UiButton variant="secondary" small icon="refresh-cw" :disabled="making" @click="retryVoice">Try the voice again</UiButton>
        <UiButton variant="ghost" small icon="volume-x" @click="playSilently">Play without sound</UiButton>
      </div>
    </div>
    <p v-else class="sp-note">
      <Icon name="info" :size="14" />
      {{
        voiceReady
          ? "Read by an AI voice (OpenAI). The scenes show this session's own words, numbers and chat."
          : "The first play plans the scenes (Claude) and records an AI voice (OpenAI) from the take's text, in a minute or so. Then both are kept."
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
  min-width: 0;
}

.sp-idle-text {
  position: relative;
  max-width: 34ch;
  margin: 0;
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  color: var(--siq-muted);
}

/* ── The soft moving background ── */
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

/* ── A punchline: a flash of colour behind the scene ── */
.sp-flash {
  position: absolute;
  inset: -60%;
  background: radial-gradient(closest-side, color-mix(in srgb, var(--siq-accent) 45%, transparent), transparent);
  opacity: 0;
  animation: sp-flash 1.1s var(--siq-ease) both;
  pointer-events: none;
}

@keyframes sp-flash {
  0% {
    opacity: 0;
    transform: scale(0.4);
  }
  25% {
    opacity: 1;
  }
  100% {
    opacity: 0.18;
    transform: scale(1);
  }
}

/* ── The headline: words of the beat, never the sentence ── */
.sp-headline {
  position: relative;
  max-width: 18ch;
  margin: 0;
  font-size: var(--siq-text-display);
  line-height: var(--siq-text-display-lh);
  font-weight: var(--siq-weight-semibold);
  letter-spacing: -0.015em;
  color: var(--siq-ink);
  text-wrap: balance;
  overflow-wrap: anywhere;
}

.sp-headline.is-small {
  max-width: 30ch;
  font-size: var(--siq-text-section);
  line-height: var(--siq-text-section-lh);
}

.sp-headline.is-slam,
.sp-headline.is-flash {
  font-size: calc(var(--siq-text-display) * 1.2);
  animation: sp-slam 0.55s cubic-bezier(0.2, 1.4, 0.4, 1) both;
}

.sp-headline.is-flash {
  color: var(--siq-accent);
}

@keyframes sp-slam {
  0% {
    opacity: 0;
    transform: scale(2.6);
    filter: blur(6px);
  }
  55% {
    opacity: 1;
    transform: scale(0.94);
    filter: blur(0);
  }
  72% {
    transform: scale(1.04) rotate(-1deg);
  }
  100% {
    transform: scale(1);
  }
}

.sp-headline.is-stamp {
  padding: var(--siq-space-2) var(--siq-space-4);
  border: 4px solid var(--siq-accent);
  border-radius: var(--siq-radius-control);
  color: var(--siq-accent);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  transform: rotate(-6deg);
  animation: sp-stamp 0.45s cubic-bezier(0.3, 1.6, 0.5, 1) 0.15s both;
}

@keyframes sp-stamp {
  0% {
    opacity: 0;
    transform: rotate(-14deg) scale(2.2);
  }
  60% {
    opacity: 1;
    transform: rotate(-5deg) scale(0.92);
  }
  100% {
    transform: rotate(-6deg) scale(1);
  }
}

/* Pop: the headline's words, one by one. */
.sp-pop {
  display: inline-block;
  margin: 0 0.12em;
  animation: sp-pop 0.42s cubic-bezier(0.2, 1.6, 0.4, 1) both;
}

@keyframes sp-pop {
  0% {
    opacity: 0;
    transform: translateY(0.4em) scale(0.3);
  }
  70% {
    opacity: 1;
    transform: translateY(-0.06em) scale(1.12);
  }
  100% {
    transform: none;
  }
}

/* ── Count: a number the beat says, counting up; a ring for a percentage ── */
.sp-count {
  position: relative;
  display: grid;
  place-items: center;
  animation: sp-zoom 0.5s cubic-bezier(0.2, 1.4, 0.4, 1) both;
}

.sp-count.has-ring {
  width: 11rem;
  height: 11rem;
}

.sp-count-value {
  position: relative;
  font-size: calc(var(--siq-text-display) * 1.8);
  line-height: 1;
  font-weight: var(--siq-weight-semibold);
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.03em;
  color: var(--siq-accent);
}

.sp-count.has-ring .sp-count-value {
  font-size: var(--siq-text-display);
}

@keyframes sp-zoom {
  0% {
    opacity: 0;
    transform: scale(0.2);
  }
  100% {
    opacity: 1;
    transform: scale(1);
  }
}

.sp-ring-svg {
  position: absolute;
  inset: 0;
  transform: rotate(-90deg);
}

.sp-ring-track,
.sp-ring-fill {
  fill: none;
  stroke-width: 9;
}

.sp-ring-track {
  stroke: var(--siq-border);
}

.sp-ring-fill {
  stroke: var(--siq-accent);
  stroke-linecap: round;
  stroke-dasharray: 100;
  animation: sp-ring 1.1s var(--siq-ease) 0.2s both;
}

@keyframes sp-ring {
  from {
    stroke-dashoffset: 100;
  }
}

/* ── Chat: the room's own line flies in ── */
.sp-bubble {
  position: relative;
  max-width: min(100%, 26rem);
  margin: 0;
  padding: var(--siq-space-3) var(--siq-space-4);
  border-radius: var(--siq-radius-card) var(--siq-radius-card) var(--siq-radius-card) var(--siq-radius-inline);
  background: var(--siq-card);
  box-shadow: var(--siq-card-shadow), inset 3px 0 0 var(--siq-s1);
  text-align: left;
  animation: sp-fly 0.6s cubic-bezier(0.2, 1.3, 0.4, 1) both;
}

.sp-bubble.is-question {
  box-shadow: var(--siq-card-shadow), inset 3px 0 0 var(--siq-s2);
}

@keyframes sp-fly {
  0% {
    opacity: 0;
    transform: translate3d(30%, 60%, 0) rotate(8deg) scale(0.6);
  }
  70% {
    opacity: 1;
    transform: translate3d(-2%, -4%, 0) rotate(-1.5deg) scale(1.03);
  }
  100% {
    transform: none;
  }
}

.sp-bubble-head {
  display: flex;
  align-items: center;
  gap: var(--siq-space-1);
  margin-bottom: var(--siq-space-1);
  font-size: var(--siq-text-caption);
  line-height: var(--siq-text-caption-lh);
  color: var(--siq-muted);
  font-variant-numeric: tabular-nums;
}

.sp-bubble-text {
  margin: 0;
  font-size: var(--siq-text-card-title);
  line-height: var(--siq-text-card-title-lh);
  color: var(--siq-ink);
  overflow-wrap: anywhere;
}

/* ── Pulse: the audience chat draws in, the minute lights up ── */
.sp-scene.is-pulse {
  padding-top: var(--siq-space-6);
}

.sp-curve {
  position: relative;
  width: 100%;
  height: 8rem;
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

/* ── CC: the sentence as a subtitle, when asked for ── */
.sp-cc {
  position: absolute;
  left: var(--siq-space-3);
  right: var(--siq-space-3);
  bottom: var(--siq-space-3);
  margin: 0 auto;
  max-width: 60ch;
  padding: var(--siq-space-1) var(--siq-space-3);
  border-radius: var(--siq-radius-control);
  background: var(--siq-ink);
  color: var(--siq-page);
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
}

.sp-stage:has(.sp-cc) {
  padding-bottom: calc(var(--siq-space-6) + 3rem);
}

/* ── Changing beats ── */
.sp-scene-enter-active,
.sp-scene-leave-active {
  transition-property: opacity, transform;
  transition-duration: 0.22s;
  transition-timing-function: var(--siq-ease);
}

.sp-scene-enter-from {
  opacity: 0;
  transform: scale(0.96);
}

.sp-scene-leave-to {
  opacity: 0;
  transform: scale(1.04);
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
  .sp-headline,
  .sp-headline.is-slam,
  .sp-headline.is-flash {
    font-size: var(--siq-text-section);
    line-height: var(--siq-text-section-lh);
  }
  .sp-headline.is-small {
    font-size: var(--siq-text-card-title);
    line-height: var(--siq-text-card-title-lh);
  }
  .sp-curve {
    height: 6rem;
  }
  .sp-count.has-ring {
    width: 8rem;
    height: 8rem;
  }
  .sp-count-value {
    font-size: calc(var(--siq-text-display) * 1.3);
  }
  .sp-count.has-ring .sp-count-value {
    font-size: var(--siq-text-section);
  }
  .sp-bubble-text {
    font-size: var(--siq-text-body);
    line-height: var(--siq-text-body-lh);
  }
}

@media (prefers-reduced-motion: reduce) {
  .sp-glow,
  .sp-flash,
  .sp-headline,
  .sp-pop,
  .sp-count,
  .sp-bubble,
  .sp-curve-area,
  .sp-curve-line,
  .sp-curve-rule,
  .sp-curve-dot,
  .sp-curve-tag,
  .sp-ring-fill {
    animation: none;
  }
  .sp-flash {
    opacity: 0.18;
  }
  .sp-scene-enter-active,
  .sp-scene-leave-active {
    transition: none;
  }
}

@media (forced-colors: active) {
  .sp-stage,
  .sp-bubble {
    border: 1px solid CanvasText;
  }
  .sp-segment {
    border: 1px solid CanvasText;
  }
}
</style>
