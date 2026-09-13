<template>
  <div class="term-panel">
    <div class="tabs">
      <div class="tab active">
        <Icon name="terminal" :size="13" />
        <span class="tab-label">{{ tabLabel }}</span>
      </div>
      <button class="addtab" title="新终端标签" @click="noop">
        <Icon name="plus" :size="14" />
      </button>
      <span class="grow" />
      <span class="conn" :class="{ ok: store.connected }">{{ store.connected ? '事件流已连接' : '未连接' }}</span>
      <button class="close" @click="ui.terminalOpen = false">
        <Icon name="x" :size="15" />
      </button>
    </div>
    <pre ref="preEl" class="term-view">{{ logText }}</pre>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useSessionStore } from '../stores/sessions'
import { useUiStore } from '../stores/ui'
import { useMessage } from 'naive-ui'
import Icon from './Icon.vue'

const store = useSessionStore()
const ui = useUiStore()
const message = useMessage()
const preEl = ref<HTMLElement>()

const logText = computed(() => store.logs.join('\n') || '暂无日志')

const tabLabel = computed(() => {
  const ws = store.current?.workspace || ''
  if (!ws) return '会话日志'
  const parts = ws.replace(/\\/g, '/').split('/').filter(Boolean)
  return parts.slice(-2).join('/')
})

function noop() {
  message.info('演示环境：单标签终端')
}

watch(
  () => store.logs.length,
  async () => {
    await nextTick()
    const el = preEl.value
    if (el) el.scrollTop = el.scrollHeight
  }
)
</script>

<style scoped>
.term-panel {
  flex: none;
  height: 250px;
  border-top: 1px solid var(--line);
  background: #fff;
  display: flex;
  flex-direction: column;
}

.tabs {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px 4px;
}

.tab {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  background: #efede7;
  border-radius: 8px;
  padding: 5px 12px;
  font-family: var(--mono);
  font-size: 12px;
  color: var(--text-2);
  max-width: 260px;
}

.tab-label {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.addtab,
.close {
  border: none;
  background: none;
  width: 26px;
  height: 26px;
  border-radius: 6px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--text-3);
  cursor: pointer;
}

.addtab:hover,
.close:hover {
  background: rgba(60, 52, 24, 0.06);
  color: var(--text-2);
}

.grow {
  flex: 1;
}

.conn {
  font-size: 11.5px;
  color: #d07a7a;
}

.conn.ok {
  color: #58a977;
}

.term-view {
  flex: 1;
  margin: 0;
  padding: 6px 14px 10px;
  overflow: auto;
  font-family: var(--mono);
  font-size: 12.5px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-all;
  color: #33322e;
}
</style>
