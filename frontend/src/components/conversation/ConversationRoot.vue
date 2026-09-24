<template>
  <div class="root" :data-phase="store.isRunning ? 'running' : 'active'">
    <div class="header">
      <nav class="crumbs" aria-label="会话层级">
        <span class="crumb dim">{{ store.current?.project_name || store.current?.id }}</span>
        <span class="sep">/</span>
        <span class="crumb cur">{{ store.current?.idea || '新会话' }}</span>
      </nav>
      <span class="modeChip">{{ paradigmLabel }}</span>
      <!-- 子 agent 胶囊：多角色线（经典 SOP 的 PM/Architect/Engineer/QA、动态线的 Mike/Alice…）
           跑起来后头部要看得出「此刻是谁在干活」，否则流里全是 Think 行、根本分不出人。 -->
      <AgentChip v-if="agentLabel" :label="agentLabel" :running="store.isRunning" />
      <span class="grow" />
      <!-- C25②：这颗金额是**厂商回执原样累加**的。thinking 模型（StepFun `step-3.5-flash` 实测）把内部
           迭代也计进 pt/ct，同一条 prompt 它报 59 万而 7B 报 1.2k ⇒ 账面能显出几百元这种荒谬数。
           只补一句口径，不改数也不折算（真账去厂商控制台核）。 -->
      <span class="cost" title="按厂商回执原样累加：thinking 模型的 token 含其内部迭代，不等于本会话提示量">{{ moneyBoth(store.cost) }}</span>
      <button
        class="iconBtn"
        :class="{ on: ui.rightPanel && ui.rightView === 'cards' }"
        title="会话日志"
        aria-label="会话日志"
        @click="openLogs"
      >
        <DsIcon name="code" :size="16" />
      </button>
      <button class="iconBtn" title="工具面板" aria-label="工具面板" @click="ui.toggleRight()">
        <DsIcon name="panel-left" :size="16" />
      </button>
    </div>

    <div class="tabs" role="tablist">
      <button
        v-for="v in VIEWS"
        :key="v.key"
        type="button"
        role="tab"
        class="tab"
        :class="{ tabActive: ui.centerView === v.key }"
        :aria-selected="ui.centerView === v.key"
        @click="ui.centerView = v.key"
      >
        {{ v.label }}
      </button>
    </div>

    <div ref="scrollEl" class="scroller" data-conversation-scroll @scroll="onScroll">
      <div v-show="ui.centerView === 'chat'" ref="flowEl" class="column" data-chat-flow>
        <div v-if="!store.blockList.length" class="placeholder">
          {{ store.isRunning ? '等待智能体输出…' : '暂无事件' }}
        </div>

        <!-- 加载更早（B2）：服务端 has_more 说前面还有事件才现身，所以它不是一颗永远点不动的假钮 -->
        <button v-if="store.hasMoreEarlier" class="earlier" :disabled="store.loadingEarlier"
                @click="loadEarlier">
          {{ store.loadingEarlier ? '加载中…' : '加载更早' }}
        </button>

        <template v-for="r in rows" :key="r.kind === 'node' ? r.b.key : r.turn.key">
          <ChatNode
            v-if="r.kind === 'node'"
            :b="r.b"
            :is-open="expanded[r.b.key] === true"
            @toggle="setOpen"
          />
          <TurnTail v-else :turn="r.turn" />
        </template>

        <div v-if="store.isRunning" class="turnStatus" role="status" aria-live="polite">
          <span class="shimmer">Deep diving...</span>
          <span v-if="elapsedLabel" class="elapsed">{{ elapsedLabel }}</span>
        </div>
      </div>

      <TrajectoryTable v-if="ui.centerView === 'trajectory'" :rows="trajRows" @jump="jumpToBlock" />

      <div v-if="ui.centerView === 'chat'" class="toBottomSlot">
        <button v-if="!follow.atBottom.value" class="toBottom" aria-label="回到底部" @click="follow.scrollToBottom(true)">
          <DsIcon name="chevron-down" :size="14" />
        </button>
      </div>

      <div ref="seatEl" class="composerSeat" data-composer-seat>
        <QuestionCard v-if="takeover === 'question'" :question="store.humanQuestion?.value || ''" />
        <ApprovalCard v-else-if="takeover === 'approval' && store.pendingApproval"
                      :item="store.pendingApproval" />
        <template v-else>
          <slot name="composer" />
          <StatsLine />
        </template>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/** 中栏：748px 单列 + 唯一滚动容器 + sticky composer 座位。
 *  顶栏并入这里（参考项目没有独立顶栏，只有会话头）。 */
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue'
import ChatNode from './ChatNode.vue'
import TurnTail from './TurnTail.vue'
import StatsLine from './StatsLine.vue'
import AgentChip from './AgentChip.vue'
import { activeRole, agentChipLabel } from '../../utils/agents'
import TrajectoryTable from './TrajectoryTable.vue'
import QuestionCard from '../composer/QuestionCard.vue'
import { moneyBoth } from '../../utils/money'
import ApprovalCard from '../composer/ApprovalCard.vue'
import DsIcon from '../ui/DsIcon.vue'
import { useSessionStore } from '../../stores/sessions'
import { useUiStore } from '../../stores/ui'
import { useFollowScroll } from '../../composables/useFollowScroll'
import { buildRows } from '../../utils/turns'
import { buildTrajectory } from '../../utils/trajectory'

const store = useSessionStore()
const ui = useUiStore()

/** 页签条：参考项目由 conversation.view 的贡献数决定（>1 才显示），我们固定两个视图。 */
const VIEWS = [
  { key: 'chat', label: 'Chat' },
  { key: 'trajectory', label: 'Trajectory' }
] as const

const trajRows = computed(() => buildTrajectory(store.spans, store.blockList))

/** 台账行点击跳回对话流里它产出的那一块：切视图与滚动要等一次 DOM 更新。 */
async function jumpToBlock(key: string) {
  ui.centerView = 'chat'
  await nextTick()
  const el = scrollEl.value?.querySelector<HTMLElement>(`[data-chat-anchor-key="${CSS.escape(key)}"]`)
  el?.scrollIntoView({ block: 'center' })
}

/** 右栏（回放的超步行）递过来的口令：先清再跳，清早于跳是为了下一次点同一行仍能触发。
 *  元素找不到就什么都不做——那一块可能压根没加载（对话流是游标翻页的，超步却能翻到更老的场），
 *  这时候不假装滚到了，也不弹错：右栏那颗按钮自己会把「就近/没有对应块」写在旁边。 */
watch(() => ui.jumpKey, (k) => {
  if (!k) return
  ui.jumpKey = ''
  void jumpToBlock(k)
})

const scrollEl = ref<HTMLElement>()
const flowEl = ref<HTMLElement>()
const seatEl = ref<HTMLElement>()
const expanded = reactive<Record<string, boolean>>({})

function setOpen(key: string, v: boolean) {
  expanded[key] = v
}

/** 日志钮：已在日志页就收起右栏，否则开右栏并切过去 */
function openLogs() {
  if (ui.rightPanel && ui.rightView === 'cards') ui.closeRight()
  else {
    ui.openRight()
    ui.rightView = 'cards'
  }
}

const paradigmLabel = computed(
  () => ({ classic: '标准模式', dynamic: '动态组队', react: 'ReAct 模式' })[store.current?.paradigm || 'classic'] || '标准模式'
)

/** 此刻在干活的子 agent：从块序列**倒着**找第一个在册角色（内层节点名 think/act/gate 不算，
 *  理由见 `utils/agents.ts` 文件头）。没角色可认就整颗胶囊不出现——不是显示「未知」。 */
const agentLabel = computed(() =>
  agentChipLabel(
    activeRole(store.current?.roles, store.blockList.map((b) => b.role)),
    store.current?.roles
  )
)

/** takeover 优先级栈：靠前的赢。顺序照施工5 F6 定的「问题卡 > 审批条 > 常规 composer」。 */
const TAKEOVERS: { kind: 'question' | 'approval'; when: (s: typeof store) => boolean }[] = [
  { kind: 'question', when: (s) => !!s.humanQuestion },
  { kind: 'approval', when: (s) => s.hasPendingApproval }
]
const takeover = computed(() => TAKEOVERS.find((t) => t.when(store))?.kind ?? null)

/** 只在「结构」变化时跟滚，普通重渲染不抢滚动条。尾行也算结构变化。 */
const rows = computed(() => buildRows(store.blockList, store.spans))
const followSig = computed(() => {
  const last = rows.value.at(-1)
  const tail = last && last.kind === 'node' ? last.b.key : last ? 'tail' : ''
  return `${rows.value.length}:${tail}:${store.isRunning ? 1 : 0}`
})

const follow = useFollowScroll(scrollEl, flowEl, seatEl, () => followSig.value)

/** 「加载更早」（B2）：整页往前拼会改变内容高度，读者那一屏必须原地不动——
 *  先抓视觉锚点，DOM 更新后按锚点补回差值。captureAnchor/restoreAnchor 就是为这一步留的。 */
const anchorKeyOf = (el: Element) => el.getAttribute('data-chat-anchor-key')
async function loadEarlier() {
  const anchor = follow.captureAnchor(anchorKeyOf)
  await store.loadEarlier()
  await nextTick()
  follow.restoreAnchor(anchor, anchorKeyOf)
}

function onScroll() {
  const el = scrollEl.value
  if (!el) return
  follow.atBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight <= 25
}

/** forced = 新出现的末节点是用户自己的话：自己的话必须被看到，即使正在往上翻 */
let lastKey = ''
watch(followSig, () => {
  const last = store.blockList.at(-1)
  const key = last?.key || ''
  const forced = !!last && last.type === 'User' && key !== lastKey
  lastKey = key
  follow.notify(forced)
})

/* composer 会随输入长高，浮在它上面的控件必须读到它的实时高度 */
let seatRo: ResizeObserver | undefined
watch(
  seatEl,
  (el) => {
    seatRo?.disconnect()
    if (!el || typeof ResizeObserver === 'undefined') return
    seatRo = new ResizeObserver(() => {
      scrollEl.value?.style.setProperty('--dsh-composer-height', `${Math.round(el.offsetHeight)}px`)
    })
    seatRo.observe(el)
  },
  { immediate: true }
)
onBeforeUnmount(() => seatRo?.disconnect())

/* ---------- 运行耗时：15s 之后才显示，避免短任务闪字 ---------- */
const turnStart = ref(0)
const elapsedLabel = ref('')
let tick: ReturnType<typeof setInterval> | undefined

watch(
  () => store.isRunning,
  (running) => {
    clearInterval(tick)
    if (!running) {
      turnStart.value = 0
      elapsedLabel.value = ''
      return
    }
    turnStart.value = Date.now()
    tick = setInterval(() => {
      const s = Math.floor((Date.now() - turnStart.value) / 1000)
      elapsedLabel.value = s >= 15 ? (s < 60 ? `用时 ${s}s` : `用时 ${Math.floor(s / 60)}m${s % 60}s`) : ''
    }, 1000)
  },
  { immediate: true }
)

onBeforeUnmount(() => clearInterval(tick))
</script>

<style scoped>
.root {
  --dsh-chat-content-width: 748px;
  --dsh-composer-side-clearance: 16px;
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  min-width: 0;
  background: var(--dsw-alias-bg-base);
}

.header {
  /* 参考项目 .header 的 padding 是 12/28/0/20，行高由 .titleRow 的 min-height 32 撑出来，
     不写死总高——源里页签条包在 header 内，整块高度是内容加出来的。 */
  flex: none;
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 32px;
  padding: 12px 28px 0 20px;
}

.crumbs {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
}

.crumb {
  max-width: 220px;
  padding: 4px 8px;
  border-radius: 12px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: 14px;
  line-height: 20px;
  color: var(--dsw-alias-label-primary);
}

.crumb:not(.cur):hover {
  background: var(--dsw-alias-interactive-bg-hover);
}

.crumb.dim {
  color: var(--dsw-alias-label-tertiary);
}

.crumb.cur {
  font-weight: 500;
}

.sep {
  color: var(--dsw-alias-label-caption);
}

.modeChip {
  padding: 1px 8px;
  border-radius: 12px;
  background: var(--dsw-alias-bg-module-platform);
  font-size: 12px;
  line-height: 18px;
  color: var(--dsw-alias-label-secondary);
}

.grow {
  flex: 1;
}

.cost {
  font-size: 12px;
  color: var(--dsw-alias-label-tertiary);
}

.iconBtn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--dsw-alias-label-secondary);
  cursor: pointer;
}

.iconBtn:hover {
  background: var(--dsw-alias-interactive-bg-hover);
}

.iconBtn.on {
  color: var(--dsw-alias-state-business-primary);
}

/* 页签条，几何照抄参考项目 ConversationRoot.module.css 的 .tabs/.tab/.tabActive */
.tabs {
  position: relative;
  z-index: 1;
  display: flex;
  gap: 36px;
  /* 源里页签条包在 .header 内：左移量 = header 的 20px + 自身 8px。
     我们是 header 的兄弟节点，不补这 20px 的话页签文字会比面包屑偏左 8px。 */
  margin: 4px 0 0 20px;
  padding-left: 8px;
}

/* 分隔线：源是 .header::after（1px、bottom 1px、border-l2），而页签在 header 内，
   所以线落在页签之下而不是会话头与页签之间。 */
.tabs::after {
  content: '';
  position: absolute;
  right: 0;
  bottom: 0;
  left: 0;
  z-index: 0;
  height: 1px;
  background: var(--dsw-alias-border-l2);
  pointer-events: none;
}

.tab {
  position: relative;
  padding: 0 0 11px;
  border: none;
  background: transparent;
  font-size: 13px;
  line-height: 16px;
  font-weight: 500;
  color: var(--dsw-alias-label-tertiary);
  cursor: pointer;
}

.tab::after {
  content: '';
  position: absolute;
  right: 0;
  bottom: 1px;
  left: 0;
  height: 2px;
  border-radius: 2px;
  background: transparent;
}

/* 选中态走业务蓝而不是墨色：brand-primary 在这张令牌表里落成中性黑，
   业务蓝是两套主题下都保持蓝的最近语义令牌。 */
.tabActive {
  color: var(--dsw-alias-state-business-primary);
}

.tabActive::after {
  background: var(--dsw-alias-state-business-primary);
}

.scroller {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
  overflow-x: hidden;
  scrollbar-gutter: stable;
  container-type: inline-size;
}

.column {
  display: flex;
  flex-direction: column;
  gap: 16px;
  flex: 1 0 auto;
  max-width: var(--dsh-chat-content-width);
  margin: 0 auto;
  padding: 16px calc(var(--dsh-composer-side-clearance) + 16px) 8px;
  box-sizing: border-box;
  width: 100%;
}

.placeholder {
  padding: 60px 0;
  text-align: center;
  font-size: 13px;
  color: var(--dsw-alias-label-tertiary);
}

/* 加载更早（B2）：沿用本仓的披露行家族（FoldToggle `.fold`：无框无底、三级字、
   hover 提到二级），不另造一颗胶囊——同一类「点开才有下文」的开关只该有一种长相。
   居中是因为它领着整条流，而披露行领着一张卡。 */
.earlier {
  display: block;
  width: 100%;
  padding: 8px 0;
  border: none;
  background: transparent;
  color: var(--dsw-alias-label-tertiary);
  font-size: 13px;
  text-align: center;
  cursor: pointer;
}

.earlier:hover {
  color: var(--dsw-alias-label-secondary);
}

.earlier:disabled {
  cursor: default;
  opacity: 0.6;
}

/* composer 座位：sticky 在滚动容器底部，上方 36px 渐变把正文淡掉 */
.composerSeat {
  position: sticky;
  bottom: 0;
  z-index: 7;
  background: linear-gradient(
    180deg,
    color-mix(in srgb, var(--dsw-alias-bg-base) 0%, transparent) 0px,
    var(--dsw-alias-bg-base) 36px
  );
}

.toBottomSlot {
  position: sticky;
  flex: none;
  bottom: calc(var(--dsh-composer-height, 152px) + 16px);
  z-index: 8;
  height: 0;
  display: flex;
  justify-content: flex-end;
  pointer-events: none;
  padding-right: max(0px, calc((100% - var(--dsh-chat-content-width)) / 2));
}

.toBottom {
  width: 34px;
  height: 34px;
  margin-top: -34px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--dsw-alias-border-l2);
  border-radius: 100px;
  background: var(--dsw-alias-button-floating-fill);
  box-shadow: var(--dsw-shadow-lv2);
  color: var(--dsw-alias-label-secondary);
  cursor: pointer;
  pointer-events: auto;
}

.toBottom:hover {
  background: var(--dsw-alias-button-floating-hover);
}

.turnStatus {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 26px;
  font: var(--dsw-font-s-strong-14);
}

.shimmer {
  background: linear-gradient(
    90deg,
    var(--dsw-alias-state-business-primary) 500 0%,
    var(--dsw-alias-state-business-primary) 500 40%,
    color-mix(in srgb, var(--dsw-alias-state-business-primary) 35%, transparent) 50%,
    var(--dsw-alias-state-business-primary) 500 60%,
    var(--dsw-alias-state-business-primary) 500 100%
  );
  background-size: 250% 100%;
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  animation: shimmer 1.8s linear infinite;
}

@keyframes shimmer {
  from {
    background-position: 100% 0;
  }
  to {
    background-position: -100% 0;
  }
}

.elapsed {
  font: var(--dsw-font-s-14);
  color: var(--dsw-alias-label-tertiary);
}

@media (prefers-reduced-motion: reduce) {
  .shimmer {
    animation: none;
    color: var(--dsw-alias-state-business-primary);
    background: none;
    -webkit-text-fill-color: currentColor;
  }
}
</style>
