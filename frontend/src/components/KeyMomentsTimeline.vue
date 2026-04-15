<script setup>
import { computed } from "vue";

const props = defineProps({
  body: { type: String, default: "" },
  emptyMessage: { type: String, default: "No key moments available yet." },
});

// Regex handles both bold and plain label variants the LLM may produce
const MOMENT_RE =
  /^\s*[-*•]?\s*\**Timestamp:\s*([\d:]+)\**\s*\|\s*\**Type:\s*([^|*\n]+?)\**\s*\|\s*\**Description:\**\s*(.+)$/i;

const moments = computed(() => {
  const lines = String(props.body || "")
    .replace(/\r\n/g, "\n")
    .split("\n");
  const result = [];
  for (const line of lines) {
    const m = line.match(MOMENT_RE);
    if (!m) continue;
    result.push({
      timestamp: m[1].trim(),
      type: m[2].trim(),
      description: m[3].trim(),
    });
  }
  return result;
});

const TYPE_PALETTE = {
  engagement: "green",
  "strong interest": "blue",
  interest: "blue",
  confusion: "yellow",
  drop: "red",
  friction: "red",
  highlight: "green",
};

function paletteFor(type) {
  return TYPE_PALETTE[String(type || "").toLowerCase()] || "neutral";
}

const TYPE_ICONS = {
  green: "M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z",
  blue: "M13 10V3L4 14h7v7l9-11h-7z",
  yellow: "M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.12 2.6-2.8 3.07-.8.21-1.4.93-1.4 1.73v.3M12 17.25v.5",
  red: "M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z",
  neutral: "M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z",
};

function iconPath(type) {
  return TYPE_ICONS[paletteFor(type)] || TYPE_ICONS.neutral;
}
</script>

<template>
  <section class="panel key-moments-panel">
    <p v-if="!moments.length" class="key-moments-empty">{{ emptyMessage }}</p>
    <ol v-else class="key-moments-list">
      <li
        v-for="(moment, index) in moments"
        :key="index"
        class="key-moment-item"
        :data-palette="paletteFor(moment.type)"
      >
        <div class="key-moment-track">
          <div class="key-moment-icon-wrap">
            <svg class="key-moment-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path :d="iconPath(moment.type)" />
            </svg>
          </div>
          <div v-if="index < moments.length - 1" class="key-moment-connector" />
        </div>
        <div class="key-moment-body">
          <div class="key-moment-meta">
            <span class="key-moment-timestamp">{{ moment.timestamp }}</span>
            <span class="key-moment-type-badge">{{ moment.type }}</span>
          </div>
          <p class="key-moment-description">{{ moment.description }}</p>
        </div>
      </li>
    </ol>
  </section>
</template>

<style scoped>
.key-moments-panel {
  padding: var(--spacing-space-6);
}

.key-moments-empty {
  color: var(--color-text-neutral-tertiary);
  margin: 0;
}

.key-moments-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0;
}

/* ── row ── */
.key-moment-item {
  display: flex;
  gap: var(--spacing-space-4);
  align-items: flex-start;
}

/* ── left track: icon + connector line ── */
.key-moment-track {
  display: flex;
  flex-direction: column;
  align-items: center;
  flex-shrink: 0;
  width: 32px;
}

.key-moment-icon-wrap {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.key-moment-icon {
  width: 16px;
  height: 16px;
}

.key-moment-connector {
  width: 2px;
  flex: 1;
  min-height: 16px;
  background: var(--color-borders-neutral-light);
  margin: 4px 0;
}

/* ── right content ── */
.key-moment-body {
  flex: 1;
  padding-bottom: var(--spacing-space-6);
  min-width: 0;
}

.key-moment-meta {
  display: flex;
  align-items: center;
  gap: var(--spacing-space-3);
  margin-bottom: var(--spacing-space-2);
  flex-wrap: wrap;
}

.key-moment-timestamp {
  font-family: "SF Mono", "Fira Mono", "Consolas", monospace;
  font-size: 12px;
  font-weight: 600;
  color: var(--color-text-neutral-secondary);
  letter-spacing: 0.04em;
  white-space: nowrap;
}

.key-moment-type-badge {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  padding: 2px 8px;
  border-radius: 99px;
}

.key-moment-description {
  margin: 0;
  color: var(--color-text-neutral-base);
  font-size: 14px;
  line-height: 20px;
}

/* ── palette: green (Engagement, Highlight) ── */
[data-palette="green"] .key-moment-icon-wrap {
  background: var(--color-surface-success-alpha-200);
  color: var(--color-text-success-base);
}
[data-palette="green"] .key-moment-type-badge {
  background: var(--color-surface-success-alpha-200);
  color: var(--color-text-success-base);
}

/* ── palette: blue (Strong Interest, Interest) ── */
[data-palette="blue"] .key-moment-icon-wrap {
  background: var(--color-surface-primary-alpha-200);
  color: var(--color-text-primary-base);
}
[data-palette="blue"] .key-moment-type-badge {
  background: var(--color-surface-primary-alpha-200);
  color: var(--color-text-primary-base);
}

/* ── palette: yellow (Confusion) ── */
[data-palette="yellow"] .key-moment-icon-wrap {
  background: var(--color-surface-warning-alpha-200);
  color: var(--color-text-warning-base);
}
[data-palette="yellow"] .key-moment-type-badge {
  background: var(--color-surface-warning-alpha-200);
  color: var(--color-text-warning-base);
}

/* ── palette: red (Drop, Friction) ── */
[data-palette="red"] .key-moment-icon-wrap {
  background: var(--color-surface-danger-alpha-200);
  color: var(--color-text-danger-base);
}
[data-palette="red"] .key-moment-type-badge {
  background: var(--color-surface-danger-alpha-200);
  color: var(--color-text-danger-base);
}

/* ── palette: neutral (fallback) ── */
[data-palette="neutral"] .key-moment-icon-wrap {
  background: var(--color-surface-neutral-alpha-200);
  color: var(--color-text-neutral-secondary);
}
[data-palette="neutral"] .key-moment-type-badge {
  background: var(--color-surface-neutral-alpha-200);
  color: var(--color-text-neutral-secondary);
}
</style>
