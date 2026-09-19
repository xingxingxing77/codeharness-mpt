<template>
  <div
    ref="frameEl"
    class="frame"
    :style="gridStyle"
    :data-sidebar-collapsed="panels.sidebarCollapsed ? 'true' : undefined"
    :data-details-collapsed="cols.details > 0 ? undefined : 'true'"
    :data-dragging="dragging || undefined"
  >
    <div class="col sidebarCol"><slot name="sidebar" /></div>
    <div class="col centerCol"><slot /></div>
    <div class="col detailsCol"><slot name="details" /></div>

    <div class="overlay" data-shell-overlay><slot name="overlay" /></div>

    <div
      v-if="!panels.sidebarCollapsed"
      class="handle"
      data-side="sidebar"
      :style="{ left: `${cols.sidebar}px` }"
      @pointerdown="startDrag('sidebar', $event)"
    />
    <div
      v-if="cols.details > 0"
      class="handle"
      data-side="details"
      :style="{ left: `${viewport - cols.details}px` }"
      @pointerdown="startDrag('details', $event)"
    />
  </div>
</template>

<script setup lang="ts">
/** 三栏骨架：CSS grid 轨道宽度由求解器给，把手是绝对定位的 8px 条。
 *  照抄参考项目 ui-layout 的 AppFrame，slot 框架机制换成 Vue 插槽。 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useLayoutStore } from '../../stores/layout'
import { useSessionStore } from '../../stores/sessions'
import {
  CENTER_MIN,
  DETAILS_MAX,
  DETAILS_MIN,
  SIDEBAR_AUTO_COLLAPSE,
  SIDEBAR_DEFAULT,
  SIDEBAR_MAX,
  SIDEBAR_MIN,
  clampWidth,
  computeColumns
} from '../../stores/layout'

const panels = useLayoutStore()
const store = useSessionStore()
const frameEl = ref<HTMLElement>()
const viewport = ref(0)
const dragging = ref(false)

/** measure() 在 onMounted 里同步跑，所以未测量只存在于首帧渲染前一瞬；
 *  占位值取「中心下限 + 侧栏下限」，让这一瞬的布局已经接近真值。 */
const PRE_MEASURE_VIEWPORT = CENTER_MIN + SIDEBAR_MIN

const cols = computed(() => {
  // 求解器吃的是「有效收起态投影出的偏好」：0 是收起哨兵，由求解器映射成 56px 轨。
  // 直接喂 panels.sidebar 会让窄视口的自动收起只翻属性、轨道仍停在 280px。
  const sb = panels.sidebarCollapsed ? 0 : panels.sidebar === 0 ? SIDEBAR_DEFAULT : panels.sidebar
  const dt = panels.detailsCollapsed ? 0 : panels.details
  return computeColumns(viewport.value || PRE_MEASURE_VIEWPORT, sb, dt)
})

const gridStyle = computed(() => ({
  gridTemplateColumns: `${cols.value.sidebar}px minmax(0, 1fr) ${cols.value.details}px`
}))

/* ---------- 视口测量：量在帧元素上而不是 window，rAF 节流 ---------- */
let ro: ResizeObserver | undefined
let measureScheduled = false

function measure() {
  const w = frameEl.value?.getBoundingClientRect().width ?? 0
  if (w > 0) viewport.value = w
  panels.setNarrow(w > 0 && w < SIDEBAR_AUTO_COLLAPSE)
}

function onFrameResize() {
  if (measureScheduled) return
  measureScheduled = true
  requestAnimationFrame(() => {
    measureScheduled = false
    measure()
  })
}

/* ---------- 拖拽：起点宽度在 pointerdown 冻结，整个手势内不变 ---------- */
type Side = 'sidebar' | 'details'
let side: Side = 'sidebar'
let originX = 0
let sidebarBase = 0
let detailsBase = 0
let dragRaf = 0
let pendingX = 0

function startDrag(s: Side, e: PointerEvent) {
  side = s
  originX = e.clientX
  // 冻结的是求解后的可见宽度，抓起一个被让渡压过的面板时才不会跳
  sidebarBase = cols.value.sidebar
  detailsBase = cols.value.details
  dragging.value = true
  ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
  window.addEventListener('pointermove', onDragMove)
  window.addEventListener('pointerup', endDrag, { once: true })
}

function applyDrag(dx: number) {
  if (side === 'sidebar') panels.setSidebar(clampWidth(sidebarBase + dx, SIDEBAR_MIN, SIDEBAR_MAX))
  // details 往左长，所以 dx 取反
  else panels.setDetails(clampWidth(detailsBase - dx, DETAILS_MIN, DETAILS_MAX))
}

function onDragMove(e: PointerEvent) {
  pendingX = e.clientX
  if (dragRaf) return
  dragRaf = requestAnimationFrame(() => {
    dragRaf = 0
    applyDrag(pendingX - originX)
  })
}

function endDrag() {
  if (dragRaf) {
    // 还排在队里的最后一帧不跑了，就地结算，避免松手后跳一下
    cancelAnimationFrame(dragRaf)
    dragRaf = 0
    applyDrag(pendingX - originX)
  }
  dragging.value = false
  window.removeEventListener('pointermove', onDragMove)
}

/* 换会话时关掉 details：上一轮的选中态对新会话无意义 */
watch(
  () => store.currentId,
  () => panels.closeDetails()
)

onMounted(() => {
  measure()
  ro = new ResizeObserver(onFrameResize)
  if (frameEl.value) ro.observe(frameEl.value)
})

onBeforeUnmount(() => {
  ro?.disconnect()
  window.removeEventListener('pointermove', onDragMove)
  if (dragRaf) cancelAnimationFrame(dragRaf)
})

defineExpose({ viewport })
</script>

<style scoped>
.frame {
  position: relative;
  display: grid;
  grid-template-rows: 100%;
  height: 100%;
  overflow: hidden;
  background: var(--dsw-alias-bg-base);
  transition: grid-template-columns var(--ds-transition-duration-slow) var(--ds-ease-in-out);
}

/* 拖拽期间轨道与把手都不能带过渡，否则跟手感全无 */
.frame[data-dragging] {
  transition: none;
}

.frame[data-dragging] .handle {
  transition: none;
}

.col {
  min-width: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.sidebarCol {
  background: var(--dsw-specific-sidebar-fill);
  border-right: 1px solid var(--dsw-alias-border-l1);
}

.detailsCol {
  border-left: 1px solid var(--dsw-alias-border-l2);
}

/* 关掉的那一侧不能留 1px 接缝；收起的侧栏则要保留有边框的紧凑轨 */
.frame[data-details-collapsed] .detailsCol {
  border-left: none;
}

.overlay {
  position: absolute;
  inset: 0;
  z-index: 20;
  pointer-events: none;
}

.overlay > * {
  pointer-events: auto;
}

.handle {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 8px;
  margin-left: -4px;
  cursor: col-resize;
  z-index: 2;
  touch-action: none;
  transition: left var(--ds-transition-duration-slow) var(--ds-ease-in-out);
}

/* details 把手给可见 affordance，sidebar 把手不给（源设计如此） */
.handle[data-side='details']::after {
  content: '';
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: 12px;
  height: 32px;
  border-radius: 10px;
  box-sizing: border-box;
  background: var(--dsw-alias-button-floating-fill);
  border: 1px solid var(--dsw-alias-border-l2-darkmode-thin);
  opacity: 0;
  transition: opacity var(--ds-transition-duration) var(--ds-ease-in-out),
    background var(--ds-transition-duration) var(--ds-ease-in-out);
}

.detailsCol:hover ~ .handle[data-side='details']::after,
.handle[data-side='details']:hover::after,
.handle[data-side='details'][data-dragging]::after {
  opacity: 1;
}

.handle[data-side='details']:hover::after {
  background: var(--dsw-alias-button-floating-hover);
  border-color: var(--dsw-alias-border-l3);
}

@media (prefers-reduced-motion: reduce) {
  .frame,
  .handle {
    transition: none;
  }
}
</style>
