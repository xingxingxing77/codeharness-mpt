<template>
  <Teleport to="body">
    <div v-if="open" class="overlay" role="presentation" @pointerdown.self="closeIfAllowed">
      <div class="mask" @pointerdown="closeIfAllowed" />
      <div ref="panelEl" class="panel" :style="{ width, height }" role="dialog" aria-modal="true" :aria-label="title">
        <slot />
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
/** 参考项目 SettingsRoot 的弹窗骨架：mask + blur(2px)、面板 shadow-lv3、r24、
 *  高度由视口而非内容决定（切分节时不能在小指针底下变化）。 */
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'

const props = withDefaults(
  defineProps<{ open: boolean; title?: string; width?: string; height?: string; dismissable?: boolean }>(),
  { width: '800px', height: 'min(800px, calc(100vh - 48px))', dismissable: true }
)
const emit = defineEmits<{ close: [] }>()

const panelEl = ref<HTMLElement>()
let lastFocus: HTMLElement | null = null

function closeIfAllowed() {
  if (props.dismissable) emit('close')
}

const FOCUSABLE =
  'a[href],button:not([disabled]),textarea,input,select,[tabindex]:not([tabindex="-1"])'

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    closeIfAllowed()
    return
  }
  if (e.key !== 'Tab' || !panelEl.value) return
  // 焦点陷阱：只在面板内的可聚焦元素间循环
  const items = [...panelEl.value.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(
    (el) => el.offsetParent !== null
  )
  if (!items.length) return
  const first = items[0]
  const last = items[items.length - 1]
  const cur = document.activeElement as HTMLElement | null
  if (e.shiftKey && (cur === first || !panelEl.value.contains(cur))) {
    e.preventDefault()
    last.focus()
  } else if (!e.shiftKey && cur === last) {
    e.preventDefault()
    first.focus()
  }
}

watch(
  () => props.open,
  async (v) => {
    if (v) {
      lastFocus = document.activeElement as HTMLElement | null
      document.addEventListener('keydown', onKeydown, true)
      await nextTick()
      ;(panelEl.value?.querySelector<HTMLElement>('[data-autofocus]') ??
        panelEl.value?.querySelector<HTMLElement>(FOCUSABLE))?.focus()
    } else {
      document.removeEventListener('keydown', onKeydown, true)
      lastFocus?.focus()
    }
  }
)

onBeforeUnmount(() => document.removeEventListener('keydown', onKeydown, true))
</script>

<style scoped>
.overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
}

.mask {
  position: absolute;
  inset: 0;
  background: var(--dsw-alias-bg-mask-1);
  backdrop-filter: var(--dsw-mask-blur);
}

.panel {
  position: relative;
  z-index: 1;
  display: flex;
  max-width: calc(100vw - 48px);
  box-sizing: border-box;
  border-radius: 24px;
  overflow: hidden;
  background: var(--dsw-alias-bg-layer-2);
  box-shadow: var(--dsw-shadow-lv3);
  /* 抬升面重绑滚动条到 l2 一档 */
  --dsh-scrollbar-thumb: var(--dsw-alias-scrollbar-bg-l2);
  --dsh-scrollbar-thumb-hover: var(--dsw-alias-scrollbar-hover-l2);
  animation: panel-in 180ms var(--ds-ease-in-out);
}

@keyframes panel-in {
  from {
    opacity: 0;
    transform: translateY(6px);
  }
}

@media (prefers-reduced-motion: reduce) {
  .panel {
    animation: none;
  }
}
</style>
