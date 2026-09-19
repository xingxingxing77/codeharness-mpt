<template>
  <div class="panel">
    <div class="bar">
      <div class="tabs" role="tablist">
        <button
          v-for="v in VIEWS"
          :key="v.key"
          class="tab"
          :class="{ on: ui.rightView === v.key }"
          role="tab"
          :aria-selected="ui.rightView === v.key"
          @click="pick(v.key)"
        >
          {{ v.label }}
        </button>
      </div>
      <span class="grow" />
      <button class="iconBtn" aria-label="收起面板" @click="ui.closeRight()"><DsIcon name="close" :size="14" /></button>
    </div>

    <!-- 日志：原先是底部抽屉，参考项目没有那个东西，并进来当一屏 -->
    <div v-if="ui.rightView === 'cards'" ref="logEl" class="body logs">
      <div v-if="!store.logs.length" class="dim">暂无日志</div>
      <pre v-for="(l, i) in store.logs" :key="i" class="logLine">{{ l }}</pre>
    </div>

    <div v-else-if="ui.rightView === 'files'" class="body">
      <div v-if="!tree.length" class="dim">{{ store.current ? '工作区为空' : '未选择会话' }}</div>
      <FileNode v-for="n in tree" :key="n.path" :n="n" :depth="0" @open="openFile" />
    </div>

    <div v-else-if="ui.rightView === 'review'" class="body">
      <div v-if="!edits.length" class="dim">暂无文件变更</div>
      <ToolCard v-for="b in edits" :key="b.key" :b="b" />
    </div>

    <div v-else-if="ui.rightView === 'graph'" class="body">
      <MermaidView v-if="mermaid" :src="mermaid" name="编排图" />
      <div v-else class="dim">{{ store.current ? '尚无编排图（会话未装配）' : '未选择会话' }}</div>
    </div>

    <div v-else class="body">
      <div v-if="!spans.length" class="dim">trace 仅在 Redis 模式下有数据</div>
      <table v-else class="trace">
        <thead><tr><th>节点</th><th class="num">入</th><th class="num">出</th><th class="num">成本</th></tr></thead>
        <tbody>
          <tr v-for="(s, i) in spans" :key="i">
            <td>{{ s.node }}</td><td class="num">{{ s.pt }}</td><td class="num">{{ s.ct }}</td>
            <td class="num">{{ s.cost.toFixed(4) }}</td>
          </tr>
        </tbody>
        <tfoot><tr><td>合计</td><td class="num">{{ totals.pt }}</td><td class="num">{{ totals.ct }}</td><td class="num">{{ totals.cost.toFixed(4) }}</td></tr></tfoot>
      </table>
    </div>

    <div v-if="preview" class="preview">
      <div class="pvBar">
        <span class="pvName">{{ preview.name }}</span>
        <button class="iconBtn" aria-label="关闭预览" @click="preview = null"><DsIcon name="close" :size="14" /></button>
      </div>
      <img v-if="preview.kind === 'image'" :src="preview.url" class="pvImg" :alt="preview.name" />
      <div v-else-if="preview.kind === 'markdown'" class="pvMd"><MarkdownText :src="preview.text || ''" /></div>
      <pre v-else class="pvCode">{{ preview.text }}</pre>
    </div>
  </div>
</template>

<script setup lang="ts">
/** 右栏 detailsCol：日志 / 文件 / 变更 / 编排 / trace。
 *  替掉原 ToolsPanel + 底部 TerminalPanel 抽屉（参考项目没有底部抽屉）。 */
import { computed, h, defineComponent, onMounted, ref, watch } from 'vue'
import MarkdownText from './conversation/MarkdownText.vue'
import MermaidView from './MermaidView.vue'
import ToolCard from './conversation/ToolCard.vue'
import DsIcon from './ui/DsIcon.vue'
import { api } from '../api/client'
import { useSessionStore } from '../stores/sessions'
import { useUiStore } from '../stores/ui'
import { useToastStore } from '../stores/toast'
import type { FileNode as FileNodeT } from '../types'

const store = useSessionStore()
const ui = useUiStore()
const toast = useToastStore()

const VIEWS = [
  { key: 'cards', label: '日志' },
  { key: 'files', label: '文件' },
  { key: 'review', label: '变更' },
  { key: 'graph', label: '编排' },
  { key: 'trace', label: 'trace' }
] as const

const tree = ref<FileNodeT[]>([])
const mermaid = ref('')
const spans = ref<{ node: string; pt: number; ct: number; cost: number; ts: number }[]>([])
const preview = ref<{ name: string; kind: string; url?: string; text?: string } | null>(null)

const edits = computed(() => store.blockList.filter((b) => b.type === 'Editor'))
const totals = computed(() =>
  spans.value.reduce((a, s) => ({ pt: a.pt + s.pt, ct: a.ct + s.ct, cost: a.cost + s.cost }), { pt: 0, ct: 0, cost: 0 })
)

function pick(k: string) {
  ui.rightView = k as typeof ui.rightView
}

async function load() {
  if (!store.currentId) {
    tree.value = []
    mermaid.value = ''
    spans.value = []
    return
  }
  try {
    const [f, g, t] = await Promise.all([api.fileTree(store.currentId), api.sessionGraph(store.currentId), api.sessionTrace(store.currentId)])
    tree.value = f.tree || []
    mermaid.value = g.mermaid || ''
    spans.value = t.spans || []
  } catch (e) {
    toast.push((e as Error).message, 'error')
  }
}

async function openFile(n: FileNodeT) {
  if (n.type !== 'file') return
  try {
    const rsp = await api.fileContent(store.currentId, n.path)
    // 预览分支认 ext：mime 判不出 markdown / 代码，之前踩过
    const kind = rsp.ext === '.png' || rsp.ext === '.jpg' || rsp.ext === '.jpeg' || rsp.ext === '.webp' || rsp.ext === '.gif' ? 'image' : rsp.ext === '.md' ? 'markdown' : 'code'
    preview.value = {
      name: n.name,
      kind,
      url: kind === 'image' ? store.workspaceUrl(n.path) : undefined,
      text: rsp.content
    }
  } catch (e) {
    toast.push((e as Error).message, 'error')
  }
}

watch(() => [store.currentId, ui.rightView], load)
onMounted(load)

/** 文件树用原生 <details> 递归：深度个位数，不值得为它写虚拟滚动 */
const FileNode = defineComponent({
  name: 'FileNode',
  props: { n: { type: Object as () => FileNodeT, required: true }, depth: { type: Number, required: true } },
  emits: ['open'],
  setup(props, { emit }) {
    const row = (label: string, icon: string) =>
      h('span', { class: 'frow' }, [h(DsIcon, { name: icon, size: 14 }), h('span', { class: 'fname' }, label)])
    if (props.n.type === 'dir') {
      return () =>
        h('details', { class: 'fdir' }, [
          h('summary', null, [row(props.n.name, 'folder')]),
          ...(props.n.children || []).map((c) => h(FileNode, { n: c, depth: props.depth + 1, onOpen: (x: FileNodeT) => emit('open', x) }))
        ])
    }
    return () => h('button', { class: 'ffile', onClick: () => emit('open', props.n) }, [row(props.n.name, 'file')])
  }
})
</script>

<style scoped>
.panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: var(--dsw-alias-bg-layer-1);
}

.bar {
  flex: none;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px 0;
}

.tabs {
  display: flex;
  gap: 18px;
}

.tab {
  position: relative;
  border: none;
  background: transparent;
  padding: 6px 0 8px;
  font-family: inherit;
  font-size: 13px;
  font-weight: 500;
  line-height: 16px;
  color: var(--dsw-alias-label-tertiary);
  cursor: pointer;
}

.tab.on {
  color: var(--dsw-alias-state-business-primary);
}

.tab.on::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 2px;
  border-radius: 2px;
  background: var(--dsw-alias-state-business-primary);
}

.grow {
  flex: 1;
}

.iconBtn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--dsw-alias-label-tertiary);
  cursor: pointer;
}

.iconBtn:hover {
  background: var(--dsw-alias-interactive-bg-hover);
  color: var(--dsw-alias-label-primary);
}

.body {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 10px 12px 14px;
  font-size: 13px;
  color: var(--dsw-alias-label-secondary);
}

.dim {
  padding: 16px 2px;
  color: var(--dsw-alias-label-tertiary);
}

.logs {
  font-family: var(--ds-font-family-code);
  font-size: 12px;
  line-height: 18px;
}

.logLine {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  padding: 1px 0;
}

.panel :deep(.fdir) {
  margin: 1px 0;
}

.panel :deep(summary) {
  list-style: none;
  cursor: pointer;
}

.panel :deep(summary::-webkit-details-marker) {
  display: none;
}

.panel :deep(.frow),
.panel :deep(.ffile) {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 3px 6px;
  border: none;
  border-radius: 6px;
  background: transparent;
  font-family: inherit;
  font-size: 13px;
  color: var(--dsw-alias-label-secondary);
  text-align: left;
  cursor: pointer;
}

.panel :deep(.frow:hover),
.panel :deep(.ffile:hover) {
  background: var(--dsw-alias-interactive-bg-hover);
}

.panel :deep(.fname) {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.trace {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.trace th,
.trace td {
  padding: 5px 6px;
  border-bottom: 1px solid var(--dsw-alias-border-l2);
  text-align: left;
}

.num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

tfoot td {
  font-weight: 600;
  color: var(--dsw-alias-label-primary);
}

.preview {
  flex: none;
  max-height: 46%;
  display: flex;
  flex-direction: column;
  border-top: 1px solid var(--dsw-alias-border-l2);
}

.pvBar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
}

.pvName {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: 12px;
  color: var(--dsw-alias-label-tertiary);
}

.pvImg {
  max-width: 100%;
  object-fit: contain;
  padding: 0 10px 10px;
}

.pvMd,
.pvCode {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 0 12px 12px;
}

.pvCode {
  font-family: var(--ds-font-family-code);
  font-size: 12px;
  line-height: 18px;
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0;
}
</style>
