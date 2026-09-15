<template>
  <div class="set-shell">
    <!-- 左侧：设置导航（米色，与主侧栏同风格） -->
    <aside class="set-side">
      <div class="set-back" @click="back">
        <Icon name="arrow-left" :size="16" />
        <span>返回应用</span>
      </div>
      <div class="set-nav">
        <div
          v-for="item in NAV"
          :key="item.key"
          class="set-nav-item"
          :class="{ active: ui.settingsPage === item.key }"
          @click="ui.settingsPage = item.key"
        >
          <Icon :name="item.icon" :size="16" />
          <span>{{ item.label }}</span>
        </div>
      </div>
    </aside>

    <!-- 右侧：内容区（顶部留空条 + 分隔线，与参考图一致） -->
    <main class="set-main">
      <div class="set-header">
        <template v-if="ui.settingsPage === 'profile'">
          <span>个人资料</span>
          <span class="grow" />
          <span class="hact"><Icon name="lock" :size="13" /> 私有</span>
          <span class="hact" style="margin-left: 18px" @click="editProfile"><Icon name="pencil" :size="13" /> Edit</span>
        </template>
      </div>
      <div class="set-scroll">
        <component :is="pageComp" />
      </div>
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useMessage } from 'naive-ui'
import { useSettingsStore } from '../stores/settings'
import { useUiStore } from '../stores/ui'
import Icon from './Icon.vue'
import ArchivedPage from './settings/ArchivedPage.vue'
import AppearancePage from './settings/AppearancePage.vue'
import BrowserPage from './settings/BrowserPage.vue'
import ComputerPage from './settings/ComputerPage.vue'
import ConfigPage from './settings/ConfigPage.vue'
import ConnectionsPage from './settings/ConnectionsPage.vue'
import EnvPage from './settings/EnvPage.vue'
import GeneralPage from './settings/GeneralPage.vue'
import GitPage from './settings/GitPage.vue'
import HooksPage from './settings/HooksPage.vue'
import McpPage from './settings/McpPage.vue'
import PersonalizePage from './settings/PersonalizePage.vue'
import ProfilePage from './settings/ProfilePage.vue'
import ShortcutsPage from './settings/ShortcutsPage.vue'
import WorktreesPage from './settings/WorktreesPage.vue'

const ui = useUiStore()
const settings = useSettingsStore()
const message = useMessage()

const NAV = [
  { key: 'general', icon: 'gear', label: '常规' },
  { key: 'profile', icon: 'user', label: '个人资料' },
  { key: 'appearance', icon: 'sun', label: '外观' },
  { key: 'config', icon: 'sliders', label: '配置' },
  { key: 'personalize', icon: 'palette', label: '个性化' },
  { key: 'shortcuts', icon: 'keyboard', label: '键盘快捷键' },
  { key: 'mcp', icon: 'plug', label: 'MCP 服务器' },
  { key: 'hooks', icon: 'anchor', label: '钩子' },
  { key: 'connections', icon: 'globe', label: '连接' },
  { key: 'git', icon: 'branch', label: 'Git' },
  { key: 'env', icon: 'monitor', label: '环境' },
  { key: 'worktrees', icon: 'branch', label: '工作树' },
  { key: 'browser', icon: 'window', label: '浏览器' },
  { key: 'computer', icon: 'cursor', label: '电脑操控' },
  { key: 'archived', icon: 'archive', label: '已归档对话' }
]

const PAGES: Record<string, any> = {
  general: GeneralPage,
  profile: ProfilePage,
  appearance: AppearancePage,
  config: ConfigPage,
  personalize: PersonalizePage,
  shortcuts: ShortcutsPage,
  mcp: McpPage,
  hooks: HooksPage,
  connections: ConnectionsPage,
  git: GitPage,
  env: EnvPage,
  worktrees: WorktreesPage,
  browser: BrowserPage,
  computer: ComputerPage,
  archived: ArchivedPage
}

const pageComp = computed(() => PAGES[ui.settingsPage] || GeneralPage)

function back() {
  ui.settingsFull = false
}

function editProfile() {
  message.info('演示环境：资料编辑未开放')
}

// 设置项持久化到 localStorage
onMounted(() =>
  settings.$subscribe(() => {
    settings.persist()
  })
)
</script>
