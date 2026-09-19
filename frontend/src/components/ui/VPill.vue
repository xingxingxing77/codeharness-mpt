<template>
  <span class="pill" :class="tone"><slot /></span>
</template>

<script setup lang="ts">
/** 小徽标：exit code、状态、耗时。tone 走语义色而非随意色。 */
withDefaults(defineProps<{ tone?: 'neutral' | 'success' | 'warn' | 'error' | 'business' }>(), {
  tone: 'neutral'
})
</script>

<style scoped>
.pill {
  display: inline-flex;
  align-items: center;
  height: 18px;
  padding: 0 6px;
  border-radius: 6px;
  font-family: var(--ds-font-family-code);
  font-size: 11px;
  line-height: 18px;
  white-space: nowrap;
}

.neutral {
  background: var(--dsw-alias-markdown-tag);
  color: var(--dsw-alias-label-secondary);
}

.success {
  background: var(--dsw-alias-state-success-tertiary);
  color: var(--dsw-alias-state-success-primary);
}

.warn {
  background: var(--dsw-alias-state-warn-tertiary);
  color: var(--dsw-alias-state-warn-label);
}

.error {
  background: var(--dsw-alias-interactive-bg-hover-danger);
  color: var(--dsw-alias-state-error-primary);
}

.business {
  background: var(--dsw-alias-state-business-tertiary);
  color: var(--dsw-alias-state-business-primary);
}
</style>
