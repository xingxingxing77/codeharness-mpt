<template>
  <BlockHead :block="block" :label="`代码 · ${fileName}`">
    <pre class="code-view" v-html="html" />
  </BlockHead>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { langOfFilename, highlightCode } from '../../utils/render'
import type { Block } from '../../types'
import BlockHead from './BlockHead.vue'

const props = defineProps<{ block: Block }>()

const fileName = computed(() => props.block.meta?.filename || props.block.doc?.filename || 'untitled')
const code = computed(() => {
  if (props.block.doc?.content) return props.block.doc.content
  return props.block.tokens.join('')
})
const html = computed(() => highlightCode(code.value, langOfFilename(fileName.value)))
</script>
