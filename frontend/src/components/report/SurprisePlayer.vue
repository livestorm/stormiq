<script setup>
import { computed } from "vue";
import Icon from "../ui/Icon.vue";
import ProgressSteps from "../ui/ProgressSteps.vue";
import UiButton from "../ui/UiButton.vue";
import { api } from "../../api";
import { durationLabel, makeLabel, sizeLabel, videoView } from "../../followup/surpriseVideo";

// Surprise me, the show: the main output, as a real video. One click ("Make
// the show") makes it in the workers: the take, the performance script, the
// voice, the timing, the shot list and the pictures (the show job), then the
// MP4 (the render worker) (`useSurpriseShow.js`, owned by the page). Once it
// is made, the page plays the server's MP4 in a native player (seekable: the
// route answers Range requests) with a Download button. The poster is the
// fixed intro card, the same on every show.
const props = defineProps({
  // The page's show model (`createSurpriseShow`).
  show: { type: Object, required: true },
  sessionId: { type: String, required: true },
  // The session has a transcript: a take can be written.
  canWrite: { type: Boolean, default: true },
});

const POSTER = "/media/surprise-me-poster.jpg";

const view = computed(() => videoView(props.show.state.body, { starting: props.show.state.starting, error: props.show.state.error }));
const src = computed(() => api.getSurpriseVideoUrl(props.sessionId, view.value.version));
const downloadUrl = computed(() => api.getSurpriseVideoUrl(props.sessionId, view.value.version, { download: true }));
const facts = computed(() => [durationLabel(view.value.durationS), sizeLabel(view.value.sizeBytes)].filter(Boolean).join(" · "));
const canMake = computed(() => view.value.hasTake || props.canWrite);
</script>

<template>
  <section class="sp" aria-label="Surprise me, the show" :aria-busy="view.mode === 'making' ? 'true' : 'false'">
    <div v-if="view.mode === 'ready'" class="sp-frame">
      <video
        :key="src"
        class="sp-video"
        :src="src"
        :poster="POSTER"
        controls
        playsinline
        preload="metadata"
      >
        Your browser can't play this video. Download it instead.
      </video>
    </div>

    <div v-else class="sp-stage">
      <div v-if="view.mode === 'making'" class="sp-making" role="status">
        <ProgressSteps :steps="view.steps" :percent="view.percent" label="Making the video" note="Made once, then kept: it plays at once next time." />
      </div>
      <div v-else class="sp-idle">
        <p class="sp-idle-title">The session as a short video</p>
        <p class="sp-idle-text">
          An unexpected take, performed by an AI voice, over motion graphics made from the session's own words, numbers and chat.
        </p>
        <UiButton v-if="canMake" variant="primary" icon="zap" :disabled="show.state.loading" @click="show.start()">
          {{ makeLabel(view) }}
        </UiButton>
        <p v-else class="sp-idle-text">This session has no transcript yet: the take is written from it.</p>
      </div>
    </div>

    <div v-if="view.mode === 'failed'" class="sp-failure" role="alert">
      <p class="sp-note"><Icon name="info" :size="14" /> {{ view.failure }}</p>
    </div>

    <div v-if="view.mode === 'ready'" class="sp-bar">
      <UiButton variant="secondary" small icon="download" :href="downloadUrl" download>Download</UiButton>
      <span v-if="facts" class="sp-facts">MP4 · 720p · {{ facts }}</span>
    </div>

    <p v-if="view.mode === 'ready'" class="sp-note">
      <Icon name="info" :size="14" />
      <span>
        Performed by an AI voice<template v-if="view.voiceLabel"> ({{ view.voiceLabel }})</template>. The pictures show this
        session's own words, numbers and chat; attendees are never named.
        <strong v-if="view.fellBack" class="sp-backup">Recorded with the backup voice.</strong>
      </span>
    </p>
    <p v-else-if="view.mode === 'idle'" class="sp-note">
      <Icon name="info" :size="14" />
      One click writes the take (Claude), turns it into a short spoken bit, records the voice, directs the shots and renders
      the video, in a few minutes. Then it is kept.
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

.sp-frame,
.sp-stage {
  position: relative;
  width: 100%;
  aspect-ratio: 16 / 9;
  border-radius: var(--siq-radius-card);
  background: var(--siq-surface-2);
  box-shadow: inset 0 0 0 1px var(--siq-hairline);
  overflow: hidden;
}

.sp-video {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: contain;
  background: var(--siq-surface-2);
}

.sp-stage {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--siq-space-6);
  text-align: center;
}

.sp-making {
  width: min(100%, 28rem);
  text-align: left;
}

.sp-idle {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--siq-space-3);
}

.sp-idle-title {
  margin: 0;
  font-size: var(--siq-text-section);
  line-height: var(--siq-text-section-lh);
  font-weight: var(--siq-weight-semibold);
  color: var(--siq-ink);
}

.sp-idle-text {
  max-width: 44ch;
  margin: 0;
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  color: var(--siq-muted);
}

.sp-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--siq-space-3);
}

.sp-facts {
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
  align-items: flex-start;
  gap: var(--siq-space-1);
  margin: 0;
  font-size: var(--siq-text-caption);
  line-height: var(--siq-text-caption-lh);
  color: var(--siq-muted);
}

.sp-backup {
  font-weight: var(--siq-weight-semibold);
  color: var(--siq-ink);
}

/* On a phone the loader needs more height than a 16:9 frame gives it. */
@media (max-width: 640px) {
  .sp-stage {
    aspect-ratio: auto;
    min-height: 16rem;
    padding: var(--siq-space-4);
  }
}

@media (forced-colors: active) {
  .sp-frame,
  .sp-stage {
    border: 1px solid CanvasText;
  }
}

@media print {
  .sp-bar,
  .sp-video {
    display: none;
  }
}
</style>
