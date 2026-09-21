<template>
  <!-- B5 常驻目标条。参照系没有「用户点完成」这一枚按钮（它的 complete 走 authority 门），
       所以按钮是本仓新加的；条的几何与文案逐条取源值：
       packages/client/ui-goal/src/client/GoalBar.module.css 的 .bar/.label/.objective/.iconBtn，
       文案取 GoalBar.tsx 用的 locales.ts:5-15（'进行中的目标'/'目标内容'/'保存目标'/'取消编辑'/
       '编辑目标'/'清除目标'）与 ui-conversation/src/client/locales.ts:15-16 的那句 composer 提示。 -->
  <div v-if="!done" class="bar" :class="{ empty: !goal }">
    <template v-if="!editing">
      <span class="label">{{ goal ? '进行中的目标' : '目标内容' }}</span>
      <input
        v-if="!goal"
        v-model="draft"
        class="objectiveInput"
        :placeholder="'输入目标，智能体将持续执行'"
        :aria-label="'目标内容'"
        @keyup.enter="save"
      />
      <span v-else class="objective" :title="goal">{{ goal }}</span>
      <button v-if="!goal" class="btn" type="button" :disabled="!draft.trim() || busy" @click="save">
        保存目标
      </button>
      <button v-else class="btn" type="button" :disabled="busy" @click="editing = true">编辑目标</button>
      <button v-if="goal" class="btn ok" type="button" :disabled="busy" @click="complete">确认完成</button>
      <button v-if="goal" class="btn" type="button" :disabled="busy" @click="clear">清除目标</button>
    </template>
    <template v-else>
      <span class="label">编辑目标</span>
      <input v-model="draft" class="objectiveInput" :aria-label="'目标内容'" @keyup.enter="save" />
      <button class="btn" type="button" :disabled="!draft.trim() || busy" @click="save">保存目标</button>
      <button class="btn" type="button" :disabled="busy" @click="cancel">取消编辑</button>
    </template>
    <span v-if="err" class="error">{{ err }}</span>
  </div>
</template>

<script setup lang="ts">
/** 目标只有三个动作：设/改、完成、清除——全部由人点（口径 2026-09-20：模型没有写完成的出口）。
 *  写完立刻 mergeSessionLocal 是自己回执自己：SSE 那一跳要等事件回来才更新，中间会闪回旧值。 */
import { computed, ref, watch } from 'vue'
import { api } from '../../api/client'
import { useSessionStore } from '../../stores/sessions'
import type { Session } from '../../types'

const store = useSessionStore()

const goal = computed(() => store.current?.goal || '')
const done = computed(() => !!store.current?.goal_done_at)
const sid = () => store.current!.id
const draft = ref(goal.value)
const editing = ref(false)
const busy = ref(false)
const err = ref('')

watch(goal, (v) => { if (!editing.value) draft.value = v })

/** 回执用**响应体里的两个字段**，不自己拼：`goal_done_at` 的格式是后端 `_now()` 那一份，
 *  前端再抄一种格式就是第二个真值（下次比较会静默不相等）。 */
async function run(fn: () => Promise<Session>) {
  if (busy.value || !store.current) return
  const sid = store.current.id
  busy.value = true
  err.value = ''
  try {
    const s = await fn()
    store.mergeSessionLocal(sid, { goal: s.goal, goal_done_at: s.goal_done_at })
    editing.value = false
  } catch (e) {
    err.value = (e as Error).message        // 状态码不猜文案：F-E 那条口径（req() 已挂 err.status）
  } finally {
    busy.value = false
  }
}

function save() {
  const text = draft.value.trim()
  if (!text) return
  run(() => api.setGoal(sid(), text))
}
function complete() {
  run(() => api.completeGoal(sid()))
}
function clear() {
  run(() => api.clearGoal(sid()))
}
function cancel() {
  draft.value = goal.value
  editing.value = false
}
</script>

<style scoped>
.bar {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 36px;
  padding: 4px 5px 4px 12px;
  border: 1px solid var(--dsw-alias-border-l1);
  border-radius: 12px;
  background: var(--dsw-specific-tip);
  margin-bottom: 8px;
}

.label {
  font-size: 13px;
  line-height: 24px;
  font-weight: 500;
  color: var(--dsw-alias-label-primary);
  white-space: nowrap;
}

.objective {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--dsw-alias-label-primary-dimmed);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.objectiveInput {
  flex: 1;
  min-width: 0;
  height: 26px;
  padding: 0 8px;
  border: 1px solid var(--dsw-alias-border-l2);
  border-radius: 6px;
  background: var(--dsw-specific-menu);
  font-family: inherit;
  font-size: 13px;
  color: var(--dsw-alias-label-primary);
}

/* 参照系是 28×28 圆按钮 + 图标；这里三枚都带中文标签（它没有「用户点完成」这一枚），
   所以只取它的高与圆角，不硬塞图标位。 */
.btn {
  height: 28px;
  padding: 0 10px;
  border: none;
  border-radius: 999px;
  background: transparent;
  font-family: inherit;
  font-size: 13px;
  color: var(--dsw-alias-label-tertiary);
  cursor: pointer;
  white-space: nowrap;
}

.btn:hover:not(:disabled) {
  background: var(--dsw-alias-interactive-bg-hover);
}

.btn:disabled {
  opacity: 0.5;
  cursor: default;
}

.btn.ok {
  color: var(--dsw-alias-state-success-primary);
}

.error {
  font-size: 12px;
  line-height: 20px;
  color: var(--dsw-alias-state-error-primary);
}
</style>
