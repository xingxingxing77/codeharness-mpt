<template>
  <div class="spage">
    <h1 class="sptitle">用量</h1>
    <div class="ssec-sub" style="margin: 6px 0 14px">
      Token 与成本为只读观测项，不设预算闸门——没有任何东西会因为超支而停你的会话。
    </div>

    <div class="u-strip">
      <div class="u-cell"><b>{{ rows.length }}</b><span>会话</span></div>
      <div class="u-cell"><b>{{ fmt(total.pt) }}</b><span>输入 tokens</span></div>
      <div class="u-cell"><b>{{ fmt(total.ct) }}</b><span>输出 tokens</span></div>
      <div class="u-cell"><b>{{ moneyBoth(total) }}</b><span>总成本（分币种·不换算）</span></div>
      <!-- B4 的读面：票今天已经落库（`Session.feedback`），但只有会话自己看得见——
           聚到这一格才算「反馈这条链跑通了」。零=诚实的零，不藏格。认不出的值单列出来，
           免得第三种票被静默吞进「有用」或「无用」任何一档。 -->
      <div class="u-cell">
        <b>{{ votes.like }} · {{ votes.dislike }}</b>
        <span>反馈（有用 · 无用）{{ votes.other ? ` · 未识别 ${votes.other}` : '' }}</span>
      </div>
    </div>

    <div v-if="rows.length" class="sgroup u-table">
      <div class="u-row u-head">
        <span>会话</span><span>状态</span><span class="n">输入</span><span class="n">输出</span>
        <span class="n">成本</span><span class="n">创建</span>
      </div>
      <div v-for="r in rows" :key="r.id" class="u-row" :class="{ dead: !r.cost }"
           role="button" tabindex="0" :aria-label="`打开会话 ${r.idea}`"
           @click="open(r)" @keydown.enter="open(r)">
        <span class="u-t" :title="r.idea">{{ r.project_name || r.idea || r.id }}</span>
        <span class="u-st">{{ STATUS[r.status] || r.status }}</span>
        <span class="n">{{ fmt(r.cost?.total_prompt_tokens ?? 0) }}</span>
        <span class="n">{{ fmt(r.cost?.total_completion_tokens ?? 0) }}</span>
        <span class="n">{{ moneyBoth(r.cost) }}</span>
        <span class="n u-d">{{ (r.created_at || '').slice(5, 16) }}</span>
      </div>
    </div>
    <div v-else class="sgroup empty">还没有用量——跑一场会话，这里就有数字。</div>
  </div>
</template>

<script setup lang="ts">
/** N1 只读用量页：数据源就是会话表本身（store.sessions.cost），无新端点。
 *  替代随预算作废删除的旧假计费页——那张页的每个字面量都在这里换成了真值。 */
import { computed, onMounted } from 'vue'
import { useSessionStore } from '../../stores/sessions'
import { useUiStore } from '../../stores/ui'
import { moneyBoth, sumCosts } from '../../utils/money'
import { countVotes } from '../../utils/stats'
import type { Session } from '../../types'

const store = useSessionStore()
const ui = useUiStore()

const STATUS: Record<string, string> = {
  created: '排队中', running: '运行中', awaiting_human: '待人工',
  stopping: '停止中', finished: '已完成', stopped: '已停止', failed: '失败'
}

onMounted(() => store.loadSessions())

const rows = computed(() =>
  [...store.sessions].sort((a, b) => (a.created_at < b.created_at ? 1 : -1))
)

const total = computed(() => ({
  ...rows.value.reduce(
    (p, r) => ({
      pt: p.pt + (r.cost?.total_prompt_tokens ?? 0),
      ct: p.ct + (r.cost?.total_completion_tokens ?? 0)
    }),
    { pt: 0, ct: 0 }
  ),
  // 成本分桶累加。原来这里是 `cost: p.cost + r.cost.total_cost`——把两种币价加成一个数，
  // 而标签写着「人民币价目」，等于每一行美元模型都在给人民币计数（C12 修的就是这个）。
  ...sumCosts(rows.value.map(r => r.cost))
}))

// 按 (会话, 尾行) 数票，不按会话数：一场会话投三票就是三票（口径与 `countVotes` 一致）
const votes = computed(() => countVotes(rows.value))

function fmt(n: number): string {
  return n >= 10000 ? `${(n / 1000).toFixed(1)}k` : n.toLocaleString('en-US')
}

function open(r: Session) {
  store.select(r.id)
  ui.settingsFull = false
}
</script>

<style scoped>
.u-strip {
  display: flex;
  border: 1px solid var(--card-border);
  border-radius: 12px;
  background: var(--card);
  margin-bottom: 18px;
}

.u-cell {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 13px 18px;
  min-width: 0;
}

.u-cell + .u-cell {
  border-left: 1px solid var(--line);
}

.u-cell b {
  font-size: 19px;
  font-weight: 600;
  color: var(--text);
  font-variant-numeric: tabular-nums;
}

.u-cell span {
  font-size: 12px;
  color: var(--text-3);
}

.u-table {
  padding: 0;
}

.u-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 72px 72px 72px 76px 82px;
  gap: 10px;
  align-items: center;
  padding: 9px 16px;
  font-size: 13px;
  color: var(--text-2);
  cursor: pointer;
}

.u-row + .u-row {
  border-top: 1px solid var(--line);
}

@media (hover: hover) and (pointer: fine) {
  .u-row:not(.u-head):hover {
    background: var(--chip-bg);
  }
}

.u-row:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: -2px;
}

.u-head {
  cursor: default;
  font-size: 12px;
  color: var(--text-3);
  background: #fbfaf7;
}

.u-row.dead {
  cursor: default;
}

.u-t {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text);
}

.u-st {
  font-size: 12px;
  color: var(--text-2);
}

.n {
  text-align: right;
  font-family: var(--mono);
  font-size: 12.5px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.u-d {
  color: var(--text-3);
}
</style>
