<template>
  <!-- 用户：右对齐气泡，文本按字面呈现（不解析 markdown） -->
  <div v-if="b.type === 'User'" class="userRow" :data-chat-anchor-key="'user:' + b.key" data-time-hover-root>
    <div class="userStack">
      <div class="bubble">{{ text }}</div>
      <MessageIconActions class="acts" :text="text" :time="timeMs" clock="start" />
    </div>
  </div>

  <!-- Thought：Think 折叠披露行（参考项目里它从来不是正文）。
       收口后仍一个字没有的行不渲染：打字机改抽真散文后，短字段的结构化调用（选动作那一类）
       全程零发布，留一行空白 Think 比原来的 JSON 乱码更没有信息；运行中照旧渲染——
       首 token 前那 40~51 秒的静默期就靠这一行的扫光表态。 -->
  <ReasoningRow v-else-if="b.type === 'Thought' && (open || text)" :b="b" :is-open="isOpen" @toggle="(k, v) => $emit('toggle', k, v)" />

  <!-- Docs：全宽正文 + 产物链接 -->
  <div v-else-if="isProse" class="prose" :data-chat-anchor-key="b.key" :data-streaming="open ? 'true' : undefined">
    <MarkdownText :src="text" :streaming="open" />
    <a v-if="artifactUrl" class="artifact" :href="artifactUrl" target="_blank" rel="noopener noreferrer">
      <DsIcon name="file" :size="14" />
      {{ fileName }}
    </a>
  </div>

  <!-- Error：轮内红点行（B1）。后端 server/runner.py 的 _fail 发 kind=error，value 是
       整段 traceback——行里只报最后那一行「异常类型: 消息」，全段留给右栏台账。
       几何照参考项目 MessageItem.module.css 的 .turnErrorRow 家族；C182 起死因码上 wire
       （runner._fail_kind 三族 blocked/context_overflow/crash），那列 auto 轨只在带码时
       出现——旧会话回放无码，仍是两列，像素零漂移。 -->
  <div v-else-if="b.type === 'Error'" class="errRow" :class="{ hasCode: !!b.code }" role="status"
       :title="traceback" :data-chat-anchor-key="b.key">
    <VStateDot state="error" class="errDot" />
    <div class="errCopy">
      <span class="errTitle">本轮运行失败</span>
      <span class="errMsg">{{ errMessage }}</span>
    </div>
    <span v-if="b.code" class="errCode">{{ b.code }}</span>
  </div>

  <!-- MaxTokens：轮内黄点行（B8）。后端 `_translate` 读出 finish_reason=length 才发 kind=turn，
       形状与文案取参照系：事件 `turn/end` + `reason.kind==='max-tokens'`
       （conversation-nodes/turn-max-tokens.ts:42）、文案 locales.ts:132-133。
       几何刻意与上面的 error 行同族（同一份 .errRow），只有点的状态色不同。 -->
  <div v-else-if="b.type === 'MaxTokens'" class="errRow" role="status"
       :data-chat-anchor-key="b.key">
    <VStateDot state="warning" class="errDot" />
    <div class="errCopy">
      <span class="warnTitle">已达到输出 token 上限</span>
      <span class="errMsg">回答被截断，已有输出保留在对话中。发送“继续”可让模型接着输出。</span>
    </div>
  </div>

  <!-- PlanOpen：轮尾黄点行（C148）。后端 `_settle` 读图里那本 Plan 状态机（对勾=`Task.is_finished`）
       才发 `turn/end` + `reason.kind==='plan-unfinished'`，两个数从 `b.meta` 来。
       几何刻意与上面 MaxTokens 行同族（同一份 .errRow、同一个 warn 档）——这也不是失败，是「没做完」。 -->
  <div v-else-if="b.type === 'PlanOpen'" class="errRow" role="status"
       :data-chat-anchor-key="b.key">
    <VStateDot state="warning" class="errDot" />
    <div class="errCopy">
      <span class="warnTitle">收工时计划还没走完</span>
      <span class="errMsg">还有 {{ b.meta?.open ?? 0 }} 条未完成（共 {{ b.meta?.total ?? 0 }} 条）。发一句「继续」可以接着做剩下的。</span>
    </div>
  </div>

  <!-- Compact：上下文压缩过这一行（P4 路线三接上的那一格）。改前 `kind=context name=compact`
       （`codeharness/roles/role_zero.py:287`）在 `applyEvent` 的 else-if 链里**没有分支**，
       整条事件被无声吞掉——压缩发生了，界面上什么都没有。
       文案只用事件自带的事实（条数与 token 差），`real_peak_pt` 那个 0 是「这一发还没账本」
       而不是读数（同 role_zero.py:280 的口径），所以带不带峰值在折的时候就定好了，渲染层不猜。 -->
  <div v-else-if="b.type === 'Compact'" class="compactRow" role="status"
       :data-chat-anchor-key="b.key">
    <span class="compactCopy">已压缩 {{ b.meta?.evicted_n ?? 0 }} 条早先消息（{{ formatTokens(b.meta?.before ?? 0) }} → {{ formatTokens(b.meta?.after ?? 0) }} token，释放 {{ formatTokens(b.meta?.freed ?? 0) }}）</span>
    <span v-if="b.meta?.hasPeak" class="compactPeak">窗口峰值 {{ formatTokens(b.meta?.real_peak_pt ?? 0) }}</span>
  </div>

  <!-- RoleLane：一个角色这一趟的标题行（C190）。后端只发事实（phase 就是 langgraph 内层节点名），
       人话由 `utils/agents.ts::lanePhase` 派生——与 `toolRow` 同一口径，不去求模型自报意图。
       收工后相位文本让位给三档：「已收工」／「没跑完」（取消与异常走不到 `on_chain_end`，不收就会
       在界面上永远「在跑」，与 C172 那条兜底行同族）／「等你批准・等你回答」（C193：图停在待批或
       待答处时跑图那个循环同样退出，原来只有 `aborted` 一支——真模型那场跑到 `finished` 的会话
       12 颗收口里 6 颗被印成「没跑完」，而那一半角色其实干完了活）。停车那一档不带用时：
       从开行到散场的墙钟差在停车时常见几十毫秒，上屏就是「他只跑了 46 毫秒」那种冒充读数的数。
       C189：这行**可点**，点下去中栏只看这个角色这一路（`ui.roleFilter`）。用 `<button>` 而不是
       给 div 挂 @click——车道行是真正的操作对象，键盘与读屏要免费拿到，别造一个"看起来能点"的死控件。 -->
  <button v-else-if="b.type === 'RoleLane'" type="button" class="laneRow"
          :data-running="open ? '1' : undefined" :data-role="laneRole"
          :aria-pressed="ui.roleFilter === laneRole ? 'true' : 'false'"
          :title="`只看 ${laneRole} 这一路`"
          :data-chat-anchor-key="b.key"
          @click="ui.roleFilter = ui.roleFilter === laneRole ? '' : laneRole">
    <span class="laneRole">{{ laneRole }}</span>
    <span class="lanePhase">{{ open ? lanePhase(b.meta?.phase)
      : b.meta?.park ? lanePark(b.meta.park)
      : (b.meta?.aborted ? '没跑完' : '已收工') }}</span>
    <span v-if="b.meta?.ms" class="laneMs">{{ laneMs(b.meta.ms) }}</span>
  </button>

  <!-- 其余：24px 折叠行 + 展开卡 -->
  <VDisclosureRow
    v-else
    :data-chat-anchor-key="b.key"
    :data-chat-call-id="b.key"
    :title="row.title"
    :summary="row.summary"
    :icon="row.icon"
    :state="row.state"
    :open="isOpen"
    @update:open="$emit('toggle', b.key, $event)"
  >
    <ToolCard :b="b" />
    <button type="button" class="inspectPill" @click="ui.inspect(b.key)">
      <DsIcon name="inspect" :size="12" /> 检视
    </button>
  </VDisclosureRow>
</template>

<script setup lang="ts">
/** 一个节点 = 后端一个 block。分派：User→气泡、Thought→Think 披露行、
 *  Docs→全宽正文、Error→轮内红点行、Compact→压缩事实行、其余→折叠行。
 *  每个 BlockType 必须有显式分支（s8 t1 守这条）；`Error`/`MaxTokens`/`PlanOpen`/`Compact`
 *  是合成块、不是 BlockType，t1 查不到，各由 s8 自己的那一格钉住分支在不在。 */
import { computed } from 'vue'
import { formatTokens } from '../../utils/stats'
import { lanePark, lanePhase } from '../../utils/agents'
import MarkdownText from './MarkdownText.vue'
import MessageIconActions from './MessageIconActions.vue'
import ReasoningRow from './ReasoningRow.vue'
import ToolCard from './ToolCard.vue'
import VStateDot from '../ui/VStateDot.vue'
import VDisclosureRow from '../ui/VDisclosureRow.vue'
import DsIcon from '../ui/DsIcon.vue'
import { useSessionStore } from '../../stores/sessions'
import { useUiStore } from '../../stores/ui'
import { toolRow } from '../../utils/toolRow'
import type { Block } from '../../types'

defineEmits<{ toggle: [key: string, open: boolean] }>()
const props = defineProps<{ b: Block; isOpen: boolean }>()

const store = useSessionStore()
const ui = useUiStore()

const b = computed(() => props.b)
const text = computed(() => b.value.tokens.join('') + b.value.live.join(''))
const open = computed(() => !b.value.closed)
const isProse = computed(() => b.value.type === 'Docs')
/** 车道行归属的角色：优先块上的 `role`（报道槽与打字机流都写它），meta 那份是兜底。 */
const laneRole = computed(() => b.value.role || b.value.meta?.role || '角色')
/** 轮内 error 行（B1）：store 把 traceback 按行存进 lines，行里只报最后一行非空——
 *  `format_exc` 的末行才是「异常类型: 消息」，前面全是栈。全段挂 title，右栏台账也留了一份。 */
const traceback = computed(() => b.value.lines.join('\n'))
const errMessage = computed(() => b.value.lines.map((l) => l.trim()).filter(Boolean).at(-1) || '未知错误')
/** 块与 span 都用 unix 秒（后端事件原样），只有读数组件要 ms。 */
const timeMs = computed(() => (b.value.ts === undefined ? undefined : b.value.ts * 1000))

const fileName = computed(
  () => b.value.meta?.filename || b.value.doc?.filename || b.value.path?.split(/[\\/]/).pop() || ''
)
const artifactUrl = computed(() => (isProse.value && b.value.path ? store.workspaceUrl(b.value.path) : ''))

/** 车道的用时：不足 1 秒必须原样说毫秒。真图第一次跑就量到 5ms——`(5/1000).toFixed(1)` 会印成
 *  「0.0s」，那是一个看着像读数的假数（同一角色第二次激活就是这么短）。 */
function laneMs(ms: number): string {
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`
}

const KIND: Record<string, { title: string; icon: string }> = {
  Terminal: { title: 'Bash', icon: 'code' },
  Editor: { title: 'Write', icon: 'edit' },
  Task: { title: '更新任务清单', icon: 'checklist' },
  Browser: { title: 'Fetch', icon: 'globe' },
  'Browser-RT': { title: 'Fetch', icon: 'globe' },
  Gallery: { title: 'Image', icon: 'browse' },
  Notebook: { title: 'Code', icon: 'code' },
  System: { title: 'Tool call', icon: 'settings' }
}

/** 一次工具调用的块（后端 `report.tool_call_report` 发的第十值）。
 *  它必须有自己这一句判据式的分支：门禁 s8 t1 拿 `BlockType` 全集去查 ChatNode/ToolCard 里的
 *  `type === '...'` 分发名，少一支就会静默降级成灰色通用块。 */
const isToolCall = computed(() => b.value.type === 'ToolCall')

const row = computed(() => {
  // ToolCall 块：动词与摘要**从 args 派生**（照参照系 `tool-call-model.ts` 的 `SUMMARY_KEYS`），
  // 后端只发事实，不发明"意图"字段。其余块仍走类型表。
  if (isToolCall.value && b.value.meta?.tool) {
    const t = toolRow(String(b.value.meta.tool), b.value.meta.args as Record<string, string>)
    return { ...t, state: (b.value.meta.ok === false ? 'error' : 'ok') as 'ok' | 'error' }
  }
  const kind = KIND[b.value.type] || { title: b.value.type || 'Tool call', icon: 'settings' }
  const state = open.value ? 'running' : b.value.meta?.ok === false ? 'error' : 'ok'
  const raw =
    b.value.cmd ||
    fileName.value ||
    b.value.page?.page_url ||
    b.value.url ||
    b.value.lines[0] ||
    text.value ||
    ''
  // 摘要只取**首行**，长度交给 CSS 的 ellipsis——参照系 `tool-call-model.ts` 的 `firstLine()`
  // 加 `.summary{text-overflow:ellipsis}` 就是这个组合。原来这里 `slice(0, 200)` 会把一行 300 字的
  // grep 模式硬切成"看不懂的半截"，而 CSS 那套是"看得见的半截 + 悬停有 tooltip"。
  const nl = raw.indexOf('\n')
  return {
    ...kind,
    state: state as 'running' | 'ok' | 'error' | 'stopped',
    summary: nl === -1 ? raw : raw.slice(0, nl)
  }
})
</script>

<style scoped>
.userRow {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 6px;
}

.userStack {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 8px;
  max-width: min(525px, 82%);
}

.bubble {
  background: var(--dsw-specific-bubble);
  border-radius: 22px;
  padding: 10px 16px;
  font-size: 16px;
  line-height: 24px;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--dsw-alias-label-primary);
}

/* 横排与间距在 MessageIconActions 的 .actions 里；这里只留用户侧的 6px 出血 */
.acts {
  margin-right: -6px;
}

.prose {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

/* ---- 轮内 error 红点行（B1）：源值 = 参考项目 MessageItem.module.css ---- */
.errRow {
  display: grid;
  grid-template-columns: 10px minmax(0, 1fr);
  gap: 8px;
  align-items: start;
  padding: 2px 0;
  font-size: 13px;
  line-height: 20px;
}

/* 点 10px、行 20px → 下移 5px 才和文字光学居中 */
.errDot {
  margin-top: 5px;
}

/* C182：第三列（死因码轨）只在带码时开——同一份 .errRow 还被 MaxTokens/PlanOpen 行共用，
   无条件改三列会让那两行的 1fr 平白让出一条 8px 的 gap，等一行像素漂移。 */
.errRow.hasCode {
  grid-template-columns: 10px minmax(0, 1fr) auto;
}

.errCode {
  margin-top: 4px;
  padding: 0 6px;
  border: 1px solid var(--dsw-alias-border-l2);
  border-radius: 4px;
  color: var(--dsw-alias-label-secondary);
  font-size: 11px;
  line-height: 16px;
  align-self: start;
  white-space: nowrap;
}

.errCopy {
  min-width: 0;
  overflow-wrap: anywhere;
}

.errTitle {
  margin-right: 6px;
  color: var(--dsw-alias-state-error-primary);
  font-weight: 600;
}

/* 同一族，只换强调色：截断不是失败，是「这段少了尾巴」（参照系给的就是 warn 档） */
.warnTitle {
  margin-right: 6px;
  color: var(--dsw-alias-state-warn-primary);
  font-weight: 600;
}

.errMsg {
  color: var(--dsw-alias-label-secondary);
}

/* RoleLane（C190 的角色车道行）：它是「这一段是谁的」的分隔，不是又一条状态提示，
   所以刻意比 Compact 重一点（角色名用主文本色 + 600 字重），但不用 error/warn 那一族的颜色。 */
.laneRow {
  display: flex;
  align-items: baseline;
  gap: 6px;
  width: 100%;
  padding: 10px 0 2px;
  border: 0;
  background: none;
  font-family: inherit;
  font-size: 12px;
  line-height: 18px;
  text-align: left;
  color: var(--dsw-alias-label-tertiary);
  cursor: pointer;
}

.laneRow:hover {
  color: var(--dsw-alias-label-secondary);
}

/* 筛选态：角色名后面挂一道短下划线，说「这一路正在被看」——比整行变色安静，
   也比"只有 hover 才像能点"诚实（读屏靠 aria-pressed，视觉靠这条）。 */
.laneRow[aria-pressed="true"] .laneRole {
  border-bottom: 1px solid var(--dsw-alias-label-secondary);
}

/* 焦点环照 `VDisclosureRow.vue:71-73` 那一套（同一个 token、同样 1px 外扩），不自造样式 */
.laneRow:focus-visible {
  outline: 2px solid var(--dsw-alias-state-business-primary);
  outline-offset: 1px;
}

.laneRole {
  color: var(--dsw-alias-label-primary);
  font-weight: 600;
  font-size: 13px;
}

/* 用时是读数：等宽数字，跑起来不会左右跳 */
.laneMs {
  font-variant-numeric: tabular-nums;
}

.laneRow[data-running='1'] .lanePhase {
  animation: lanePulse 1.6s ease-in-out infinite;
}

@keyframes lanePulse {
  50% { opacity: 0.45; }
}

@media (prefers-reduced-motion: reduce) {
  .laneRow[data-running='1'] .lanePhase { animation: none; }
}

/* Compact：刻意不是 .errRow 那一族——压缩不是失败也不是警告，是「这里发生过一件系统动作」。
   参照系同判（`CompactionDivider.tsx` 是一条居中的浅色分隔线，不走红/黄卡）。 */
.compactRow {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 4px 0;
  color: var(--dsw-alias-label-tertiary);
  font-size: 12px;
  line-height: 18px;
}

.compactPeak {
  padding: 0 6px;
  border: 1px solid var(--dsw-alias-border-l2);
  border-radius: 4px;
  color: var(--dsw-alias-label-secondary);
}

/* 参考项目 ToolRow.module.css 的 .inspectButton：常驻流内、只改 opacity，
   所以显现时不会顶动布局；底色用 bg-base 而非 bg-overlay（后者是抬起的深色面，
   对这么安静的流内控件太重）。 */
.inspectPill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin: 4px 0 2px 4px;
  padding: 2px 8px;
  border: 1px solid var(--dsw-alias-border-l2);
  border-radius: 999px;
  background: var(--dsw-alias-bg-base);
  color: var(--dsw-alias-label-secondary);
  font-size: 11px;
  line-height: 16px;
  cursor: pointer;
  opacity: 0;
  transition: opacity 100ms ease;
}

.wrap:hover .inspectPill,
.inspectPill:focus-visible {
  opacity: 1;
}

.inspectPill:hover {
  background: var(--dsw-alias-interactive-bg-hover-solid);
  color: var(--dsw-alias-label-primary);
}

.artifact {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  align-self: flex-start;
  font-size: 13px;
  color: var(--dsw-alias-state-business-primary);
  text-decoration: none;
}

.artifact:hover {
  text-decoration: underline;
  text-underline-offset: 3px;
}
</style>
