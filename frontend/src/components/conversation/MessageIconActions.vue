<template>
  <div class="actions">
    <span v-if="parts.length" :class="clock === 'start' ? 'timeStart' : 'timeEnd'">
      <template v-for="(p, i) in parts" :key="i">
        <span v-if="i" class="dot" aria-hidden="true">·</span>
        {{ p }}
      </template>
    </span>
    <VTooltip :label="copied ? '已复制' : '复制'" side="bottom">
      <button type="button" class="act" :aria-label="copied ? '已复制' : '复制'" @click="copy">
        <DsIcon :name="copied ? 'check' : 'copy'" :size="16" />
      </button>
    </VTooltip>
    <slot />
  </div>
</template>

<script setup lang="ts">
/** 参考项目 chat/MessageIconActions.tsx 的移植：复制 + 扩展动作插槽 + 日期感知时钟。
 *  图标挂载后一直可见，只有时间读数在 data-time-hover-root 作用域里 hover 才淡入（80ms），
 *  用 opacity 而不是 display，所以读数出现时布局不跳。
 *  没有 fork 之前不画 branch 钮：参考项目 onBranch 缺省即隐藏，这里同样缺省。 */
import { computed, onBeforeUnmount, ref } from 'vue'
import DsIcon from '../ui/DsIcon.vue'
import VTooltip from '../ui/VTooltip.vue'
import { useToastStore } from '../../stores/toast'
import { formatLatencySeconds, formatMessageClock, formatRunDuration, formatTokensPerSecond } from '../../utils/messageChrome'

const props = withDefaults(
  defineProps<{
    /** 复制动作写入的纯文本。 */
    text: string
    /** 时钟读数（unix ms）；缺省则整段时间读数不出现。 */
    time?: number
    runMs?: number
    ttftMs?: number
    tokensPerSecond?: number
    /** 用户气泡时钟在图标前，助手在图标后。 */
    clock?: 'start' | 'end'
  }>(),
  { clock: 'end' }
)

const toast = useToastStore()
const copied = ref(false)
let timer: ReturnType<typeof setTimeout> | undefined

/** 读数之间的点是装饰性的，但屏幕阅读器要靠它把「用时 13秒 · TTFT 0.2秒 · 12 tok/s」
 *  切成三段，否则会读成一串连读。 */
const parts = computed(() => {
  if (props.time === undefined) return []
  const out = [formatMessageClock(props.time)]
  if (props.runMs !== undefined) out.push(`用时 ${formatRunDuration(props.runMs)}`)
  if (props.ttftMs !== undefined) out.push(`TTFT ${formatLatencySeconds(props.ttftMs)}秒`)
  if (props.tokensPerSecond !== undefined) out.push(`${formatTokensPerSecond(props.tokensPerSecond)} tok/s`)
  return out
})

async function copy() {
  if (copied.value || !navigator.clipboard) return
  try {
    await navigator.clipboard.writeText(props.text)
    copied.value = true
    timer = setTimeout(() => (copied.value = false), 1000)
  } catch {
    toast.push('复制失败', 'error')
  }
}

onBeforeUnmount(() => clearTimeout(timer))
</script>

<style scoped>
.actions {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 28px;
}

/* 时钟在图标前（用户 figma 388:20051）/ 在图标后（助手 43:32997）。
   模板里读数在钮前，所以 start 天然正确，end 靠 order 挪到钮后。 */
.timeStart,
.timeEnd {
  font-size: 14px;
  line-height: 24px;
  color: var(--dsw-alias-label-tertiary);
  white-space: nowrap;
}

.timeStart {
  padding-right: 12px;
}

.timeEnd {
  order: 1;
  padding-left: 12px;
}

.dot {
  margin: 0 10px;
}

.act {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  padding: 6px;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--dsw-alias-label-tertiary);
  cursor: pointer;
}

.act:hover {
  background: var(--dsw-alias-interactive-bg-hover);
  color: var(--dsw-alias-label-primary);
}

@media (hover: hover) {
  [data-time-hover-root] :is(.timeStart, .timeEnd) {
    opacity: 0;
    transition: opacity 80ms ease;
  }

  [data-time-hover-root]:hover :is(.timeStart, .timeEnd),
  [data-time-hover-root]:focus-within :is(.timeStart, .timeEnd) {
    opacity: 1;
  }
}
</style>
