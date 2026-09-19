<template>
  <div class="spage">
    <h1 class="sptitle">工作树</h1>

    <div class="sec-bar">
      <span class="ssec" style="margin: 0">尚无工作树</span>
      <span class="grow" />
      <button class="icon-btn" title="刷新" @click="refresh">
        <Icon name="refresh" :size="15" />
      </button>
    </div>
    <div class="sgroup empty">Codex 创建的工作树将显示在此处。</div>
  </div>
</template>

<script setup lang="ts">
import { useToastStore } from '../../stores/toast'
import Icon from '../Icon.vue'

const message = useToastStore()

function refresh() {
  message.push('暂无工作树可刷新')
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

.icon-btn {
  border: none;
  background: none;
  width: 28px;
  height: 28px;
  border-radius: 7px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--text-3);
  cursor: pointer;
}

.icon-btn:hover {
  background: rgba(60, 52, 24, 0.06);
  color: var(--text-2);
}
</style>
