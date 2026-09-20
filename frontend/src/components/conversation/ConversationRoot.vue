<template>
  <div class="root" :data-phase="store.isRunning ? 'running' : 'active'">
    <div class="header">
      <nav class="crumbs" aria-label="会话层级">
        <span class="crumb dim">{{ store.current?.project_name || store.current?.id }}</span>
        <span class="sep">/</span>
        <span class="crumb cur">{{ store.current?.idea || '新会话' }}</span>
      </nav>
      <span class="modeChip">{{ paradigmLabel }}</span>
      <span class="grow" />
      <span class="cost">¥{{ (store.cost?.total_cost ?? 0).toFixed(3) }}</span>
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
import TrajectoryTable from './TrajectoryTable.vue'
import QuestionCard from '../composer/QuestionCard.vue'
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
  () => ({ classic: '标准模式', dynamic: '计划模式', react: 'ReAct 模式' })[store.current?.paradigm || 'classic'] || '标准模式'
)

/** takeover 优先级栈：靠前的赢。后端目前只有 ask_human 一种挂起交互
 *  （没有审批与计划评审通道），所以只有一项；将来加审批条是往这里添一项，
 *  而不是往模板里加 v-else-if。 */
const TAKEOVERS: { kind: 'question'; when: (s: typeof store) => boolean }[] = [
  { kind: 'question', when: (s) => !!s.humanQuestion }
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
