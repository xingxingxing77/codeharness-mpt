<template>
  <div class="hero">
    <div class="glow" aria-hidden="true" />
    <div class="inner">
      <div class="brand">
        <DsIcon name="sparkle" :size="34" />
        <h1>把想法交给智能体</h1>
        <span class="badge">预览</span>
      </div>

      <div class="wsRow">
        <VMenu :items="projectItems" align="start" compact @select="pickProject">
          <template #default="{ open, toggle }">
            <button class="wsChip" :aria-expanded="open" @click="toggle()">
              <DsIcon name="folder" :size="14" />
              <span>{{ ui.composer.project || '选择项目' }}</span>
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
        <button class="wsChip" :class="{ on: planMode }" @click="planMode = !planMode">
          <DsIcon name="checklist" :size="14" />
          <span>先出计划</span>
        </button>
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
 *  只放后端真支持的三个参数：项目名、轮数、是否先出计划（paradigm=dynamic）。 */
import { computed, ref } from 'vue'
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
const planMode = computed({
  get: () => ui.composer.paradigm === 'dynamic',
  set: (v: boolean) => (ui.composer.paradigm = v ? 'dynamic' : 'classic')
})

/** 项目候选来自已有会话的 project_name，不给它编一个假的下拉列表 */
const projectItems = computed<MenuItem[]>(() => {
  const names = [...new Set(store.sessions.map((s) => s.project_name).filter(Boolean))].slice(0, 12)
  const items = names.map((n) => ({
    key: n,
    label: n,
    checked: ui.composer.project === n
  }))
  items.push({ kind: 'sep' })
  items.push({ key: '__new', label: '用新名字（下一个输入框填）', checked: false })
  return items
})

const roundItems = computed<MenuItem[]>(() =>
  [1, 3, 5, 8].map((n) => ({ key: String(n), label: `${n} 轮`, checked: ui.composer.rounds === n }))
)

function pickProject(it: MenuItem) {
  if (it.key === '__new') {
    const v = window.prompt('新项目名（不能含路径分隔符）', ui.composer.project)
    if (v) ui.composer.project = v.trim()
    return
  }
  if (it.key) ui.composer.project = String(it.key)
}

function pickRounds(it: MenuItem) {
  const n = Number(it.key)
  if (n > 0) ui.composer.rounds = n
}

async function onDrafted(idea: string) {
  try {
    const s = await store.createSession({
      idea,
      project_name: ui.composer.project,
      n_round: ui.composer.rounds,
      paradigm: ui.composer.paradigm
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
.hero {
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
  align-items: center;
  gap: 12px;
  /* 780 是 composer 卡的目标宽度，内衬要算在外面，否则卡被挤成 748 */
  width: 100%;
  max-width: calc(780px + 32px);
  padding: 32px 16px 48px;
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
  justify-content: center;
  /* figma 75:8208 workspace row：卡上方那一行左右各留 8px */
  padding: 0 8px;
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

.wsChip.on {
  color: var(--dsw-alias-state-business-primary);
  background: var(--dsw-alias-interactive-bg-hover);
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
