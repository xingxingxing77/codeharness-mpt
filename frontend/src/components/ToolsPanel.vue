<template>
  <div class="tools">
    <!-- 顶栏：模型胶囊 + 新建 + 右侧开关（参考图右侧面板工具条） -->
    <div class="tools-bar">
      <span class="pill">
        <Icon name="sparkle" :size="13" />
        {{ modelShort }}
        <Icon name="chevron-down" :size="12" />
      </span>
      <button class="bar-btn" title="新建会话" @click="ui.showCreate = true">
        <Icon name="plus" :size="16" />
      </button>
      <span class="grow" />
      <button class="bar-btn" title="在浏览器打开工作区" @click="openWorkspace">
        <Icon name="external" :size="15" />
      </button>
      <button class="bar-btn" title="收起面板" @click="ui.closeRight()">
        <Icon name="x" :size="15" />
      </button>
    </div>

    <div class="tools-body">
      <!-- 工具卡视图 -->
      <div v-if="ui.rightView === 'cards'" class="cards-wrap">
        <div class="cards">
          <div class="tcard" @click="openFiles">
            <Icon name="folder" :size="22" />
            <div class="t-title">文件</div>
            <div class="t-sub">浏览项目文件</div>
            <span class="kbd">Ctrl+P</span>
          </div>
          <div class="tcard" @click="openWorkspace">
            <Icon name="globe" :size="22" />
            <div class="t-title">浏览器</div>
            <div class="t-sub">打开网站</div>
            <span class="kbd">Ctrl+T</span>
          </div>
          <div class="tcard" @click="openReview">
            <Icon name="eye" :size="22" />
            <div class="t-title">审查</div>
            <div class="t-sub">查看代码变更</div>
            <span class="kbd">Ctrl+Shift+G</span>
          </div>
          <div class="tcard" @click="toggleTerm">
            <Icon name="terminal" :size="22" />
            <div class="t-title">终端</div>
            <div class="t-sub">启动交互式 shell</div>
            <span class="kbd">Ctrl+`</span>
          </div>
          <div class="tcard" @click="openGraph">
            <Icon name="branch" :size="22" />
            <div class="t-title">编排</div>
            <div class="t-sub">查看智能体拓扑</div>
          </div>
          <div class="tcard" @click="openTrace">
            <Icon name="coins" :size="22" />
            <div class="t-title">用量</div>
            <div class="t-sub">LLM 调用与 token</div>
          </div>
        </div>
      </div>

      <!-- 文件视图 -->
      <div v-else-if="ui.rightView === 'files'" class="files">
        <div class="files-bar">
          <button class="back" @click="ui.rightView = 'cards'">
            <Icon name="chevron-down" :size="14" style="transform: rotate(90deg)" />
            工具
          </button>
          <span class="proj">{{ store.current?.project_name }}</span>
          <button class="back" @click="loadTree">
            <Icon name="refresh" :size="13" />
            刷新
          </button>
        </div>
        <div class="files-body">
          <div class="tree">
            <n-tree
              :data="treeData"
              block-line
              selectable
              :default-expanded-keys="defaultExpanded"
              :key="treeKey"
              @update:selected-keys="onSelect"
            />
            <div v-if="loaded && !treeData.length" class="none">工作区暂无产物</div>
          </div>
          <div class="preview">
            <template v-if="preview.type === 'image'">
              <n-image :src="preview.url" style="max-width: 100%" />
            </template>
            <template v-else-if="preview.type === 'markdown'">
              <div ref="mdEl" class="markdown-body" v-html="preview.html" />
            </template>
            <template v-else-if="preview.type === 'mermaid'">
              <MermaidView :src="preview.content" :name="preview.name" />
            </template>
            <template v-else-if="preview.type === 'code'">
              <pre class="code-view" v-html="preview.html" />
            </template>
            <template v-else-if="preview.type === 'text'">
              <pre class="code-view">{{ preview.content }}</pre>
            </template>
            <div v-else class="none">点击左侧文件预览</div>
          </div>
        </div>
      </div>

      <!-- N6 编排视图：节点与边取自服务端真实装配（/graph），mermaid 前端渲染 -->
      <div v-else-if="ui.rightView === 'graph'" class="graph">
        <div class="files-bar">
          <button class="back" @click="ui.rightView = 'cards'">
            <Icon name="chevron-down" :size="14" style="transform: rotate(90deg)" />
            工具
          </button>
          <span class="proj">编排图 · {{ store.current?.project_name }}</span>
          <button class="back" @click="loadGraph">
            <Icon name="refresh" :size="13" />
            刷新
          </button>
        </div>
        <div class="graph-body">
          <MermaidView v-if="graphSrc" :src="graphSrc" name="orchestration" />
          <div v-else class="none">{{ graphMsg }}</div>
        </div>
      </div>

      <!-- N4 用量视图：每笔 LLM 调用 span（/trace，S7 trace 存储的前端半边） -->
      <div v-else-if="ui.rightView === 'trace'" class="review">
        <div class="files-bar">
          <button class="back" @click="ui.rightView = 'cards'">
            <Icon name="chevron-down" :size="14" style="transform: rotate(90deg)" />
            工具
          </button>
          <span class="proj">LLM 用量 · {{ store.current?.project_name }}</span>
          <button class="back" @click="loadTrace">
            <Icon name="refresh" :size="13" />
            刷新
          </button>
        </div>
        <div class="review-body">
          <div v-if="spans.length" class="trace-sum">
            {{ spans.length }} 次调用 · prompt {{ sumPt }} tok · completion {{ sumCt }} tok ·
            ¥{{ sumCost }}
          </div>
          <template v-if="spans.length">
            <div v-for="(s, i) in spans" :key="i" class="trace-row">
              <span class="t-time">{{ fmtTs(s.ts) }}</span>
              <span class="t-node">{{ s.node }}</span>
              <span class="t-tok">↑{{ s.pt }} ↓{{ s.ct }}</span>
              <span class="t-cost">¥{{ s.cost.toFixed(6) }}</span>
            </div>
          </template>
          <div v-else class="none">{{ traceMsg || '暂无调用记录' }}</div>
        </div>
      </div>

      <!-- 审查视图：代码变更块 -->
      <div v-else class="review">
        <div class="files-bar">
          <button class="back" @click="ui.rightView = 'cards'">
            <Icon name="chevron-down" :size="14" style="transform: rotate(90deg)" />
            工具
          </button>
          <span class="proj">代码变更（{{ edits.length }}）</span>
        </div>
        <div class="review-body">
          <template v-if="edits.length">
            <div v-for="b in edits" :key="b.key" class="review-item">
              <EditorBlock :block="b" />
            </div>
          </template>
          <div v-else class="none">暂无代码变更</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { NImage, NTree, useMessage } from 'naive-ui'
import type { TreeOption } from 'naive-ui'
import { api } from '../api/client'
import { useSessionStore } from '../stores/sessions'
import { useUiStore } from '../stores/ui'
import { highlightCode, langOfFilename, renderMarkdown } from '../utils/render'
import { hydrateMermaid } from '../utils/mermaid'
import type { FileNode } from '../types'
import EditorBlock from './blocks/EditorBlock.vue'
import Icon from './Icon.vue'
import MermaidView from './MermaidView.vue'

const store = useSessionStore()
const ui = useUiStore()
const message = useMessage()

const treeData = ref<TreeOption[]>([])
const treeKey = ref(0)
const loaded = ref(false)
const defaultExpanded = ref<string[]>([])
const preview = ref<any>({ type: 'none' })

const modelShort = computed(() => {
  const m = store.health?.model || 'LLM'
  return m.length > 14 ? m.slice(0, 13) + '…' : m
})

const edits = computed(() => store.blockList.filter((b) => b.type === 'Editor'))

const MD_EXTS = new Set(['.md'])
const IMG_EXTS = new Set(['.png', '.jpg', '.jpeg', '.svg', '.gif', '.webp'])

function toTree(nodes: FileNode[]): TreeOption[] {
  return nodes.map((n) => ({
    key: n.path,
    label: n.name,
    isLeaf: n.type === 'file',
    children: n.children ? toTree(n.children) : undefined
  }))
}

async function loadTree() {
  if (!store.currentId) return
  try {
    const rsp = await api.fileTree(store.currentId)
    treeData.value = rsp.exists ? toTree(rsp.tree || []) : []
    defaultExpanded.value = (rsp.tree || [])
      .filter((n: FileNode) => n.type === 'dir')
      .map((n: FileNode) => n.path)
    treeKey.value += 1
    loaded.value = true
  } catch {
    /* backend offline */
  }
}

async function onSelect(keys: (string | number)[]) {
  const path = keys[0] as string
  if (!path) return
  try {
    const rsp = await api.fileContent(store.currentId, path)
    if (rsp.type === 'image') {
      preview.value = { type: 'image', url: rsp.url }
    } else if (IMG_EXTS.has(rsp.ext || '')) {
      preview.value = { type: 'image', url: store.workspaceUrl(path) }
    } else if (MD_EXTS.has(rsp.ext || '')) {
      preview.value = { type: 'markdown', html: renderMarkdown(rsp.content) }
    } else if (rsp.ext === '.mmd') {
      // 方案 C：.mmd 是落盘真源，预览直接渲染成图（源码/导出走图上的工具条）
      preview.value = { type: 'mermaid', content: rsp.content, name: path.split(/[\\/]/).pop() }
    } else {
      preview.value = {
        type: 'code',
        html: highlightCode(rsp.content || '', langOfFilename(path)),
        content: rsp.content
      }
    }
  } catch (e: any) {
    preview.value = { type: 'text', content: `加载失败：${e.message}` }
  }
}

function openFiles() {
  ui.rightView = 'files'
  loadTree()
}

function openReview() {
  ui.rightView = 'review'
}

function toggleTerm() {
  ui.terminalOpen = !ui.terminalOpen
}

function openWorkspace() {
  if (!store.currentId) {
    message.info('先选择一个会话')
    return
  }
  window.open(store.workspaceUrl('') || '/workspace/', '_blank')
}

const sid = computed(() => store.currentId)

watch(sid, (v, old) => {
  if (v && v !== old) {
    preview.value = { type: 'none' }
    loaded.value = false
    if (ui.rightView === 'files') loadTree()
  }
})

watch(
  () => store.status,
  (v) => {
    if (['finished', 'stopped', 'failed'].includes(v) && ui.rightView === 'files') loadTree()
    if (['finished', 'stopped', 'failed'].includes(v) && ui.rightView === 'trace') loadTrace()
  }
)

/* markdown 预览里的 ```mermaid 围栏 → 水合成图 */
const mdEl = ref<HTMLElement>()
watch(
  () => preview.value.html,
  async () => {
    await nextTick()
    hydrateMermaid(mdEl.value)
  }
)

/* N6 编排视图 */
const graphSrc = ref('')
const graphMsg = ref('')

async function loadGraph() {
  if (!store.currentId) return
  graphSrc.value = ''
  graphMsg.value = '编排图加载中…'
  try {
    graphSrc.value = (await api.sessionGraph(store.currentId)).mermaid
  } catch (e: any) {
    graphMsg.value = `编排图加载失败：${e.message}`
  }
}

function openGraph() {
  ui.rightView = 'graph'
  loadGraph()
}

/* N4 用量视图 */
const spans = ref<any[]>([])
const traceMsg = ref('')

async function loadTrace() {
  if (!store.currentId) return
  traceMsg.value = ''
  try {
    spans.value = (await api.sessionTrace(store.currentId)).spans || []
  } catch (e: any) {
    spans.value = []
    traceMsg.value = `加载失败：${e.message}`
  }
}

function openTrace() {
  ui.rightView = 'trace'
  loadTrace()
}

const sumPt = computed(() => spans.value.reduce((a, s) => a + s.pt, 0))
const sumCt = computed(() => spans.value.reduce((a, s) => a + s.ct, 0))
const sumCost = computed(() => spans.value.reduce((a, s) => a + s.cost, 0).toFixed(4))

function fmtTs(ts: number) {
  return new Date(ts * 1000).toLocaleTimeString('zh-CN', { hour12: false })
}
</script>

<style scoped>
.tools {
  width: 52%;
  min-width: 420px;
  flex: none;
  border-left: 1px solid var(--line);
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.tools-bar {
  height: 52px;
  flex: none;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 14px;
}

.pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 12px;
  padding: 5px 9px;
  border-radius: 9px;
  max-width: 150px;
  overflow: hidden;
  white-space: nowrap;
}

.bar-btn {
  border: none;
  background: none;
  width: 28px;
  height: 28px;
  border-radius: 8px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--text-2);
  cursor: pointer;
}

.bar-btn:hover {
  background: rgba(60, 52, 24, 0.06);
}

.grow {
  flex: 1;
}

.tools-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

/* 工具卡 */
.cards-wrap {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}

.cards {
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
  justify-content: center;
}

.tcard {
  width: 150px;
  padding: 24px 12px 16px;
  background: var(--tool-card);
  border-radius: 14px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 7px;
  cursor: pointer;
  color: #4a4843;
  transition: background 0.15s;
}

.tcard:hover {
  background: #efeee9;
}

.t-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
}

.t-sub {
  font-size: 12px;
  color: var(--text-3);
}

.kbd {
  margin-top: 8px;
  font-size: 11px;
  background: var(--kbd);
  color: var(--text-3);
  padding: 2px 8px;
  border-radius: 6px;
  font-family: var(--mono);
}

/* 文件 / 审查视图 */
.files,
.review {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.files-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 12px 8px;
}

.back {
  border: none;
  background: none;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12.5px;
  color: var(--text-2);
  cursor: pointer;
  padding: 4px 8px;
  border-radius: 7px;
}

.back:hover {
  background: rgba(60, 52, 24, 0.06);
}

.proj {
  flex: 1;
  font-size: 12px;
  color: var(--text-3);
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.files-body {
  flex: 1;
  min-height: 0;
  display: flex;
  border-top: 1px solid var(--line);
}

.tree {
  width: 42%;
  border-right: 1px solid var(--line);
  overflow: auto;
  padding: 6px;
}

.preview {
  flex: 1;
  overflow: auto;
  padding: 10px;
  min-width: 0;
}

.review-body {
  flex: 1;
  min-height: 0;
  overflow: auto;
  border-top: 1px solid var(--line);
  padding: 6px 0;
}

.review-item {
  margin: 0 12px 10px;
}

/* N4 用量视图 */
.trace-sum {
  padding: 8px 12px;
  font-size: 12.5px;
  color: var(--text-2);
  border-bottom: 1px solid var(--line);
}

.trace-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 12px;
  font-size: 12.5px;
  font-family: var(--mono);
  border-bottom: 1px solid var(--line);
}

.t-time {
  flex: none;
  color: var(--text-3);
}

.t-node {
  flex: 1;
  min-width: 0;
  color: var(--text);
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.t-tok {
  flex: none;
  color: var(--text-2);
}

.t-cost {
  flex: none;
  color: var(--text-3);
}

.none {
  text-align: center;
  opacity: 0.5;
  padding: 24px 6px;
  font-size: 12.5px;
}
</style>
