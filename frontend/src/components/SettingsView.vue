<template>
  <VModal :open="ui.settingsFull" title="设置" @close="ui.settingsFull = false">
    <nav class="side">
      <div class="navTitle">设置</div>
      <div class="navList">
        <button
          v-for="item in NAV"
          :key="item.key"
          class="navCell"
          :class="{ active: ui.settingsPage === item.key }"
          :aria-current="ui.settingsPage === item.key ? 'page' : undefined"
          @click="ui.settingsPage = item.key"
        >
          <Icon :name="item.icon" :size="16" />
          <span class="navLabel">{{ item.label }}</span>
        </button>
      </div>
    </nav>

    <main class="content">
      <div class="header">
        <span />
        <button class="close" data-autofocus aria-label="关闭" @click="ui.settingsFull = false">
          <Icon name="x" :size="14" />
          <span class="hiddenLabel">关闭</span>
        </button>
      </div>
      <div class="options">
        <component :is="pageComp" />
      </div>
    </main>
  </VModal>
</template>

<script setup lang="ts">
/** 设置：从「整页接管」改成参考项目那样的居中弹窗（800 宽、左 188 nav、面板高度
 *  对所有分节恒定，切分节时不会在小指针底下伸缩）。 */
import { computed, onMounted } from 'vue'
import VModal from './ui/VModal.vue'
import Icon from './Icon.vue'
import { useSettingsStore } from '../stores/settings'
import { useUiStore } from '../stores/ui'
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
import UsagePage from './settings/UsagePage.vue'
import WorktreesPage from './settings/WorktreesPage.vue'

const ui = useUiStore()
const settings = useSettingsStore()

const NAV = [
  { key: 'general', icon: 'gear', label: '常规' },
  { key: 'usage', icon: 'coins', label: '用量' },
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

const PAGES: Record<string, unknown> = {
  general: GeneralPage, usage: UsagePage, profile: ProfilePage, appearance: AppearancePage,
  config: ConfigPage, personalize: PersonalizePage, shortcuts: ShortcutsPage, mcp: McpPage,
  hooks: HooksPage, connections: ConnectionsPage, git: GitPage, env: EnvPage,
  worktrees: WorktreesPage, browser: BrowserPage, computer: ComputerPage, archived: ArchivedPage
}

const pageComp = computed(() => PAGES[ui.settingsPage] || GeneralPage)

// 设置项持久化到 localStorage
onMounted(() =>
  settings.$subscribe(() => {
    settings.persist()
  })
)
</script>

<style scoped>
.side {
  flex: none;
  display: flex;
  flex-direction: column;
  gap: 18px;
  width: 188px;
  padding: 22px 12px 0;
  box-sizing: border-box;
}

.navTitle {
  padding: 0 12px;
  font-size: 16px;
  font-weight: 500;
  line-height: 24px;
  color: var(--dsw-alias-label-primary);
}

.navList {
  display: flex;
  flex-direction: column;
  gap: 4px;
  overflow-y: auto;
}

.navCell {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 40px;
  padding: 9px 16px 9px 12px;
  border: none;
  border-radius: 12px;
  background: transparent;
  font-family: inherit;
  font-size: 14px;
  line-height: 22px;
  color: var(--dsw-alias-label-primary);
  text-align: left;
  cursor: pointer;
  white-space: nowrap;
}

.navCell:hover {
  background: var(--dsw-specific-sidebar-nav-item-hover);
}

.navCell.active {
  background: var(--dsw-specific-sidebar-nav-item-active);
}

.navCell svg {
  flex: none;
  color: var(--dsw-alias-label-tertiary);
}

.navLabel {
  overflow: hidden;
  text-overflow: ellipsis;
}

.content {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.header {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  height: 54px;
  padding: 20px 14px 8px 10px;
  box-sizing: border-box;
}

.close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: none;
  border-radius: 28px;
  background: transparent;
  color: var(--dsw-alias-label-primary);
  cursor: pointer;
}

.close:hover {
  background: var(--dsw-alias-interactive-bg-hover);
}

.options {
  flex: 1;
  min-height: 0;
  padding: 0 24px 24px;
  overflow-y: auto;
}

.hiddenLabel {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}
</style>
