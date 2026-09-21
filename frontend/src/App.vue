<template>
  <LoginPage v-if="auth.needLogin" />
  <AppFrame v-else>
    <template #sidebar>
      <SidebarRoot @new-session="store.goHome()" />
    </template>

    <main class="center">
      <ConversationRoot v-if="store.current">
        <template #composer>
          <GoalBar />
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

  <!-- 断线横幅（B1）。几何与配色照参考项目 ui-primitives/ConnectionBanner.module.css 的
       .banner 逐条落（fixed 顶条 / 4px 12px / 12·18 / state-error-primary）；
       触发权在 store.stream 那一个三态值——「open 过之后报错」才算断线，首屏握手与
       没选会话都不是（同源原子的 null/connecting 保持安静口径）。 -->
  <div v-if="store.stream === 'down'" class="connBanner" role="status" aria-live="polite">
    连接已断开，正在重连…
  </div>
</template>

<script setup lang="ts">
import { onMounted, watch } from 'vue'
import { useSessionStore } from './stores/sessions'
import { useUiStore } from './stores/ui'
import { useAuthStore } from './stores/auth'
import AppFrame from './components/frame/AppFrame.vue'
import ComposerCard from './components/composer/ComposerCard.vue'
import GoalBar from './components/composer/GoalBar.vue'
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

/* 参考项目 ConnectionBanner.module.css 的 .banner，一字未改（除令牌名照本仓）。
   100 这档在本仓上面还有 1000/1100 的 toast 与模态，所以横幅不会盖住回执。 */
.connBanner {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: 100;
  padding: 4px 12px;
  text-align: center;
  font-size: 12px;
  line-height: 18px;
  background: var(--dsw-alias-state-error-primary);
  color: var(--dsw-alias-label-primary-foreground);
}
</style>
