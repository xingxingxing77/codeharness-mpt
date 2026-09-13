<template>
  <n-modal
    v-model:show="ui.showCreate"
    preset="card"
    title="新建会话"
    style="width: 560px"
    :mask-closable="!creating"
  >
    <n-form label-placement="top">
      <n-form-item label="需求 / Idea" required>
        <n-input
          v-model:value="form.idea"
          type="textarea"
          :rows="4"
          placeholder="例如：开发一个 2048 小游戏，要求纯前端实现"
        />
      </n-form-item>
      <n-grid :cols="2" :x-gap="12">
        <n-gi>
          <n-form-item label="项目名（留空自动生成）">
            <n-input v-model:value="form.project_name" placeholder="如 game_2048" />
          </n-form-item>
        </n-gi>
        <n-gi>
          <n-form-item label="协作轮数">
            <n-input-number v-model:value="form.n_round" :min="1" :max="50" style="width: 100%" />
          </n-form-item>
        </n-gi>
      </n-grid>
      <n-form-item label="预算（美元，超限自动停止）">
        <n-input-number v-model:value="form.investment" :min="0.1" :max="100" :step="0.5" style="width: 200px" />
      </n-form-item>

      <n-collapse>
        <n-collapse-item title="高级：LLM 覆盖（留空使用服务端 config2.yaml）" name="llm">
          <n-grid :cols="2" :x-gap="12">
            <n-gi>
              <n-form-item label="model">
                <n-input v-model:value="form.llm.model" placeholder="gpt-4-turbo / deepseek-chat ..." />
              </n-form-item>
            </n-gi>
            <n-gi>
              <n-form-item label="api_type">
                <n-input v-model:value="form.llm.api_type" placeholder="openai" />
              </n-form-item>
            </n-gi>
            <n-gi>
              <n-form-item label="base_url">
                <n-input v-model:value="form.llm.base_url" placeholder="https://api.openai.com/v1" />
              </n-form-item>
            </n-gi>
            <n-gi>
              <n-form-item label="api_key">
                <n-input v-model:value="form.llm.api_key" type="password" show-password-on="click" placeholder="仅本次会话生效" />
              </n-form-item>
            </n-gi>
          </n-grid>
        </n-collapse-item>
      </n-collapse>
    </n-form>

    <template #footer>
      <div class="footer">
        <span v-if="error" class="error">{{ error }}</span>
        <n-button :disabled="creating || !form.idea.trim()" type="primary" :loading="creating" @click="submit">
          创建并开始
        </n-button>
      </div>
    </template>
  </n-modal>
</template>

<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import {
  NButton,
  NCollapse,
  NCollapseItem,
  NForm,
  NFormItem,
  NGi,
  NGrid,
  NInput,
  NInputNumber,
  NModal
} from 'naive-ui'
import { useSessionStore } from '../stores/sessions'
import { useUiStore } from '../stores/ui'

const store = useSessionStore()
const ui = useUiStore()
const creating = ref(false)
const error = ref('')

const form = reactive({
  idea: '',
  project_name: '',
  n_round: 5,
  investment: 3.0,
  llm: { model: '', api_type: '', base_url: '', api_key: '' } as Record<string, string>
})

// 首页输入卡通过 + 按钮进高级设置时，预填已输入的需求
watch(
  () => ui.showCreate,
  (v) => {
    if (v && ui.createIdea) {
      form.idea = ui.createIdea
      ui.createIdea = ''
    }
  }
)

function cleanOverride() {
  const out: Record<string, string> = {}
  for (const [k, v] of Object.entries(form.llm)) if (v && v.trim()) out[k] = v.trim()
  return out
}

async function submit() {
  error.value = ''
  creating.value = true
  try {
    await store.createSession({
      idea: form.idea.trim(),
      project_name: form.project_name.trim() || undefined,
      investment: form.investment,
      n_round: form.n_round,
      llm: cleanOverride()
    })
    await store.start()
    ui.showCreate = false
    Object.assign(form, { idea: '', project_name: '', n_round: 5, investment: 3.0 })
    form.llm = { model: '', api_type: '', base_url: '', api_key: '' }
  } catch (e: any) {
    error.value = e.message || String(e)
  } finally {
    creating.value = false
  }
}
</script>

<style scoped>
.footer {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 12px;
}

.error {
  color: #e88080;
  font-size: 12.5px;
  margin-right: auto;
}
</style>
