<script setup>
import { computed, ref } from "vue";
import Card from "../ui/Card.vue";
import CardHeader from "../ui/CardHeader.vue";
import Icon from "../ui/Icon.vue";
import StatusWord from "../ui/StatusWord.vue";
import { findingsView } from "../../report/findings";

// "What stood out": the Report's findings (`GET /record/findings`), the
// strongest first, each worded by `report/findings.js`, then what was
// checked and found nothing, as tiles. What couldn't be checked gets one
// line here; its reasons are in "Method and data quality". The
// chapter finding draws the room's own chat a minute per chapter against
// the usual chapter (the dashed line). Renders text only.

const props = defineProps({
  // `GET /record/findings`, or null while loading or missing.
  body: { type: Object, default: null },
});

// English or French: the words to paste into a report or an email.
const LANGS = [
  { value: "en", label: "English" },
  { value: "fr", label: "Français" },
];
const lang = ref("en");
const view = computed(() => findingsView(props.body, lang.value));
</script>

<template>
  <Card v-if="body && !view.empty" class="fs">
    <CardHeader icon="target" :title="view.labels.title" :level="2">
      <template #right>
        <div class="fs-segmented" role="group" aria-label="Language of the findings">
          <button
            v-for="option in LANGS"
            :key="option.value"
            type="button"
            class="fs-seg siq-fx"
            :class="{ 'is-on': lang === option.value }"
            :aria-pressed="lang === option.value ? 'true' : 'false'"
            :lang="option.value"
            @click="lang = option.value"
          >
            {{ option.label }}
          </button>
        </div>
      </template>
    </CardHeader>
    <p v-if="!view.found.length" class="fs-quiet">{{ view.labels.quiet }}</p>

    <article v-for="finding in view.found" :key="finding.id" class="fs-finding" :lang="lang">
      <div class="fs-head">
        <h3 class="fs-headline">{{ finding.headline }}</h3>
        <StatusWord
          :status="finding.confidence === 'measured' ? 'measured' : 'estimated'"
          :label="finding.confidence === 'measured' ? view.labels.measured : view.labels.estimated"
        />
      </div>
      <p v-for="line in finding.lines" :key="line" class="fs-line">{{ line }}</p>

      <figure v-if="finding.chart" class="fs-chart">
        <figcaption class="fs-chart-head">
          <span class="fs-chart-title">{{ finding.chart.title }}</span>
          <span v-if="finding.chart.usual" class="fs-chart-key"><span class="fs-usual-key" aria-hidden="true" />{{ finding.chart.usual }}</span>
        </figcaption>
        <ul class="fs-bars">
          <li v-for="row in finding.chart.rows" :key="row.title + row.when" class="fs-bar" :class="row.tone && `is-${row.tone}`">
            <span class="fs-bar-name">
              <span class="fs-bar-title">{{ row.title }}</span>
              <span class="fs-bar-when">{{ row.when }}</span>
            </span>
            <span class="fs-bar-track" aria-hidden="true">
              <span v-if="row.width" class="fs-bar-fill" :style="{ width: `${row.width}%` }" />
              <span v-if="finding.chart.usualLeft !== null" class="fs-bar-usual" :style="{ left: `${finding.chart.usualLeft}%` }" />
            </span>
            <span class="fs-bar-value">{{ row.value }}</span>
          </li>
        </ul>
      </figure>

      <ul v-if="finding.rows.length" class="fs-rows">
        <li v-for="row in finding.rows" :key="row">{{ row }}</li>
      </ul>
      <p v-if="finding.next" class="fs-next">
        <span class="fs-next-label">{{ view.labels.next }}</span>
        <span>{{ finding.next }}</span>
      </p>
    </article>

    <section v-if="view.nothing.length" class="fs-checks" :aria-label="view.labels.checked" :lang="lang">
      <h3 class="fs-checks-title">
        <Icon name="check" :size="16" />
        <span>{{ view.labels.checked }}</span>
      </h3>
      <ul class="fs-tiles">
        <li v-for="item in view.nothing" :key="item.line" class="fs-tile">
          <span class="fs-tile-icon" aria-hidden="true"><Icon :name="item.icon" :size="16" /></span>
          <span class="fs-tile-body">
            <span v-if="item.topic" class="fs-tile-topic">{{ item.topic }}</span>
            <span class="fs-tile-text">{{ item.text }}</span>
          </span>
        </li>
      </ul>
    </section>
    <p v-if="view.notChecked.length" class="fs-unchecked" :lang="lang">{{ view.labels.notCheckedNote(view.notChecked.length) }}</p>
  </Card>
</template>

<style scoped>
.fs-quiet,
.fs-line {
  max-width: 72ch;
  margin: var(--siq-space-2) 0 0;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-ink-2);
}

.fs-finding {
  margin-top: var(--siq-space-4);
  padding-top: var(--siq-space-4);
  box-shadow: inset 0 1px 0 var(--siq-hairline-row);
}

.fs-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--siq-space-2) var(--siq-space-4);
}

.fs-headline {
  margin: 0;
  font-size: var(--siq-text-card-title);
  line-height: var(--siq-text-card-title-lh);
  font-weight: var(--siq-weight-semibold);
  color: var(--siq-ink);
}

.fs-chart {
  margin: var(--siq-space-4) 0 0;
}

.fs-bars,
.fs-rows {
  margin: var(--siq-space-3) 0 0;
  padding: 0;
  list-style: none;
}

.fs-chart-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--siq-space-1) var(--siq-space-4);
  margin-bottom: var(--siq-space-2);
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
}

.fs-chart-title {
  font-weight: var(--siq-weight-semibold);
  color: var(--siq-ink-2);
}

.fs-chart-key {
  display: inline-flex;
  align-items: center;
  gap: var(--siq-space-1);
  color: var(--siq-muted);
}

.fs-usual-key {
  height: 12px;
  border-left: 1px dashed var(--siq-ink-2);
}

.fs-bar {
  display: grid;
  grid-template-columns: minmax(0, 2fr) minmax(0, 3fr) 7.5rem;
  align-items: center;
  gap: var(--siq-space-3);
  padding: var(--siq-space-2) 0;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  box-shadow: inset 0 -1px 0 var(--siq-hairline-row);
}

.fs-bar-name {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.fs-bar-title {
  color: var(--siq-ink);
  overflow-wrap: anywhere;
}

.fs-bar-when {
  font-size: var(--siq-text-caption);
  line-height: var(--siq-text-caption-lh);
  color: var(--siq-muted);
  font-variant-numeric: tabular-nums;
}

.fs-bar-track {
  position: relative;
  height: 10px;
  border-radius: var(--siq-radius-pill);
  background: var(--siq-surface-2);
}

.fs-bar-fill {
  position: absolute;
  inset: 0 auto 0 0;
  border-radius: var(--siq-radius-pill);
  background: var(--siq-nontext);
}

.fs-bar.is-high .fs-bar-fill {
  background: var(--siq-accent);
}

.fs-bar.is-low .fs-bar-fill {
  background: var(--siq-fair);
}

.fs-bar-usual {
  position: absolute;
  top: -3px;
  bottom: -3px;
  border-left: 1px dashed var(--siq-ink-2);
}

.fs-bar-value {
  color: var(--siq-muted);
  font-variant-numeric: tabular-nums;
}

.fs-bar.is-high .fs-bar-value,
.fs-bar.is-low .fs-bar-value {
  color: var(--siq-ink);
  font-weight: var(--siq-weight-medium);
}

.fs-rows li {
  max-width: 80ch;
  padding: var(--siq-space-1) 0;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-ink-2);
  font-variant-numeric: tabular-nums;
}

.fs-segmented {
  display: inline-flex;
  padding: 2px;
  border: 1px solid var(--siq-border);
  border-radius: var(--siq-radius-control);
  background: var(--siq-input);
}

.fs-seg {
  min-height: calc(var(--siq-target) - 6px);
  margin: 0;
  padding: 0 var(--siq-space-3);
  border: 0;
  border-radius: calc(var(--siq-radius-control) - 2px);
  background: transparent;
  color: var(--siq-ink-2);
  font: inherit;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  font-weight: var(--siq-weight-medium);
  cursor: pointer;
}

.fs-seg.is-on {
  background: var(--siq-selected-bg);
  color: var(--siq-selected-ink);
}

.fs-next {
  display: flex;
  flex-wrap: wrap;
  gap: var(--siq-space-1) var(--siq-space-2);
  max-width: 80ch;
  margin: var(--siq-space-3) 0 0;
  padding: var(--siq-space-3);
  border-radius: var(--siq-radius-tile);
  background: var(--siq-surface-2);
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-ink);
}

.fs-next-label {
  font-weight: var(--siq-weight-semibold);
}


.fs :deep(.siq-collapsible) {
  margin-top: var(--siq-space-4);
}

@media (max-width: 640px) {
  .fs-bar {
    grid-template-columns: minmax(0, 1fr) 5rem;
  }

  .fs-bar-track {
    grid-column: 1 / -1;
    grid-row: 2;
  }
}

@media (forced-colors: active) {
  .fs-bar-fill {
    background: CanvasText;
  }

  .fs-bar.is-high .fs-bar-fill,
  .fs-bar.is-low .fs-bar-fill {
    background: Highlight;
  }
}

.fs-checks {
  margin-top: var(--siq-space-4);
  padding-top: var(--siq-space-4);
  box-shadow: inset 0 1px 0 var(--siq-hairline-row);
}

.fs-checks-title {
  display: flex;
  align-items: center;
  gap: var(--siq-space-2);
  margin: 0 0 var(--siq-space-3);
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  font-weight: var(--siq-weight-medium);
  color: var(--siq-ink);
}

.fs-checks-title :deep(svg) {
  color: var(--siq-good);
}

.fs-tiles {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 16rem), 1fr));
  gap: var(--siq-space-3);
  margin: 0;
  padding: 0;
  list-style: none;
}

.fs-tile {
  display: flex;
  gap: var(--siq-space-3);
  padding: var(--siq-space-3) var(--siq-space-4);
  border-radius: var(--siq-radius-control);
  box-shadow: inset 0 0 0 1px var(--siq-hairline);
}

.fs-tile-icon {
  flex: none;
  margin-top: 2px;
  color: var(--siq-muted);
}

.fs-tile-body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.fs-tile-topic {
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  font-weight: var(--siq-weight-medium);
  color: var(--siq-ink);
}

.fs-tile-text {
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-ink-2);
}

.fs-unchecked {
  margin: var(--siq-space-3) 0 0;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-muted);
}

@media (forced-colors: active) {
  .fs-tile {
    border: 1px solid CanvasText;
  }
}
</style>
