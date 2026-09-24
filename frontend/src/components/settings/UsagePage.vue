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
      <div class="u-cell"><b>{{ moneyBoth(total) }}</b><span title="厂商回执原样累加：thinking 模型的 token 含其内部迭代，不等于本会话提示量；真账去厂商控制台核">总成本（分币种·不换算｜回执原样）</span></div>
      <!-- B4 的读面：票今天已经落库（`Session.feedback`），但只有会话自己看得见——
           聚到这一格才算「反馈这条链跑通了」。零=诚实的零，不藏格。认不出的值单列出来，
           免得第三种票被静默吞进「有用」或「无用」任何一档。 -->
      <div class="u-cell">
        <b>{{ votes.like }} · {{ votes.dislike }}</b>
        <span>反馈（有用 · 无用）{{ votes.other ? ` · 未识别 ${votes.other}` : '' }}</span>
      </div>
      <!-- T4-③/C19：三笔「花了钱却没产出」的调用。分开报——截断是端点把回答切了，未知命令是
           模型要的工具有些没给它看见，空正文是模型说完却没吐一个字（并成一个"浪费数"就没人知道该去修哪一个）。 -->
      <div class="u-cell">
        <b>{{ waste.unknown }} · {{ waste.truncated }} · {{ waste.empty }}</b>
        <span>无效调用（未知命令 · 被截断 · 空正文）</span>
      </div>
    </div>

    <!-- B4「按时间分」：按投票时刻分天。旧票（09-23 之前）没记时刻 ⇒ 单列一档，
         绝不拿 created_at 顶上去：那会把"不知道什么时候点的"报成"那天点的"。 -->
    <div v-if="days.length || undated.like || undated.dislike" class="sgroup u-days">
      <div class="u-days-head">反馈按天</div>
      <div v-for="d in days" :key="d.day" class="u-day">
        <span class="u-d">{{ d.day.slice(5) }}</span>
        <span class="n">有用 {{ d.like }}</span><span class="n">没用 {{ d.dislike }}</span>
      </div>
      <div v-if="undated.like || undated.dislike" class="u-day old">
        <span class="u-d">未记时刻</span>
        <span class="n">有用 {{ undated.like }}</span><span class="n">没用 {{ undated.dislike }}</span>
      </div>
    </div>

    <!-- B4「按票筛」：数据源就是列表带出的 `Session.feedback`，所以筛选也在前端——
         这张表本来就是全量渲染（无分页），筛出来的数与表里的数同源，不会各说一遍。 -->
    <div v-if="rows.length" class="u-filters" role="group" aria-label="按票筛选会话">
      <button v-for="f in FILTERS" :key="f.k" class="u-chip" :class="{ on: filter === f.k }"
              type="button" :aria-pressed="filter === f.k ? 'true' : 'false'" @click="filter = f.k">
        {{ f.label }}<em>{{ counted(f.k) }}</em>
      </button>
    </div>

    <div v-if="shown.length" class="sgroup u-table">
      <div class="u-row u-head">
        <span>会话</span><span>状态</span><span class="n">输入</span><span class="n">输出</span>
        <span class="n">成本</span><span class="n">创建</span>
      </div>
      <div v-for="r in shown" :key="r.id" class="u-row" :class="{ dead: !r.cost }"
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
    <div v-else-if="rows.length" class="sgroup empty">这一筛没有会话。</div>
    <div v-else class="sgroup empty">还没有用量——跑一场会话，这里就有数字。</div>
  </div>
</template>

<script setup lang="ts">
/** N1 只读用量页：数据源就是会话表本身（store.sessions.cost），无新端点。
 *  替代随预算作废删除的旧假计费页——那张页的每个字面量都在这里换成了真值。 */
import { computed, onMounted, ref } from 'vue'
import { useSessionStore } from '../../stores/sessions'
import { useUiStore } from '../../stores/ui'
import { moneyBoth, sumCosts } from '../../utils/money'
import { countVotes, wasteTotals } from '../../utils/stats'
import { filterRowsByVote, voteBuckets, type VoteFilter } from '../../utils/votes'
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
// 跨会话求和吃的就是列表带出的那份 cost 快照（零新端点；老记录里没这两个键 ⇒ 按 0 计）
const waste = computed(() => wasteTotals(rows.value))

const FILTERS: { k: VoteFilter; label: string }[] = [
  { k: 'all', label: '全部' }, { k: 'any', label: '有票' }, { k: 'like', label: '有用' },
  { k: 'dislike', label: '没用' }, { k: 'none', label: '未评' },
]
const filter = ref<VoteFilter>('all')
const shown = computed(() => filterRowsByVote(rows.value, filter.value))
const days = computed(() => voteBuckets(rows.value).days)
const undated = computed(() => voteBuckets(rows.value).undated)
const counted = (k: VoteFilter) => (k === 'all' ? rows.value.length : filterRowsByVote(rows.value, k).length)

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

/* 按票筛：沿用设置页既有的小胶囊，不新造几何（高度跟着 26px 的输入框走） */
.u-filters { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }

.u-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 26px;
  padding: 0 10px;
  border: 1px solid var(--dsw-alias-border-l1);
  border-radius: 999px;
  background: transparent;
  font-family: inherit;
  font-size: 12px;
  color: var(--dsw-alias-label-secondary);
  cursor: pointer;
}

.u-chip em { font-style: normal; color: var(--dsw-alias-label-tertiary); }

.u-chip.on {
  border-color: var(--dsw-alias-label-primary);
  color: var(--dsw-alias-label-primary);
  background: var(--dsw-alias-interactive-bg-hover);
}

/* 按天分：一小块列表。「未记时刻」那一档压暗——它是缺数据，不是零票 */
.u-days { padding: 10px 12px; margin-bottom: 14px; }
.u-days-head { font-size: 12px; color: var(--dsw-alias-label-tertiary); margin-bottom: 6px; }

.u-day {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 13px;
  color: var(--dsw-alias-label-secondary);
  line-height: 22px;
}

.u-day .n { margin-left: auto; }
.u-day .u-d { min-width: 56px; }
.u-day.old { color: var(--dsw-alias-label-tertiary); }

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
