<template>
  <div class="topbar">
    <!-- 首页态：左 Plus，右模型胶囊 + 面板开关 -->
    <template v-if="home">
      <button class="plus-pill" @click="upgrade">
        <Icon name="sparkle" :size="13" />
        获取 Plus
      </button>
      <span class="grow" />
    </template>

    <!-- 会话态：状态点 + 标题 + 更多菜单 -->
    <template v-else>
      <span class="status-dot" :class="store.status" :title="store.status" />
      <span class="title" :title="store.current?.idea">{{ store.current?.idea }}</span>
      <div class="dots-wrap">
        <button class="icon-btn" @click.stop="menuMore = !menuMore">
          <Icon name="dots" :size="18" />
        </button>
        <div v-if="menuMore" class="menu-panel more" @click.stop>
          <div
            v-if="store.isRunning"
            class="menu-item"
            @click="doStop"
          >
            <Icon name="x" :size="15" />
            <span class="grow">停止运行</span>
          </div>
          <div
            v-else-if="['created', 'finished', 'stopped', 'failed'].includes(store.status)"
            class="menu-item"
            @click="restart"
          >
            <Icon name="arrow-up" :size="15" />
            <span class="grow">{{ store.status === 'created' ? '开始运行' : '用相同需求新建会话' }}</span>
          </div>
          <div class="menu-divider" />
          <div class="menu-item" @click="goHome">
            <Icon name="edit" :size="15" />
            <span class="grow">新对话</span>
          </div>
          <div class="menu-item" @click="copyId">
            <Icon name="copy" :size="15" />
            <span class="grow">复制会话 ID</span>
          </div>
        </div>
      </div>
      <span class="grow" />
    </template>

    <!-- 右侧公共区 -->
    <span v-if="!home" class="cost" :title="costTip">
      ${{ (cost.total_cost ?? 0).toFixed(3) }}
    </span>
    <button v-if="!home" class="icon-btn" title="审查代码变更" @click="openReview">
      <Icon name="sliders" :size="18" />
    </button>
    <button v-if="home" class="model-pill" @click.stop="menuModel = !menuModel">
      <Icon name="sparkle" :size="14" />
      <span class="m-name">{{ modelShort }}</span>
      <Icon name="chevron-down" :size="13" />
      <div v-if="menuModel" class="menu-panel model-menu" @click.stop>
        <div class="menu-item static">
          <Icon name="sparkle" :size="15" />
          <span class="grow">{{ store.health?.model || 'LLM 未配置' }}</span>
        </div>
        <div class="menu-item static">
          <Icon name="globe" :size="15" />
          <span class="grow sub">{{ store.health?.base_url }}</span>
        </div>
        <div class="menu-divider" />
        <div class="menu-item" @click="openSettings">
          <Icon name="gear" :size="15" />
          <span class="grow">设置</span>
        </div>
      </div>
    </button>
    <button class="icon-btn" title="终端面板" @click="ui.terminalOpen = !ui.terminalOpen">
      <Icon name="monitor" :size="18" />
    </button>
    <button class="icon-btn" title="工具面板" @click="ui.rightPanel = !ui.rightPanel">
      <Icon name="panel-right" :size="18" />
    </button>
    <div v-if="menuMore || menuModel" class="overlay" @click="closeMenus" />
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useMessage } from 'naive-ui'
import { useSessionStore } from '../stores/sessions'
import { useUiStore } from '../stores/ui'
import Icon from './Icon.vue'

defineProps<{ home?: boolean }>()

const store = useSessionStore()
const ui = useUiStore()
const message = useMessage()

const menuMore = ref(false)
const menuModel = ref(false)

const cost = computed(() => store.cost || {})
const costTip = computed(
  () => `prompt ${cost.value.total_prompt_tokens ?? 0} / completion ${cost.value.total_completion_tokens ?? 0} tokens`
)

const modelShort = computed(() => {
  const m = store.health?.model || 'LLM'
  return m.length > 16 ? m.slice(0, 15) + '…' : m
})

function closeMenus() {
  menuMore.value = menuModel.value = false
}

function openReview() {
  ui.rightPanel = true
  ui.rightView = 'review'
}

function openSettings() {
  menuModel.value = false
  ui.settingsPage = 'general'
  ui.settingsFull = true
}

function upgrade() {
  message.info('演示环境：升级入口未开放')
}

function goHome() {
  menuMore.value = false
  store.goHome()
}

async function copyId() {
  menuMore.value = false
  try {
    await navigator.clipboard.writeText(store.currentId)
    message.success('已复制')
  } catch {
    message.error('复制失败')
  }
}

async function doStop() {
  menuMore.value = false
  await store.stop()
}

async function restart() {
  menuMore.value = false
  const cur = store.current
  if (!cur) return
  if (cur.status !== 'created') {
    await store.createSession({
      idea: cur.idea,
      project_name: '',
      n_round: cur.n_round,
      llm: cur.llm_override
    })
    await store.start()
    message.info('已用相同需求创建新会话')
    return
  }
  await store.start()
}
</script>

<style scoped>
.topbar {
  position: relative;
  height: 52px;
  flex: none;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 16px;
}

.grow {
  flex: 1;
}

.plus-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: none;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 12.5px;
  padding: 6px 12px;
  border-radius: 999px;
  cursor: pointer;
}

.plus-pill:hover {
  background: #e9e1fc;
}

.model-pill {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: none;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 12.5px;
  padding: 6px 10px;
  border-radius: 10px;
  cursor: pointer;
}

.m-name {
  max-width: 140px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.model-menu {
  top: calc(100% + 6px);
  right: 0;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex: none;
}

.status-dot.running {
  background: #58a977;
  animation: pulse 1.4s infinite;
}

.status-dot.awaiting_human,
.status-dot.stopping {
  background: #d9a13f;
}

.status-dot.created {
  background: #7d9bf2;
}

.status-dot.finished,
.status-dot.stopped {
  background: #b6b2a6;
}

.status-dot.failed {
  background: #d96565;
}

@keyframes pulse {
  50% {
    opacity: 0.35;
  }
}

.title {
  font-size: 15px;
  font-weight: 500;
  color: var(--text);
  max-width: 44%;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.dots-wrap {
  position: relative;
}

.more {
  top: calc(100% + 6px);
  left: 0;
}

.icon-btn {
  position: relative;
  border: none;
  background: none;
  width: 30px;
  height: 30px;
  border-radius: 8px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #55524a;
  cursor: pointer;
  flex: none;
}

.icon-btn:hover {
  background: rgba(60, 52, 24, 0.06);
}

.cost {
  font-size: 12px;
  color: var(--text-3);
  white-space: nowrap;
}

.menu-item.static {
  cursor: default;
}

.menu-item.static:hover {
  background: none;
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
