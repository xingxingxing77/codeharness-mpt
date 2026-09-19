<template>
  <div v-if="visible" class="os-card">
    <div class="os-sec">
      <div class="os-h">输出</div>
      <div v-if="!outputs.length" class="os-none">暂无产物</div>
      <div v-for="o in outputs" :key="o" class="os-item" @click="openFiles">{{ o }}</div>
    </div>
    <div class="os-div" />
    <div class="os-sec">
      <div class="os-h">来源</div>
      <div v-if="!sources.length" class="os-none">暂无来源</div>
      <div v-for="s in sources" :key="s" class="os-item" @click="openFiles">{{ s }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useSessionStore } from '../stores/sessions'
import { useUiStore } from '../stores/ui'

const store = useSessionStore()
const ui = useUiStore()

/** 会话有内容（用户消息或智能体事件）后才出现，与参考图一致 */
const visible = computed(() => store.blockList.length > 0)

const outputs = computed(() =>
  store.blockList
    .filter((b) => b.type === 'Docs' && b.closed)
    .map((b) => b.doc?.filename || b.doc?.name || '文档产物')
    .slice(0, 5)
)

const sources = computed(() => {
  const set = new Set<string>()
  for (const b of store.blockList) {
    if ((b.type === 'Editor' || b.type === 'Terminal') && b.path) set.add(b.path.split(/[\\/]/).pop() || b.path)
    if (b.type === 'Editor' && b.cmd) set.add(b.cmd)
  }
  return [...set].slice(0, 5)
})

function openFiles() {
  ui.openRight()
  ui.rightView = 'files'
}
</script>

<style scoped>
.os-card {
  position: absolute;
  top: 12px;
  right: 18px;
  width: 290px;
  background: #fbfaf7;
  border: 1px solid var(--card-border);
  border-radius: 14px;
  padding: 13px 16px;
  z-index: 5;
}

.os-h {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text);
  margin-bottom: 7px;
}

.os-none {
  font-size: 13px;
  color: var(--text-3);
}

.os-item {
  font-size: 13px;
  color: var(--text-2);
  padding: 2px 0;
  cursor: pointer;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.os-item:hover {
  color: var(--text);
  text-decoration: underline;
}

.os-div {
  height: 1px;
  background: var(--card-border);
  margin: 11px 0;
}
</style>
