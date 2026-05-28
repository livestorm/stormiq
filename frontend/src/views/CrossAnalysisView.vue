<script setup>
import { RouterLink } from "vue-router";
</script>

<template>
  <section class="page-section cross-page">
    <div class="cross-hero">
      <div class="cross-glow" aria-hidden="true" />

      <div class="cross-badge">
        <span class="cross-badge-dot" />
        In development
      </div>

      <h2 class="cross-title">Cross Analysis</h2>
      <p class="cross-subtitle">
        Two sessions. Ten sessions. One read.<br />
        Compare, contrast, and spot trends across your entire event library.
      </p>

      <div class="cross-modes">
        <div class="cross-mode-card">
          <div class="cross-mode-icon cross-mode-icon--comparative">
            <svg width="28" height="28" viewBox="0 0 28 28" fill="none" aria-hidden="true">
              <rect x="2" y="6" width="10" height="16" rx="2" fill="currentColor" opacity="0.9" />
              <rect x="16" y="6" width="10" height="16" rx="2" fill="currentColor" opacity="0.4" />
              <path d="M13 14h2M13 10h2M13 18h2" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" opacity="0.6" />
            </svg>
          </div>
          <h3 class="cross-mode-title">Comparative</h3>
          <p class="cross-mode-body">
            Pick any two sessions and get a head-to-head breakdown — engagement, pace, audience signals, and friction — with actionable deltas.
          </p>
          <div class="cross-mode-tags">
            <span class="cross-tag">Engagement</span>
            <span class="cross-tag">Pace</span>
            <span class="cross-tag">Audience</span>
            <span class="cross-tag">Friction</span>
          </div>
        </div>

        <div class="cross-mode-divider" aria-hidden="true">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
            <path d="M5 12h14M13 6l6 6-6 6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
        </div>

        <div class="cross-mode-card">
          <div class="cross-mode-icon cross-mode-icon--cumulative">
            <svg width="28" height="28" viewBox="0 0 28 28" fill="none" aria-hidden="true">
              <rect x="2" y="18" width="4" height="8" rx="1" fill="currentColor" opacity="0.35" />
              <rect x="8" y="13" width="4" height="13" rx="1" fill="currentColor" opacity="0.5" />
              <rect x="14" y="9" width="4" height="17" rx="1" fill="currentColor" opacity="0.7" />
              <rect x="20" y="4" width="4" height="22" rx="1" fill="currentColor" opacity="0.9" />
              <path d="M3 6l5 4 6-5 6-3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" opacity="0.5" />
            </svg>
          </div>
          <h3 class="cross-mode-title">Cumulative</h3>
          <p class="cross-mode-body">
            Stack up to 10 sessions and watch how your events evolve — topic drift, audience DNA, speaker development, and unanswered question backlogs.
          </p>
          <div class="cross-mode-tags">
            <span class="cross-tag">Topic Drift</span>
            <span class="cross-tag">Audience DNA</span>
            <span class="cross-tag">Trends</span>
            <span class="cross-tag">Up to 10</span>
          </div>
        </div>
      </div>

      <div class="cross-prep">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <circle cx="8" cy="8" r="7" stroke="currentColor" stroke-width="1.25" opacity="0.6" />
          <path d="M8 5v4M8 11v.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
        </svg>
        <span>
          Get a head start — run
          <RouterLink to="/single-analysis" class="cross-prep-link">Single Analysis</RouterLink>
          on your sessions now. Cross Analysis reuses those cached results, so it'll be instant when it ships.
        </span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.cross-page {
  display: flex;
  flex-direction: column;
}

.cross-hero {
  position: relative;
  overflow: hidden;
  border: 1px solid var(--color-border-neutral-alpha-100, rgba(255,255,255,0.08));
  border-radius: var(--border-radius-large, 12px);
  background: var(--color-surface-neutral-alpha-100, rgba(255,255,255,0.03));
  padding: 56px 48px 48px;
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: var(--spacing-space-6);
}

/* Radial glow behind everything */
.cross-glow {
  position: absolute;
  inset: 0;
  background:
    radial-gradient(ellipse 60% 40% at 30% 20%, rgba(11, 84, 254, 0.12) 0%, transparent 70%),
    radial-gradient(ellipse 50% 35% at 75% 75%, rgba(46, 184, 154, 0.08) 0%, transparent 65%);
  pointer-events: none;
}

/* Badge */
.cross-badge {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 5px 12px;
  border-radius: 99px;
  border: 1px solid rgba(11, 84, 254, 0.3);
  background: rgba(11, 84, 254, 0.1);
  color: var(--color-brand-300, #7ba2fe);
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.cross-badge-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-brand-400, #447cfd);
  animation: cross-pulse 2s ease-in-out infinite;
}

@keyframes cross-pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.4; transform: scale(0.75); }
}

/* Title & subtitle */
.cross-title {
  font-size: 32px;
  font-weight: 700;
  letter-spacing: -0.5px;
  color: var(--color-text-neutral-base, #fff);
  margin: 0;
  line-height: 1.2;
}

.cross-subtitle {
  font-size: 15px;
  color: var(--color-text-neutral-secondary, rgba(255,255,255,0.55));
  line-height: 1.65;
  max-width: 440px;
  margin: 0;
}

/* Mode cards row */
.cross-modes {
  display: flex;
  align-items: center;
  gap: var(--spacing-space-4);
  width: 100%;
  max-width: 760px;
  margin-top: var(--spacing-space-2);
}

.cross-mode-card {
  flex: 1;
  text-align: left;
  background: var(--color-surface-neutral-alpha-100, rgba(255,255,255,0.04));
  border: 1px solid var(--color-border-neutral-alpha-100, rgba(255,255,255,0.08));
  border-radius: var(--border-radius-medium, 8px);
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  transition: border-color 0.2s, background 0.2s;
}

.cross-mode-card:hover {
  border-color: rgba(11, 84, 254, 0.25);
  background: rgba(11, 84, 254, 0.04);
}

.cross-mode-icon {
  width: 48px;
  height: 48px;
  border-radius: var(--border-radius-medium, 8px);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.cross-mode-icon--comparative {
  background: rgba(11, 84, 254, 0.12);
  color: var(--color-brand-300, #7ba2fe);
}

.cross-mode-icon--cumulative {
  background: rgba(46, 184, 154, 0.12);
  color: var(--color-green-500, #2eb89a);
}

.cross-mode-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--color-text-neutral-base, #fff);
  margin: 0;
}

.cross-mode-body {
  font-size: 13px;
  line-height: 1.6;
  color: var(--color-text-neutral-secondary, rgba(255,255,255,0.55));
  margin: 0;
  flex: 1;
}

.cross-mode-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.cross-tag {
  font-size: 11px;
  font-weight: 600;
  padding: 3px 8px;
  border-radius: 99px;
  background: var(--color-surface-neutral-alpha-200, rgba(255,255,255,0.07));
  color: var(--color-text-neutral-tertiary, rgba(255,255,255,0.35));
  letter-spacing: 0.02em;
}

/* Arrow between cards */
.cross-mode-divider {
  flex-shrink: 0;
  color: var(--color-text-neutral-tertiary, rgba(255,255,255,0.2));
}

/* Prep note at bottom */
.cross-prep {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  max-width: 520px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--color-text-neutral-tertiary, rgba(255,255,255,0.35));
  padding-top: var(--spacing-space-2);
  border-top: 1px solid var(--color-border-neutral-alpha-100, rgba(255,255,255,0.06));
  width: 100%;
  max-width: 760px;
}

.cross-prep svg {
  flex-shrink: 0;
  margin-top: 2px;
}

.cross-prep-link {
  color: var(--color-brand-400, #447cfd);
  text-decoration: none;
  font-weight: 500;
}

.cross-prep-link:hover {
  text-decoration: underline;
}
</style>
