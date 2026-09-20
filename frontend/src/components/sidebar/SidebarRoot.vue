<template>
  <div
    class="root"
    :class="rootClass"
    :style="frozenWidth ? { width: `${frozenWidth}px` } : undefined"
    @pointerenter="pointerInside = true"
    @pointerleave="onLeave"
  >
    <div class="logoRow">
      <button v-if="wide" class="brand" aria-label="新建会话" @click="emit('newSession')">
        <span class="brandIdentity">
          <span class="brandMark"><DsIcon name="sparkle" :size="24" /></span>
          <span class="brandName">Codeharness</span>
        </span>
      </button>
      <VTooltip :label="collapsed ? '打开侧边栏' : '收起侧边栏'" :delay="500">
        <button class="iconBtn toggle" :aria-label="collapsed ? '打开侧边栏' : '收起侧边栏'" @click="panels.toggleSidebar()">
          <!-- 轨态：静止是品牌标，hover 才换成本体图标（源设计的 sidebar-hover 流转） -->
          <DsIcon v-if="!wide" name="sparkle" :size="24" class="railMark" />
          <DsIcon name="panel-left" :size="wide ? 16 : 18" class="panelIcon" />
        </button>
      </VTooltip>
    </div>

    <VTooltip label="新建会话" :delay="500" :disabled="wide">
      <button class="newSession" aria-label="新建会话" @click="emit('newSession')">
        <DsIcon name="new-chat" :size="wide ? 14 : 18" />
        <span v-if="wide" class="newSessionLabel">新会话</span>
      </button>
    </VTooltip>

    <div class="regionArea">
      <WorkspaceBrowser :wide="wide" />
    </div>

    <div class="footArea">
      <!-- 参考项目这里是一个空的 footer.action 位；本项目 auth 开着就必须有退出入口 -->
      <div v-if="auth.enabled && auth.user" class="footerActions">
        <button class="userRow" @click="doLogout">
          <DsIcon name="user" :size="16" />
          <span v-if="wide" class="uName">{{ auth.user }}</span>
          <span v-if="wide" class="uOut">退出</span>
        </button>
      </div>
      <div class="settingsArea">
        <button
          class="settingsTrigger"
          :class="{ rail: !wide }"
          aria-haspopup="dialog"
          :aria-expanded="ui.settingsFull"
          @click.stop="ui.settingsFull = true"
        >
          <DsIcon :name="settingsGlyph" :size="wide ? 16 : 18" />
          <span v-if="wide">设置</span>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/** 侧栏列壳：三段收起编排。照抄参考项目 SidebarRoot 的 choreography——
 *  收起是「轨道滑动 + 交叉淡出」而不是形变：内容先冻结在原宽度淡出，
 *  滑动的列把它裁掉，淡出settled 之后才换轨态布局。冷启动直接是收起态则静态渲染。 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useLayoutStore } from '../../stores/layout'
import { useUiStore } from '../../stores/ui'
import { useAuthStore } from '../../stores/auth'
import { useSessionStore } from '../../stores/sessions'
import { useToastStore } from '../../stores/toast'
import DsIcon from '../ui/DsIcon.vue'
import VTooltip from '../ui/VTooltip.vue'
import WorkspaceBrowser from './WorkspaceBrowser.vue'

const emit = defineEmits<{ newSession: [] }>()

const COLLAPSE_SETTLE_MS = 150
const SCROLLBAR_LINGER_MS = 2000

const panels = useLayoutStore()
const ui = useUiStore()
const auth = useAuthStore()
const store = useSessionStore()
const toast = useToastStore()

async function doLogout() {
  await auth.logout()
  store.goHome()
  toast.push('已退出登录', 'success')
}

const collapsed = computed(() => panels.sidebarCollapsed)
const pointerInside = ref(true)
const settled = ref(collapsed.value)
const everWide = ref(!collapsed.value)
const lastWideWidth = ref(panels.sidebar || 280)

let linger: ReturnType<typeof setTimeout> | undefined

watch(
  () => [collapsed.value, panels.sidebar] as const,
  ([c, w]) => {
    clearTimeout(linger)
    if (!c) {
      settled.value = false
      everWide.value = true
      if (w) lastWideWidth.value = w
      return
    }
    if (!everWide.value) {
      // 冷启动就是收起态：静态画轨，不演「淡出后浮出图标」那一段
      settled.value = true
      return
    }
    const t = setTimeout(() => (settled.value = true), COLLAPSE_SETTLE_MS)
    onCleanup(() => clearTimeout(t))
  },
  { immediate: true }
)

/** 冻结展开宽度过渡：收起过程中内容不重排，靠滑动的列裁切 */
const wide = computed(() => !collapsed.value || !settled.value)
const frozenWidth = computed(() => (wide.value && collapsed.value ? lastWideWidth.value : 0))

const rootClass = computed(() => ({
  collapsed: collapsed.value && wide.value === false,
  railIn: collapsed.value && !wide.value && everWide.value,
  fading: collapsed.value && wide.value,
  quietBars: !pointerInside.value
}))

const settingsGlyph = computed(() => 'settings')

/** 指针离开用几何判定而不是 DOM containment：设置面板是本列的 fixed 后代，
 *  pointerleave 永远不在它上面触发。留 2s 再收滚动条。 */
function onLeave(e: PointerEvent) {
  const root = e.currentTarget as HTMLElement | null
  if (!root) return
  const r = root.getBoundingClientRect()
  const { clientX: x, clientY: y } = e
  if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) return
  clearTimeout(linger)
  linger = setTimeout(() => (pointerInside.value = false), SCROLLBAR_LINGER_MS)
}

const cleanups: (() => void)[] = []
function onCleanup(fn: () => void) {
  cleanups.push(fn)
}

onBeforeUnmount(() => {
  clearTimeout(linger)
  cleanups.forEach((f) => f())
})
</script>

<style scoped>
.root {
  --dsh-sidebar-inline-padding: 12px;
  display: flex;
  flex-direction: column;
  height: 100%;
  box-sizing: border-box;
  padding: 6px var(--dsh-sidebar-inline-padding);
  background: var(--dsw-specific-sidebar-fill);
  color: var(--dsw-alias-label-primary);
  font-size: 14px;
  overflow: hidden;
  /* 抬升面用 l2 一档滚动条 */
  --dsh-scrollbar-thumb: var(--dsw-alias-scrollbar-bg-l2);
  --dsh-scrollbar-thumb-hover: var(--dsw-alias-scrollbar-hover-l2);
}

.root.collapsed {
  padding: 18px 10px 6px;
}

/* 滚动条隐藏靠重绑成透明，gutter 保留 ⇒ 显隐不引起行回流 */
.root.quietBars {
  --dsh-scrollbar-thumb: transparent;
  --dsh-scrollbar-thumb-hover: transparent;
}

/* 阶段 1：内容冻结宽度原地淡出，不重排 */
.fading > * {
  opacity: 0;
  transition: opacity 150ms var(--ds-ease-in-out);
}

/* 阶段 2：150ms 后轨态控件从 x=+49 进入，接满 300ms 的滑动 */
.railIn .iconBtn,
.railIn .newSession,
.railIn .regionArea {
  animation: rail-in 150ms var(--ds-ease-in-out) backwards;
}

.railIn .footArea {
  animation: rail-fade-in 150ms var(--ds-ease-in-out) backwards;
}

@keyframes rail-in {
  from {
    opacity: 0;
    transform: translateX(49px);
  }
}

@keyframes rail-fade-in {
  from {
    opacity: 0;
  }
}

.logoRow {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  height: 60px;
  padding: 8px 0 8px 4px;
  margin-bottom: 8px;
  box-sizing: border-box;
  overflow: hidden;
}

.collapsed .logoRow {
  height: 36px;
  padding: 0;
  margin-bottom: 12px;
  justify-content: flex-start;
}

.brand {
  flex: 1;
  min-width: 0;
  display: inline-flex;
  align-items: center;
  overflow: hidden;
  padding: 0;
  border: none;
  background: transparent;
  color: inherit;
  cursor: pointer;
}

.brandIdentity {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  height: 24px;
  min-width: 0;
}

.brandName {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  height: 24px;
  font-size: 18px;
  font-weight: 600;
  line-height: 24px;
  letter-spacing: 0.04em;
  white-space: nowrap;
}

.iconBtn {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: transparent;
  cursor: pointer;
  color: var(--dsw-alias-label-secondary);
}

.iconBtn:hover {
  background: var(--dsw-alias-interactive-bg-hover);
}

.collapsed .iconBtn {
  width: 36px;
  height: 36px;
  color: var(--dsw-alias-label-primary);
}

/* 轨态的收起按钮静止只显品牌标、无 hover 圆圈；hover 换成本体图标 */
.collapsed .toggle {
  position: relative;
  background: transparent;
}

.collapsed .toggle:hover {
  background: transparent;
}

.collapsed .toggle .panelIcon {
  display: none;
}

.collapsed .toggle:hover .panelIcon {
  display: inline;
}

.collapsed .toggle:hover .railMark {
  display: none;
}

.newSession {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  height: 38px;
  padding: 8px 16px;
  margin: 0 2px 8px;
  box-sizing: border-box;
  border: 1px solid var(--dsw-alias-border-l2);
  border-radius: 12px;
  background: var(--dsw-alias-button-elevated-fill);
  color: var(--dsw-alias-label-primary);
  font-family: inherit;
  font-size: 14px;
  font-weight: 500;
  line-height: 22px;
  cursor: pointer;
  overflow: hidden;
}

.newSession:hover {
  background: var(--dsw-alias-button-floating-hover);
}

.collapsed .newSession {
  align-self: flex-start;
  width: 36px;
  height: 36px;
  padding: 0;
  margin: 0 0 12px;
  gap: 0;
  border-color: transparent;
  background: transparent;
}

.collapsed .newSession:hover {
  background: var(--dsw-alias-interactive-bg-hover);
}

.newSessionLabel {
  max-width: 200px;
  overflow: hidden;
  white-space: nowrap;
}

.regionArea {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  /* 负边距让滚动条能贴到列边缘 */
  margin-left: -4px;
  margin-right: calc(-1 * var(--dsh-sidebar-inline-padding));
  padding-left: 4px;
  overflow: hidden;
}

.collapsed .regionArea {
  margin: 0;
  padding-left: 0;
}

.footArea {
  flex: none;
  display: flex;
  flex-direction: column;
}

.collapsed .footArea {
  align-items: center;
}

.footerActions {
  flex: none;
  display: flex;
  min-width: 0;
  width: 100%;
}

.userRow {
  display: flex;
  align-items: center;
  gap: 8px;
  width: calc(100% + 4px);
  height: 42px;
  margin: 0 -2px;
  padding: 0 10px 0 8px;
  box-sizing: border-box;
  border: none;
  border-radius: 12px;
  background: transparent;
  font-family: inherit;
  font-size: 14px;
  line-height: 22px;
  color: var(--dsw-alias-label-primary);
  cursor: pointer;
  overflow: hidden;
}

.userRow:hover {
  background: var(--dsw-alias-interactive-bg-hover);
}

.userRow svg {
  flex: none;
  color: var(--dsw-alias-label-tertiary);
}

.uName {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  text-align: left;
}

.uOut {
  flex: none;
  font-size: 12px;
  color: var(--dsw-alias-label-tertiary);
}

.collapsed .footerActions {
  justify-content: center;
  width: auto;
}

.settingsArea {
  flex: none;
  min-width: 0;
  width: 100%;
}

.settingsTrigger {
  flex: none;
  display: flex;
  align-items: center;
  gap: 8px;
  width: calc(100% + 4px);
  height: 42px;
  margin: 4px -2px;
  padding: 0 10px 0 8px;
  box-sizing: border-box;
  border: none;
  border-radius: 12px;
  background: transparent;
  cursor: pointer;
  overflow: hidden;
  font-family: inherit;
  font-size: 14px;
  line-height: 22px;
  color: var(--dsw-alias-label-primary);
}

.settingsTrigger:hover {
  background: var(--dsw-alias-interactive-bg-hover);
}

.settingsTrigger.rail {
  width: 36px;
  height: 36px;
  margin: 8px 0 10px;
  justify-content: center;
  gap: 0;
  padding: 0;
  border-radius: 50%;
}

@media (prefers-reduced-motion: reduce) {
  .fading > *,
  .railIn .iconBtn,
  .railIn .newSession,
  .railIn .regionArea,
  .railIn .footArea {
    transition: none;
    animation: none;
  }
}
</style>
