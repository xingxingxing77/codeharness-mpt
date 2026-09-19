<template>
  <div class="spage">
    <h1 class="sptitle">Git</h1>

    <div class="sgroup" style="margin-top: 18px">
      <Row title="分支前缀" desc="在 MetaGPT 中创建新分支时使用的前缀">
        <input v-model="p.branchPrefix" class="tin" />
      </Row>
      <Row title="拉取请求合并方法" desc="选择 MetaGPT 合并拉取请求的方法">
        <div class="seg">
          <span :class="{ on: p.prMerge === '合并' }" @click="p.prMerge = '合并'">合并</span>
          <span :class="{ on: p.prMerge === '压缩' }" @click="p.prMerge = '压缩'">压缩</span>
        </div>
      </Row>
      <Row title="在侧边栏显示 PR 图标" desc="在侧边栏的对话行中显示 PR 状态图标">
        <Toggle v-model="p.prIcon" />
      </Row>
      <Row title="始终强制推送" desc="从 MetaGPT 推送时使用 --force-with-lease 参数">
        <Toggle v-model="p.forcePush" />
      </Row>
      <Row title="创建草稿拉取请求" desc="从 MetaGPT 创建 PR 时默认使用草稿拉取请求">
        <Toggle v-model="p.draftPr" />
      </Row>
      <Row title="自动删除旧工作树" desc="推荐大多数用户启用。仅当你需要手动管理旧工作树和磁盘使用空间时，再关闭此功能。">
        <Toggle v-model="p.autoDeleteTrees" />
      </Row>
      <Row
        title="自动删除限制"
        desc="自动清理较旧工作树前保留的 MetaGPT 工作树数量。MetaGPT 会在删除前为工作树创建快照，因此被清理的工作树始终均可恢复。"
      >
        <input v-model.number="p.treeLimit" type="number" class="tin" style="width: 100px" />
      </Row>
    </div>

    <div class="sec-bar">
      <div>
        <div class="ssec" style="margin: 0">提交指令</div>
        <div class="ssec-sub" style="margin-top: 3px">已添加到提交信息生成提示中</div>
      </div>
      <span class="grow" />
      <button class="sbtn" :disabled="!p.commitGuide.trim()" @click="save('提交指令')">保存</button>
    </div>
    <textarea v-model="p.commitGuide" class="tarea" style="min-height: 120px" placeholder="添加提交消息指引..." />

    <div class="sec-bar">
      <div>
        <div class="ssec" style="margin: 0">拉取请求指令</div>
        <div class="ssec-sub" style="margin-top: 3px">已添加到 PR 标题/描述生成提示中</div>
      </div>
      <span class="grow" />
      <button class="sbtn" :disabled="!p.prGuide.trim()" @click="save('拉取请求指令')">保存</button>
    </div>
    <textarea v-model="p.prGuide" class="tarea" style="min-height: 120px" placeholder="添加拉取请求指引..." />
  </div>
</template>

<script setup lang="ts">
import { useToastStore } from '../../stores/toast'
import { useSettingsStore } from '../../stores/settings'
import Row from './Row.vue'
import Toggle from './Toggle.vue'

const store = useSettingsStore()
const p = store.prefs
const message = useToastStore()

function save(name: string) {
  store.persist()
  message.push(`已保存${name}`, 'success')
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
