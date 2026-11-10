<script setup>
// The OAuth return page (`/auth/callback`). The shell leaves it at once
// (`App.vue`, `auth/callback.js`); this is what shows meanwhile.
</script>

<template>
  <div class="cb" role="status">
    <span class="cb-dot" aria-hidden="true" />
    <div>
      <h1 class="cb-title">Connecting with Livestorm</h1>
      <p class="cb-text">Finishing the sign-in and loading your account.</p>
    </div>
  </div>
</template>

<style scoped>
.cb {
  display: flex;
  align-items: center;
  gap: var(--siq-space-4);
  padding: var(--siq-space-12) 0;
}

.cb-dot {
  width: 20px;
  height: 20px;
  flex: none;
  border: 2px solid var(--siq-hairline);
  border-top-color: var(--siq-accent);
  border-radius: 50%;
  animation: cb-spin 0.9s linear infinite;
}

.cb-title {
  margin: 0;
  font-size: var(--siq-text-section);
  line-height: var(--siq-text-section-lh);
  font-weight: var(--siq-weight-regular);
  color: var(--siq-ink);
}

.cb-text {
  margin: 0;
  font-size: var(--siq-text-ui);
  line-height: var(--siq-text-ui-lh);
  color: var(--siq-ink-2);
}

@keyframes cb-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .cb-dot {
    animation: none;
  }
}
</style>
