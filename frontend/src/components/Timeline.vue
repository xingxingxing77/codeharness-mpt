<template>
  <div ref="scrollEl" class="timeline" @scroll="onScroll">
    <template v-for="b in store.blockList" :key="b.key">
      <div v-if="b.type === 'User'" class="urow">
        <div class="ustack">
          <div class="ububble">{{ b.tokens.join('') }}</div>
          <div class="uacts">
            <span class="ua" title="复制" @click="copyText(b.tokens.join(''))"><Icon name="copy" :size="14" /></span>
            <span class="ua" title="复制以修改" @click="copyText(b.tokens.join(''))"><Icon name="pencil" :size="14" /></span>
          </div>
        </div>
      </div>
      <ThoughtBlock v-else-if="b.type === 'Thought'" :block="b" />
      <DocsBlock v-else-if="b.type === 'Docs'" :block="b" />
      <EditorBlock v-else-if="b.type === 'Editor'" :block="b" />
      <TerminalBlock v-else-if="b.type === 'Terminal'" :block="b" />
      <TaskBlock v-else-if="b.type === 'Task'" :block="b" />
      <GalleryBlock v-else-if="b.type === 'Gallery'" :block="b" />
      <BrowserBlock v-else-if="b.type === 'Browser' || b.type === 'Browser-RT'" :block="b" />
      <NotebookBlock v-else-if="b.type === 'Notebook'" :block="b" />
      <GenericBlock v-else :block="b" />
    </template>
    <div v-if="!store.blockList.length" class="placeholder">
      {{ store.isRunning ? '等待智能体输出…' : '暂无事件' }}
    </div>
    <n-back-top :right="24" :bottom="120" />
  </div>
</template>

<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { NBackTop, useMessage } from 'naive-ui'
import { useSessionStore } from '../stores/sessions'
import BrowserBlock from './blocks/BrowserBlock.vue'
import DocsBlock from './blocks/DocsBlock.vue'
import EditorBlock from './blocks/EditorBlock.vue'
import GalleryBlock from './blocks/GalleryBlock.vue'
import GenericBlock from './blocks/GenericBlock.vue'
import Icon from './Icon.vue'
import NotebookBlock from './blocks/NotebookBlock.vue'
import TaskBlock from './blocks/TaskBlock.vue'
import TerminalBlock from './blocks/TerminalBlock.vue'
import ThoughtBlock from './blocks/ThoughtBlock.vue'

const store = useSessionStore()
const message = useMessage()
const scrollEl = ref<HTMLElement>()
const pinned = ref(true)

async function copyText(t: string) {
  try {
    await navigator.clipboard.writeText(t)
    message.success('已复制')
  } catch {
    message.error('复制失败')
  }
}

function onScroll() {
  const el = scrollEl.value
  if (!el) return
  pinned.value = el.scrollHeight - el.scrollTop - el.clientHeight < 60
}

watch(
  () => store.blockList.length,
  async () => {
    if (!pinned.value) return
    await nextTick()
    const el = scrollEl.value
    if (el) el.scrollTop = el.scrollHeight
  }
)

// streaming updates inside blocks also change content height
watch(
  () => store.blockList.map((b) => b.tokens.length + b.lines.length).join(','),
  async () => {
    if (!pinned.value) return
    await nextTick()
    const el = scrollEl.value
    if (el) el.scrollTop = el.scrollHeight
  }
)
</script>

<style scoped>
.timeline {
  flex: 1;
  overflow: auto;
  min-height: 0;
  padding: 6px 0;
}

.placeholder {
  text-align: center;
  opacity: 0.45;
  padding: 60px 0;
  font-size: 13px;
}

.urow {
  display: flex;
  justify-content: flex-end;
  padding: 12px 24px 2px;
}

.ustack {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  max-width: 72%;
}

.ububble {
  background: var(--bubble);
  border-radius: 18px;
  padding: 10px 16px;
  font-size: 14px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
}

.uacts {
  display: flex;
  gap: 8px;
  margin-top: 5px;
  opacity: 0.45;
}

.ua {
  cursor: pointer;
  color: var(--text-2);
  display: inline-flex;
}

.ua:hover {
  opacity: 1;
}
</style>
