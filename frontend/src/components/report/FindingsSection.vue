<script setup>
import { computed } from "vue";
import Card from "../ui/Card.vue";
import CardHeader from "../ui/CardHeader.vue";
import Collapsible from "../ui/Collapsible.vue";
import StatusWord from "../ui/StatusWord.vue";
import { findingsView } from "../../report/findings";

// "What stood out": the Report's findings (`GET /record/findings`), the
// strongest first, each worded by `report/findings.js`, then what was
// checked and found nothing, and what couldn't be checked and why. The
// chapter finding draws a bar per chapter against the session's usual
// chapter (the line at 100). Renders text only.

const props = defineProps({
  // `GET /record/findings`, or null while loading or missing.
  body: { type: Object, default: null },
});

const view = computed(() => findingsView(props.body));
const checkedCount = computed(() => view.value.nothing.length + view.value.notChecked.length);
// The bars' scale: the highest index, at least twice the usual chapter.
const barTop = (bars) => Math.max(200, ...bars.map((bar) => bar.index || 0));
</script>

<template>
  <Card v-if="body && !view.empty" class="fs">
    <CardHeader icon="target" title="What stood out" :level="2" />
    <p v-if="!view.found.length" class="fs-quiet">Nothing stood out from the session's own pattern.</p>

    <article v-for="finding in view.found" :key="finding.id" class="fs-finding">
      <div class="fs-head">
        <h3 class="fs-headline">{{ finding.headline }}</h3>
        <StatusWord :status="finding.confidence === 'measured' ? 'measured' : 'estimated'" />
      </div>
      <p v-for="line in finding.lines" :key="line" class="fs-line">{{ line }}</p>

      <ul v-if="finding.bars.length" class="fs-bars" aria-label="Unprompted chat per chapter, the usual chapter at 100">
        <li v-for="bar in finding.bars" :key="bar.title + bar.start" class="fs-bar" :class="bar.tone && `is-${bar.tone}`">
          <span class="fs-bar-name">
            <span class="fs-bar-title">{{ bar.title }}</span>
            <span class="fs-bar-when">{{ bar.start }} · {{ bar.length }}</span>
          </span>
          <span class="fs-bar-track" aria-hidden="true">
            <span
              v-if="bar.index !== null"
              class="fs-bar-fill"
              :style="{ width: `${Math.min((bar.index / barTop(finding.bars)) * 100, 100)}%` }"
            />
            <span class="fs-bar-usual" :style="{ left: `${(100 / barTop(finding.bars)) * 100}%` }" />
          </span>
          <span class="fs-bar-value">{{ bar.value }}</span>
        </li>
      </ul>
      <p v-if="finding.bars.length" class="fs-key">
        Unprompted chat per minute against the session's usual chapter (the dashed line, 100).
      </p>

      <ul v-if="finding.rows.length" class="fs-rows">
        <li v-for="row in finding.rows" :key="row">{{ row }}</li>
      </ul>
    </article>

    <Collapsible v-if="checkedCount" variant="link" :title="`Checked, nothing unusual (${view.nothing.length})`">
      <ul class="fs-checked">
        <li v-for="line in view.nothing" :key="line">{{ line }}</li>
      </ul>
      <template v-if="view.notChecked.length">
        <p class="fs-checked-head">Not checked</p>
        <ul class="fs-checked">
          <li v-for="line in view.notChecked" :key="line">{{ line }}</li>
        </ul>
      </template>
    </Collapsible>
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

.fs-bars,
.fs-rows,
.fs-checked {
  margin: var(--siq-space-3) 0 0;
  padding: 0;
  list-style: none;
}

.fs-bar {
  display: grid;
  grid-template-columns: minmax(0, 14rem) minmax(0, 1fr) 6rem;
  align-items: center;
  gap: var(--siq-space-3);
  padding: var(--siq-space-1) 0;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
}

.fs-bar-name {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.fs-bar-title {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  color: var(--siq-ink);
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

.fs-rows li,
.fs-checked li {
  padding: var(--siq-space-1) 0;
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-ink-2);
  font-variant-numeric: tabular-nums;
}

.fs-key {
  margin: var(--siq-space-2) 0 0;
  font-size: var(--siq-text-caption);
  line-height: var(--siq-text-caption-lh);
  color: var(--siq-muted);
}

.fs-checked-head {
  margin: var(--siq-space-3) 0 0;
  font-size: var(--siq-text-caption);
  line-height: var(--siq-text-caption-lh);
  color: var(--siq-muted);
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
</style>
