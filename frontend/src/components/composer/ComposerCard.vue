<template>
  <div class="card" data-composer-card :class="{ hero }">
    <textarea
      ref="ta"
      v-model="content"
      :rows="hero ? 2 : 1"
      :placeholder="placeholder"
      :disabled="!editable"
      @input="grow"
      @keydown="onKey"
      @compositionstart="composing = true"
      @compositionend="onCompositionEnd"
    />

    <div class="row">
      <div class="tools">
        <VMenu v-if="showTarget" :items="targetItems" align="start" compact @select="pickTarget">
          <template #default="{ open, toggle }">
            <button class="chip" :aria-expanded="open" @mousedown.prevent="toggle()">
              <span>{{ targetLabel }}</span>
              <DsIcon name="chevron-down" :size="12" />
            </button>
          </template>
        </VMenu>
        <span v-if="store.current?.paradigm === 'dynamic'" class="chip plain">动态组队</span>
      </div>

      <span class="trailing">
        <VMenu v-if="modelPick" :items="modelItems" align="end" compact @select="pickModel">
          <template #default="{ open, toggle }">
            <button class="modelChip" :aria-expanded="open" :title="model" @mousedown.prevent="toggle()">
              <span class="modelName">{{ model }}</span>
              <DsIcon :name="open ? 'chevron-up' : 'chevron-down'" :size="12" />
            </button>
          </template>
        </VMenu>
        <span v-else-if="model" class="modelChip" :title="model">{{ model }}</span>
        <button
          v-if="store.isRunning && !editable"
          class="primary"
          aria-label="停止生成"
          title="停止生成"
          @mousedown.prevent
          @click="$emit('stop')"
        >
          <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
            <rect x="3" y="3" width="10" height="10" rx="3" fill="currentColor" />
          </svg>
        </button>
        <button
          v-else
          class="primary"
          :disabled="!canSend || sending"
          aria-label="发送"
          title="发送"
          @mousedown.prevent
          @click="submit"
        >
          <DsIcon name="send" :size="16" />
        </button>
      </span>
    </div>
  </div>
</template>

<script setup lang="ts">
/** Composer：780px 卡 + 自动增高 + 发送/停止同位互换。
 *  只做后端真支持的事：文本消息、发送对象、首条启动；没有附件/steer/上下文环的
 *  接口，就不画那些控件。 */
import { computed, nextTick, ref, watch } from 'vue'
import DsIcon from '../ui/DsIcon.vue'
import VMenu from '../ui/VMenu.vue'
import type { MenuItem } from '../ui/menuTypes'
import { useToastStore } from '../../stores/toast'
import { useSessionStore } from '../../stores/sessions'
import { useSettingsStore } from '../../stores/settings'
import { useUiStore } from '../../stores/ui'

const props = withDefaults(
  defineProps<{ hero?: boolean; draft?: boolean; stoppable?: boolean }>(),
  { hero: false, draft: false, stoppable: true }
)
const emit = defineEmits<{ stop: []; drafted: [idea: string] }>()

const store = useSessionStore()
const toast = useToastStore()
const ui = useUiStore()
const prefs = useSettingsStore().prefs

const content = ref('')
const sending = ref(false)
const composing = ref(false)
const ta = ref<HTMLTextAreaElement>()

/** 直聊目标只渲染后端真装配给出的角色名（`Session.roles`，runner._prepare 回填）。
 *  原先这里硬编码 ProductManager/Engineer2/DataAnalyst——一个都不在装配里，
 *  选中后追问会被 route 的 `recv in agents` 判假而静默丢弃。 */
const target = ref('')
const roles = computed(() => store.current?.roles ?? [])
const targetItems = computed<MenuItem[]>(() => [
  { key: '', label: '自动调度', checked: target.value === '' },
  ...roles.value.map((r) => ({ key: r, label: r, checked: target.value === r }))
])
const targetLabel = computed(() => target.value || store.current?.entry_role || '自动调度')

// 切会话后原目标可能不在这场装配里：清回自动调度，别让 /chat 吃 422
watch(roles, (list) => {
  if (target.value && !list.includes(target.value)) target.value = ''
})

const showTarget = computed(() => !props.draft && !!store.currentId)

/** 生效模型：草稿态看 composer 上选的那个，会话内看它建会话时定下的 llm_override，
 *  都没选就是后端配置的默认模型（llm_configured=false 时不给名字，与健康检查同口径）。 */
const model = computed(() => {
  const chosen = props.draft
    ? ui.composer.model
    : String(store.current?.llm_override?.model || '')
  return chosen || (store.health?.llm_configured ? store.health.model : '')
})
/** 会话的模型在建会话那一刻定，运行中改不了 → 只在草稿态画箭头 */
const modelPick = computed(() => props.draft && store.modelsOk && store.models.length > 0)
const modelItems = computed<MenuItem[]>(() =>
  store.models.map((m) => ({ key: m, label: m, checked: model.value === m }))
)

function pickModel(it: MenuItem) {
  if (it.key) ui.composer.model = String(it.key)
}

/** draft = 空态首页，此时还没有会话，可以直接发；否则必须会话在跑或刚创建 */
const editable = computed(() => props.draft || store.isRunning || store.status === 'created')
const canSend = computed(() => !!content.value.trim() && editable.value)

const placeholder = computed(() => {
  if (props.draft) return '描述你想要构建的内容'
  if (store.isRunning) return '要求后续变更'
  if (store.status === 'created') return '输入内容并回车以开始运行'
  return '会话未在运行中'
})

/** 上限 14 行（336px），与参考项目同一档 */
function grow() {
  const el = ta.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 336) + 'px'
}

function onCompositionEnd() {
  // Safari 会在 compositionend 之后再补一个 keydown，立刻清标记会漏判
  setTimeout(() => (composing.value = false), 10)
}

/** 偏好「需按 ^ + 回车键发送」关（默认）：Enter 发送、Shift+Enter 换行——接线前的行为。
 *  开：只有 Ctrl/Cmd+Enter 发送，Enter 交回浏览器原生换行。 */
function onKey(e: KeyboardEvent) {
  if (e.key !== 'Enter') return
  if (composing.value || e.isComposing || e.keyCode === 229) return
  const send = prefs.sendWithCtrlOnly ? (e.ctrlKey || e.metaKey) : !e.shiftKey
  if (!send) return
  e.preventDefault()
  // 运行中且允许打断时，发送键承担「停止」
  if (store.isRunning && props.stoppable) emit('stop')
  else submit()
}

function pickTarget(it: MenuItem) {
  target.value = String(it.key ?? '')
}

async function submit() {
  const text = content.value.trim()
  if (!text || sending.value) return
  sending.value = true
  try {
    if (props.draft) {
      emit('drafted', text)
    } else {
      if (store.status === 'created') await store.start()
      await store.sendChat(text, target.value)
      content.value = ''
      await nextTick(grow)
    }
  } catch (e) {
    toast.push((e as Error).message || '发送失败', 'error')
  } finally {
    sending.value = false
  }
}

defineExpose({ focus: () => ta.value?.focus() })
</script>

<style scoped>
.card {
  display: flex;
  flex-direction: column;
  gap: 12px;
  box-sizing: border-box;
  width: 100%;
  max-width: calc(var(--dsh-chat-content-width, 748px) + 32px);
  margin: 0 auto;
  /* 源 .card 只有 padding-top:10 —— 横向与底部内衬由 textarea 与 .row 各自承担，
     这里再补一层会把卡比源垫高 6px。 */
  padding: 10px 0 0;
  border: 1px solid var(--dsw-alias-border-l2-darkmode-thin);
  border-radius: 22px;
  background: var(--dsw-specific-input-major);
  box-shadow: var(--dsw-shadow-lv2);
  font-size: 16px;
  line-height: 24px;
}

/* hero 与 docked 只差一处：文字栈保留 2 行下限（figma min-h 52 = 2×24 + 4pt）。
   卡壳（内距、阴影）与非 hero 一致——参考项目 InputBar.module.css 里
   `.hero` 只作用于 `.mirror`。 */
.hero textarea {
  min-height: 52px;
}

textarea {
  width: 100%;
  max-height: 336px;
  min-height: 24px;
  border: none;
  outline: none;
  resize: none;
  background: transparent;
  font-family: inherit;
  font-size: 16px;
  line-height: 24px;
  color: var(--dsw-alias-label-primary);
  padding: 4px 12px 0 16px;
  box-sizing: border-box;
}

textarea::placeholder {
  color: var(--dsw-alias-label-caption);
}

textarea:disabled {
  cursor: not-allowed;
  color: var(--dsw-alias-label-dimmed);
}

.row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 2px 8px 6px;
}

.tools {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

/* 参考项目 .trailing：右控件成组，行宽不够时整组换到下一行，
   而不是把左边的模式 chip 压到与模型名重叠 */
.trailing {
  display: flex;
  align-items: center;
  flex: none;
  margin-left: auto;
  gap: 12px;
  min-width: 0;
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 28px;
  max-width: 220px;
  padding: 0 8px;
  border: none;
  border-radius: 8px;
  background: transparent;
  font-family: inherit;
  font-size: 13px;
  font-weight: 500;
  line-height: 20px;
  color: var(--dsw-alias-label-secondary);
  cursor: pointer;
}

.chip:hover {
  background: var(--dsw-alias-interactive-bg-hover);
}

.chip.plain {
  cursor: default;
  color: var(--dsw-alias-label-tertiary);
}

/* 模型名：与 .chip/.select 同一质感（透明底、r8、13/20 二级字）。
   /api/models 给得出目录时它是按钮（带箭头），给不出就退回纯文本——
   箭头只在真能点开有东西的地方出现。 */
.modelChip {
  display: flex;
  align-items: center;
  gap: 2px;
  height: 28px;
  max-width: 220px;
  padding: 0 8px;
  border: none;
  border-radius: 8px;
  background: transparent;
  font-family: inherit;
  font-size: 13px;
  font-weight: 500;
  line-height: 20px;
  color: var(--dsw-alias-label-secondary);
  white-space: nowrap;
}

.modelName {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

button.modelChip {
  padding-right: 6px;
  cursor: pointer;
}

button.modelChip:hover {
  background: var(--dsw-alias-interactive-bg-hover);
}

.primary {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: var(--dsw-alias-button-info-fill);
  color: var(--dsw-alias-label-primary-foreground);
  cursor: pointer;
  transform: translateY(-2px);
}

.primary:hover:not(:disabled) {
  background: var(--dsw-alias-button-info-hover);
}

.primary:disabled {
  opacity: 0.4;
  cursor: default;
}
</style>
