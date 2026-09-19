<template>
  <div
    class="sessionRow"
    :class="{ selected }"
    role="treeitem"
    :aria-selected="selected"
    @click="$emit('open', s.id)"
  >
    <span class="slot">
      <VStateDot v-if="dot" :state="dot" />
      <span class="srOnly">{{ statusLabel }}</span>
    </span>
    <span class="title">{{ displayTitle }}</span>
    <span class="time">{{ rel }}</span>
    <span class="rowActions" @click.stop>
      <VMenu :items="menu" align="end" @select="onMenu">
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
</template>

<script setup lang="ts">
/** 会话行 32px。hover 才显操作钮、时间同时让位——纯 CSS 互斥，不进 JS。 */
import { computed } from 'vue'
import DsIcon from '../ui/DsIcon.vue'
import VMenu from '../ui/VMenu.vue'
import VStateDot from '../ui/VStateDot.vue'
import type { MenuItem } from '../ui/menuTypes'
import { formatRelative } from '../../utils/relativeTime'
import type { Session } from '../../types'

const props = defineProps<{ s: Session; selected: boolean; pinned: boolean }>()
const emit = defineEmits<{
  open: [id: string]
  rename: [s: Session]
  archive: [s: Session]
  pin: [s: Session]
  remove: [s: Session]
}>()

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
