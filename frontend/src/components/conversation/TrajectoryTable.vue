<template>
  <div class="wrap">
    <div v-if="!rows.length" class="empty">trace 仅在 Redis 模式下有数据（进程内 runner 不落 span）</div>
    <table v-else class="tbl">
      <thead>
        <tr>
          <th class="num">#</th>
          <th>节点</th>
          <th>时刻</th>
          <th class="num">耗时</th>
          <th class="num">入</th>
          <th class="num">出</th>
          <th class="num">成本</th>
          <th>产出</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="r in rows"
          :key="r.idx"
          :class="{ clickable: !!jumpKey(r) }"
          @click="jump(r)"
        >
          <td class="num">{{ r.idx }}</td>
          <td>{{ r.node }}</td>
          <td class="mono">{{ clock(r.ts) }}</td>
          <td class="num">{{ r.durMs === undefined ? '—' : dur(r.durMs) }}</td>
          <td class="num">{{ r.pt }}</td>
          <td class="num">{{ r.ct }}</td>
          <td class="num">{{ r.cost.toFixed(4) }}</td>
          <td class="produced">{{ producedLabel(r.blocks) }}</td>
        </tr>
      </tbody>
      <tfoot>
        <tr>
          <td colspan="4">合计 {{ rows.length }} 次调用</td>
          <td class="num">{{ totals.pt }}</td>
          <td class="num">{{ totals.ct }}</td>
          <td class="num">{{ totals.cost.toFixed(4) }}</td>
          <td />
        </tr>
      </tfoot>
    </table>
  </div>
</template>

<script setup lang="ts">
/** Trajectory 台账：一次 LLM 调用一行，行点击跳回对话流里它产出的最后一块。
 *  报文检视做不了——我们没把 prompt/响应原文落盘，见 utils/trajectory.ts 的天花板注释。 */
import { computed } from 'vue'
import { producedLabel, type TrajRow } from '../../utils/trajectory'
import { formatLatencySeconds, formatMessageClock } from '../../utils/messageChrome'

const emit = defineEmits<{ jump: [key: string] }>()
const props = defineProps<{ rows: TrajRow[] }>()

const clock = (sec: number) => formatMessageClock(sec * 1000)
const dur = (ms: number) => `${formatLatencySeconds(ms)}秒`
/** 台账一次跑几十到几百行，够不上虚拟化的门槛；真到了再照参考项目那套上。 */
const totals = computed(() =>
  props.rows.reduce((a, r) => ({ pt: a.pt + r.pt, ct: a.ct + r.ct, cost: a.cost + r.cost }), { pt: 0, ct: 0, cost: 0 })
)
const jumpKey = (r: TrajRow) => r.blocks.at(-1)?.key || ''

function jump(r: TrajRow) {
  const key = jumpKey(r)
  if (key) emit('jump', key)
}
</script>

<style scoped>
.wrap {
  width: 100%;
  padding: 4px 16px 16px;
  box-sizing: border-box;
}

.tbl {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
  line-height: 20px;
  color: var(--dsw-alias-label-secondary);
}

.tbl th,
.tbl td {
  padding: 6px 10px;
  text-align: left;
  border-bottom: 1px solid var(--dsw-alias-border-l1);
  white-space: nowrap;
}

.tbl th {
  position: sticky;
  top: 0;
  z-index: 1;
  background: var(--dsw-alias-bg-base);
  font-weight: 500;
  color: var(--dsw-alias-label-tertiary);
}

.num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.mono {
  font-family: var(--ds-font-family-code);
  font-size: 12px;
  color: var(--dsw-alias-label-tertiary);
}

.produced {
  overflow: hidden;
  max-width: 260px;
  text-overflow: ellipsis;
  color: var(--dsw-alias-label-tertiary);
}

tfoot td {
  border-top: 1px solid var(--dsw-alias-border-l2);
  border-bottom: none;
  color: var(--dsw-alias-label-primary);
}

.clickable {
  cursor: pointer;
}

.clickable:hover td {
  background: var(--dsw-alias-interactive-bg-hover);
}

.empty {
  padding: 24px 2px;
  color: var(--dsw-alias-label-tertiary);
}
</style>
