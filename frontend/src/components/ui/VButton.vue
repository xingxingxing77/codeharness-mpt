<template>
  <button class="btn" :class="[variant, size, { circle, block }]" type="button">
    <slot />
  </button>
</template>

<script setup lang="ts">
/** 按钮变体直接对着参考项目的按钮令牌命名，而不是「primary/secondary/三态」：
 *  elevated=新会话卡钮、floating=浮层、info=发送（业务蓝）、contrast=品牌反色。 */
withDefaults(
  defineProps<{
    variant?: 'elevated' | 'floating' | 'ghost' | 'info' | 'contrast' | 'danger'
    size?: 's' | 'm' | 'l'
    circle?: boolean
    block?: boolean
  }>(),
  { variant: 'ghost', size: 'm' }
)
</script>

<style scoped>
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  flex: none;
  box-sizing: border-box;
  border: 1px solid transparent;
  background: transparent;
  font-family: inherit;
  font-size: 14px;
  font-weight: 500;
  line-height: 22px;
  color: var(--dsw-alias-label-primary);
  cursor: pointer;
  white-space: nowrap;
  transition: background-color var(--ds-transition-duration) var(--ds-ease-in-out),
    border-color var(--ds-transition-duration) var(--ds-ease-in-out),
    color var(--ds-transition-duration) var(--ds-ease-in-out);
}

.s {
  height: 28px;
  padding: 0 10px;
  border-radius: 8px;
  font-size: 13px;
  line-height: 20px;
}

.m {
  height: 38px;
  padding: 8px 16px;
  border-radius: 12px;
}

.l {
  height: 44px;
  padding: 0 20px;
  border-radius: 22px;
}

.circle {
  padding: 0;
  border-radius: 50%;
}

.circle.s {
  width: 28px;
}
.circle.m {
  width: 38px;
}
.circle.l {
  width: 44px;
}

.block {
  width: 100%;
}

.elevated {
  border-color: var(--dsw-alias-border-l2);
  background: var(--dsw-alias-button-elevated-fill);
}

.elevated:hover:not(:disabled) {
  background: var(--dsw-alias-button-floating-hover);
}

.floating {
  border-color: var(--dsw-alias-border-l2-darkmode-thin);
  background: var(--dsw-alias-button-floating-fill);
  box-shadow: var(--dsw-shadow-lv2);
}

.floating:hover:not(:disabled) {
  background: var(--dsw-alias-button-floating-hover);
}

.ghost:hover:not(:disabled) {
  background: var(--dsw-alias-interactive-bg-hover);
}

.info {
  background: var(--dsw-alias-button-info-fill);
  color: var(--dsw-alias-label-primary-foreground);
}

.info:hover:not(:disabled) {
  background: var(--dsw-alias-button-info-hover);
}

.contrast {
  background: var(--dsw-alias-button-primary-fill);
  color: var(--dsw-alias-label-primary-inverted);
}

.contrast:hover:not(:disabled) {
  background: var(--dsw-alias-button-primary-hover);
}

.danger {
  color: var(--dsw-alias-state-error-primary);
}

.danger:hover:not(:disabled) {
  background: var(--dsw-alias-interactive-bg-hover-danger);
}

.btn:focus-visible {
  outline: 2px solid var(--dsw-alias-state-business-primary);
  outline-offset: 2px;
}

.btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
</style>
