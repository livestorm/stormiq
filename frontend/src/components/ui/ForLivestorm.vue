<script setup>
// The co-brand half of the "StormIQ for Livestorm" lockup: a thin rule, the
// word "for" and the official Livestorm logo (docs/brand.md). The logo is the
// brand kit's file, unchanged: the on-light version on light surfaces, the
// on-dark one on dark surfaces, chosen by the --siq-logo-on-* tokens.
import logoOnLight from "../../assets/livestorm/logo-on-light.svg";
import logoOnDark from "../../assets/livestorm/logo-on-dark.svg";

defineProps({
  // Logo height in px. The width follows the file's 922×155 ratio.
  height: { type: Number, default: 18 },
  // The rule separates the lockup from the StormIQ wordmark on the same line.
  rule: { type: Boolean, default: true },
});
</script>

<template>
  <span class="siq-for-livestorm">
    <span v-if="rule" class="siq-for-livestorm-rule" aria-hidden="true"></span>
    <span class="siq-for-livestorm-for">for</span>
    <img :src="logoOnLight" alt="Livestorm" class="siq-for-livestorm-logo siq-for-livestorm-on-light" :style="{ height: `${height}px` }" />
    <img :src="logoOnDark" alt="Livestorm" class="siq-for-livestorm-logo siq-for-livestorm-on-dark" :style="{ height: `${height}px` }" />
  </span>
</template>

<style scoped>
.siq-for-livestorm {
  display: inline-flex;
  align-items: center;
  gap: var(--siq-space-2);
}

.siq-for-livestorm-rule {
  width: 1px;
  height: 20px;
  margin-right: var(--siq-space-1);
  background: var(--siq-lockup-rule);
}

.siq-for-livestorm-for {
  font-family: var(--siq-font);
  font-size: var(--siq-text-small);
  line-height: var(--siq-text-small-lh);
  color: var(--siq-lockup-ink);
}

.siq-for-livestorm-logo {
  width: auto;
  flex: none;
}

.siq-for-livestorm-on-light {
  display: var(--siq-logo-on-light);
}

.siq-for-livestorm-on-dark {
  display: var(--siq-logo-on-dark);
}
</style>
