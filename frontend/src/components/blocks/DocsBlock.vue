<template>
  <BlockHead :block="block" :label="typeName">
    <div class="markdown-body" ref="mdEl" v-html="html" />
    <div v-if="block.path" class="doc-link">
      <n-button tag="a" :href="store.workspaceUrl(block.path)" target="_blank" text type="primary" size="small">
        打开产物文件：{{ fileName }}
      </n-button>
    </div>
  </BlockHead>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { NButton } from 'naive-ui'
import { useSessionStore } from '../../stores/sessions'
import { renderMarkdown } from '../../utils/render'
import { hydrateMermaid } from '../../utils/mermaid'
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

/* 文档里的 ```mermaid 围栏水合成图（方案 C：渲染在前端）。
 * ⚠ 只在块关闭后渲一次：流式中 v-html 每次追加都重建 DOM，逐 token 重渲图是纯浪费 */
const mdEl = ref<HTMLElement>()
onMounted(() => {
  if (props.block.closed) hydrateMermaid(mdEl.value)
})
watch(
  () => props.block.closed,
  async (v) => {
    if (!v) return
    await nextTick()
    hydrateMermaid(mdEl.value)
  }
)
</script>

<style scoped>
.doc-link {
  margin-top: 6px;
}
</style>
