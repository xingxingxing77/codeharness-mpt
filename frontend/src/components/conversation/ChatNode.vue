<template>
  <!-- 用户：右对齐气泡，文本按字面呈现（不解析 markdown） -->
  <div v-if="b.type === 'User'" class="userRow" :data-chat-anchor-key="'user:' + b.key" data-time-hover-root>
    <div class="userStack">
      <div class="bubble">{{ text }}</div>
      <MessageIconActions class="acts" :text="text" :time="timeMs" clock="start" />
    </div>
  </div>

  <!-- Thought：Think 折叠披露行（参考项目里它从来不是正文） -->
  <ReasoningRow v-else-if="b.type === 'Thought'" :b="b" :is-open="isOpen" @toggle="(k, v) => $emit('toggle', k, v)" />

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
       几何照参考项目 MessageItem.module.css 的 .turnErrorRow 家族；源里第三列 auto 是
       放错误码的（.turnErrorCode），我们的 error 事件不带码，所以不留空轨。 -->
  <div v-else-if="b.type === 'Error'" class="errRow" role="status" :title="traceback"
       :data-chat-anchor-key="b.key">
    <VStateDot state="error" class="errDot" />
    <div class="errCopy">
      <span class="errTitle">本轮运行失败</span>
      <span class="errMsg">{{ errMessage }}</span>
    </div>
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
 *  Docs→全宽正文、Error→轮内红点行、其余→折叠行。每个 BlockType 必须有显式分支（s8 t1 守这条）。 */
import { computed } from 'vue'
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
const text = computed(() => b.value.tokens.join(''))
const open = computed(() => !b.value.closed)
const isProse = computed(() => b.value.type === 'Docs')
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
