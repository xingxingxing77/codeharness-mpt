<template>
  <BlockHead :block="block">
    <pre class="code-view">{{ text }}</pre>
  </BlockHead>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { Block } from '../../types'
import BlockHead from './BlockHead.vue'

const props = defineProps<{ block: Block }>()
const text = computed(() => {
  if (props.block.tokens.length) return props.block.tokens.join('')
  return JSON.stringify(props.block.raw, null, 2)
})
</script>
