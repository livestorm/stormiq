<script setup>
import { computed, ref } from "vue";
import Card from "../ui/Card.vue";
import CardHeader from "../ui/CardHeader.vue";
import Icon from "../ui/Icon.vue";
import StatusWord from "../ui/StatusWord.vue";
import UiButton from "../ui/UiButton.vue";
import { chosenLabel, figuresSummary, momentLine } from "../../followup/figures";

// Before you share: the figures said on stage that may be wrong (the same
// metric said with two values, an acronym the recording is unsure of), each
// as a question with one-click answers, saved for the session. The analysis
// found them; the host decides. The data comes from the view's
// `createFigureChecks()`. Quotes are the speakers' words: rendered as text.
const props = defineProps({
  figures: { type: Object, required: true },
});

const state = computed(() => props.figures.state);
const body = computed(() => state.value.body);
const checks = computed(() => body.value?.checks || []);
const summary = computed(() => figuresSummary(body.value));
const open = computed(() => Number(body.value?.unchecked || 0) > 0);
// Once every figure is checked, the card folds to its header; "Review" opens it.
const reviewing = ref(false);
const showList = computed(() => open.value || reviewing.value);
</script>

<template>
  <Card v-if="checks.length" class="bys" :tone="open ? 'fair' : 'default'">
    <CardHeader icon="shield" title="Before you share" :subtitle="summary" :level="2">
      <template #right>
        <StatusWord v-if="open" status="check" label="Needs your check" />
        <template v-else>
          <StatusWord status="measured" label="Checked" />
          <UiButton variant="ghost" small :aria-expanded="reviewing ? 'true' : 'false'" @click="reviewing = !reviewing">
            {{ reviewing ? "Hide" : "Review" }}
          </UiButton>
        </template>
      </template>
    </CardHeader>
    <p v-if="showList" class="bys-lead">
      These figures were said on stage in a way the recording can't settle. Your answers go into the session analysis, and
      a figure you leave out is never stated as a fact.
    </p>
    <ul v-if="showList" class="bys-list">
      <li v-for="check in checks" :key="check.key" class="bys-item">
        <p class="bys-question">{{ check.question }}</p>
        <ul class="bys-moments">
          <li v-for="(moment, index) in check.moments" :key="index">{{ momentLine(moment) }}</li>
        </ul>
        <div v-if="check.choice" class="bys-row">
          <span class="bys-answer"><Icon name="check" :size="16" /> {{ chosenLabel(check) }}</span>
          <UiButton variant="ghost" small :disabled="Boolean(state.saving)" @click="figures.answer(check.key, null)">
            Change
          </UiButton>
        </div>
        <div v-else class="bys-row" role="group" :aria-label="check.question">
          <UiButton
            v-for="option in check.options"
            :key="option.value"
            variant="secondary"
            small
            :disabled="Boolean(state.saving)"
            @click="figures.answer(check.key, option.value)"
          >
            {{ option.label }}
          </UiButton>
        </div>
        <p v-if="state.saveError.key === check.key" class="bys-error" role="alert">{{ state.saveError.message }}</p>
      </li>
    </ul>
  </Card>
</template>

<style scoped>
.bys-lead {
  max-width: 72ch;
  margin: var(--siq-space-2) 0 0;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-ink-2);
}

.bys-list {
  margin: var(--siq-space-4) 0 0;
  padding: 0;
  list-style: none;
}

.bys-item {
  padding: var(--siq-space-4) 0;
  box-shadow: inset 0 1px 0 var(--siq-hairline-row);
}

.bys-question {
  margin: 0;
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  font-weight: var(--siq-weight-semibold);
  color: var(--siq-ink);
}

.bys-moments {
  margin: var(--siq-space-1) 0 0;
  padding: 0;
  list-style: none;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-muted);
  overflow-wrap: anywhere;
}

.bys-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--siq-space-2);
  margin-top: var(--siq-space-3);
}

.bys-answer {
  display: inline-flex;
  align-items: center;
  gap: var(--siq-space-1);
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  font-weight: var(--siq-weight-medium);
  color: var(--siq-ink);
}

.bys-error {
  margin: var(--siq-space-2) 0 0;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-low);
}

@media (forced-colors: active) {
  .bys-item {
    border-top: 1px solid CanvasText;
  }
}
</style>
