<script setup>
import { computed } from "vue";
import { sparkPaths } from "../../sessions/library";

// A session's shape in one line: the audience's chat per minute, drawn as
// the Report's cover chart is (the same fill and ink), small enough for a
// table cell. `label` is its accessible name ("busiest at 23:00, 26
// messages"); with nothing to draw, a dash.
const props = defineProps({
  points: { type: Array, default: () => [] },
  label: { type: String, default: "" },
});

const WIDTH = 96;
const HEIGHT = 24;
const paths = computed(() => sparkPaths(props.points, { width: WIDTH, height: HEIGHT }));
</script>

<template>
  <svg
    v-if="paths"
    class="sp"
    :viewBox="`0 0 ${WIDTH} ${HEIGHT}`"
    :width="WIDTH"
    :height="HEIGHT"
    role="img"
    :aria-label="label"
    preserveAspectRatio="none"
  >
    <path class="sp-area" :d="paths.area" />
    <path class="sp-line" :d="paths.line" />
  </svg>
  <span v-else class="sp-none" :aria-label="label || 'No audience chat'">—</span>
</template>

<style scoped>
.sp {
  display: block;
  overflow: visible;
}

.sp-area {
  fill: var(--siq-ridge-fill);
}

.sp-line {
  fill: none;
  stroke: var(--siq-ink-2);
  stroke-width: 1.25;
  stroke-linejoin: round;
  vector-effect: non-scaling-stroke;
}

.sp-none {
  color: var(--siq-muted);
}
</style>
