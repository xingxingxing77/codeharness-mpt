<template>
  <LoginPage v-if="auth.needLogin" />
  <AppFrame v-else>
    <template #sidebar>
      <SidebarRoot @new-session="store.goHome()" />
    </template>

    <main class="center">
      <ConversationRoot v-if="store.current">
        <template #composer>
          <ComposerCard @stop="store.stop()" />
        </template>
      </ConversationRoot>
      <EmptyHero v-else />
    </main>

    <!-- 关闭时列宽为 0，但组件仍挂载会白拉接口，所以显式不渲染 -->
    <template #details>
      <DetailsPanel v-if="ui.rightPanel" />
    </template>
  </AppFrame>

  <SettingsView />
  <ToastLayer />
</template>

<script setup lang="ts">
import { onMounted, watch } from 'vue'
import { useSessionStore } from './stores/sessions'
import { useUiStore } from './stores/ui'
import { useAuthStore } from './stores/auth'
import AppFrame from './components/frame/AppFrame.vue'
import ComposerCard from './components/composer/ComposerCard.vue'
import ConversationRoot from './components/conversation/ConversationRoot.vue'
import DetailsPanel from './components/DetailsPanel.vue'
import EmptyHero from './components/composer/EmptyHero.vue'
import LoginPage from './components/LoginPage.vue'
import SettingsView from './components/SettingsView.vue'
import SidebarRoot from './components/sidebar/SidebarRoot.vue'
import ToastLayer from './components/ui/ToastLayer.vue'

const store = useSessionStore()
const ui = useUiStore()
const auth = useAuthStore()

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
