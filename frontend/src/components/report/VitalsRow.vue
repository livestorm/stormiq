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
.r-vitals {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: var(--siq-space-4);
  /* An open "how we know" panel grows its own card, not the whole row. */
  align-items: start;
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
}

.is-hidden-value :deep(.siq-stat-value) {
  color: var(--siq-muted);
}
</style>
