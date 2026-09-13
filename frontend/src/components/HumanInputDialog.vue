<template>
  <n-modal
    :show="!!store.humanQuestion"
    preset="dialog"
    type="warning"
    title="智能体请求人工介入"
    :show-icon="true"
    :mask-closable="false"
    @update:show="store.dismissHuman"
  >
    <div class="q">{{ question }}</div>
    <n-input v-model:value="answer" type="textarea" :rows="3" placeholder="输入你的回复（如 yes / no / 补充信息）" />
    <div class="tip">提示：回复包含 “stop” 可让该智能体终止当前任务；不回复将超时后由智能体自行判断。</div>
    <template #action>
      <n-button @click="store.dismissHuman">稍后处理</n-button>
      <n-button type="primary" :disabled="!answer.trim()" @click="submit">回复</n-button>
    </template>
  </n-modal>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { NButton, NInput, NModal } from 'naive-ui'
import { useSessionStore } from '../stores/sessions'

const store = useSessionStore()
const answer = ref('')

const question = computed(() => String(store.humanQuestion?.value ?? ''))

watch(
  () => store.humanQuestion,
  (v) => {
    if (v) answer.value = ''
  }
)

async function submit() {
  const ok = await store.answerHuman(answer.value.trim())
  if (!ok) store.dismissHuman()
}
</script>

<style scoped>
.q {
  white-space: pre-wrap;
  word-break: break-word;
  margin-bottom: 12px;
  font-size: 13.5px;
  line-height: 1.6;
}

.tip {
  margin-top: 8px;
  font-size: 12px;
  opacity: 0.55;
}
</style>
