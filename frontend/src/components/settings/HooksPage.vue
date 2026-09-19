<template>
  <div class="spage">
    <h1 class="sptitle">钩子</h1>
    <p class="spdesc">在智能体生命周期的关键节点自动运行自定义脚本。<span class="slink">了解更多</span></p>

    <div class="sec-bar">
      <span class="ssec" style="margin: 0">已配置的钩子</span>
      <span class="grow" />
      <button class="sbtn" @click="add">
        <Icon name="plus" :size="13" />
        添加钩子
      </button>
    </div>
    <div class="sgroup empty">暂无钩子。点击右上角"添加钩子"开始配置。</div>
  </div>
</template>

<script setup lang="ts">
import { useToastStore } from '../../stores/toast'
import Icon from '../Icon.vue'

const message = useToastStore()

function add() {
  message.push('演示环境：钩子配置未开放')
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
