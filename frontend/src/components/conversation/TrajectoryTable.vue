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
          <td class="num">{{ moneyBoth(r, 4) }}</td>
          <td class="produced">{{ producedLabel(r.blocks) }}</td>
        </tr>
      </tbody>
      <tfoot>
        <tr>
          <td colspan="4">合计 {{ rows.length }} 次调用</td>
          <td class="num">{{ totals.pt }}</td>
          <td class="num">{{ totals.ct }}</td>
          <td class="num">{{ moneyBoth(totals, 4) }}</td>
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
import { anchorKeyOf } from '../../utils/cpJump'
import { formatLatencySeconds, formatMessageClock } from '../../utils/messageChrome'
import { moneyBoth, sumCosts } from '../../utils/money'

const emit = defineEmits<{ jump: [key: string] }>()
const props = defineProps<{ rows: TrajRow[] }>()

const clock = (sec: number) => formatMessageClock(sec * 1000)
const dur = (ms: number) => `${formatLatencySeconds(ms)}秒`
/** 台账一次跑几十到几百行，够不上虚拟化的门槛；真到了再照参考项目那套上。
 *  成本用 sumCosts 分桶累加——把 ¥ 和 $ 加成一个是 C12 修掉的口径错误。 */
const totals = computed(() => ({
  ...props.rows.reduce((a, r) => ({ pt: a.pt + r.pt, ct: a.ct + r.ct }), { pt: 0, ct: 0 }),
  ...sumCosts(props.rows)
}))
/** 产出块的最后一块当作跳转目标；键必须过 `anchorKeyOf`——用户块在 DOM 上带 `user:` 前缀，
 *  裸 `b.key` 找不到元素，而滚动失败是**静默**的（点了没反应，没有报错可看）。 */
const jumpKey = (r: TrajRow) => {
  const b = r.blocks.at(-1)
  return b ? anchorKeyOf(b) : ''
}

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
  font-size: 12px;
  line-height: 18px;
  color: var(--dsw-alias-label-primary);
  background: var(--dsw-alias-bg-layer-1);
}

.tbl th,
.tbl td {
  height: 30px;
  padding: 0 8px;
  text-align: left;
  white-space: nowrap;
}

.tbl td {
  border-bottom: 1px solid var(--dsw-alias-border-l1);
}

.tbl th {
  position: sticky;
  top: 0;
  z-index: 3;
  background: var(--dsw-specific-sidebar-fill);
  border-bottom: 1px solid var(--dsw-alias-border-l2);
  font-weight: 500;
  color: var(--dsw-alias-label-tertiary);
}

tbody tr {
  transition: background-color 120ms var(--ds-ease-in-out), opacity 120ms var(--ds-ease-in-out);
}

tbody tr:hover {
  background: var(--dsw-alias-interactive-bg-hover);
}

/* 数字/时间列：源是定宽 71px + 左对齐（.metric/.time），不是右对齐 */
.num {
  width: 71px;
  text-align: left;
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
