<template>
  <div v-if="turn.closing" class="root" :data-turn-tail="turn.key" data-time-hover-root>
    <MessageIconActions
      class="tail"
      :text="text"
      :time="turn.endTs * 1000"
      :run-ms="turn.runMs"
      :tokens-per-second="turn.tokensPerSecond"
      clock="end"
    />
  </div>
</template>

<script setup lang="ts">
/** 参考项目 chat/TurnTailNodeView.tsx 的移植：一轮收口后的动作行。
 *  本轮没有收口正文（只有工具调用没说话）就整行不出现——参考项目同样 return null。
 *  branch 钮要 session.fork，后端还没有，所以这里也不留占位。 */
import { computed } from 'vue'
import MessageIconActions from './MessageIconActions.vue'
import { blockText, type Turn } from '../../utils/turns'

const props = defineProps<{ turn: Turn }>()
const text = computed(() => (props.turn.closing ? blockText(props.turn.closing) : ''))
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
</style>
