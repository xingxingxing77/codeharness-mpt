<template>
  <div v-if="turn.closing" class="root" :data-turn-tail="turn.key" data-time-hover-root>
    <MessageIconActions
      class="tail"
      :text="text"
      :time="turn.endTs * 1000"
      :run-ms="turn.runMs"
      :ttft-ms="turn.ttftMs"
      :tokens-per-second="turn.tokensPerSecond"
      clock="end"
    />
    <!-- B3：branch 钮 = 从这一轮分叉一条新会话（参照系 chat/TurnTailNodeView 的 branch 动作位）。
         分叉点取本轮最后一个事件的游标，**含本轮**——切在本轮之后才有意义。 -->
    <button type="button" class="branchBtn" title="从这一轮分叉出新会话"
            :disabled="busy || !turn.endCursor" @click="forkHere">
      <DsIcon name="branch" :size="14" />
      {{ busy ? '分叉中…' : '分叉' }}
    </button>
  </div>
</template>

<script setup lang="ts">
/** 参考项目 chat/TurnTailNodeView.tsx 的移植：一轮收口后的动作行。
 *  本轮没有收口正文（只有工具调用没说话）就整行不出现——参考项目同样 return null。 */
import { computed, ref } from 'vue'
import MessageIconActions from './MessageIconActions.vue'
import DsIcon from '../ui/DsIcon.vue'
import { blockText, type Turn } from '../../utils/turns'
import { useSessionStore } from '../../stores/sessions'

const props = defineProps<{ turn: Turn }>()
const text = computed(() => (props.turn.closing ? blockText(props.turn.closing) : ''))

const store = useSessionStore()
const busy = ref(false)

async function forkHere() {
  if (busy.value || !props.turn.endCursor) return
  busy.value = true
  try {
    await store.forkFrom(props.turn.endCursor)
  } finally {
    busy.value = false
  }
}
</script>

<style scoped>
.root {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* 图标钮自带 6px 内边距，-6px 出血让图标视觉左缘与正文对齐（参考项目 .actions 同值） */
.tail {
  margin-left: -6px;
}

.branchBtn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  align-self: flex-start;
  height: 28px;
  padding: 0 10px 0 6px;
  margin-left: -6px;
  border: none;
  border-radius: 999px;
  background: transparent;
  font-family: inherit;
  font-size: 13px;
  color: var(--dsw-alias-label-tertiary);
  cursor: pointer;
}

.branchBtn:hover:not(:disabled) {
  background: var(--dsw-alias-interactive-bg-hover);
  color: var(--dsw-alias-label-primary);
}

.branchBtn:disabled {
  opacity: 0.5;
  cursor: default;
}
</style>
