<template>
  <HoverCard :disabled="menuOpen || dragActive" :copy-text="displayTitle">
    <template #content>
      <div class="hoverTitle">{{ displayTitle }}</div>
      <div class="hoverTime">{{ rel }}</div>
      <div v-if="statusLabel" class="hoverStatus">
        <VStateDot :state="dot || 'done'" />
        <span>{{ statusLabel }}</span>
      </div>
    </template>
    <div
      v-bind="$attrs"
      class="sessionRow"
      :class="{ selected, dropBefore: marker === 'before', dropAfter: marker === 'after' }"
      role="treeitem"
      :aria-selected="selected"
      :draggable="draggable"
      @click="$emit('open', s.id)"
      @dragstart="onDragStart"
      @dragend="$emit('dragEnd')"
      @dragover="onDragOver"
      @drop="onDrop"
    >
      <span class="slot">
        <VStateDot v-if="dot" :state="dot" />
        <span class="srOnly">{{ statusLabel }}</span>
      </span>
      <span class="title">{{ displayTitle }}</span>
      <span class="time">{{ rel }}</span>
      <span class="rowActions" @click.stop>
        <VMenu :items="menu" align="end" @select="onMenu" @update:open="menuOpen = $event">
          <template #default="{ open, toggle }">
            <button
              class="iconBtn"
              :aria-haspopup="true"
              :aria-expanded="open"
              :aria-label="`会话操作：${displayTitle}`"
              @click.stop="toggle()"
            >
              <DsIcon name="ellipsis" :size="16" />
            </button>
          </template>
        </VMenu>
      </span>
    </div>
  </HoverCard>
</template>

<script setup lang="ts">
/** 会话行 32px。hover 才显操作钮、时间同时让位——纯 CSS 互斥，不进 JS。
 *  拖拽只报「落在哪一行的上半/下半」，真正的插位由 owner（WorkspaceBrowser）算，
 *  与参考项目 RowDragProps 同一分工。 */
import { computed, ref } from 'vue'
import DsIcon from '../ui/DsIcon.vue'
import HoverCard from '../ui/HoverCard.vue'
import VMenu from '../ui/VMenu.vue'
import VStateDot from '../ui/VStateDot.vue'
import type { MenuItem } from '../ui/menuTypes'
import { formatRelative } from '../../utils/relativeTime'
import type { Session } from '../../types'

defineOptions({ inheritAttrs: false })   // owner 的 class="indented" 必须落在行元素上，不是 hover 卡的包装

const props = defineProps<{
  s: Session
  selected: boolean
  pinned: boolean
  /** 允许拖（搜索中与收起态不给拖）。 */
  draggable?: boolean
  /** 有别的行正在被拖：本行的 dragover 才有意义，hover 卡要让路。 */
  dragActive?: boolean
  /** 插入标记画在本行的上沿还是下沿。 */
  marker?: 'before' | 'after' | null
}>()
const emit = defineEmits<{
  open: [id: string]
  rename: [s: Session]
  archive: [s: Session]
  pin: [s: Session]
  remove: [s: Session]
  dragStart: [id: string]
  dragEnd: []
  dragOver: [id: string, half: 'before' | 'after']
  drop: [id: string, half: 'before' | 'after']
}>()

const menuOpen = ref(false)

/** 指针落在行的上半还是下半——决定插到它前面还是后面（参考项目 rowHalf 同式）。 */
function rowHalf(e: DragEvent): 'before' | 'after' {
  const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
  return e.clientY < r.top + r.height / 2 ? 'before' : 'after'
}

function onDragStart(e: DragEvent) {
  if (!e.dataTransfer) return
  e.dataTransfer.effectAllowed = 'move'
  e.dataTransfer.setData('text/plain', props.s.id)
  emit('dragStart', props.s.id)
}

function onDragOver(e: DragEvent) {
  if (!props.dragActive || props.s.id === e.dataTransfer?.getData('text/plain')) return
  e.preventDefault()                       // 不 preventDefault 就不会触发 drop
  if (e.dataTransfer) e.dataTransfer.dropEffect = 'move'
  emit('dragOver', props.s.id, rowHalf(e))
}

function onDrop(e: DragEvent) {
  if (!props.dragActive) return
  e.preventDefault()
  emit('drop', props.s.id, rowHalf(e))
}

const displayTitle = computed(() => props.s.idea || props.s.project_name || props.s.id)
const rel = computed(() => formatRelative(props.s.created_at))

const STATUS: Record<string, { dot: 'done' | 'warning' | 'error' | 'ongoing' | null; label: string }> = {
  created: { dot: null, label: '' },
  running: { dot: 'ongoing', label: '进行中' },
  stopping: { dot: 'ongoing', label: '停止中' },
  awaiting_human: { dot: 'warning', label: '等待回答' },
  finished: { dot: 'done', label: '已完成' },
  stopped: { dot: 'done', label: '已停止' },
  failed: { dot: 'error', label: '失败' }
}

const cur = computed(() => STATUS[props.s.status] ?? { dot: null, label: '' })
const dot = computed(() => cur.value.dot)
const statusLabel = computed(() => cur.value.label)

const menu = computed<MenuItem[]>(() => [
  { key: 'rename', label: '重命名', icon: 'edit' },
  { key: 'archive', label: props.s.archived ? '取消归档' : '归档', icon: 'archive' },
  { key: 'pin', label: props.pinned ? '取消置顶' : '置顶', icon: 'plus' },
  { kind: 'sep' },
  { key: 'delete', label: '删除', icon: 'trash', danger: true }
])

function onMenu(it: MenuItem) {
  if (it.key === 'rename') emit('rename', props.s)
  else if (it.key === 'archive') emit('archive', props.s)
  else if (it.key === 'pin') emit('pin', props.s)
  else if (it.key === 'delete') emit('remove', props.s)
}
</script>

<style scoped>
.sessionRow {
  display: flex;
  align-items: center;
  gap: 0;
  height: 32px;
  padding: 0 8px;
  border-radius: 8px;
  cursor: pointer;
  user-select: none;
  color: var(--dsw-alias-label-primary);
  animation: row-in 150ms var(--ds-ease-in-out);
}

.sessionRow:hover,
.sessionRow.menuOpen {
  background: var(--dsw-alias-interactive-bg-hover);
}

.sessionRow.selected {
  background: var(--dsw-alias-interactive-bg-hover);
}

/* 拖拽插入标记：逐值照参考项目 Rows.module.css 的 .dropBefore/.dropAfter */
.sessionRow.dropBefore,
.sessionRow.dropAfter {
  position: relative;
}

.sessionRow.dropBefore::before,
.sessionRow.dropAfter::after {
  content: '';
  position: absolute;
  z-index: 1;
  left: 0;
  right: 4px;
  height: 12px;
  background: linear-gradient(
    55deg,
    transparent calc(50% - 1px),
    var(--dsw-alias-state-business-primary) calc(50% - 1px) calc(50% + 1px),
    transparent calc(50% + 1px)
  );
}

.sessionRow.dropBefore::before {
  top: -7px;
}

.sessionRow.dropAfter::after {
  bottom: -7px;
}

/* hover 卡正文：固定深色板上的三行字，两主题同值（figma 原色，所以不是令牌） */
.hoverTitle {
  font-size: 14px;
  line-height: 20px;
  color: #ffffff;
  overflow-wrap: break-word;
}

.hoverTime {
  font-size: 12px;
  line-height: 16px;
  color: #cfd3d6;
}

.hoverStatus {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  line-height: 20px;
  color: #adb2b8;
}

.slot {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 20px;
  color: var(--dsw-alias-label-tertiary);
}

.title {
  flex: 1;
  min-width: 0;
  margin: 0 6px 0 4px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: 14px;
  line-height: 20px;
}

.time {
  flex: none;
  font-size: 12px;
  line-height: 20px;
  color: var(--dsw-alias-label-tertiary);
}

.rowActions {
  flex: none;
  display: none;
  align-items: center;
  gap: 12px;
}

.sessionRow:hover .rowActions {
  display: inline-flex;
}

.sessionRow:hover .time {
  display: none;
}

.iconBtn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  padding: 0;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: var(--dsw-alias-label-tertiary);
  cursor: pointer;
}

.iconBtn:hover {
  color: var(--dsw-alias-label-primary);
}

.srOnly {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}

@keyframes row-in {
  from {
    opacity: 0;
  }
}

@media (prefers-reduced-motion: reduce) {
  .sessionRow {
    animation: none;
  }
}
</style>
