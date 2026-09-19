<template>
  <n-config-provider :theme="naiveTheme" :locale="zhCN" :date-locale="dateZhCN" style="height: 100%">
    <n-message-provider>
      <n-dialog-provider>
        <LoginPage v-if="auth.needLogin" />
        <SettingsView v-else-if="ui.settingsFull" />
        <div v-else class="shell">
          <aside class="sb">
            <SessionSidebar />
          </aside>
          <main class="main">
            <template v-if="store.current">
              <SessionTopBar />
              <div class="main-body">
                <div class="chat-col" :class="{ 'os-gap': !ui.rightPanel && store.blockList.length > 0 }">
                  <Timeline />
                  <OutputsCard v-if="!ui.rightPanel" />
                  <ChatInput />
                </div>
                <ToolsPanel v-if="ui.rightPanel" />
              </div>
              <TerminalPanel v-if="ui.terminalOpen" />
            </template>
            <template v-else>
              <BoltHero />
            </template>
          </main>
        </div>
        <HumanInputDialog />
        <CreateSessionModal />
        <ToastLayer />
      </n-dialog-provider>
    </n-message-provider>
  </n-config-provider>
</template>

<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import {
  NConfigProvider,
  NDialogProvider,
  NMessageProvider,
  darkTheme,
  dateZhCN,
  zhCN
} from 'naive-ui'
import { useSessionStore } from './stores/sessions'
import { useUiStore } from './stores/ui'
import { useAuthStore } from './stores/auth'
import { useThemeStore } from './stores/theme'
import BoltHero from './components/BoltHero.vue'
import ChatInput from './components/ChatInput.vue'
import CreateSessionModal from './components/CreateSessionModal.vue'
import HumanInputDialog from './components/HumanInputDialog.vue'
import LoginPage from './components/LoginPage.vue'
import OutputsCard from './components/OutputsCard.vue'
import SessionSidebar from './components/SessionSidebar.vue'
import SessionTopBar from './components/SessionTopBar.vue'
import SettingsView from './components/SettingsView.vue'
import TerminalPanel from './components/TerminalPanel.vue'
import Timeline from './components/Timeline.vue'
import ToastLayer from './components/ui/ToastLayer.vue'
import ToolsPanel from './components/ToolsPanel.vue'

const store = useSessionStore()
const ui = useUiStore()
const auth = useAuthStore()
const theme = useThemeStore()
const naiveTheme = computed(() => (theme.dark ? darkTheme : null))

// N1：先探 auth（enabled=0 直接进主界面），登录态成立才拉会话——顺序反了会 401 一片
onMounted(async () => {
  try {
    await auth.init()
  } catch { /* health 挂了也照旧进主界面，后续请求自行报错 */ }
  if (!auth.needLogin) store.init()
})
// 登录成功（needLogin 翻假）后补拉会话列表
watch(
  () => auth.needLogin,
  (v, old) => {
    if (old && !v) store.init()
  }
)
</script>

<style scoped>
.shell {
  display: flex;
  height: 100vh;
  overflow: hidden;
  background: var(--dsw-alias-bg-base);
}

.sb {
  width: 300px;
  flex: none;
  background: var(--sb-bg);
  border-right: 1px solid var(--sb-line);
}

.main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.main-body {
  display: flex;
  flex: 1;
  min-height: 0;
}

/* 输出/来源卡浮在右上时，对话内容列让出右侧空间（对齐参考图比例） */
.chat-col.os-gap :deep(.timeline) {
  padding-right: 330px;
}
</style>
