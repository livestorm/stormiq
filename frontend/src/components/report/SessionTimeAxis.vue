<script setup>
import { computed } from "vue";
import { minuteTicks } from "../../report/chartGeometry";

// The shared time axis of the Report's time charts: the session clock from
// 0:00 to the end, ticked every 5, 10, 15, 30 or 60 minutes. Decorative for
// screen readers: each chart says its finding in words.

const props = defineProps({
  // How many live minutes the axis spans (`record.pulse.minutes.length`).
  count: { type: Number, required: true },
});

// Two sets: up to 10 ticks on a wide screen, up to 4 on a phone, where long
// labels ("1:45:00") would otherwise run into each other.
const sets = computed(() => [
  { key: "wide", ticks: minuteTicks(props.count) },
  { key: "narrow", ticks: minuteTicks(props.count, { maxTicks: 4 }) },
]);
</script>

<template>
  <div class="r-axis" aria-hidden="true">
    <div v-for="set in sets" :key="set.key" class="r-axis-set" :class="`r-axis-${set.key}`">
      <span
        v-for="tick in set.ticks"
        :key="tick.minute"
        class="r-axis-tick"
        :class="{ 'is-first': tick.left === 0, 'is-last': tick.left === 100 }"
        :style="{ left: `${tick.left}%` }"
      >
        {{ tick.label }}
      </span>
    </div>
  </div>
</template>

<style scoped>
.r-axis {
  position: relative;
  height: var(--siq-text-caption-lh);
  margin-top: var(--siq-space-2);
  font-size: var(--siq-text-caption);
  line-height: var(--siq-text-caption-lh);
  color: var(--siq-muted);
  font-variant-numeric: tabular-nums;
}

.r-axis-set {
  position: absolute;
  inset: 0;
}

.r-axis-narrow {
  display: none;
}

@media (max-width: 640px) {
  .r-axis-wide {
    display: none;
  }
  .r-axis-narrow {
    display: block;
  }
}

.r-axis-tick {
  position: absolute;
  top: 0;
  transform: translateX(-50%);
  white-space: nowrap;
}

.r-axis-tick.is-first {
  transform: none;
}

.r-axis-tick.is-last {
  transform: translateX(-100%);
}
</style>
