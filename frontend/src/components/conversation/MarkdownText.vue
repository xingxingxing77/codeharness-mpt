<template>
  <div ref="el" class="md" :class="{ streaming }" v-html="html" />
</template>

<script setup lang="ts">
/** 增量 markdown：除末尾 2 个顶层块外都冻结成缓存，只重解析尾部。
 *  逐 token 重渲染整篇是流式期最大的开销。 */
import { computed, nextTick, ref, watch } from 'vue'
import { renderMarkdown } from '../../utils/render'
import { splitStable } from '../../utils/mdBlocks'
import { hydrateMermaid } from '../../utils/mermaid'

const props = withDefaults(defineProps<{ src: string; streaming?: boolean }>(), { streaming: false })
const el = ref<HTMLElement>()

let cachedKey: string | null = null
let cachedHtml = ''

const html = computed(() => {
  const { stable, tail } = splitStable(props.src || '')
  if (stable !== cachedKey) {
    cachedKey = stable
    cachedHtml = stable ? renderMarkdown(stable) : ''
  }
  return cachedHtml + (tail ? renderMarkdown(tail) : '')
})

// mermaid 只在收口后水合：流式期围栏还没闭合，每帧重画图会把主线程吃光
watch(
  () => [props.streaming, html.value] as const,
  async ([streaming]) => {
    if (streaming || !el.value) return
    await nextTick()
    hydrateMermaid(el.value)
  },
  { immediate: true }
)
</script>

<style scoped>
.md {
  font: var(--dsw-font-markdown-base);
  color: var(--dsw-alias-label-primary);
  overflow-wrap: anywhere;
}

.md :deep(> *:first-child) {
  margin-top: 0 !important;
}

.md :deep(> *:last-child) {
  margin-bottom: 0 !important;
}

.md :deep(h1) {
  font: var(--dsw-font-markdown-h1);
  margin: 32px 0 16px;
}

.md :deep(h2) {
  font: var(--dsw-font-markdown-h2);
  margin: 32px 0 16px;
}

.md :deep(h3) {
  font: var(--dsw-font-markdown-h3);
  margin: 32px 0 16px;
}

.md :deep(h4) {
  font: var(--dsw-font-markdown-h4);
  margin: 16px 0;
}

.md :deep(h5),
.md :deep(h6) {
  font: var(--dsw-font-markdown-base-strong);
  margin: 16px 0;
}

.md :deep(p) {
  margin: 16px 0;
}

.md :deep(ul),
.md :deep(ol) {
  margin: 16px 0;
  padding-left: 18px;
}

.md :deep(li:not(:first-child)) {
  margin-top: 6px;
}

.md :deep(li::marker) {
  line-height: 28px;
  color: var(--dsw-alias-label-secondary);
}

.md :deep(a) {
  color: var(--dsw-alias-state-business-primary);
  text-decoration: none;
}

.md :deep(a:hover),
.md :deep(a:focus-visible) {
  text-decoration: underline;
}

.md :deep(a:focus-visible) {
  outline: 2px solid var(--dsw-alias-state-business-primary);
  border-radius: 2px;
}

.md :deep(hr) {
  height: 1px;
  border: none;
  margin: 32px 0;
  background: var(--dsw-alias-border-l2);
}

.md :deep(blockquote) {
  margin: 16px 0;
  padding-left: 14px;
  border-left: 2px solid var(--dsw-alias-label-caption);
  color: var(--dsw-alias-label-secondary);
}

.md :deep(code:not(pre code)) {
  font-family: var(--ds-font-family-code);
  font-size: 0.875em;
  background: var(--dsw-alias-markdown-inline-code);
  border-radius: 6px;
  padding: 0 5px;
  display: inline-flex;
}

.md :deep(table) {
  width: 100%;
  margin: 16px 0;
  border-collapse: collapse;
  font: var(--dsw-font-markdown-table);
}

.md :deep(th),
.md :deep(td) {
  padding: 10px 16px;
  text-align: left;
}

.md :deep(th) {
  font: var(--dsw-font-markdown-table-head);
  border-bottom: 1px solid var(--dsw-alias-border-l3);
}

.md :deep(td) {
  border-bottom: 1px solid var(--dsw-alias-border-l2);
}

.md :deep(img) {
  max-width: 100%;
  border-radius: 12px;
}

.md :deep(pre) {
  margin: 0;
}
</style>
