<template>
  <span ref="wrapEl" class="wrap" @pointerenter="onEnter" @pointerleave="onLeave" @focusin="show" @focusout="hide">
    <slot />
    <Teleport to="body">
      <span
        v-if="visible"
        class="bubble"
        :data-side="side"
        :style="pos"
        role="tooltip"
      >{{ label }}</span>
    </Teleport>
  </span>
</template>

<script setup lang="ts">
/** 参考项目 Tooltip：fixed 定位贴着锚点矩形，单一深色底板两主题通用，无箭头。 */
import { nextTick, onBeforeUnmount, ref } from 'vue'

const props = withDefaults(
  defineProps<{ label: string; delay?: number; side?: 'top' | 'bottom' | 'right'; disabled?: boolean }>(),
  { delay: 500, side: 'bottom' }
)

const visible = ref(false)
const wrapEl = ref<HTMLElement>()
const pos = ref<Record<string, string>>({})
let timer: ReturnType<typeof setTimeout> | undefined

async function show() {
  const anchor = wrapEl.value?.firstElementChild as HTMLElement | null
  if (props.disabled || !props.label || !anchor) return
  const r = anchor.getBoundingClientRect()
  const gap = 6
  if (props.side === 'top') pos.value = { left: `${r.left + r.width / 2}px`, top: `${r.top - gap}px` }
  else if (props.side === 'right') pos.value = { left: `${r.right + gap}px`, top: `${r.top + r.height / 2}px` }
  else pos.value = { left: `${r.left + r.width / 2}px`, top: `${r.bottom + gap}px` }
  visible.value = true
  await nextTick()
}

function onEnter() {
  clearTimeout(timer)
  timer = setTimeout(show, props.delay)
}

function onLeave() {
  clearTimeout(timer)
  hide()
}

function hide() {
  visible.value = false
}

onBeforeUnmount(() => clearTimeout(timer))
</script>

<style scoped>
.wrap {
  display: inline-flex;
  min-width: 0;
}

.bubble {
  position: fixed;
  z-index: 100;
  width: max-content;
  max-width: 50vw;
  padding: 3px 7px;
  border-radius: 8px;
  background: var(--dsw-alias-tooltip-bg);
  color: var(--dsw-static-neutral-bluish-00);
  font-size: 13px;
  line-height: 20px;
  white-space: pre-line;
  overflow-wrap: break-word;
  pointer-events: none;
  animation: tooltip-in 150ms var(--ds-ease-in-out);
}

.bubble[data-side='right'] {
  transform: translateY(-50%);
}
.bubble[data-side='bottom'] {
  transform: translateX(-50%);
}
.bubble[data-side='top'] {
  transform: translate(-50%, -100%);
}

@keyframes tooltip-in {
  from {
    opacity: 0;
  }
}

@media (prefers-reduced-motion: reduce) {
  .bubble {
    animation: none;
  }
}
</style>
