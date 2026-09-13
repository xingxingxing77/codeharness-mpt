<template>
  <div class="spage">
    <h1 class="sptitle">配置</h1>
    <p class="spdesc">配置审批策略和沙盒设置 <span class="slink">了解更多</span></p>

    <div class="ssec">自定义 config.toml 设置</div>
    <div class="cfg-bar">
      <SelectBox v-model="profile" :options="['用户配置', '项目配置']" style="min-width: 200px" />
      <span class="grow" />
      <span class="slink" @click="openToml">打开 config.toml <Icon name="external" :size="12" /></span>
    </div>
    <div class="sgroup">
      <Row title="批准策略" desc="选择 MetaGPT 何时请求批准">
        <SelectBox v-model="p.approval" :options="['On request', 'Always', 'Never']" />
      </Row>
      <Row title="沙盒设置" desc="选择 MetaGPT 的命令执行权限">
        <SelectBox v-model="p.sandbox" :options="['Read only', 'Workspace write', 'Full access']" />
      </Row>
    </div>

    <div class="ssec">工作空间依赖项</div>
    <div class="sgroup">
      <Row title="当前版本">
        <span class="s-desc" style="font-size: 12.5px; color: var(--text-3)">0.1.0</span>
      </Row>
      <Row title="MetaGPT 依赖项" desc="允许 MetaGPT 安装并提供随附的 Node.js 和 Python 工具">
        <Toggle v-model="p.depsToggle" />
      </Row>
      <Row title="诊断 MetaGPT 工作空间中的问题" desc="检查当前捆绑包并记录诊断日志">
        <button class="sbtn" @click="diagnose">
          <Icon name="search" :size="13" />
          诊断
        </button>
      </Row>
      <Row title="重置并安装工作空间" desc="删除本地捆绑包，重新下载后再重新加载工具">
        <button class="sbtn red" @click="reinstall">
          <Icon name="download" :size="13" />
          重新安装
        </button>
      </Row>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useMessage } from 'naive-ui'
import { useSettingsStore } from '../../stores/settings'
import Icon from '../Icon.vue'
import Row from './Row.vue'
import SelectBox from './SelectBox.vue'
import Toggle from './Toggle.vue'

const p = useSettingsStore().prefs
const message = useMessage()
const profile = ref('用户配置')

function openToml() {
  message.info('演示环境：config.toml 打开入口未接线')
}

function diagnose() {
  message.success('诊断完成：未发现问题')
}

function reinstall() {
  message.info('演示环境：重新安装未执行')
}
</script>

<style scoped>
.cfg-bar {
  display: flex;
  align-items: center;
  margin-bottom: 10px;
}

.grow {
  flex: 1;
}

.slink {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
}
</style>
