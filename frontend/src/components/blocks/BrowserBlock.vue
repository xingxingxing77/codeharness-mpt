<template>
  <BlockHead :block="block" label="浏览器">
    <div class="browser-info">
      <template v-if="block.page">
        <div>
          <b>{{ block.page.title }}</b>
        </div>
        <a :href="block.page.page_url" target="_blank" rel="noreferrer">{{ block.page.page_url }}</a>
        <div class="note">（截图暂不在时间线渲染，可用工作区面板查看产物）</div>
      </template>
      <a v-else-if="block.url" :href="block.url" target="_blank" rel="noreferrer">{{ block.url }}</a>
      <div v-else class="loading">浏览中…</div>
    </div>
  </BlockHead>
</template>

<script setup lang="ts">
import type { Block } from '../../types'
import BlockHead from './BlockHead.vue'

defineProps<{ block: Block }>()
</script>

<style scoped>
.browser-info {
  font-size: 13px;
  word-break: break-all;
}

.note {
  opacity: 0.5;
  font-size: 12px;
  margin-top: 4px;
}

.loading {
  opacity: 0.5;
  font-size: 12.5px;
}
</style>
