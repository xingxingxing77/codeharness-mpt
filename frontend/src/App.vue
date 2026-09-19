<template>
  <n-config-provider :theme="naiveTheme" :locale="zhCN" :date-locale="dateZhCN" style="height: 100%">
    <n-message-provider>
      <n-dialog-provider>
        <LoginPage v-if="auth.needLogin" />
        <SettingsView v-else-if="ui.settingsFull" />
        <AppFrame v-else>
          <template #sidebar>
            <SidebarRoot @new-session="store.goHome()" />
          </template>

          <main class="center">
            <template v-if="store.current">
              <ConversationRoot>
                <template #composer>
                  <ChatInput />
                </template>
              </ConversationRoot>
              <!-- 日志暂时只有这一个去处；F7 把它并进右栏 detailsCol 后一起删 -->
              <TerminalPanel v-if="ui.terminalOpen" />
            </template>
            <template v-else>
              <BoltHero />
            </template>
          </main>

          <template #details>
            <ToolsPanel v-if="ui.rightPanel" />
          </template>
        </AppFrame>
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
import AppFrame from './components/frame/AppFrame.vue'
import ChatInput from './components/ChatInput.vue'
import ConversationRoot from './components/conversation/ConversationRoot.vue'
import CreateSessionModal from './components/CreateSessionModal.vue'
import HumanInputDialog from './components/HumanInputDialog.vue'
import LoginPage from './components/LoginPage.vue'
import SidebarRoot from './components/sidebar/SidebarRoot.vue'
import SettingsView from './components/SettingsView.vue'
import TerminalPanel from './components/TerminalPanel.vue'
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
/* 中栏自己只是一根竖直列；三栏几何在 AppFrame + stores/layout 里。
   必须显式 flex:1，否则它在纵向 flex 父容器里按内容收缩，
   ConversationRoot 的 flex:1 与 sticky composer 都失去参照。 */
.center {
  flex: 1;
  min-height: 0;
  min-width: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
</style>
