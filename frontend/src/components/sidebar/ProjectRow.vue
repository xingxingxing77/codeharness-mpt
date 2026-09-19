<template>
  <div
    class="projectRow"
    role="treeitem"
    :aria-expanded="expanded"
    @click="$emit('toggle', groupKey)"
  >
    <span class="slot">
      <DsIcon
        v-if="!ungrouped"
        :name="expanded ? 'folder-open' : 'folder'"
        :size="16"
        class="lead folder"
        :class="{ folderActive: expanded && containsCurrent }"
      />
      <DsIcon name="chevron" :size="14" class="lead chev" :class="{ arrowOpen: expanded }" />
    </span>
    <span class="title">{{ label }}</span>
    <span class="rowActions" @click.stop>
      <button class="iconBtn" :aria-label="`在 ${label} 里新建会话`" @click.stop="$emit('newSession', groupKey)">
        <DsIcon name="plus" :size="16" />
      </button>
    </span>
  </div>
</template>

<script setup lang="ts">
/** 项目组行 34px。前导图标静止是文件夹、hover 换成 chevron（两套绝对定位叠在 16px 槽里），
 *  展开态 chevron 转 90°；当前会话所在组在展开时文件夹染业务蓝。
 *  不挂 ⋯ 菜单：后端没有工作区实体，重命名/删除工作区无处可接。 */
import DsIcon from '../ui/DsIcon.vue'

defineProps<{
  groupKey: string
  label: string
  ungrouped: boolean
  expanded: boolean
  containsCurrent: boolean
}>()

defineEmits<{
  toggle: [key: string]
  newSession: [key: string]
}>()
</script>

<style scoped>
.projectRow {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 34px;
  box-sizing: border-box;
  padding: 0 8px;
  border-radius: 8px;
  cursor: pointer;
  user-select: none;
  color: var(--dsw-alias-label-primary);
}

.projectRow:hover,
.projectRow.menuOpen {
  background: var(--dsw-alias-interactive-bg-hover);
}

.slot {
  position: relative;
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 20px;
  color: var(--dsw-alias-label-tertiary);
}

/* 缩进台阶 = 16px 槽 + 6px gap = 22px，子会话行按这个对齐 */
.lead {
  position: absolute;
  transition: opacity 100ms var(--ds-ease-in-out);
}

.chev {
  opacity: 0;
}

.projectRow:hover .folder {
  opacity: 0;
}

.projectRow:hover .chev {
  opacity: 1;
}

.arrowOpen {
  transform: rotate(90deg);
  transition: transform 150ms var(--ds-ease-in-out), opacity 100ms var(--ds-ease-in-out);
}

.folderActive {
  color: var(--dsw-alias-state-business-primary);
}

.title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: 14px;
  line-height: 20px;
}

.rowActions {
  flex: none;
  display: none;
  align-items: center;
  gap: 12px;
  height: 20px;
}

.projectRow:hover .rowActions {
  display: inline-flex;
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
</style>
