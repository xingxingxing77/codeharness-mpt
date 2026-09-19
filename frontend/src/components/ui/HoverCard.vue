<template>
  <span
    ref="rootEl"
    class="root"
    @pointerenter="onEnter"
    @pointerleave="onLeave"
    @pointerdown.capture="onDown"
  >
    <slot />
    <span v-if="open && copyable" class="status" role="status">{{ copied ? copiedLabel : '' }}</span>
    <Teleport to="body">
      <div
        v-if="open && pos"
        ref="cardEl"
        class="card"
        :class="{ copyable, feedback: copied }"
        :style="cardStyle"
        :role="copyable ? 'button' : undefined"
        :tabindex="copyable ? 0 : undefined"
        :aria-label="copyable ? `${copyLabel}: ${copyText}` : undefined"
        @pointerenter="cancelClose"
        @click="onCardClick"
        @keydown="onKey"
      >
        <span v-if="copied" class="copied" aria-hidden="true">{{ copiedLabel }}</span>
        <div v-else class="content"><slot name="content" /></div>
      </div>
    </Teleport>
  </span>
</template>

<script setup lang="ts">
/** 参考项目 ui-primitives/HoverCard.tsx 的移植：延迟出现的预览卡，挂在 body 上。
 *  三条不可省的 mechanics：
 *  ① 卡片**吃指针事件**（不是 pointer-events:none），离开锚点只武装一个 200ms 的宽限关闭，
 *     所以指针能跨过 8px 缝落在卡上读被截断的标题；
 *  ② 锚点内按下（行点击、⋯ 菜单）立即收卡，不等 owner 翻 disabled；但按在卡上不收——
 *     那是一次选区起始，收了浏览器的 click 就没了；
 *  ③ 可复制时点卡即复制并显示 1s「复制成功」，期间用 minHeight 钉住原高度，
 *     免得内容换成一行字把卡片抽瘪。 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

const props = withDefaults(
  defineProps<{
    openDelay?: number
    disabled?: boolean
    copyText?: string
    copyLabel?: string
    copiedLabel?: string
  }>(),
  { openDelay: 500, disabled: false, copyText: undefined, copyLabel: '复制', copiedLabel: '复制成功' }
)

const GRACE_MS = 200
const copyable = computed(() => props.copyText !== undefined)

const rootEl = ref<HTMLElement>()
const cardEl = ref<HTMLElement>()
const open = ref(false)
const copied = ref(false)
const pos = ref<{ left: number; top: number } | null>(null)
let openTimer: ReturnType<typeof setTimeout> | undefined
let graceTimer: ReturnType<typeof setTimeout> | undefined
let copiedTimer: ReturnType<typeof setTimeout> | undefined
let copyHeight = 0
let copying = false
let epoch = 0

const cardStyle = computed(() => {
  if (!pos.value) return {}
  const s: Record<string, string> = { left: `${pos.value.left}px`, top: `${pos.value.top}px` }
  if (copied.value && copyHeight) s.minHeight = `${copyHeight}px`
  return s
})

function close() {
  epoch += 1
  clearTimeout(copiedTimer)
  copied.value = false
  open.value = false
  pos.value = null
}

function cancelClose() {
  clearTimeout(graceTimer)
  graceTimer = undefined
}

function armClose() {
  cancelClose()
  graceTimer = setTimeout(close, GRACE_MS)
}

function onEnter() {
  if (props.disabled) return
  cancelClose()
  if (open.value) return                 // 宽限期内回到锚点：续上现有这张卡，不重头再等 500ms
  clearTimeout(openTimer)
  openTimer = setTimeout(() => (open.value = true), props.openDelay)
}

function onLeave() {
  clearTimeout(openTimer)
  if (open.value) armClose()             // 卡还没开就离开，武装的是空操作，不开口就不收尾
}

function onDown(e: PointerEvent) {
  if (cardEl.value?.contains(e.target as Node)) return
  clearTimeout(openTimer)
  cancelClose()
  close()
}

// owner 中途禁用（开了菜单、起了拖拽）→ 立刻收
watch(() => props.disabled, (d) => { if (d) { clearTimeout(openTimer); cancelClose(); close() } })

async function place() {
  const el = rootEl.value
  if (!el) return
  const r = el.getBoundingClientRect()
  const h = cardEl.value?.offsetHeight ?? 0
  pos.value = { left: r.right + 8, top: Math.min(r.top, window.innerHeight - h - 8) }
}

function track(on: boolean) {
  if (!on) {
    window.removeEventListener('scroll', place, true)
    window.removeEventListener('resize', place)
    return
  }
  window.addEventListener('scroll', place, true)
  window.addEventListener('resize', place)
}

watch(open, async (on) => {
  track(on)
  if (!on) return
  await nextTick()
  await place()
  // 第一次定位时卡还没挂载（量到高度 0），量准之后再纠一次底边钳位；
  // 钳位后的 top 已满足条件，所以这条自然只收敛一次。
  const h = cardEl.value?.offsetHeight ?? 0
  if (pos.value && pos.value.top + h > window.innerHeight - 8) {
    pos.value = { left: pos.value.left, top: window.innerHeight - h - 8 }
  }
})

async function doCopy() {
  const text = props.copyText
  if (!text || copied.value || copying || !navigator.clipboard) return
  copying = true
  const at = epoch
  const ok = await navigator.clipboard.writeText(text).then(() => true).catch(() => false)
  copying = false
  // 等回执期间卡关过又开（epoch 变了），这次成功不属于当前这张卡
  if (!ok || at !== epoch) return
  const h = cardEl.value?.offsetHeight ?? 0
  if (h > 0) copyHeight = h
  copied.value = true
  copiedTimer = setTimeout(() => (copied.value = false), 1000)
}

function onCardClick(e: MouseEvent) {
  // 卡上起手的是选区（读被截断的标题），不能当成「我要复制」
  const sel = window.getSelection()
  if (sel && !sel.isCollapsed) {
    for (let i = 0; i < sel.rangeCount; i += 1) {
      if (e.currentTarget instanceof Node && sel.getRangeAt(i).intersectsNode(e.currentTarget)) return
    }
  }
  void doCopy()
}

function onKey(e: KeyboardEvent) {
  if (!copyable.value || (e.key !== 'Enter' && e.key !== ' ')) return
  e.preventDefault()
  void doCopy()
}

onBeforeUnmount(() => {
  track(false)
  clearTimeout(openTimer)
  cancelClose()
  clearTimeout(copiedTimer)
})
</script>

<style scoped>
/* 块级而非 inline-flex：宿主是整行宽的列表行，行内包装会把行挤窄 */
.root {
  position: relative;
  display: block;
}

.card {
  /* figma 会话 hover 卡：244 宽、r12、12/16 内距、lv3 投影；
     底色两主题同一块 #2C2C2E（figma 原值），所以是组件级变量而不是令牌 */
  --dsw-hovercard-bg: #2c2c2e;
  position: fixed;
  z-index: 100;
  box-sizing: border-box;
  width: 244px;
  padding: 12px 16px;
  border-radius: 12px;
  background: var(--dsw-hovercard-bg);
  box-shadow: var(--dsw-shadow-lv3);
}

.copyable {
  cursor: pointer;
}

.copyable:focus-visible {
  outline: 2px solid var(--dsw-alias-state-business-primary);
  outline-offset: 2px;
}

.feedback {
  display: flex;
  align-items: center;
  justify-content: center;
}

.content {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.copied {
  color: #ffffff;
  font-size: 14px;
  line-height: 20px;
  text-align: center;
}

.status {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}
</style>
