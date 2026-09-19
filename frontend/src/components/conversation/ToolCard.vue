<template>
  <!-- Terminal：提示头 + 输出，横向滚动保对齐（不换行） -->
  <div v-if="b.type === 'Terminal'" class="card term">
    <div class="termPrompt">
      <span class="cwd" :title="b.cmd">{{ cwdLabel }}</span>
      <code>{{ b.cmd || '(无命令)' }}</code>
    </div>
    <template v-if="b.lines.length">
      <pre class="termOut">{{ term.head }}</pre>
      <FoldToggle
        v-if="term.cap.hidden > 0"
        :hidden="term.cap.hidden"
        :expanded="open.term"
        @toggle="open.term = !open.term"
      />
      <pre v-if="term.tail" class="termOut">{{ term.tail }}</pre>
    </template>
    <div v-else-if="b.closed" class="dim">无输出</div>
    <div v-if="exitCode !== null" class="termFoot">
      <VPill :tone="exitCode === 0 ? 'success' : 'error'">exit code {{ exitCode }}</VPill>
      <button class="copyBtn" data-action="copy-raw" @click="copy(b.lines.join('\n'))">复制</button>
    </div>
  </div>

  <!-- Editor / Notebook：后端整份下发内容（没有 hunk），所以是读视图而不是假 diff -->
  <div v-else-if="b.type === 'Editor' || b.type === 'Notebook'" class="card">
    <div class="filePath">
      <DsIcon name="file" :size="14" />
      <span>{{ fileName }}</span>
      <button class="copyBtn" data-action="copy-raw" @click="copy(code)">复制</button>
    </div>
    <pre class="codeLines"><code v-html="highlighted" /></pre>
  </div>

  <!-- Task：清单，当前项高亮 -->
  <div v-else-if="b.type === 'Task'" class="card taskList">
    <div v-for="(t, i) in tasks" :key="t.task_id || i" class="taskRow" :class="{ current: t.task_id === currentId }">
      <span class="glyph">{{ t.is_finished ? '✓' : t.task_id === currentId ? '▶' : '○' }}</span>
      <span class="ttext">{{ t.instruction || t.description || t.task_id }}</span>
    </div>
    <div v-if="!tasks.length" class="dim">{{ b.tokens.join('') || '暂无任务' }}</div>
  </div>

  <!-- Browser：标题 + 链接 + 截图（旧实现明确不画截图，这里补上） -->
  <div v-else-if="b.type === 'Browser' || b.type === 'Browser-RT'" class="card web">
    <div v-if="b.page?.title" class="webTitle">{{ b.page.title }}</div>
    <a v-if="url" class="webUrl" :href="url" target="_blank" rel="noopener noreferrer">{{ url }}</a>
    <img v-if="shotUrl" :src="shotUrl" class="shot" loading="lazy" decoding="async" alt="页面截图" />
    <div v-else-if="b.tokens.length" class="webText">{{ b.tokens.join('') }}</div>
  </div>

  <!-- Gallery：图片 -->
  <div v-else-if="b.type === 'Gallery'" class="card media">
    <img v-if="pathUrl" :src="pathUrl" :alt="fileName" loading="lazy" decoding="async" referrerpolicy="no-referrer" />
    <div v-else class="dim">图片路径未就绪</div>
  </div>

  <!-- 其余：IN / OUT 两段，各自独立限高 -->
  <div v-else class="card inOut">
    <div class="ioRow">
      <span class="ioLabel">IN</span>
      <div class="ioCol">
        <pre class="ioBody">{{ inFold.head }}</pre>
        <FoldToggle
          v-if="inFold.cap.hidden > 0"
          :hidden="inFold.cap.hidden"
          :expanded="open.in"
          @toggle="open.in = !open.in"
        />
        <pre v-if="inFold.tail" class="ioBody">{{ inFold.tail }}</pre>
      </div>
    </div>
    <div class="ioRow">
      <span class="ioLabel">OUT</span>
      <div class="ioCol">
        <pre class="ioBody" :class="{ err: failed }">{{ outFold.head }}</pre>
        <FoldToggle
          v-if="outFold.cap.hidden > 0"
          :hidden="outFold.cap.hidden"
          :expanded="open.out"
          @toggle="open.out = !open.out"
        />
        <pre v-if="outFold.tail" class="ioBody" :class="{ err: failed }">{{ outFold.tail }}</pre>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/** 工具行展开后的正文卡。按后端 block 词汇表分派，未知类型落到 IN/OUT。 */
import { computed, reactive } from 'vue'
import { useSessionStore } from '../../stores/sessions'
import { highlightCode, langOfFilename } from '../../utils/render'
import { DEFAULT_MAX_LINES, sliceHeadTail } from '../../utils/headTailCap'
import { useToastStore } from '../../stores/toast'
import DsIcon from '../ui/DsIcon.vue'
import FoldToggle from './FoldToggle.vue'
import VPill from '../ui/VPill.vue'
import type { Block } from '../../types'

const props = defineProps<{ b: Block }>()
const store = useSessionStore()
const toast = useToastStore()

const b = computed(() => props.b)
const failed = computed(() => b.value.meta?.ok === false || /error|失败/i.test(outText.value))

const cwdLabel = computed(() => {
  const p = (b.value.meta?.cwd || b.value.path || '').replace(/\\/g, '/')
  if (!p) return '$'
  const seg = p.split('/').filter(Boolean)
  return seg.length ? '~/' + seg[seg.length - 1] + '/' : '$'
})

const exitCode = computed<number | null>(() => {
  const m = /(?:exit(?:\s+code)?|返回码)\s*[:=]?\s*(-?\d+)/i.exec(b.value.lines.join('\n'))
  return m ? Number(m[1]) : null
})

const fileName = computed(
  () => b.value.meta?.filename || b.value.doc?.filename || b.value.path?.split(/[\\/]/).pop() || ''
)

const code = computed(() => b.value.doc?.content || b.value.tokens.join(''))
const highlighted = computed(() => highlightCode(code.value, langOfFilename(fileName.value)))

const tasks = computed<any[]>(() => b.value.obj?.tasks || b.value.obj?.task_list || [])
const currentId = computed(() => b.value.obj?.current_task_id || '')
const url = computed(() => b.value.page?.page_url || b.value.url || '')
const shotUrl = computed(() => (b.value.page?.screenshot ? store.workspaceUrl(b.value.page.screenshot) : ''))
const pathUrl = computed(() => (b.value.path ? store.workspaceUrl(b.value.path) : ''))

const inText = computed(() =>
  b.value.cmd
    ? b.value.cmd
    : b.value.raw.length
      ? JSON.stringify(b.value.raw, null, 2)
      : b.value.meta
        ? JSON.stringify(b.value.meta, null, 2)
        : ''
)
const outText = computed(() =>
  b.value.lines.length
    ? b.value.lines.join('\n')
    : b.value.tokens.join('') || (b.value.obj ? JSON.stringify(b.value.obj, null, 2) : '')
)

/* 长输出的中段折叠：三处各自记展开态（ToolCard 一实例一块，所以是组件内态、不落盘）。
   Editor 代码卡不在这里折叠——它整份下发、自带 320px 滚动，切它要把高亮结果重算一遍。 */
const open = reactive({ term: false, in: false, out: false })
const term = computed(() => sliceHeadTail(b.value.lines.join('\n'), DEFAULT_MAX_LINES, open.term))
const inFold = computed(() => sliceHeadTail(inText.value, DEFAULT_MAX_LINES, open.in))
const outFold = computed(() => sliceHeadTail(outText.value, DEFAULT_MAX_LINES, open.out))

async function copy(t: string) {
  try {
    await navigator.clipboard.writeText(t)
    toast.push('已复制', 'success')
  } catch {
    toast.push('复制失败', 'error')
  }
}
</script>

<style scoped>
.card {
  border: 1px solid var(--dsw-alias-border-l2);
  border-radius: 12px;
  background: var(--dsw-alias-markdown-code-block);
  padding: 10px 12px;
  margin: 6px 0 10px;
  font-family: var(--ds-font-family-code);
  font-size: 12px;
  line-height: 18px;
  color: var(--dsw-alias-label-secondary);
  overflow: hidden;
}

.dim {
  color: var(--dsw-alias-label-tertiary);
}

.termPrompt {
  display: flex;
  gap: 8px;
  align-items: baseline;
  white-space: pre;
  overflow-x: auto;
}

.cwd {
  color: var(--dsw-alias-label-caption);
  flex: none;
}

.termOut {
  margin: 8px 0 0;
  white-space: pre;
  overflow-x: auto;
}

.termFoot {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}

.filePath {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
  color: var(--dsw-alias-label-primary);
}

.codeLines {
  margin: 0;
  max-height: 320px;
  overflow: auto;
  white-space: pre;
}

.taskList {
  font-family: var(--dsw-font-family);
  font-size: 13px;
  line-height: 20px;
}

.taskRow {
  display: flex;
  gap: 8px;
  align-items: baseline;
  padding: 3px 0;
}

.taskRow.current {
  color: var(--dsw-alias-state-business-primary);
  font-weight: 500;
}

.glyph {
  flex: none;
  width: 14px;
  text-align: center;
}

.webTitle {
  font-family: var(--dsw-font-family);
  font-size: 13px;
  color: var(--dsw-alias-label-primary);
  margin-bottom: 4px;
}

.webUrl {
  color: var(--dsw-alias-state-business-primary);
  text-decoration: none;
  word-break: break-all;
}

.webText {
  font-family: var(--dsw-font-family);
  margin-top: 6px;
}

.shot {
  display: block;
  max-width: 100%;
  margin-top: 8px;
  border-radius: 8px;
  border: 1px solid var(--dsw-alias-border-l2);
}

.media {
  padding: 0;
  background: transparent;
  border: none;
}

.media img {
  max-width: 100%;
  border-radius: 12px;
}

.inOut {
  padding: 0;
  display: grid;
  grid-template-columns: 1fr;
}

.ioRow {
  display: grid;
  grid-template-columns: 44px minmax(0, 1fr);
  align-items: start;
}

.ioRow + .ioRow {
  border-top: 1px solid var(--dsw-alias-border-l2);
}

.ioLabel {
  position: sticky;
  top: 0;
  padding: 8px 0 8px 12px;
  font-size: 11px;
  color: var(--dsw-alias-label-caption);
}

.ioBody {
  margin: 0;
  padding: 8px 12px 8px 0;
  max-height: 150px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
}

.ioBody.err {
  color: var(--dsw-alias-state-error-primary);
}

.copyBtn {
  margin-left: auto;
  border: none;
  background: transparent;
  color: var(--dsw-alias-label-tertiary);
  font-family: inherit;
  font-size: 11px;
  cursor: pointer;
  padding: 2px 4px;
  border-radius: 4px;
}

.copyBtn:hover {
  color: var(--dsw-alias-label-primary);
  background: var(--dsw-alias-interactive-bg-hover);
}
</style>
