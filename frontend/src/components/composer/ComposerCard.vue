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
        <!-- 免审档：后端真有 permission 字段与 gate 判定，所以这枚 chip 不是装饰 -->
        <VMenu v-if="showTarget" :items="permissionItems" align="start" compact @select="pickPermission">
          <template #default="{ open, toggle }">
            <button class="chip" :aria-expanded="open" @mousedown.prevent="toggle()">
              <span>{{ permissionLabel }}</span>
              <DsIcon name="chevron-down" :size="12" />
            </button>
          </template>
        </VMenu>
        <!-- C184 上下文预算：草稿态=档位选择（枚举来自后端 context_tiers，建会话那一刻定，运行中改不了）；
             会话内=窗口占用 chip + hover 卡。预算未设时 chip 退成只读文本，卡里给一行只读说明——
             没有预算就没有百分比，不渲染假的 0%。 -->
        <VMenu v-if="draftTierPick" :items="tierItems" align="start" compact @select="pickTier">
          <template #default="{ open, toggle }">
            <button class="chip" :aria-expanded="open" title="上下文预算档位" @mousedown.prevent="toggle()">
              <span>{{ tierLabel }}</span>
              <DsIcon name="chevron-down" :size="12" />
            </button>
          </template>
        </VMenu>
        <HoverCard v-else-if="showCtxChip" light>
          <template #default>
            <span class="chip plain" :aria-label="`上下文用量 ${chipLabel}`">{{ chipLabel }}</span>
          </template>
          <template #content>
            <template v-if="budget > 0">
              <template v-if="used != null">
                <div class="ctxHead">
                  <span class="ctxTitle">上下文容量</span>
                  <span class="ctxNum">{{ fmtWan(used) }} / {{ fmtWan(budget) }}（{{ pct }}%）</span>
                </div>
                <div class="ctxBar" aria-hidden="true">
                  <div
                    class="ctxFill"
                    :class="{ over: pct > 100 }"
                    :style="{ width: Math.min(pct, 100) + '%' }"
                  />
                </div>
                <div v-if="peak" class="ctxPeak">本轮峰值 {{ fmtWan(peak) }}</div>
              </template>
              <div v-else class="ctxPeak">预算 {{ fmtWan(budget) }}，还没有用量回执</div>
              <!-- 分类行只画有数据源的：厂商只回单发总 input_tokens，没有分类归因。system 段是
                   网关侧本地 tiktoken 计数、「其余」= used − system——**两把尺相减**的派生量
                   （P0③：估算不冒充真值、差额不冒充直接计量；修法是标注口径，不是再造一把尺）。 -->
              <template v-if="sysTok">
                <div class="ctxRow"><span class="dot dotSys" />系统提示词（估算）<span class="ctxVal">{{ sysPct }}%</span></div>
                <!-- C185（P4）：跨阶段事实清单**单独一行**（口径=末笔注入段的 token，后端 `last_facts_tokens`）。
                     没有它这一行就渲染成 0%（后端计数为 0＝这一场还没注入过），不假报占用。 -->
                <div class="ctxRow"><span class="dot dotFacts" />跨阶段事实（估算）<span class="ctxVal">{{ factsPct }}%</span></div>
                <div class="ctxRow"><span class="dot dotRest" />其余对话与工具结果（差额）<span class="ctxVal">{{ restPct }}%</span></div>
              </template>
            </template>
            <div v-else class="ctxPeak">未设上下文预算（超窗由厂商报错兜底）</div>
            <div class="ctxModel">
              <span class="ctxModelName">{{ model || '未配置模型' }}</span>
              <span v-if="modelWindow" class="ctxVal">{{ fmtWan(modelWindow) }}</span>
            </div>
            <!-- 知情选择不偷偷拦：档位 > 模型窗口时压缩闸永远不会先触发，唯一的闸是厂商 400 -->
            <div v-if="windowWarn" class="ctxWarn">{{ windowWarn }}</div>
          </template>
        </HoverCard>
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
        <!-- 运行中+空输入=停止（没有可发的 steer，唯一有意义动作）；有内容=发送。v-if 原为
              isRunning && !editable，而 editable 恒含 isRunning ⇒ 恒假，鼠标停止入口死掉。 -->
        <button
          v-if="store.isRunning && props.stoppable && !content.trim()"
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
import HoverCard from '../ui/HoverCard.vue'
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

/** C184 上下文预算。口径：used = 末笔请求的输入总量（=发那一刻的窗口占用，后端
 *  `CostManager.last_prompt_tokens`，随 SSE cost 快照来，零新端点零轮询）；budget = 会话档位
 *  （llm_override.context_length，建会话那一刻定）。缺键按「不渲染」处理而不是渲染 0
 *  （同 _trace_span 的 t0/ft 口径）；分母没有就没有百分比。 */
const budget = computed(() => Number(store.current?.llm_override?.context_length) || 0)
const cost = computed(() => store.cost)
const used = computed(() => {
  const v = cost.value.last_prompt_tokens
  return typeof v === 'number' && v > 0 ? v : null
})
const peak = computed(() => (typeof cost.value.peak_prompt_tokens === 'number' ? cost.value.peak_prompt_tokens : null))
const sysTok = computed(() => {
  const v = cost.value.last_system_tokens
  return typeof v === 'number' && v > 0 ? v : null
})
const pct = computed(() =>
  used.value != null && budget.value > 0 ? Math.round((used.value / budget.value) * 100) : 0
)
const overWindow = computed(() => store.modelWindow != null && budget.value > store.modelWindow)
/** 警示整句进 computed：档位 > 模型窗口才给（那是「闸不会先触发」这个事实成立的时候）。 */
const windowWarn = computed(() =>
  overWindow.value
    ? `当前模型窗口 ${fmtWan(store.modelWindow!)}，${budgetLabel.value} 档下压缩闸不会先触发，超窗会以 HTTP 400 中断本场`
    : ''
)
const budgetLabel = computed(
  () => store.contextTiers.find((t) => t.value === budget.value)?.label || fmtWan(budget.value)
)

/** 万位缩写（参考图口径）：25800→2.6万、200000→20万、1000000→100万；万以下原样。 */
function fmtWan(n: number): string {
  if (n < 10000) return String(n)
  const w = n / 10000
  return `${w >= 100 ? Math.round(w) : Math.round(w * 10) / 10}万`
}

/** 草稿态档位选择（照 modelPick 的条件与写法）。items 来自 store 里缓存的后端档位，
 *  「不设」是前端默认态（不发键），不是后端枚举的一个值。 */
const draftTierPick = computed(() => props.draft && store.contextTiers.length > 0)
const tier = computed(() => String(ui.composer.tier || ''))
const tierLabel = computed(
  () => store.contextTiers.find((t) => String(t.value) === tier.value)?.label || '预算未设'
)
const tierItems = computed<MenuItem[]>(() => [
  { key: '', label: '不设', desc: '超窗由厂商报错兜底', checked: tier.value === '' },
  ...store.contextTiers.map((t) => ({
    key: String(t.value),
    label: t.label,
    desc: t.note,
    checked: tier.value === String(t.value)
  }))
])

function pickTier(it: MenuItem) {
  ui.composer.tier = String(it.key ?? '')
}

/** 会话内 chip：有预算有回执给百分比；有预算没回执给档位；没预算给只读说明。 */
const showCtxChip = computed(() => !props.draft && !!store.currentId)
const chipLabel = computed(() => {
  if (budget.value > 0 && used.value != null) return `${pct.value}%`
  if (budget.value > 0) return budgetLabel.value
  return '预算未设'
})
/** 分类占比：system 段 / 跨阶段事实 / used（各组 token 都到齐才算，别拿半份数据拼 100）。
 *  C185（P4）：事实清单是**单独计数**的第三行（后端 `last_facts_tokens`），它本来是「其余」的一部分
 *  ⇒ 「其余」要把它减掉，否则三行加起来超过 100%。计数为 0＝这一场还没注入过（不渲染假占用）。 */
const sysPct = computed(() =>
  sysTok.value && used.value ? Math.min(100, Math.round((sysTok.value / used.value) * 100)) : 0
)
const factsPct = computed(() => {
  const v = cost.value.last_facts_tokens
  return typeof v === 'number' && v > 0 && used.value
    ? Math.min(100, Math.round((v / used.value) * 100)) : 0
})
const restPct = computed(() => Math.max(0, 100 - sysPct.value - factsPct.value))

/** 免审档 = 「哪些动作不用问我」。文案与后端判定表同源三档
 *  （codeharness/tools/_approval.py），改档位从下一个节点边界起生效。 */
const PERMISSIONS = [
  { key: 'readonly', label: '只读', desc: '只读免审；写文件、执行命令、联网都要批' },
  { key: 'workspace_write', label: '工作区写入', desc: '写进本会话工作区免审；命令与联网仍要批' },
  { key: 'full_access', label: '全面访问', desc: '全免审' }
]
const permission = computed(() => store.current?.permission || 'readonly')
const permissionLabel = computed(
  () => PERMISSIONS.find((p) => p.key === permission.value)?.label || permission.value
)
const permissionItems = computed<MenuItem[]>(() =>
  PERMISSIONS.map((p) => ({ key: p.key, label: p.label, desc: p.desc, checked: p.key === permission.value }))
)

async function pickPermission(it: MenuItem) {
  if (!it.key || it.key === permission.value) return
  try {
    await store.setPermission(String(it.key))
  } catch (e) {
    toast.push((e as Error).message || '改档失败', 'error')
  }
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
  // 与主按钮同语义：运行中+空输入=停止，有内容=发送（steer）
  if (store.isRunning && props.stoppable && !content.value.trim()) emit('stop')
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

/* C184 上下文容量卡内容：卡面 opt-in 白底（HoverCard `light` 档，参考产品同款），
   文字用静态 token（theme-constant，白底上两主题都读得清），不走会翻转的 alias 令牌。
   进度条只在「超预算」这个事实上换警示色——用量侧不写死 80% 之类的人为告警阈值
   （ADR-20260922-01 的口径）。 */
.ctxHead {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}

.ctxTitle {
  font-size: 14px;
  line-height: 20px;
  color: var(--dsw-static-neutral-bluish-1000);
  white-space: nowrap;
}

.ctxNum {
  font-size: 12px;
  line-height: 16px;
  color: var(--dsw-static-neutral-bluish-700);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.ctxBar {
  height: 6px;
  border-radius: 3px;
  background: var(--dsw-static-neutral-200);
  overflow: hidden;
}

.ctxFill {
  height: 100%;
  border-radius: 3px;
  background: var(--dsw-static-deepseek-500);
}

.ctxFill.over {
  background: var(--dsw-static-red-600);
}

.ctxPeak {
  font-size: 12px;
  line-height: 16px;
  color: var(--dsw-static-neutral-bluish-700);
}

.ctxRow {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  line-height: 18px;
  color: var(--dsw-static-neutral-bluish-1000);
}

.ctxVal {
  margin-left: auto;
  color: var(--dsw-static-neutral-bluish-700);
  font-variant-numeric: tabular-nums;
}

.dot {
  flex: none;
  width: 6px;
  height: 6px;
  border-radius: 50%;
}

.dotSys {
  background: var(--dsw-static-deepseek-500);
}

/* C185（P4）：第三颗点复用**同一个 token**、只差一档透明度 —— C184 的「零新增色值」照旧成立。 */
.dotFacts {
  background: var(--dsw-static-deepseek-500);
  opacity: 0.7;
}

.dotRest {
  background: var(--dsw-static-deepseek-500);
  opacity: 0.45;}

.ctxModel {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  font-size: 12px;
  line-height: 16px;
  color: var(--dsw-static-neutral-bluish-700);
}

.ctxModelName {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ctxWarn {
  font-size: 12px;
  line-height: 16px;
  color: var(--dsw-static-amber-600);
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
