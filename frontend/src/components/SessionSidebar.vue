<template>
  <div class="root">
    <!-- 顶部导航 -->
    <div class="nav">
      <div class="nav-item" @click="newChat">
        <Icon name="edit" :size="16" />
        <span>新对话</span>
      </div>
      <div class="nav-item">
        <Icon name="search" :size="16" />
        <span>搜索</span>
      </div>
      <div class="nav-item">
        <Icon name="grid" :size="16" />
        <span>插件</span>
      </div>
      <div class="nav-item">
        <Icon name="clock" :size="16" />
        <span>自动化</span>
      </div>
      <div class="nav-item">
        <Icon name="phone" :size="16" />
        <span>MetaGPT 移动版</span>
      </div>
    </div>

    <!-- 滚动区：项目 / 对话 -->
    <div class="scroll">
      <div class="sec">项目</div>
      <template v-for="p in store.projects()" :key="p.name">
        <div class="proj" @click="toggle(p.name)">
          <Icon :name="openSet.has(p.name) ? 'folder-open' : 'folder'" :size="16" />
          <span class="pname">{{ p.name }}</span>
        </div>
        <template v-if="openSet.has(p.name)">
          <div
            v-for="c in p.chats"
            :key="c.id"
            class="chat-item sub"
            :class="{ active: c.id === store.currentId }"
            @click="store.select(c.id)"
          >
            <span class="ctitle">{{ title(c) }}</span>
            <span class="time">{{ rel(c.created_at) }}</span>
          </div>
          <div v-if="!p.chats.length" class="none sub">暂无对话</div>
        </template>
      </template>

      <div class="sec">对话</div>
      <div
        v-for="c in store.looseChats()"
        :key="c.id"
        class="chat-item"
        :class="{ active: c.id === store.currentId }"
        @click="store.select(c.id)"
      >
        <span class="ctitle">{{ title(c) }}</span>
        <span class="time">{{ rel(c.created_at) }}</span>
      </div>
      <div v-if="!store.looseChats().length" class="none">暂无对话</div>
    </div>

    <!-- 底部：设置 + 升级 -->
    <div class="bottom">
      <div v-if="auth.enabled && auth.user" class="nav-item" @click.stop="doLogout" title="退出登录">
        <Icon name="user" :size="16" />
        <span class="grow">{{ auth.user }}</span>
        <span class="sub">退出</span>
      </div>
      <div class="nav-item" @click.stop="ui.settingsMenuOpen = !ui.settingsMenuOpen">
        <Icon name="gear" :size="16" />
        <span>设置</span>
      </div>
      <span class="grow" />
      <button class="upgrade" @click="upgrade">升级</button>

      <!-- 设置弹出面板（向上） -->
      <div v-if="ui.settingsMenuOpen" class="menu-panel settings" @click.stop>
        <div class="s-row head">
          <Icon name="user" :size="15" />
          <span>MetaGPT Studio</span>
        </div>
        <div class="s-row">
          <Icon name="sparkle" :size="15" />
          <span class="grow">{{ health?.llm_configured ? 'LLM 已配置' : 'LLM 未配置' }}</span>
          <span class="sub">{{ health?.model }}</span>
        </div>
        <div v-if="!health?.llm_configured" class="s-row" @click="fixLLM">
          <Icon name="key" :size="15" />
          <span class="grow">配置 API Key</span>
          <Icon name="external" :size="13" />
        </div>
        <div class="menu-divider" />
        <div class="s-row" @click="openSettings('general')">
          <Icon name="gear" :size="15" />
          <span class="grow">设置</span>
          <span class="sub">Ctrl+,</span>
        </div>
        <div class="s-row" @click="openSettings('profile')">
          <Icon name="user" :size="15" />
          <span class="grow">个人资料</span>
        </div>
        <div class="s-row">
          <Icon name="coins" :size="15" />
          <span class="grow">已用</span>
          <span class="sub">¥{{ (store.cost?.total_cost ?? 0).toFixed(3) }}</span>
        </div>
        <div class="menu-divider" />
        <div class="s-row">
          <Icon name="logout" :size="15" />
          <span>退出登录</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, watch, onMounted, onBeforeUnmount } from 'vue'
import { useMessage } from 'naive-ui'
import { useSessionStore } from '../stores/sessions'
import { useUiStore } from '../stores/ui'
import { useAuthStore } from '../stores/auth'
import type { Session } from '../types'
import Icon from './Icon.vue'

const store = useSessionStore()
const ui = useUiStore()
const auth = useAuthStore()
const message = useMessage()

async function doLogout() {
  await auth.logout()
  store.goHome()
  message.success('已退出登录')
}
const health = computed(() => store.health)

const openSet = reactive(new Set<string>())
// 会话异步加载后自动展开所有项目（新项目也默认展开）
watch(
  () => store.sessions,
  () => {
    for (const p of store.projects()) openSet.add(p.name)
  },
  { immediate: true }
)

function toggle(name: string) {
  openSet.has(name) ? openSet.delete(name) : openSet.add(name)
}

function newChat() {
  store.goHome()
}

function title(s: Session) {
  return s.idea || s.project_name || s.id
}

function rel(iso: string): string {
  if (!iso) return ''
  const ms = Date.now() - new Date(iso).getTime()
  const day = 86400000
  if (ms < day) return '今天'
  if (ms < 7 * day) return `${Math.floor(ms / day)} 天`
  if (ms < 30 * day) return `${Math.floor(ms / (7 * day))} 周`
  return `${Math.floor(ms / (30 * day))} 个月`
}

function toggleTheme() {
  ui.theme = ui.theme === 'dark' ? 'light' : 'dark'
}

function openSettings(page: string) {
  ui.settingsMenuOpen = false
  ui.settingsPage = page
  ui.settingsFull = true
}

function fixLLM() {
  ui.settingsMenuOpen = false
  ui.showCreate = true
}

function upgrade() {
  message.info('演示环境：升级入口未开放')
}

function onGlobalClick() {
  if (ui.settingsMenuOpen) ui.settingsMenuOpen = false
}

onMounted(() => document.addEventListener('click', onGlobalClick))
onBeforeUnmount(() => document.removeEventListener('click', onGlobalClick))
</script>

<style scoped>
.root {
  height: 100%;
  display: flex;
  flex-direction: column;
  color: var(--sb-text);
}

.nav {
  padding: 12px 14px 4px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 32px;
  padding: 0 9px;
  border-radius: 8px;
  font-size: 13.5px;
  cursor: pointer;
  color: var(--sb-text);
  white-space: nowrap;
}

.nav-item:hover {
  background: var(--sb-hover);
}

.nav-item svg {
  color: #6f6957;
  flex: none;
}

.scroll {
  flex: 1;
  overflow: auto;
  padding: 4px 14px 12px;
}

.sec {
  font-size: 12px;
  color: var(--sb-muted);
  padding: 18px 9px 6px;
}

.proj {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 32px;
  padding: 0 9px;
  border-radius: 8px;
  font-size: 13.5px;
  cursor: pointer;
}

.proj:hover {
  background: var(--sb-hover);
}

.proj svg {
  color: #6f6957;
  flex: none;
}

.chat-item {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 30px;
  padding: 0 9px;
  border-radius: 8px;
  font-size: 13px;
  cursor: pointer;
  color: #4c493f;
}

.chat-item:hover {
  background: var(--sb-hover);
}

.chat-item.active {
  background: var(--sb-active);
}

.chat-item.sub {
  padding-left: 33px;
}

.ctitle {
  flex: 1;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.time {
  flex: none;
  font-size: 11.5px;
  color: var(--sb-muted);
}

.none {
  font-size: 12.5px;
  color: var(--sb-muted);
  padding: 6px 9px;
}

.none.sub {
  padding-left: 33px;
}

.bottom {
  position: relative;
  display: flex;
  align-items: center;
  padding: 10px 14px 14px;
}

.grow {
  flex: 1;
}

.upgrade {
  border: none;
  background: #fffdf8;
  color: #4c493f;
  font-size: 12.5px;
  padding: 5px 14px;
  border-radius: 999px;
  cursor: pointer;
  box-shadow: 0 1px 4px rgba(70, 58, 20, 0.14);
}

.upgrade:hover {
  background: #fff;
}

.settings {
  position: absolute;
  left: 10px;
  bottom: 52px;
  min-width: 264px;
  padding: 8px;
}

.s-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: 8px;
  font-size: 13px;
  cursor: pointer;
  color: var(--text);
}

.s-row:hover {
  background: #f5f4f1;
}

.s-row.head {
  color: var(--text-3);
  cursor: default;
}

.s-row.head:hover {
  background: none;
}

.s-row svg {
  color: #7a7568;
  flex: none;
}

.s-row .sub {
  color: var(--text-3);
  font-size: 12px;
  max-width: 110px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
</style>
