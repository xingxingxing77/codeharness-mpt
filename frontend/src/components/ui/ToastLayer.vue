<template>
  <Teleport to="body">
    <div class="layer" aria-live="polite">
      <div v-for="t in toast.items" :key="t.seq" class="item" :class="t.tone" @click="toast.dismiss(t.seq)">
        <VStateDot v-if="t.tone === 'error'" state="error" />
        <span>{{ t.text }}</span>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
/** 顶掉 NMessageProvider / NDialogProvider 的自绘提示层。 */
import { useToastStore } from '../../stores/toast'
import VStateDot from './VStateDot.vue'

const toast = useToastStore()
</script>

<style scoped>
.layer {
  position: fixed;
  left: 50%;
  bottom: 32px;
  transform: translateX(-50%);
  z-index: 1100;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  pointer-events: none;
}

.item {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  max-width: min(560px, 80vw);
  padding: 8px 14px;
  border-radius: 12px;
  background: var(--dsw-alias-toast-bg);
  color: var(--dsw-static-neutral-bluish-00);
  font-size: 13px;
  line-height: 20px;
  box-shadow: var(--dsw-shadow-lv3);
  pointer-events: auto;
  cursor: pointer;
  animation: toast-in 180ms var(--ds-ease-in-out);
  overflow-wrap: break-word;
}

.warn {
  background: var(--dsw-alias-state-warn-primary);
  color: var(--dsw-static-neutral-bluish-1000);
}

.error {
  background: var(--dsw-alias-state-error-primary);
  color: var(--dsw-static-neutral-bluish-00);
}

.success {
  background: var(--dsw-alias-state-success-primary);
  color: var(--dsw-static-neutral-bluish-00);
}

@keyframes toast-in {
  from {
    opacity: 0;
    transform: translateY(6px);
  }
}

@media (prefers-reduced-motion: reduce) {
  .item {
    animation: none;
  }
}
</style>
