<template>
  <VDisclosureRow
    ref="rowEl"
    :data-chat-anchor-key="b.key"
    title="Think"
    icon="think-14"
    :state="running ? 'running' : 'ok'"
    :open="isOpen"
    :summary="summary"
    :following="running"
    @update:open="$emit('toggle', b.key, $event)"
  >
    <div class="thinkBody">{{ text }}</div>
  </VDisclosureRow>
</template>

<script setup lang="ts">
/** 参考项目 chat/ReasoningRow.tsx 的移植：助手的思考过程是一条 Think 披露行，
 *  不是全宽正文。折叠态只显示一行摘要——流式期贴最新一行并滚到行尾，收口后回到首行；
 *  展开体是原样 pre-wrap 文本（参考项目不解析它的 markdown）。 */
import { computed, nextTick, ref, watch } from 'vue'
import VDisclosureRow from '../ui/VDisclosureRow.vue'
import type { Block } from '../../types'

defineEmits<{ toggle: [key: string, open: boolean] }>()
const props = defineProps<{ b: Block; isOpen: boolean }>()

const rowEl = ref<{ $el: HTMLElement } | null>(null)
const text = computed(() => props.b.tokens.join(''))
const running = computed(() => !props.b.closed)

function firstLine(s: string): string {
  const i = s.indexOf('\n')
  return i === -1 ? s : s.slice(0, i)
}

function latestLine(s: string): string {
  const trimmed = s.trimEnd()
  const i = trimmed.lastIndexOf('\n')
  return i === -1 ? trimmed : trimmed.slice(i + 1)
}

const summary = computed(() => (running.value ? latestLine(text.value) : firstLine(text.value)))

/** following 已经让 CSS 关掉省略号，滚位这一步得自己写：摘要滚到行尾才看得到最新的话。 */
watch(
  [summary, running],
  async () => {
    await nextTick()
    const el = rowEl.value?.$el?.querySelector<HTMLElement>('.summary')
    if (!el) return
    el.scrollLeft = running.value ? el.scrollWidth - el.clientWidth : 0
  },
  { immediate: true }
)
</script>

<style scoped>
/* 折叠行的 24px 行高、三级色与 body 左缩进 22px 都在 VDisclosureRow 里，
   这里只补参考项目 thinkBody 特有的 pre-wrap 与断词。 */
.thinkBody {
  color: var(--dsw-alias-label-tertiary);
  font-size: 14px;
  line-height: 24px;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
