<template>
  <div class="spage">
    <h1 class="sptitle">MCP 服务器</h1>
    <p class="spdesc">连接外部工具和数据源。<span class="slink">了解更多</span>。</p>

    <div class="sec-bar">
      <span class="ssec" style="margin: 0">服务器</span>
      <span class="grow" />
      <button class="sbtn" @click="add">
        <Icon name="plus" :size="13" />
        添加服务器
      </button>
    </div>
    <div class="sgroup">
      <Row title="node_repl">
        <Icon name="gear" :size="15" style="color: var(--text-3)" />
        <Toggle v-model="p.mcpRepl" />
      </Row>
    </div>

    <div class="ssec">来自插件</div>
    <div class="sgroup">
      <Row title="codex_apps" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { useToastStore } from '../../stores/toast'
import { useSettingsStore } from '../../stores/settings'
import Icon from '../Icon.vue'
import Row from './Row.vue'
import Toggle from './Toggle.vue'

const p = useSettingsStore().prefs
const message = useToastStore()

function add() {
  message.push('演示环境：MCP 服务器添加未开放')
}
</script>

<style scoped>
.sec-bar {
  display: flex;
  align-items: center;
  margin: 30px 0 10px;
}

.grow {
  flex: 1;
}
</style>
