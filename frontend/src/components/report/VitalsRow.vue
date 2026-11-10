<script setup>
import { computed } from "vue";
import StatCard from "../ui/StatCard.vue";
import { vitalCards } from "../../report/vitals";

// The Report's five vitals as stat cards, from `record.measures`
// (`report/vitals.js` words them; the server computes them). Each card's
// info button opens how the number was got.

const props = defineProps({
  measures: { type: Object, required: true },
});

const cards = computed(() => vitalCards(props.measures));
</script>

<template>
  <section class="r-vitals" aria-label="Vitals">
    <StatCard
      v-for="item in cards"
      :key="item.id"
      :icon="item.icon"
      :label="item.label"
      :value="item.value"
      :sub="item.sub"
      :info="item.info"
      :status="item.status"
      :class="{ 'is-hidden-value': item.hidden }"
    />
  </section>
</template>

<style scoped>
/* The cards of a row share their rows (subgrid): the header, the number,
   the sub-line and the "how we know" panel. A label that wraps to more
   lines than its neighbours' then moves every number of the row down
   together, so the numbers always line up. */
.r-vitals {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: var(--siq-space-4);
}

.r-vitals > :deep(.siq-stat) {
  display: grid;
  grid-row: span 4;
  grid-template-rows: subgrid;
  row-gap: 0;
  align-content: start;
}

/* Grid items don't shrink below their content by default: let the header
   and the sub-line fit the card (the sub-line clamps itself). */
.r-vitals > :deep(.siq-stat) > * {
  min-width: 0;
}

@media (max-width: 1100px) {
  .r-vitals {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 720px) {
  .r-vitals {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  /* Two cards a row leave the label too little room beside the icon tile
     and the info button: the tile goes, the label and its icon meaning
     stay (the tile is decorative, aria-hidden). */
  .r-vitals :deep(.siq-stat-head .siq-tile) {
    display: none;
  }
}

.is-hidden-value :deep(.siq-stat-value) {
  color: var(--siq-muted);
}
</style>
