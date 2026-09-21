<template>
  <!-- B6 排队胶囊行：跑图途中投进来的插话，在 route 下一轮取走之前一直是「可撤回」的。
       参照系没有这一面（`grep -rln "updateQueue|queued" packages/*/src` 零命中），
       按它的风格取语义最近的类比：ui-conversation 的 `.turnErrorRow` 那一族 13px/20px 安静行 +
       本仓 `.inspectPill`/`.older` 的胶囊形状（999px 圆角、只改 opacity 不顶布局）。 -->
  <div v-if="store.queue.length" class="dock" role="list" aria-label="排队中的插话">
    <div v-for="q in store.queue" :key="q.id" class="capsule" role="listitem">
      <span class="text" :title="q.content">{{ q.content }}</span>
      <span v-if="q.send_to" class="to">{{ q.send_to }}</span>
      <button class="drop" type="button" :aria-label="'撤回这条插话'" :disabled="busy === q.id"
              @click="drop(q.id)">
        {{ busy === q.id ? '…' : '×' }}
      </button>
    </div>
    <span v-if="err" class="err">{{ err }}</span>
  </div>
</template>

<script setup lang="ts">
/** 「只在节点边界生效」不是这里做的（route 每轮 drain，见 codeharness/team_graph.py），
 *  这里只负责让人在**那一刻之前**改主意。撤回成功后不自己 splice：
 *  服务端会发 kind=queue/name=remove 事件回来，两条路（点的人、别的标签页）都靠它对齐。 */
import { ref } from 'vue'
import { api } from '../../api/client'
import { useSessionStore } from '../../stores/sessions'

const store = useSessionStore()
const busy = ref('')
const err = ref('')

async function drop(qid: string) {
  if (busy.value || !store.current) return
  busy.value = qid
  err.value = ''
  try {
    await api.dropQueued(store.current.id, qid)
  } catch (e) {
    // 404 的两种原因（已被 route 取走 / 这场没在跑）都由服务端原文说出去；
    // 同时**向服务端对齐**：这一条既然撤不掉，多半是投影落后了（活流断档期间的事件按游标丢掉），
    // 留着胶囊就是让人对着一条已经不存在的插话点第二下。
    err.value = (e as Error).message
    await store.loadQueue(store.current.id)
  } finally {
    busy.value = ''
  }
}
</script>

<style scoped>
.dock {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
}

.capsule {
  display: flex;
  align-items: center;
  gap: 6px;
  max-width: 320px;
  height: 24px;
  padding: 0 4px 0 10px;
  border: 1px solid var(--dsw-alias-border-l2);
  border-radius: 999px;
  background: var(--dsw-specific-tip);
  font-size: 13px;
  line-height: 20px;
  color: var(--dsw-alias-label-secondary);
}

.text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.to {
  flex: none;
  padding: 0 4px;
  border-radius: 6px;
  background: var(--dsw-alias-interactive-bg-active);
  font-size: 12px;
  color: var(--dsw-alias-label-primary);
}

.drop {
  flex: none;
  width: 18px;
  height: 18px;
  border: none;
  border-radius: 999px;
  background: transparent;
  font-size: 14px;
  line-height: 18px;
  color: var(--dsw-alias-label-tertiary);
  cursor: pointer;
}

.drop:hover:not(:disabled) {
  background: var(--dsw-alias-interactive-bg-hover);
  color: var(--dsw-alias-label-primary);
}

.err {
  font-size: 12px;
  line-height: 20px;
  color: var(--dsw-alias-state-error-primary);
}
</style>
