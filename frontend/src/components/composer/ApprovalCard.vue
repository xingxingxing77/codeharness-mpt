<template>
  <div class="card" data-composer-card>
    <div class="strip">
      <span class="dot" />
      <span class="stripText">等待审批</span>
      <span class="tier">{{ tierLabel(item.tier_session) }} · 需 {{ tierLabel(item.tier_required) }}</span>
    </div>
    <div class="headline">{{ item.reason || `工具 ${item.tool} 请求越权执行` }}</div>
    <pre class="cmd">{{ item.args_preview }}</pre>

    <div class="row">
      <span class="grow" />
      <button class="ghostBtn" :disabled="busy" @mousedown.prevent="answer('rejected')">拒绝</button>
      <button class="primary" :disabled="busy" @mousedown.prevent="answer('allowed-once')">
        {{ busy ? '提交中…' : '允许一次' }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
/** 工具审批的 takeover：与 QuestionCard 占同一个 sticky 座位，所以正文不会跳动。
 *  只有「允许一次 / 拒绝」两个钮——后端台账没有 always-allow 这个概念，就不画第三个假按钮。
 *  档位与判定表都在 `codeharness/tools/_approval.py`，这里只翻译成人话。 */
import { ref } from 'vue'
import { useSessionStore } from '../../stores/sessions'
import { useToastStore } from '../../stores/toast'
import type { ApprovalItem } from '../../types'

const props = defineProps<{ item: ApprovalItem }>()

const store = useSessionStore()
const toast = useToastStore()
const busy = ref(false)

const TIERS: Record<string, string> = {
  readonly: '只读', workspace_write: '工作区写入', full_access: '全面访问'
}
const tierLabel = (t: string) => TIERS[t] || t

async function answer(outcome: 'allowed-once' | 'rejected') {
  if (busy.value) return
  busy.value = true
  try {
    await store.respondApproval(props.item.id, outcome)
    toast.push(outcome === 'allowed-once' ? `已允许 ${props.item.tool} 执行一次` : '已拒绝，智能体继续跑',
               outcome === 'allowed-once' ? 'success' : 'info')
  } catch (e) {
    toast.push((e as Error).message || '回执未被接收', 'error')
  } finally {
    busy.value = false
  }
}
</script>

<style scoped>
.card {
  display: flex;
  flex-direction: column;
  gap: 10px;
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

.tier {
  margin-left: auto;
  font-size: 12px;
  line-height: 18px;
  color: var(--dsw-alias-state-warn-label);
  opacity: 0.75;
}

.headline {
  padding: 2px 12px 0 16px;
  font-size: 15px;
  font-weight: 500;
  line-height: 22px;
  color: var(--dsw-alias-label-primary);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

/* 命令原文用等宽：审批要看的就是「到底会跑什么」 */
.cmd {
  margin: 0 16px;
  padding: 6px 10px;
  border-radius: 8px;
  background: var(--dsw-alias-bg-layer-2);
  font-family: var(--mono);
  font-size: 12px;
  line-height: 18px;
  color: var(--dsw-alias-label-secondary);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-height: 120px;
  overflow-y: auto;
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

.primary:disabled,
.ghostBtn:disabled {
  opacity: 0.4;
  cursor: default;
}
</style>
