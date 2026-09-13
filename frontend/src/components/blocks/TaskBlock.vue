<template>
  <BlockHead :block="block" label="计划">
    <div v-for="t in tasks" :key="t.task_id" class="task-row" :class="{ current: t.task_id === currentId }">
      <span>{{ t.task_id === currentId ? '▶' : doneIds.has(t.task_id) ? '✓' : '○' }}</span>
      <span>{{ t.task_id }}. {{ t.description || t.instruction || '' }}</span>
    </div>
    <div v-if="!tasks.length && !block.closed" class="loading">计划生成中…</div>
  </BlockHead>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { Block } from '../../types'
import BlockHead from './BlockHead.vue'

const props = defineProps<{ block: Block }>()

const tasks = computed(() => props.block.obj?.tasks || [])
const currentId = computed(() => props.block.obj?.current_task_id)
const doneIds = computed(() => {
  const cur = currentId.value
  return new Set(tasks.value.filter((t: any) => cur != null && t.task_id < cur).map((t: any) => t.task_id))
})
</script>

<style scoped>
.loading {
  opacity: 0.5;
  font-size: 12.5px;
}
</style>
