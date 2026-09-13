<template>
  <BlockHead :block="block" :label="typeName">
    <div class="markdown-body" v-html="html" />
    <div v-if="block.path" class="doc-link">
      <n-button tag="a" :href="store.workspaceUrl(block.path)" target="_blank" text type="primary" size="small">
        打开产物文件：{{ fileName }}
      </n-button>
    </div>
  </BlockHead>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { NButton } from 'naive-ui'
import { useSessionStore } from '../../stores/sessions'
import { renderMarkdown } from '../../utils/render'
import type { Block } from '../../types'
import BlockHead from './BlockHead.vue'

const props = defineProps<{ block: Block }>()
const store = useSessionStore()

const TYPE_NAMES: Record<string, string> = {
  prd: '产品需求文档',
  design: '系统设计',
  task: '任务分解'
}
const typeName = computed(() => TYPE_NAMES[props.block.meta?.type] || '文档')
const fileName = computed(() => props.block.path?.split(/[\\/]/).pop() || '')
const html = computed(() => renderMarkdown(props.block.tokens.join('')))
</script>

<style scoped>
.doc-link {
  margin-top: 6px;
}
</style>
