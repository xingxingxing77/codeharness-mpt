<template>
  <div class="spage">
    <h1 class="sptitle">键盘快捷键</h1>
    <p class="spdesc">为常用操作配置全局与应用内快捷键。留空则保持关闭。</p>

    <div class="sgroup" style="margin-top: 18px">
      <Row v-for="k in keys" :key="k.title" :title="k.title" :desc="k.desc">
        <span style="font-size: 12.5px; color: var(--text-3)">未设置</span>
        <button class="sbtn" @click="record(k.title)">设置</button>
      </Row>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useMessage } from 'naive-ui'
import Row from './Row.vue'

const message = useMessage()

const keys = [
  { title: '打开命令面板', desc: '快速执行任意命令' },
  { title: '新建对话', desc: '开始一个新会话' },
  { title: '搜索', desc: '搜索项目与对话' },
  { title: '切换终端面板', desc: '显示或隐藏底部终端' },
  { title: '切换工具面板', desc: '显示或隐藏右侧工具面板' }
]

function record(title: string) {
  message.info(`演示环境：「${title}」快捷键录制未开放`)
}
</script>
