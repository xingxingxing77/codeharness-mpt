<template>
  <div class="ic-wrap" :class="{ home: mode === 'home' }">
    <!-- 沙盒提示胶囊条（仅首页，参考图"设置智能体沙盒以继续"） -->
    <div v-if="mode === 'home'" class="sandbox">
      <Icon name="lock" :size="15" />
      <span class="s-text">{{ sandboxText }}</span>
      <span class="grow" />
      <button class="dark-btn" @click="ui.showCreate = true">设置</button>
    </div>

    <!-- 输入卡 -->
    <div class="icard">
      <textarea
        ref="ta"
        v-model="content"
        rows="1"
        :placeholder="placeholder"
        :disabled="mode === 'follow' && !store.isRunning && store.status !== 'created'"
        @input="grow"
        @keydown="onKey"
      />
      <div class="irow">
        <button class="icon-btn" title="高级设置（预算 / LLM 覆盖）" @click="openAdvanced">
          <Icon name="plus" :size="18" />
        </button>

        <!-- 发送对象（蓝色，参考图"自动审查"位） -->
        <div class="target" @click.stop="menuTarget = !menuTarget">
          <Icon name="shield" :size="15" />
          <span class="t-label">{{ targetLabel }}</span>
          <Icon name="chevron-down" :size="13" class="caret" />
          <div v-if="menuTarget" class="menu-panel up">
            <div class="menu-label">发送给</div>
            <div v-for="t in targets" :key="t.value" class="menu-item" @click="pickTarget(t.value)">
              <span class="grow">{{ t.label }}</span>
              <Icon v-if="target === t.value" name="check" :size="14" class="tick" />
            </div>
          </div>
        </div>

        <span class="grow" />

        <span v-if="mode === 'home'" class="model-txt">{{ modelShort }} <span class="zh">中</span></span>
        <button class="icon-btn" disabled title="语音输入暂未开放">
          <Icon name="mic" :size="17" />
        </button>
        <button class="send" :disabled="!canSend || sending" @click="send">
          <Icon name="arrow-up" :size="18" />
        </button>
      </div>
    </div>

    <!-- chips 行（仅首页：项目 / 启动模式 / 协作轮数） -->
    <div v-if="mode === 'home'" class="chips">
      <div class="chip" @click.stop="menuProject = !menuProject">
        <Icon name="folder" :size="15" />
        {{ ui.composer.project }}
        <Icon name="chevron-down" :size="13" class="caret" />
      </div>
      <div class="chip" @click.stop="menuMode = !menuMode">
        <Icon name="monitor" :size="15" />
        本地模式
        <Icon name="chevron-down" :size="13" class="caret" />
      </div>
      <div class="chip" @click.stop="menuRounds = !menuRounds">
        <Icon name="clock" :size="15" />
        {{ ui.composer.rounds }} 轮
        <Icon name="chevron-down" :size="13" class="caret" />
      </div>

      <!-- 项目下拉（向下，带搜索） -->
      <div v-if="menuProject" class="menu-panel proj-menu" @click.stop>
        <div class="menu-search">
          <Icon name="search" :size="14" />
          <input v-model="projFilter" placeholder="搜索项目" />
        </div>
        <div
          v-for="p in projOptions"
          :key="p"
          class="menu-item"
          @click="pickProject(p)"
        >
          <Icon name="folder" :size="15" />
          <span class="grow">{{ p }}</span>
          <Icon v-if="ui.composer.project === p" name="check" :size="14" class="tick" />
        </div>
        <div class="menu-divider" />
        <div class="menu-item" @click="addProject">
          <Icon name="folder" :size="15" />
          <span class="grow">添加新项目</span>
          <span class="sub">›</span>
        </div>
        <div class="menu-item" @click="pickProject('不使用项目')">
          <Icon name="folder" :size="15" />
          <span class="grow">不使用项目</span>
        </div>
      </div>

      <!-- 启动模式下拉（向上，参考图"启动模式"） -->
      <div v-if="menuMode" class="menu-panel mode-menu" @click.stop>
        <div class="menu-label">启动模式</div>
        <div class="menu-item" @click="pickMode('在本地处理')">
          <Icon name="monitor" :size="15" />
          <span class="grow">在本地处理</span>
          <Icon v-if="ui.composer.mode === '在本地处理'" name="check" :size="14" class="tick" />
        </div>
        <div class="menu-item" @click="pickMode('新工作树')">
          <Icon name="branch" :size="15" />
          <span class="grow">新工作树</span>
        </div>
        <div class="menu-item disabled">
          <Icon name="globe" :size="15" />
          <span class="grow">发送至云端</span>
        </div>
        <div class="menu-divider" />
        <div class="menu-item" @click="usage">
          <Icon name="clock" :size="15" />
          <span class="grow">剩余用量</span>
          <span class="sub">›</span>
        </div>
      </div>

      <!-- 协作轮数下拉（向上，参考图"分支"菜单位） -->
      <div v-if="menuRounds" class="menu-panel rounds-menu" @click.stop>
        <div class="menu-search">
          <Icon name="clock" :size="14" />
          <span>协作轮数</span>
        </div>
        <div v-for="r in [1, 3, 5, 10, 20]" :key="r" class="menu-item" @click="pickRounds(r)">
          <Icon name="branch" :size="15" />
          <span class="grow">{{ r }} 轮</span>
          <Icon v-if="ui.composer.rounds === r" name="check" :size="14" class="tick" />
        </div>
        <div class="menu-divider" />
        <div class="menu-item" @click="openAdvanced">
          <Icon name="plus" :size="15" />
          <span class="grow">更多设置（预算 / LLM）...</span>
        </div>
      </div>
    </div>

    <!-- 菜单打开时的透明遮罩：点任意处关闭 -->
    <div v-if="anyMenu" class="overlay" @click="closeMenus" />
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { useMessage } from 'naive-ui'
import { useSessionStore } from '../stores/sessions'
import { useUiStore } from '../stores/ui'
import Icon from './Icon.vue'

const props = defineProps<{ mode: 'home' | 'follow' }>()

const store = useSessionStore()
const ui = useUiStore()
const message = useMessage()

const content = ref('')
const sending = ref(false)
const target = ref('')
const ta = ref<HTMLTextAreaElement>()

const menuTarget = ref(false)
const menuProject = ref(false)
const menuMode = ref(false)
const menuRounds = ref(false)
const projFilter = ref('')

const anyMenu = computed(() => menuTarget.value || menuProject.value || menuMode.value || menuRounds.value)
const projFilterClean = computed(() => projFilter.value.trim())

const targets = [
  { label: 'TeamLeader（自动调度）', value: '' },
  { label: 'ProductManager', value: 'ProductManager' },
  { label: 'Architect', value: 'Architect' },
  { label: 'Engineer2', value: 'Engineer2' },
  { label: 'DataAnalyst', value: 'DataAnalyst' }
]
const targetLabel = computed(() =>
  target.value === '' ? '自动审查' : targets.find((t) => t.value === target.value)!.label
)

const modelShort = computed(() => {
  const m = store.health?.model || 'LLM'
  return m.length > 18 ? m.slice(0, 17) + '…' : m
})

const sandboxText = computed(() =>
  store.health?.llm_configured ? `LLM 已就绪：${store.health.model}` : '设置智能体沙盒以继续'
)

const placeholder = computed(() =>
  props.mode === 'home'
    ? '随心输入'
    : store.isRunning
      ? '要求后续变更'
      : store.status === 'created'
        ? '输入内容并回车以开始运行'
        : '会话未在运行中'
)

const canSend = computed(() => {
  if (!content.value.trim()) return false
  if (props.mode === 'home') return true
  return store.isRunning || store.status === 'created'
})

const projOptions = computed(() => {
  const names = store.projects().map((p) => p.name)
  const all = ['MetaGPT', ...names.filter((n) => n !== 'MetaGPT')]
  return projFilterClean.value ? all.filter((n) => n.includes(projFilterClean.value)) : all
})

function closeMenus() {
  menuTarget.value = menuProject.value = menuMode.value = menuRounds.value = false
}

function pickTarget(v: string) {
  target.value = v
  menuTarget.value = false
}

function pickProject(p: string) {
  ui.composer.project = p
  menuProject.value = false
}

function pickMode(m: string) {
  ui.composer.mode = m
  menuMode.value = false
}

function pickRounds(r: number) {
  ui.composer.rounds = r
  menuRounds.value = false
}

function addProject() {
  menuProject.value = false
  ui.createIdea = content.value
  ui.showCreate = true
}

function openAdvanced() {
  ui.createIdea = content.value
  ui.showCreate = true
}

function usage() {
  menuMode.value = false
  const c = store.cost || {}
  message.info(`已用 $${(c.total_cost ?? 0).toFixed(3)}${c.max_budget ? ` / 预算 $${c.max_budget}` : ''}`)
}

function grow() {
  const el = ta.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 160) + 'px'
}

function onKey(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    send()
  }
}

async function send() {
  const idea = content.value.trim()
  if (!idea || sending.value) return
  sending.value = true
  try {
    if (props.mode === 'home') {
      if (store.health && !store.health.llm_configured) {
        message.warning('请先配置 LLM API Key')
        ui.showCreate = true
        return
      }
      await store.createSession({
        idea,
        project_name: ui.composer.project === '不使用项目' ? undefined : ui.composer.project,
        investment: 3.0,
        n_round: ui.composer.rounds,
        llm: {}
      })
      await store.start()
    } else {
      if (store.status === 'created') await store.start()
      await store.sendChat(idea, target.value)
    }
    content.value = ''
    nextTick(grow)
  } catch (e: any) {
    message.error(e.message || '发送失败')
  } finally {
    sending.value = false
  }
}
</script>

<style scoped>
.ic-wrap {
  position: relative;
  width: 100%;
  margin: 0 auto;
}

.grow {
  flex: 1;
}

/* 沙盒胶囊条 */
.sandbox {
  display: flex;
  align-items: center;
  gap: 9px;
  height: 44px;
  padding: 0 8px 0 16px;
  border-radius: 999px;
  background: var(--card);
  border: 1px solid var(--card-border);
  box-shadow: 0 4px 16px rgba(40, 34, 16, 0.06);
  margin-bottom: 14px;
  color: #3f3d38;
  font-size: 13.5px;
}

.sandbox svg {
  color: #7a7568;
}

.dark-btn {
  border: none;
  background: var(--dark-btn);
  color: #fff;
  font-size: 13px;
  padding: 6px 14px;
  border-radius: 999px;
  cursor: pointer;
}

.dark-btn:hover {
  background: #1d1c1a;
}

/* 输入卡 */
.icard {
  background: var(--card);
  border: 1px solid var(--card-border);
  border-radius: 16px;
  box-shadow: 0 10px 30px rgba(40, 34, 16, 0.08);
  padding: 13px 14px 10px;
}

.icard textarea {
  width: 100%;
  border: none;
  outline: none;
  resize: none;
  background: transparent;
  font-size: 14px;
  line-height: 1.55;
  color: var(--text);
  font-family: inherit;
  max-height: 160px;
}

.icard textarea::placeholder {
  color: #b8b2a4;
}

.irow {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 8px;
}

.icon-btn {
  border: none;
  background: none;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #6b675e;
  cursor: pointer;
}

.icon-btn:hover:not(:disabled) {
  background: rgba(60, 52, 24, 0.06);
}

.icon-btn:disabled {
  opacity: 0.45;
  cursor: default;
}

.target {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--blue);
  font-size: 13px;
  cursor: pointer;
  padding: 4px 7px;
  border-radius: 8px;
}

.target:hover {
  background: rgba(61, 111, 214, 0.07);
}

.t-label {
  white-space: nowrap;
  max-width: 150px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.target .caret {
  color: var(--blue);
  opacity: 0.7;
}

.model-txt {
  font-size: 13px;
  color: #4a4843;
  white-space: nowrap;
}

.model-txt .zh {
  color: var(--text-3);
}

.send {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  border: none;
  background: var(--dark-btn);
  color: #fff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  flex: none;
}

.send:hover:not(:disabled) {
  background: #1d1c1a;
}

.send:disabled {
  opacity: 0.35;
  cursor: default;
}

/* chips 行 */
.chips {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 2px;
  background: var(--chip-bg);
  border-radius: 12px;
  padding: 4px 8px;
  margin-top: 12px;
}

/* 下拉定位 */
.menu-panel.up {
  bottom: calc(100% + 6px);
  left: 0;
}

.proj-menu {
  top: calc(100% + 6px);
  left: 0;
  max-height: 320px;
  overflow: auto;
}

.mode-menu {
  bottom: calc(100% + 10px);
  left: 118px;
  min-width: 218px;
}

.rounds-menu {
  bottom: calc(100% + 10px);
  left: 218px;
  min-width: 230px;
}

.overlay {
  position: fixed;
  inset: 0;
  z-index: 55;
}

.menu-panel {
  z-index: 60;
}
</style>
