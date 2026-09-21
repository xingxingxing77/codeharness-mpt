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
      <!-- importRepo 的唯一入口：后端有路由，界面才有这个控件 -->
      <div v-if="store.currentId" class="importRow">
        <input
          v-model="importPath"
          class="importInput"
          :placeholder="`仓库目录（须在 ${store.health?.workspace_root || 'workspace_root'} 内）`"
          aria-label="仓库目录路径"
          :disabled="importBusy"
          @keydown.enter.prevent="doImport"
        />
        <VButton variant="ghost" size="s" :disabled="importBusy || !importPath.trim()" @click="doImport">
          {{ importBusy ? '导入中…' : '导入仓库' }}
        </VButton>
      </div>
      <div v-if="importErr" class="importErr">{{ importErr }}</div>
      <!-- upload_kb 的唯一入口（B12）：C3 把这条链接通了，此前灌一份文档只能 curl。
           刻意不设后缀白名单、也不把后端那两个上限写成数字——三条判据只住后端一份，
           拒因照原文显示（复用 F-E 那条「阈值不重抄第二份」的口径）。 -->
      <div v-if="store.currentId" class="importRow">
        <input
          ref="kbInput"
          type="file"
          multiple
          class="kbInput"
          aria-label="要加入知识库的文档"
          :disabled="kbBusy"
          @change="pickKb"
        />
        <VButton variant="ghost" size="s" :disabled="kbBusy || !kbChosen.length" @click="doUploadKb">
          {{ kbBusy ? '摄取中…' : '加入知识库' }}
        </VButton>
      </div>
      <div v-if="kbMsg.text" class="kbMsg" :class="{ err: kbMsg.err }">{{ kbMsg.text }}</div>
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

    <div v-else-if="ui.rightView === 'inspect'" class="body">
      <div v-if="!inspectBlock" class="empty">未选中调用——展开工具行，点左下角的「检视」。</div>
      <template v-else>
        <section v-if="inspectInput" class="section">
          <div class="sectionLabel">输入</div>
          <pre class="code">{{ inspectInput }}</pre>
        </section>
        <section class="section">
          <div class="sectionLabel">输出</div>
          <ToolCard :b="inspectBlock" />
        </section>
      </template>
    </div>

    <div v-else-if="ui.rightView === 'replay'" class="body">
      <!-- C11 时间旅行：后端每个 superstep 本来就落一份 checkpoint（AsyncSqliteSaver），
           这里只读不写。列表只回摘要，state 点一行才取一份（实测最单个会话 711 份、单份最大 62 KB）。
           界面文案说「步」不说 superstep/checkpoint：参照系的词汇是 trajectory/turn/request/step，
           它 UI 层根本没有 superstep 这个词（出处见本文件 style 段的引用注释）。 -->
      <div v-if="replay.reason" class="dim">{{ replay.reason }}</div>
      <template v-else>
        <table v-if="replay.rows.length" class="cpTable">
          <thead><tr><th class="num">步</th><th>来源</th><th>下一步</th><th>产出</th><th class="num">时刻</th></tr></thead>
          <tbody>
            <tr v-for="cp in replay.rows" :key="cp.checkpoint_id"
                class="cpRow" :class="{ on: cp.checkpoint_id === replay.open }"
                role="button" tabindex="0" :aria-label="`看第 ${cp.step} 步的状态`"
                @click="openCp(cp)" @keydown.enter="openCp(cp)">
              <td class="num">{{ cp.step }}</td>
              <td>{{ cp.source }}</td>
              <td>{{ cp.next.join('、') || '—' }}</td>
              <td><span class="cpWrites">{{ (cp.writes.length ? cp.writes : cp.tasks).join(' ') || '—' }}</span></td>
              <td class="num">{{ cpClock(cp.ts) }}</td>
            </tr>
          </tbody>
        </table>
        <div v-else class="dim">这个会话还没有步——跑一轮再来看。</div>
        <button v-if="replay.hasMore" class="cpMore" :disabled="replay.busy" @click="loadCp(replay.nextBefore)">
          {{ replay.busy ? '加载中…' : '加载更早' }}
        </button>
        <pre v-if="replay.state" class="code stateBox">{{ replay.state }}</pre>
      </template>
    </div>

    <div v-else class="body">
      <div v-if="!spans.length" class="dim">trace 仅在 Redis 模式下有数据</div>
      <table v-else class="trace">
        <thead><tr><th>节点</th><th class="num">入</th><th class="num">出</th><th class="num">成本</th></tr></thead>
        <tbody>
          <tr v-for="(s, i) in spans" :key="i">
            <td>{{ s.node }}</td><td class="num">{{ s.pt }}</td><td class="num">{{ s.ct }}</td>
            <td class="num">{{ moneyBoth(s, 4) }}</td>
          </tr>
        </tbody>
        <tfoot><tr><td>合计</td><td class="num">{{ totals.pt }}</td><td class="num">{{ totals.ct }}</td><td class="num">{{ moneyBoth(totals, 4) }}</td></tr></tfoot>
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
/** 右栏 detailsCol：日志 / 检视 / 文件 / 变更 / 编排 / trace / 回放。
 *  替掉原 ToolsPanel + 底部 TerminalPanel 抽屉（参考项目没有底部抽屉）。 */
import { computed, h, defineComponent, onMounted, reactive, ref, watch } from 'vue'
import MarkdownText from './conversation/MarkdownText.vue'
import MermaidView from './MermaidView.vue'
import ToolCard from './conversation/ToolCard.vue'
import DsIcon from './ui/DsIcon.vue'
import VButton from './ui/VButton.vue'
import { api } from '../api/client'
import { useSessionStore } from '../stores/sessions'
import { useUiStore } from '../stores/ui'
import { useToastStore } from '../stores/toast'
import type { FileNode as FileNodeT } from '../types'
import { moneyBoth, sumCosts } from '../utils/money'

const store = useSessionStore()
const ui = useUiStore()
const toast = useToastStore()

const VIEWS = [
  { key: 'cards', label: '日志' },
  { key: 'inspect', label: '检视' },
  { key: 'files', label: '文件' },
  { key: 'review', label: '变更' },
  { key: 'graph', label: '编排' },
  { key: 'trace', label: 'trace' },
  { key: 'replay', label: '回放' }
] as const

const tree = ref<FileNodeT[]>([])
const mermaid = ref('')
/** spans 单一来源在 store：选中会话拉一次、终态再刷一次，右栏不发第二份请求。 */
const spans = computed(() => store.spans)
const preview = ref<{ name: string; kind: string; url?: string; text?: string } | null>(null)

const edits = computed(() => store.blockList.filter((b) => b.type === 'Editor'))
/** 选中态唯一真相在 ui store：对话流的「检视」钮写、这里读，两边不各持一份。 */
const inspectBlock = computed(() => (ui.selectedKey ? store.blocks[ui.selectedKey] : undefined))
/** 参考项目这节显示 provider 下发的 argsRaw；我们的块里入参散在 cmd/path/url/page/obj/doc 上，
 *  取非空的那几项拼一份。全空（比如纯正文块）就不画这一节，不摆一个空花括号。 */
const inspectInput = computed(() => {
  const b = inspectBlock.value
  if (!b) return ''
  const args: Record<string, unknown> = {}
  if (b.cmd) args.command = b.cmd
  if (b.path) args.path = b.path
  if (b.url) args.url = b.url
  if (b.page) args.page = b.page
  if (b.obj) args.input = b.obj
  if (b.doc) args.document = b.doc
  return Object.keys(args).length ? JSON.stringify(args, null, 2) : ''
})
const totals = computed(() => ({
  ...spans.value.reduce((a, s) => ({ pt: a.pt + s.pt, ct: a.ct + s.ct }), { pt: 0, ct: 0 }),
  ...sumCosts(spans.value)        // 分桶累加，不相加（C12）
}))

/** C11 时间旅行：这一屏是本地态（分页游标 + 展开的那一份），不进 pinia——
 *  它不像 spans 那样被对话流共享，塞进 store 只是多一处要同步的记账。 */
interface CpRow { checkpoint_id: string; step: number; source: string; ts: string;
  next: string[]; writes: string[]; tasks: string[] }
const replay = reactive({
  rows: [] as CpRow[], hasMore: false, nextBefore: '', reason: '', open: '', state: '', busy: false
})
const cpClock = (ts: string) => (ts || '').slice(11, 19)

async function loadCp(before = '') {
  if (!store.currentId || replay.busy) return
  replay.busy = true
  try {
    const p = await api.checkpoints(store.currentId, before)
    replay.rows = before ? [...replay.rows, ...p.checkpoints] : p.checkpoints
    replay.hasMore = p.has_more
    replay.nextBefore = p.next_before
    replay.reason = p.reason || ''
    if (!before) {
      replay.open = ''
      replay.state = ''
    }
  } catch (e) {
    replay.reason = `读不到超步：${(e as Error).message}`      // 拉不到就说，别摆一张空表当"没有历史"
  } finally {
    replay.busy = false
  }
}

async function openCp(cp: CpRow) {
  if (replay.open === cp.checkpoint_id) {
    replay.open = ''
    replay.state = ''
    return
  }
  replay.open = cp.checkpoint_id
  replay.state = '读取中…'
  try {
    const d = await api.checkpointState(store.currentId!, cp.checkpoint_id)
    replay.state = JSON.stringify(d.state, null, 2)
  } catch (e) {
    const err = e as Error & { status?: number }
    // 413 是"这份太大不在浏览器里展开"，不是出错——与 /workspace/file 同一族，按状态码分流
    replay.state = err.status === 413 ? err.message : `读取失败：${err.message}`
  }
}

function pick(k: string) {
  ui.rightView = k as typeof ui.rightView
  if (k === 'trace' && store.currentId) void store.loadTrace(store.currentId)
  // 切到回放且这一场还没拉过：拉第一页。切走再回来不重复请求（分页游标在本地态里）
  if (k === 'replay' && store.currentId && !replay.rows.length && !replay.reason) void loadCp()
}

const importPath = ref('')
const importBusy = ref(false)
const importErr = ref('')

async function load() {
  importErr.value = ''
  if (!store.currentId) {
    tree.value = []
    mermaid.value = ''
    return
  }
  try {
    const [f, g] = await Promise.all([api.fileTree(store.currentId), api.sessionGraph(store.currentId)])
    tree.value = f.tree || []
    mermaid.value = g.mermaid || ''
  } catch (e) {
    toast.push((e as Error).message, 'error')
  }
}

/** 越界与不存在的目录由 server 判 400，报错原样显示；动作件吞错返回的 {error} 也不算成功。 */
async function doImport() {
  const p = importPath.value.trim()
  if (!p || !store.currentId || importBusy.value) return
  importBusy.value = true
  importErr.value = ''
  try {
    const r = await api.importRepo(store.currentId, { repo_path: p })
    if (r.error) {
      importErr.value = String(r.error)
    } else {
      // B7 的规模护栏把 `truncated` 回在响应里，此前前端只看 node_count →
      // 撞到上限的导入和完整导入长得一模一样，图不完整没人知道。
      const capped = Boolean(r.truncated)
      toast.push(`已导入 ${r.node_count} 节点 / ${r.edge_count} 边` +
        (capped ? '（目录过大，扫描在上限处截断，图不完整）' : ''), capped ? 'warn' : 'success')
      await load()
    }
  } catch (e) {
    importErr.value = (e as Error).message
  } finally {
    importBusy.value = false
  }
}

const kbInput = ref<HTMLInputElement>()
const kbChosen = ref<File[]>([])
const kbBusy = ref(false)
const kbMsg = ref<{ text: string; err: boolean }>({ text: '', err: false })

function pickKb(e: Event) {
  kbChosen.value = Array.from((e.target as HTMLInputElement).files || [])
  kbMsg.value = { text: '', err: false }
}

/** 后端把「拒了哪几条」和「摄入了多少」一起回（部分成功是合法结局），所以两条都得说：
 *  只报成功数=悄悄吞掉坏文件，只报错=明明进去了一半还说成一笔没成。原件落在 `kb/`，
 *  所以下一次 load() 会把它带进文件树——用户据此能看见传上去的东西。 */
async function doUploadKb() {
  if (!store.currentId || kbBusy.value || !kbChosen.value.length) return
  kbBusy.value = true
  try {
    const r = await api.uploadKb(store.currentId, kbChosen.value)
    const errs = r.errors || []
    const head = `已摄取 ${r.written?.length ?? 0} 份文档 / ${r.chunk_count} 条切片`
    kbMsg.value = { text: errs.length ? `${head}\n被拒 ${errs.length} 条：\n${errs.join('\n')}` : head,
                    err: errs.length > 0 }
    kbChosen.value = []
    if (kbInput.value) kbInput.value.value = ''    // 不清 value，同名文件第二次选不中（change 不再触发）
    await load()
  } catch (e) {
    kbMsg.value = { text: (e as Error).message, err: true }
  } finally {
    kbBusy.value = false
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
    const msg = (e as Error).message
    if ((e as { status?: number }).status === 413) {
      // B10：超过预览上限不是"出错"，是"这个文件不在浏览器里预览"。
      // 只弹一条两秒半就消失的 toast 等于什么都没发生——点开文件的人要在**落点**上看到话，
      // 所以面板里也留一句（照后端原文，不在前端重抄一遍大小阈值，两处会漂）。
      preview.value = { name: n.name, kind: 'code', text: msg }
      toast.push(msg, 'warn')
    } else {
      toast.push(msg, 'error')
    }
  }
}

watch(() => [store.currentId, ui.rightView], load)
/** 换会话必须把回放清干净：分页游标和已展开的那一份都属于上一场，
 *  不清就会把 A 场的超步显示在 B 场名下——和本仓「第二个游标」那族洞同形。 */
watch(() => store.currentId, () => {
  Object.assign(replay, { rows: [], hasMore: false, nextBefore: '', reason: '', open: '', state: '', busy: false })
  // 上传的结局与已选文件都属于上一场：不清就把 A 场「被拒 3 条」显示在 B 场名下（同一族洞）
  kbChosen.value = []
  kbMsg.value = { text: '', err: false }
  if (kbInput.value) kbInput.value.value = ''
})
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

.importRow {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
}

.importInput {
  flex: 1;
  min-width: 0;
  height: 26px;
  padding: 0 8px;
  border: 1px solid var(--dsw-alias-border-l2);
  border-radius: 8px;
  outline: none;
  background: var(--dsw-specific-menu);
  font-family: inherit;
  font-size: 12px;
  line-height: 18px;
  color: var(--dsw-alias-label-primary);
}

.importErr {
  margin-bottom: 8px;
  font-size: 12px;
  line-height: 18px;
  color: var(--dsw-alias-state-error-primary);
}
/* B12 上传那一行：参照系没有「选本地文件灌知识库」这一面，最近的是
   packages/client/ui-attachment/src/AttachmentRail.module.css 里那族 12px/18px、0~8px 内距的
   动作按钮行；这里不另造一档字号，直接落进本文件 `.importInput` 的 26px / 12px-18px 同一族，
   让它和上面「导入仓库」那行长得是一家人。 */
.kbInput {
  flex: 1;
  min-width: 0;
  height: 26px;
  padding: 0 8px;
  border: 1px solid var(--dsw-alias-border-l2);
  border-radius: 8px;
  background: var(--dsw-specific-menu);
  font-family: inherit;
  font-size: 12px;
  line-height: 18px;
  color: var(--dsw-alias-label-primary);
}
.kbMsg {
  margin-bottom: 8px;
  font-size: 12px;
  line-height: 18px;
  white-space: pre-line;          /* errors[] 是一条一行，不换行会糊成一串 */
  color: var(--dsw-alias-label-secondary);
}
.kbMsg.err {
  color: var(--dsw-alias-state-error-primary);
}

.dim {
  padding: 16px 2px;
  color: var(--dsw-alias-label-tertiary);
}

/* 检视视图三件套，取值照抄参考项目 DetailsPanel.module.css */
.section {
  margin-bottom: 16px;
}

.sectionLabel {
  margin-bottom: 6px;
  font-size: 12px;
  line-height: 18px;
  font-weight: 500;
  color: var(--dsw-alias-label-secondary);
}

/* figma Code-block（I54:42735;43:41429）：r12、pad 16、mono 13/22 */
.code {
  margin: 0;
  padding: 16px;
  border-radius: 12px;
  background: var(--dsw-alias-markdown-code-block);
  font-family: var(--ds-font-family-code);
  font-size: 13px;
  line-height: 22px;
  color: var(--dsw-alias-label-primary);
  white-space: pre-wrap;
  word-break: break-word;
}

.empty {
  padding: 8px 0;
  font-size: 13px;
  line-height: 20px;
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

/* ── C11 回放视图：样式一律取 E:\deepseek-harness 的既有形状（用户 2026-09-21 定的口径：
      参照系有的照抄源值，没有的也要按它的风格设计）。逐处出处：
   · 表格骨架 / 行高 / sticky 表头 / 行线 = packages/client/ui-trajectory/src/client/
       TrajectoryTable.module.css 的 `.table th`+`.table td`（30px 行、0 8px 内距、
       th 底边 border-l2 + sidebar-fill + tertiary + 500、td 底边 border-l1、整表 bg-layer-1、
       12/18 = --dsw-font-xxs-12、overflow+ellipsis+nowrap 在 td 上）
   · 可点行 hover / 选中 / 键盘焦点 = 同一文件：hover 用 `interactive-bg-hover`、
       **选中用 `interactive-bg-active`**（我前两版误用了 hover 那枚）、
       focus-visible 走 `inset 0 0 0 1px state-business-primary`，transition 120ms --ds-ease-in-out
   · 「加载更早」= 同一文件的 `.historyLoadButton`（表格里的同类是**整行按钮**：29px 高、12/18、
       bg-layer-1、hover 换 interactive-bg-hover + label-primary、focus-visible outline 2px offset -2px、
       disabled 只收 cursor）。**不是** ChatView.module.css `.older` 那颗 14px 圆角按钮——
       那是对话流的同类；参照系里这类开关本来就有两族，选错族也算没照它设计。
   · 展开的 state = 复用本文件已有的 `.code`（其值来自参照系 ui-conversation/.../DetailsPanel.module.css
       :79-94 的右栏代码块：pad 16 / r12 / 13-22 / pre-wrap+break-word），这里只加高度上限
   · tab 条没动：本文件 `.tab`/`.tab.on` 已是参照系 `.detailTab`/`.detailTabActive` 那一族   */
.cpTable {
  width: 100%;
  table-layout: fixed;
  border-spacing: 0;
  font-size: 12px;
  line-height: 18px;
  color: var(--dsw-alias-label-primary);
  background: var(--dsw-alias-bg-layer-1);
}

.cpTable th,
.cpTable td {
  height: 30px;
  padding: 0 8px;
  text-align: left;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.cpTable th {
  position: sticky;
  top: 0;
  z-index: 3;
  background: var(--dsw-specific-sidebar-fill);
  border-bottom: 1px solid var(--dsw-alias-border-l2);
  font-weight: 500;
  color: var(--dsw-alias-label-tertiary);
  user-select: none;
}

.cpTable td {
  border-bottom: 1px solid var(--dsw-alias-border-l1);
}

.cpRow {
  cursor: pointer;
  transition: background 120ms var(--ds-ease-in-out);
}

.cpRow:hover td {
  background: var(--dsw-alias-interactive-bg-hover);
}

.cpRow.on td {
  background: var(--dsw-alias-interactive-bg-active);
}

.cpRow:focus-visible {
  box-shadow: inset 0 0 0 1px var(--dsw-alias-state-business-primary);
  outline: none;
}

.cpWrites {
  color: var(--dsw-alias-label-tertiary);
}

.cpMore {
  display: block;
  width: 100%;
  height: 29px;
  border: none;
  font-size: 12px;
  line-height: 18px;
  color: var(--dsw-alias-label-secondary);
  background: var(--dsw-alias-bg-layer-1);
  cursor: pointer;
}

.cpMore:hover:not(:disabled) {
  color: var(--dsw-alias-label-primary);
  background: var(--dsw-alias-interactive-bg-hover);
}

.cpMore:focus-visible {
  outline: 2px solid var(--dsw-alias-state-business-primary);
  outline-offset: -2px;
}

.cpMore:disabled {
  cursor: default;
}

.stateBox {
  /* 只补高度与滚动，字号/圆角/底色全交给 `.code` */
  max-height: 42vh;
  overflow: auto;
  margin-top: 8px;
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
