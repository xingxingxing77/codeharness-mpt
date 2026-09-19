<template>
  <!-- 用户：右对齐气泡，文本按字面呈现（不解析 markdown） -->
  <div v-if="b.type === 'User'" class="userRow" :data-chat-anchor-key="'user:' + b.key">
    <div class="userStack">
      <div class="bubble">{{ text }}</div>
      <div class="acts">
        <span class="clock">{{ clock }}</span>
        <button class="act" aria-label="复制" @click="copy(text)"><DsIcon name="copy" :size="16" /></button>
      </div>
    </div>
  </div>

  <!-- Thought / Docs：全宽正文，不再是卡片 -->
  <div v-else-if="isProse" class="prose" :data-chat-anchor-key="b.key" :data-streaming="open ? 'true' : undefined">
    <MarkdownText :src="text" :streaming="open" />
    <a v-if="artifactUrl" class="artifact" :href="artifactUrl" target="_blank" rel="noopener noreferrer">
      <DsIcon name="file" :size="14" />
      {{ fileName }}
    </a>
  </div>

  <!-- 其余：24px 折叠行 + 展开卡 -->
  <VDisclosureRow
    v-else
    :data-chat-anchor-key="b.key"
    :data-chat-call-id="b.key"
    :title="row.title"
    :summary="row.summary"
    :icon="row.icon"
    :state="row.state"
    :open="isOpen"
    @update:open="$emit('toggle', b.key, $event)"
  >
    <ToolCard :b="b" />
  </VDisclosureRow>
</template>

<script setup lang="ts">
/** 一个节点 = 后端一个 block。分派规则：User→气泡、Thought/Docs→正文、其余→折叠行。 */
import { computed } from 'vue'
import MarkdownText from './MarkdownText.vue'
import ToolCard from './ToolCard.vue'
import VDisclosureRow from '../ui/VDisclosureRow.vue'
import DsIcon from '../ui/DsIcon.vue'
import { useSessionStore } from '../../stores/sessions'
import { useToastStore } from '../../stores/toast'
import { formatRelative } from '../../utils/relativeTime'
import type { Block } from '../../types'

defineEmits<{ toggle: [key: string, open: boolean] }>()
const props = defineProps<{ b: Block; isOpen: boolean }>()

const store = useSessionStore()
const toast = useToastStore()

const b = computed(() => props.b)
const text = computed(() => b.value.tokens.join(''))
const open = computed(() => !b.value.closed)
const isProse = computed(() => b.value.type === 'Thought' || b.value.type === 'Docs')
const clock = computed(() => formatRelative(b.value.meta?.ts ? new Date(b.value.meta.ts * 1000).toISOString() : ''))

const fileName = computed(
  () => b.value.meta?.filename || b.value.doc?.filename || b.value.path?.split(/[\\/]/).pop() || ''
)
const artifactUrl = computed(() => (isProse.value && b.value.path ? store.workspaceUrl(b.value.path) : ''))

const KIND: Record<string, { title: string; icon: string }> = {
  Terminal: { title: 'Bash', icon: 'code' },
  Editor: { title: 'Write', icon: 'edit' },
  Task: { title: '更新任务清单', icon: 'checklist' },
  Browser: { title: 'Fetch', icon: 'globe' },
  'Browser-RT': { title: 'Fetch', icon: 'globe' },
  Gallery: { title: 'Image', icon: 'browse' },
  Notebook: { title: 'Code', icon: 'code' },
  System: { title: 'Tool call', icon: 'settings' }
}

const row = computed(() => {
  const kind = KIND[b.value.type] || { title: b.value.type || 'Tool call', icon: 'settings' }
  const state = open.value ? 'running' : b.value.meta?.ok === false ? 'error' : 'ok'
  const summary =
    b.value.cmd ||
    fileName.value ||
    b.value.page?.page_url ||
    b.value.url ||
    b.value.lines[0] ||
    text.value.split('\n')[0] ||
    ''
  return { ...kind, state: state as 'running' | 'ok' | 'error' | 'stopped', summary: summary.slice(0, 200) }
})

async function copy(t: string) {
  try {
    await navigator.clipboard.writeText(t)
    toast.push('已复制', 'success')
  } catch {
    toast.push('复制失败', 'error')
  }
}
</script>

<style scoped>
.userRow {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 6px;
}

.userStack {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 8px;
  max-width: min(525px, 82%);
}

.bubble {
  background: var(--dsw-specific-bubble);
  border-radius: 22px;
  padding: 10px 16px;
  font-size: 16px;
  line-height: 24px;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--dsw-alias-label-primary);
}

.acts {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-right: -6px;
}

.clock {
  font-size: 12px;
  color: var(--dsw-alias-label-tertiary);
}

.act {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  padding: 6px;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--dsw-alias-label-tertiary);
  cursor: pointer;
}

.act:hover {
  background: var(--dsw-alias-interactive-bg-hover);
  color: var(--dsw-alias-label-primary);
}

.prose {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.artifact {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  align-self: flex-start;
  font-size: 13px;
  color: var(--dsw-alias-state-business-primary);
  text-decoration: none;
}

.artifact:hover {
  text-decoration: underline;
  text-underline-offset: 3px;
}
</style>
