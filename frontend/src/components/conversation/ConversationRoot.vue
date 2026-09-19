<template>
  <div class="root" :data-phase="store.isRunning ? 'running' : 'active'">
    <div class="header">
      <nav class="crumbs" aria-label="会话层级">
        <span class="crumb dim">{{ store.current?.project_name || store.current?.id }}</span>
        <span class="sep">/</span>
        <span class="crumb cur">{{ store.current?.idea || '新会话' }}</span>
      </nav>
      <span class="modeChip">{{ paradigmLabel }}</span>
      <span class="grow" />
      <span class="cost">¥{{ (store.cost?.total_cost ?? 0).toFixed(3) }}</span>
      <button
        class="iconBtn"
        :class="{ on: ui.terminalOpen }"
        title="会话日志"
        aria-label="会话日志"
        @click="ui.terminalOpen = !ui.terminalOpen"
      >
        <DsIcon name="code" :size="16" />
      </button>
      <button class="iconBtn" title="工具面板" aria-label="工具面板" @click="ui.toggleRight()">
        <DsIcon name="panel-left" :size="16" />
      </button>
    </div>

    <div ref="scrollEl" class="scroller" data-conversation-scroll @scroll="onScroll">
      <div ref="flowEl" class="column" data-chat-flow>
        <div v-if="!store.blockList.length" class="placeholder">
          {{ store.isRunning ? '等待智能体输出…' : '暂无事件' }}
        </div>

        <ChatNode
          v-for="b in store.blockList"
          :key="b.key"
          :b="b"
          :is-open="expanded[b.key] === true"
          @toggle="setOpen"
        />

        <div v-if="store.isRunning" class="turnStatus" role="status" aria-live="polite">
          <span class="shimmer">Deep diving...</span>
          <span v-if="elapsedLabel" class="elapsed">{{ elapsedLabel }}</span>
        </div>
      </div>

      <div class="toBottomSlot">
        <button v-if="!follow.atBottom.value" class="toBottom" aria-label="回到底部" @click="follow.scrollToBottom(true)">
          <DsIcon name="chevron-down" :size="14" />
        </button>
      </div>

      <div ref="seatEl" class="composerSeat" data-composer-seat>
        <slot name="composer" />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/** 中栏：748px 单列 + 唯一滚动容器 + sticky composer 座位。
 *  顶栏并入这里（参考项目没有独立顶栏，只有会话头）。 */
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import ChatNode from './ChatNode.vue'
import DsIcon from '../ui/DsIcon.vue'
import { useSessionStore } from '../../stores/sessions'
import { useUiStore } from '../../stores/ui'
import { useFollowScroll } from '../../composables/useFollowScroll'

const store = useSessionStore()
const ui = useUiStore()

const scrollEl = ref<HTMLElement>()
const flowEl = ref<HTMLElement>()
const seatEl = ref<HTMLElement>()
const expanded = reactive<Record<string, boolean>>({})

function setOpen(key: string, v: boolean) {
  expanded[key] = v
}

const paradigmLabel = computed(
  () => ({ classic: '标准模式', dynamic: '计划模式', react: 'ReAct 模式' })[store.current?.paradigm || 'classic'] || '标准模式'
)

/** 只在「结构」变化时跟滚，普通重渲染不抢滚动条 */
const followSig = computed(() => `${store.blockList.length}:${store.blockList.at(-1)?.key || ''}:${store.isRunning ? 1 : 0}`)

const follow = useFollowScroll(scrollEl, flowEl, seatEl, () => followSig.value)

function onScroll() {
  const el = scrollEl.value
  if (!el) return
  follow.atBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight <= 25
}

/** forced = 新出现的末节点是用户自己的话：自己的话必须被看到，即使正在往上翻 */
let lastKey = ''
watch(followSig, () => {
  const last = store.blockList.at(-1)
  const key = last?.key || ''
  const forced = !!last && last.type === 'User' && key !== lastKey
  lastKey = key
  follow.notify(forced)
})

/* composer 会随输入长高，浮在它上面的控件必须读到它的实时高度 */
let seatRo: ResizeObserver | undefined
watch(
  seatEl,
  (el) => {
    seatRo?.disconnect()
    if (!el || typeof ResizeObserver === 'undefined') return
    seatRo = new ResizeObserver(() => {
      scrollEl.value?.style.setProperty('--dsh-composer-height', `${Math.round(el.offsetHeight)}px`)
    })
    seatRo.observe(el)
  },
  { immediate: true }
)
onBeforeUnmount(() => seatRo?.disconnect())

/* ---------- 运行耗时：15s 之后才显示，避免短任务闪字 ---------- */
const turnStart = ref(0)
const elapsedLabel = ref('')
let tick: ReturnType<typeof setInterval> | undefined

watch(
  () => store.isRunning,
  (running) => {
    clearInterval(tick)
    if (!running) {
      turnStart.value = 0
      elapsedLabel.value = ''
      return
    }
    turnStart.value = Date.now()
    tick = setInterval(() => {
      const s = Math.floor((Date.now() - turnStart.value) / 1000)
      elapsedLabel.value = s >= 15 ? (s < 60 ? `用时 ${s}s` : `用时 ${Math.floor(s / 60)}m${s % 60}s`) : ''
    }, 1000)
  },
  { immediate: true }
)

onBeforeUnmount(() => clearInterval(tick))
</script>

<style scoped>
.root {
  --dsh-chat-content-width: 748px;
  --dsh-composer-side-clearance: 16px;
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  min-width: 0;
  background: var(--dsw-alias-bg-base);
}

.header {
  flex: none;
  display: flex;
  align-items: center;
  gap: 8px;
  height: 44px;
  padding: 0 16px;
}

.crumbs {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.crumb {
  max-width: 220px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: 14px;
  line-height: 20px;
  color: var(--dsw-alias-label-primary);
}

.crumb.dim {
  color: var(--dsw-alias-label-tertiary);
}

.crumb.cur {
  font-weight: 500;
}

.sep {
  color: var(--dsw-alias-label-caption);
}

.modeChip {
  padding: 1px 8px;
  border-radius: 12px;
  background: var(--dsw-alias-bg-module-platform);
  font-size: 12px;
  line-height: 18px;
  color: var(--dsw-alias-label-secondary);
}

.grow {
  flex: 1;
}

.cost {
  font-size: 12px;
  color: var(--dsw-alias-label-tertiary);
}

.iconBtn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--dsw-alias-label-secondary);
  cursor: pointer;
}

.iconBtn:hover {
  background: var(--dsw-alias-interactive-bg-hover);
}

.iconBtn.on {
  color: var(--dsw-alias-state-business-primary);
}

.scroller {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
  overflow-x: hidden;
  scrollbar-gutter: stable;
  container-type: inline-size;
}

.column {
  display: flex;
  flex-direction: column;
  gap: 16px;
  flex: 1 0 auto;
  max-width: var(--dsh-chat-content-width);
  margin: 0 auto;
  padding: 16px calc(var(--dsh-composer-side-clearance) + 16px) 8px;
  box-sizing: border-box;
  width: 100%;
}

.placeholder {
  padding: 60px 0;
  text-align: center;
  font-size: 13px;
  color: var(--dsw-alias-label-tertiary);
}

/* composer 座位：sticky 在滚动容器底部，上方 36px 渐变把正文淡掉 */
.composerSeat {
  position: sticky;
  bottom: 0;
  z-index: 7;
  background: linear-gradient(
    180deg,
    color-mix(in srgb, var(--dsw-alias-bg-base) 0%, transparent) 0px,
    var(--dsw-alias-bg-base) 36px
  );
}

.toBottomSlot {
  position: sticky;
  flex: none;
  bottom: calc(var(--dsh-composer-height, 152px) + 16px);
  z-index: 8;
  height: 0;
  display: flex;
  justify-content: flex-end;
  pointer-events: none;
  padding-right: max(0px, calc((100% - var(--dsh-chat-content-width)) / 2));
}

.toBottom {
  width: 34px;
  height: 34px;
  margin-top: -34px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--dsw-alias-border-l2);
  border-radius: 100px;
  background: var(--dsw-alias-button-floating-fill);
  box-shadow: var(--dsw-shadow-lv2);
  color: var(--dsw-alias-label-secondary);
  cursor: pointer;
  pointer-events: auto;
}

.toBottom:hover {
  background: var(--dsw-alias-button-floating-hover);
}

.turnStatus {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 26px;
  font: var(--dsw-font-s-strong-14);
}

.shimmer {
  background: linear-gradient(
    90deg,
    var(--dsw-alias-state-business-primary) 500 0%,
    var(--dsw-alias-state-business-primary) 500 40%,
    color-mix(in srgb, var(--dsw-alias-state-business-primary) 35%, transparent) 50%,
    var(--dsw-alias-state-business-primary) 500 60%,
    var(--dsw-alias-state-business-primary) 500 100%
  );
  background-size: 250% 100%;
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  animation: shimmer 1.8s linear infinite;
}

@keyframes shimmer {
  from {
    background-position: 100% 0;
  }
  to {
    background-position: -100% 0;
  }
}

.elapsed {
  font: var(--dsw-font-s-14);
  color: var(--dsw-alias-label-tertiary);
}

@media (prefers-reduced-motion: reduce) {
  .shimmer {
    animation: none;
    color: var(--dsw-alias-state-business-primary);
    background: none;
    -webkit-text-fill-color: currentColor;
  }
}
</style>
