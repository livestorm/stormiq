<script setup>
// Phase 4: placeholder. Phase 3 (cross-session analysis) will replace
// this with the real comparative + cumulative workflows. Kept as a
// route + sidebar entry now so the navigation reflects the planned
// product shape — users know what's coming without us shipping a
// half-implemented feature.
</script>

<template>
  <section class="page-section">
    <header>
      <h2>Cross Analysis</h2>
      <p class="page-description">
        Compare two sessions side-by-side, or roll a batch of up to 10 sessions into a single trend report.
      </p>
    </header>

    <section class="panel helper-panel cross-analysis-placeholder">
      <h3>Coming soon</h3>
      <p>
        Cross Analysis is being built. Once it ships you'll be able to pick sessions from your workspace
        and get either a comparative head-to-head or a cumulative trend read across the set.
      </p>
      <p>
        In the meantime, run
        <RouterLink class="ghost-link-button" to="/single-analysis">Single Analysis</RouterLink>
        on each session you want to compare — the cross workflow will reuse those cached results.
      </p>
    </section>
  </section>
</template>

<style scoped>
.cross-analysis-placeholder p {
  margin: 8px 0;
  line-height: 1.6;
}
</style>
