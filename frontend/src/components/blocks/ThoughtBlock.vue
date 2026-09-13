<template>
  <BlockHead :block="block" :label="typeName">
    <div class="thought-text">{{ text }}<span v-if="!block.closed" class="stream-cursor" /></div>
  </BlockHead>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { Block } from '../../types'
import BlockHead from './BlockHead.vue'

const props = defineProps<{ block: Block }>()

const TYPE_NAMES: Record<string, string> = {
  react: '决策',
  quick: '回复',
  classify: '分析'
}
const typeName = computed(() => TYPE_NAMES[props.block.obj?.type] || '思考')
const text = computed(() => props.block.tokens.join(''))
</script>

<style scoped>
.thought-text {
  font-size: 13.5px;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
