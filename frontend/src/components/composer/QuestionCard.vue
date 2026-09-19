<template>
  <div class="card" data-composer-card>
    <div class="strip">
      <span class="dot" />
      <span class="stripText">等待回答</span>
    </div>
    <div class="question">{{ question }}</div>

    <textarea
      ref="ta"
      v-model="answer"
      rows="2"
      placeholder="输入你的回答，Enter 提交 / Shift+Enter 换行"
      @keydown="onKey"
    />

    <div class="row">
      <span class="grow" />
      <button class="ghostBtn" @click="dismiss">稍后回答</button>
      <button class="primary" :disabled="!answer.trim() || busy" @mousedown.prevent @click="submit">
        提交回答
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
/** ask_human 的 takeover：占掉与 composer 同一个 sticky 座位，所以正文不会跳动。
 *  后端只下发一条纯文本问题（Event.value），没有结构化选项也没有审批通道，
 *  因此这里只做自由文本回答，不画假选项芯片。 */
import { nextTick, onMounted, ref } from 'vue'
import { useSessionStore } from '../../stores/sessions'
import { useToastStore } from '../../stores/toast'

const props = defineProps<{ question: string }>()

const store = useSessionStore()
const toast = useToastStore()
const answer = ref('')
const busy = ref(false)
const ta = ref<HTMLTextAreaElement>()

onMounted(async () => {
  await nextTick()
  ta.value?.focus()
})

function onKey(e: KeyboardEvent) {
  if (e.key !== 'Enter' || e.shiftKey || e.isComposing) return
  e.preventDefault()
  submit()
}

async function submit() {
  const text = answer.value.trim()
  if (!text || busy.value) return
  busy.value = true
  try {
    const ok = await store.answerHuman(text)
    if (!ok) toast.push('回答未被接收，会话可能已结束', 'warn')
    else answer.value = ''
  } catch (e) {
    toast.push((e as Error).message, 'error')
  } finally {
    busy.value = false
  }
}

/** 只是本地收起这张卡：问题仍在服务端挂着，下一条 ask_human 会再顶上来 */
function dismiss() {
  store.dismissHuman()
  toast.push('已暂时收起，智能体仍在等待回答')
}
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
  padding: 0 8px 6px;
  border: 1px solid var(--dsw-alias-state-warn-secondary);
  border-radius: 20px;
  background: var(--dsw-specific-input-major);
  box-shadow: var(--dsw-shadow-lv2);
}

.strip {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 -8px;
  padding: 8px 20px;
  background: var(--dsw-alias-state-warn-tertiary);
  border-radius: 19px 19px 0 0;
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--dsw-alias-state-warn-primary);
  flex: none;
}

.stripText {
  font-size: 13px;
  line-height: 18px;
  color: var(--dsw-alias-state-warn-label);
}

.question {
  padding: 4px 12px 0 16px;
  font-size: 15px;
  font-weight: 500;
  line-height: 22px;
  color: var(--dsw-alias-label-primary);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-height: 200px;
  overflow-y: auto;
}

textarea {
  width: 100%;
  min-height: 48px;
  max-height: 336px;
  border: none;
  outline: none;
  resize: none;
  background: transparent;
  font-family: inherit;
  font-size: 16px;
  line-height: 24px;
  color: var(--dsw-alias-label-primary);
  padding: 0 12px 0 16px;
  box-sizing: border-box;
}

textarea::placeholder {
  color: var(--dsw-alias-label-caption);
}

.row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 8px;
}

.grow {
  flex: 1;
}

.ghostBtn {
  height: 32px;
  padding: 0 12px;
  border: 1px solid var(--dsw-alias-border-l2);
  border-radius: 16px;
  background: transparent;
  font-family: inherit;
  font-size: 13px;
  color: var(--dsw-alias-label-secondary);
  cursor: pointer;
}

.ghostBtn:hover {
  background: var(--dsw-alias-interactive-bg-hover);
}

.primary {
  height: 34px;
  padding: 0 16px;
  border: none;
  border-radius: 17px;
  background: var(--dsw-alias-button-info-fill);
  color: var(--dsw-alias-label-primary-foreground);
  font-family: inherit;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
}

.primary:hover:not(:disabled) {
  background: var(--dsw-alias-button-info-hover);
}

.primary:disabled {
  opacity: 0.4;
  cursor: default;
}
</style>
