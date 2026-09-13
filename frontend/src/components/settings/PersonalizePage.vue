<template>
  <div class="spage">
    <h1 class="sptitle">个性化</h1>

    <div class="sgroup" style="margin-top: 18px">
      <Row title="个性" desc="选择 MetaGPT 回复的默认语气">
        <SelectBox v-model="p.tone" :options="['亲和', '简洁', '幽默', '严肃']" />
      </Row>
    </div>

    <div class="ssec">自定义指令</div>
    <div class="ssec-sub">为你的项目向 MetaGPT 提供额外说明和上下文。<span class="slink">了解更多</span></div>
    <textarea v-model="p.instructions" class="tarea" placeholder="添加自定义指令..." />
    <div class="save-row">
      <button class="sbtn" :disabled="!dirty" @click="save">保存</button>
    </div>

    <div class="ssec">记忆（实验性）</div>
    <div class="ssec-sub">设置 MetaGPT 如何收集、保留和整合记忆。<span class="slink">了解更多</span></div>
    <div class="sgroup">
      <Row title="启用记忆" desc="从聊天中生成新记忆，并将其带入新聊天">
        <Toggle v-model="p.memoryEnabled" />
      </Row>
      <Row title="跳过工具辅助对话" desc="请勿从使用了 MCP 工具或网页搜索的对话中生成记忆">
        <Toggle v-model="p.memorySkipTools" />
      </Row>
      <Row title="重置记忆" desc="删除所有 MetaGPT 记忆">
        <button class="sbtn red" @click="resetMemory">重置</button>
      </Row>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useMessage } from 'naive-ui'
import { useSettingsStore } from '../../stores/settings'
import Row from './Row.vue'
import SelectBox from './SelectBox.vue'
import Toggle from './Toggle.vue'

const store = useSettingsStore()
const p = store.prefs
const message = useMessage()

const dirty = computed(() => !!p.instructions.trim())

function save() {
  store.persist()
  message.success('已保存自定义指令')
}

function resetMemory() {
  message.info('演示环境：记忆重置未执行')
}
</script>

<style scoped>
.save-row {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
</style>
