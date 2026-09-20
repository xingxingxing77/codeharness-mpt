<template>
  <div class="heroShell">
    <div class="glow" aria-hidden="true" />
    <div class="inner">
      <div class="brand">
        <DsIcon name="sparkle" :size="34" />
        <h1>把想法交给智能体</h1>
        <span class="badge">预览</span>
      </div>

      <div class="wsRow">
        <input
          v-if="naming"
          ref="nameEl"
          v-model="nameDraft"
          class="wsInput"
          placeholder="新项目名，回车确认"
          aria-label="新项目名"
          @keydown.enter.prevent="commitName"
          @keydown.esc.prevent="cancelName"
          @blur="commitName"
        />
        <VMenu v-else :items="projectItems" align="start" compact @select="pickProject">
          <template #default="{ open, toggle }">
            <button class="wsChip" :aria-expanded="open" @click="toggle()">
              <DsIcon name="folder" :size="14" />
              <span class="chipLabel">{{ ui.composer.project || '选择项目' }}</span>
              <DsIcon name="chevron-down" :size="12" />
            </button>
          </template>
        </VMenu>

        <VMenu :items="modeItems" align="start" @select="pickMode">
          <template #default="{ open, toggle }">
            <button class="wsChip" :aria-expanded="open" @click="toggle()">
              <DsIcon name="personalization" :size="14" />
              <span>{{ modeShort }}</span>
              <DsIcon name="chevron-down" :size="12" />
            </button>
          </template>
        </VMenu>

        <VMenu :items="roundItems" align="start" compact @select="pickRounds">
          <template #default="{ open, toggle }">
            <button class="wsChip" :aria-expanded="open" @click="toggle()">
              <span>{{ ui.composer.rounds }} 轮</span>
              <DsIcon name="chevron-down" :size="12" />
            </button>
          </template>
        </VMenu>
      </div>

      <ComposerCard ref="composerEl" hero draft @drafted="onDrafted" />

      <div v-if="!health?.llm_configured" class="warn">
        <DsIcon name="warning" :size="14" />
        <span>LLM 未配置，先设置 API Key 才能运行</span>
        <button class="linkBtn" @click="ui.settingsPage = 'config'; ui.settingsFull = true">去设置</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/** 空态首页：居中品牌 + 同一个 composer 的 hero 变体。
 *  只放后端真支持的参数：项目名、范式（classic|dynamic|react）、轮数、模型覆盖。 */
import { computed, nextTick, ref } from 'vue'
import ComposerCard from './ComposerCard.vue'
import DsIcon from '../ui/DsIcon.vue'
import VMenu from '../ui/VMenu.vue'
import type { MenuItem } from '../ui/menuTypes'
import { useSessionStore } from '../../stores/sessions'
import { useUiStore } from '../../stores/ui'
import { useToastStore } from '../../stores/toast'

const store = useSessionStore()
const ui = useUiStore()
const toast = useToastStore()
const composerEl = ref<InstanceType<typeof ComposerCard>>()

const health = computed(() => store.health)

/** 后端 create 只认这三个 paradigm（server/api/sessions.py 的校验器）。
 *  说明按各自装配线如实写：classic=五角色 SOP，dynamic=单 RoleZero，react=队形不变换执行循环。 */
const MODES = [
  { key: 'classic', short: 'SOP 流程', label: 'SOP 流程（软件公司）', desc: 'PM→架构→排期→工程→QA 五角色按 SOP 路由表接力' },
  { key: 'dynamic', short: '动态组队', label: '主 Agent 动态组队', desc: '单个 RoleZero 工具循环，自己决定下一步调哪个工具' },
  { key: 'react', short: 'ReAct 队形', label: '经典队形 × ReAct', desc: '五角色不变，每人换成「思考—行动—观察」的 ReAct 循环' }
]
const modeShort = computed(
  () => MODES.find((m) => m.key === ui.composer.paradigm)?.short ?? MODES[0].short
)
const modeItems = computed<MenuItem[]>(() =>
  MODES.map((m) => ({ key: m.key, label: m.label, desc: m.desc, checked: ui.composer.paradigm === m.key }))
)

/** 项目候选与侧栏同源（store.projects()），不给它编一个假的下拉列表。
 *  实测 94 个名字里一大把是 8 位 hex（测试会话拿 id 当项目名），不藏它们，排后面。 */
const projectItems = computed<MenuItem[]>(() => {
  const cur = ui.composer.project
  const named = store.projects().map((p) => p.name)
  const list = cur && !named.includes(cur) ? [cur, ...named] : named
  const items: MenuItem[] = list
    .sort((a, b) => Number(/^[0-9a-f]{8,}$/i.test(a)) - Number(/^[0-9a-f]{8,}$/i.test(b)))
    .map((n) => ({ key: n, label: n, icon: 'folder', checked: cur === n }))
  items.push({ kind: 'sep' })
  items.push({ key: '__new', label: '新建项目', icon: 'project-add' })
  return items
})

const roundItems = computed<MenuItem[]>(() =>
  [1, 3, 5, 8].map((n) => ({ key: String(n), label: `${n} 轮`, checked: ui.composer.rounds === n }))
)

/** 新建项目就地输入（原来是 window.prompt，样式与产品口径都不对） */
const naming = ref(false)
const nameDraft = ref('')
const nameEl = ref<HTMLInputElement>()
let escCancel = false

function pickProject(it: MenuItem) {
  if (it.key !== '__new') {
    if (it.key) ui.composer.project = String(it.key)
    return
  }
  escCancel = false
  nameDraft.value = ''
  naming.value = true
  void nextTick(() => nameEl.value?.focus())
}

function pickMode(it: MenuItem) {
  if (it.key) ui.composer.paradigm = String(it.key)
}

function pickRounds(it: MenuItem) {
  const n = Number(it.key)
  if (n > 0) ui.composer.rounds = n
}

function cancelName() {
  escCancel = true
  naming.value = false
}

function commitName() {
  if (escCancel) {
    escCancel = false
    return
  }
  const v = nameDraft.value.trim()
  nameDraft.value = ''
  naming.value = false
  if (!v) return
  // 后端 create 见路径分隔就 422，这里先挡住，别让名字白填一遍
  if (/[\\/]/.test(v)) {
    toast.push('项目名不能包含路径分隔符', 'error')
    return
  }
  ui.composer.project = v
}

async function onDrafted(idea: string) {
  try {
    const s = await store.createSession({
      idea,
      project_name: ui.composer.project,
      n_round: ui.composer.rounds,
      paradigm: ui.composer.paradigm,
      llm: ui.composer.model ? { model: ui.composer.model } : {}
    })
    await store.start()
    toast.push(`已创建会话 ${s.id} 并开始运行`, 'success')
  } catch (e) {
    toast.push((e as Error).message, 'error')
    composerEl.value?.focus()
  }
}
</script>

<style scoped>
/* 类名不能叫 .hero：scoped 会把本组件 scope id 加到子组件根元素上，
   而 ComposerCard 的根就带 `hero` 修饰类，这套布局会整个泄漏到输入卡。 */
.heroShell {
  position: relative;
  flex: 1;
  min-height: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: auto;
  background: var(--dsw-alias-bg-base);
}

/* 参考项目用 feGaussianBlur 50 / #6187D8 @8% 的椭圆光晕 */
.glow {
  position: absolute;
  top: 50%;
  left: 50%;
  width: min(1051px, 90%);
  aspect-ratio: 1051 / 468;
  transform: translate(-50%, -42%);
  background: radial-gradient(ellipse at center, rgba(97, 135, 216, 0.08), transparent 62%);
  filter: blur(50px);
  pointer-events: none;
}

.inner {
  position: relative;
  display: flex;
  flex-direction: column;
  /* 源 .stack: stretch —— 卡与 chip 行占满栈宽，标题靠自己的 grid 居中 */
  align-items: stretch;
  gap: 12px;
  /* 780 是 composer 卡的目标宽度，内衬要算在外面，否则卡被挤成 748。
     纵向不给 padding：源 .root 只有横向内衬，栈靠 flex 居中精确落在视口中线。 */
  width: 100%;
  max-width: calc(780px + 32px);
  padding: 0 16px;
  box-sizing: border-box;
}

.brand {
  /* 参考项目 .headline：grid「34px 图标 / 标题 / 徽标」，26/32 wt500 居中 */
  display: grid;
  grid-template-columns: 34px auto auto;
  column-gap: 10px;
  align-items: center;
  justify-content: center;
  font-size: 26px;
  line-height: 32px;
  font-weight: 500;
  color: var(--dsw-alias-label-primary);
}

.brand > :first-child {
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.brand h1 {
  grid-row: 1;
  grid-column: 2;
  margin: 0;
  font-size: inherit;
  font-weight: inherit;
  line-height: inherit;
}

/* 徽标是骑在标题右上角的等宽小胶囊，不是跟着基线居中的标签 */
.badge {
  grid-row: 1;
  grid-column: 3;
  align-self: start;
  margin-top: 2px;
  margin-left: -3px;
  padding: 1px 7px 0;
  border: 1px solid var(--dsw-alias-interactive-bg-hover);
  border-radius: 24px;
  background: var(--dsw-alias-state-business-tertiary);
  color: var(--dsw-alias-label-primary-bluish);
  font-family: var(--ds-font-family-code);
  font-size: 12px;
  line-height: 18px;
  font-weight: 500;
}

.wsRow {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  min-width: 0;
  /* 源 .workspaceRow：卡上方那行左对齐，只有 padding-left 8px，不居中 */
  padding-left: 8px;
}

.wsChip {
  /* 参考项目 .workspace：静止无框透明，只在 hover/展开时上底色 */
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-height: 28px;
  max-width: min(100%, 360px);
  padding: 0 8px;
  border: none;
  border-radius: 16px;
  background: transparent;
  font-family: inherit;
  font-size: 13px;
  line-height: 20px;
  font-weight: 500;
  color: var(--dsw-alias-label-primary);
  cursor: pointer;
}

.wsChip:hover,
.wsChip[aria-expanded='true'] {
  background: var(--dsw-alias-interactive-bg-hover);
}

/* 就地新建项目：与 chip 同一颗 28 高胶囊，只是换成描边 + 输入 */
.wsInput {
  width: 200px;
  height: 28px;
  padding: 0 10px;
  border: 1px solid var(--dsw-alias-border-l2);
  border-radius: 16px;
  outline: none;
  background: var(--dsw-specific-menu);
  font-family: inherit;
  font-size: 13px;
  line-height: 20px;
  color: var(--dsw-alias-label-primary);
}

/* 源 .workspaceLabel：名字过长省略，不撑破这一行 */
.chipLabel {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.warn {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  border-radius: 12px;
  background: var(--dsw-alias-state-warn-tertiary);
  color: var(--dsw-alias-state-warn-label);
  font-size: 13px;
}

.linkBtn {
  border: none;
  background: transparent;
  font-family: inherit;
  font-size: 13px;
  color: var(--dsw-alias-state-business-primary);
  cursor: pointer;
  text-decoration: underline;
}
</style>
