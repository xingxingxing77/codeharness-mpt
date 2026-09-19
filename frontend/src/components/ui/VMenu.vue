<template>
  <span ref="rootEl" class="root">
    <slot :open="open" :toggle="toggle" />
    <Teleport to="body">
      <div
        v-if="open"
        ref="listEl"
        class="list"
        :class="{ compact }"
        :style="pos"
        role="menu"
        tabindex="-1"
        @pointerleave="closeOnPointerLeave ? close() : null"
        @keydown.down.prevent="move(1)"
        @keydown.up.prevent="move(-1)"
        @keydown.esc.prevent="close(true)"
        @keydown.tab.prevent="close(true)"
      >
        <template v-for="(it, i) in items" :key="it.key ?? it.label">
          <div v-if="it.kind === 'label'" class="groupLabel">{{ it.label }}</div>
          <div v-else-if="it.kind === 'sep'" class="separator" />
          <button
            v-else
            class="item"
            :class="{ danger: it.danger, checked: it.checked }"
            :disabled="it.disabled"
            :aria-disabled="it.disabled || undefined"
            role="menuitem"
            @click.stop="pick(it)"
          >
            <span class="itemIcon"><DsIcon v-if="it.icon" :name="it.icon" :size="16" /></span>
            <span class="itemLabel">{{ it.label }}</span>
            <DsIcon v-if="it.checked" name="check" :size="14" class="tick" />
          </button>
        </template>
      </div>
    </Teleport>
  </span>
</template>

<script setup lang="ts">
/** 参考项目 Menu：4px 内衬、r12、反向发丝边、shadow-lv3、主卡 218 宽。
 *  数据驱动：本项目只有行操作 / 视图选项两类菜单，不值得开插槽式 API。 */
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import DsIcon from './DsIcon.vue'
import type { MenuItem } from './menuTypes'

const props = withDefaults(
  defineProps<{ items: MenuItem[]; align?: 'start' | 'end'; compact?: boolean; closeOnPointerLeave?: boolean }>(),
  { align: 'start', compact: false, closeOnPointerLeave: true }
)
const emit = defineEmits<{ select: [item: MenuItem]; 'update:open': [v: boolean] }>()

const rootEl = ref<HTMLElement>()
const listEl = ref<HTMLElement>()
const open = ref(false)
const pos = ref<Record<string, string>>({})
// 开合状态对外可见：owner 要拿它禁用 hover 卡（参考项目 HoverCard 的 disabled={menuOpen}）
watch(open, (v) => emit('update:open', v))

const GAP = 4
const MIN_W = 218

async function toggle() {
  open.value ? close(true) : await show()
}

async function show() {
  const anchor = rootEl.value?.firstElementChild as HTMLElement | null
  if (!anchor) return
  const r = anchor.getBoundingClientRect()
  const left = props.align === 'end'
    ? { right: `${Math.max(8, innerWidth - r.right)}px` }
    : { left: `${Math.min(r.left, innerWidth - MIN_W - 8)}px` }
  // 下方放不下就翻到上方
  const below = innerHeight - r.bottom > 280
  pos.value = {
    ...left,
    ...(below ? { top: `${r.bottom + GAP}px` } : { bottom: `${innerHeight - r.top + GAP}px` })
  }
  open.value = true
  await nextTick()
  listEl.value?.focus()
}

function close(returnFocus = false) {
  if (!open.value) return
  open.value = false
  if (returnFocus) (rootEl.value?.firstElementChild as HTMLElement | null)?.focus()
}

function pick(it: MenuItem) {
  if (it.disabled) return
  emit('select', it)
  if (!it.keepOpen) close(true)
}

/** 键盘只跳可选项，分组标题与分隔线不参与。 */
function move(dir: number) {
  const btns = [...(listEl.value?.querySelectorAll<HTMLButtonElement>('.item') ?? [])]
  if (!btns.length) return
  const cur = btns.indexOf(document.activeElement as HTMLButtonElement)
  const next = (cur + dir + btns.length) % btns.length
  btns[next === -1 ? 0 : next].focus()
}

function onDocPointerDown(e: PointerEvent) {
  if (!open.value) return
  if (rootEl.value?.contains(e.target as Node) || listEl.value?.contains(e.target as Node)) return
  close()
}

document.addEventListener('pointerdown', onDocPointerDown)
onBeforeUnmount(() => document.removeEventListener('pointerdown', onDocPointerDown))

defineExpose({ close, toggle })
</script>

<style scoped>
.root {
  position: relative;
  display: inline-flex;
  min-width: 0;
}

.list {
  position: fixed;
  z-index: 100;
  box-sizing: border-box;
  padding: 4px;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--dsw-alias-border-inverted);
  border-radius: 12px;
  background: var(--dsw-specific-menu);
  box-shadow: var(--dsw-shadow-lv3);
  min-width: 218px;
  max-width: 360px;
  /* 抬升面：滚动条取 l2 一档，重绑在这张卡上由后代继承 */
  --dsh-scrollbar-thumb: var(--dsw-alias-scrollbar-bg-l2);
  --dsh-scrollbar-thumb-hover: var(--dsw-alias-scrollbar-hover-l2);
}

.item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  min-height: 40px;
  padding: 8px 10px;
  border: none;
  border-radius: 10px;
  background: transparent;
  cursor: pointer;
  font-family: inherit;
  font-size: 14px;
  line-height: 22px;
  color: var(--dsw-alias-label-primary);
  text-align: left;
}

.item:hover:not(:disabled) {
  background: var(--dsw-alias-interactive-bg-hover);
}

.item:focus-visible {
  outline: 2px solid var(--dsw-alias-state-business-primary);
  outline-offset: -2px;
}

.item:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.itemIcon {
  display: inline-flex;
  flex: none;
  width: 16px;
  height: 16px;
  align-items: center;
  justify-content: center;
  color: var(--dsw-alias-label-tertiary);
}

.itemLabel {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tick {
  color: var(--dsw-alias-state-business-primary);
}

.danger,
.danger .itemIcon {
  color: var(--dsw-alias-state-error-primary);
}

.danger:hover:not(:disabled) {
  background: var(--dsw-alias-interactive-bg-hover-danger);
}

.groupLabel {
  padding: 6px 10px 2px;
  font-size: 12px;
  line-height: 18px;
  color: var(--dsw-alias-label-tertiary);
}

.separator {
  height: 1px;
  margin: 4px 8px;
  background: var(--dsw-alias-border-l2);
}

.compact .item {
  min-height: 32px;
  padding: 5px 8px;
  font-size: 13px;
  line-height: 20px;
}

.compact .separator {
  margin: 2px 6px;
}
</style>
