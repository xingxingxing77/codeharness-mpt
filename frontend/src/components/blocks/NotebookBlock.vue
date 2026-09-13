<template>
  <BlockHead :block="block" label="Notebook">
    <div v-if="cells.length" class="code-view">
      <div v-for="(c, i) in cells" :key="i">{{ c }}</div>
    </div>
    <div v-else class="code-view">{{ block.tokens.join('') || '执行中…' }}</div>
    <div v-if="block.path" class="nb-path">notebook: {{ block.path }}</div>
  </BlockHead>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { Block } from '../../types'
import BlockHead from './BlockHead.vue'

const props = defineProps<{ block: Block }>()
const cells = computed(() =>
  props.block.tokens
    .filter((t) => t && typeof t === 'string' && t.length > 200)
    .map((t) => t)
)
</script>

<style scoped>
.nb-path {
  margin-top: 6px;
  font-size: 12px;
  opacity: 0.6;
  word-break: break-all;
}
</style>
