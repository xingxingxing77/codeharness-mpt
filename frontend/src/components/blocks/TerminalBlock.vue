<template>
  <BlockHead :block="block" label="终端">
    <div class="term">
      <div v-if="block.cmd" class="cmd-line">$ {{ block.cmd }}</div>
      <div v-for="(line, i) in block.lines" :key="i">{{ line }}</div>
      <span v-if="!block.closed" class="stream-cursor" />
    </div>
  </BlockHead>
</template>

<script setup lang="ts">
import type { Block } from '../../types'
import BlockHead from './BlockHead.vue'

defineProps<{ block: Block }>()
</script>

<style scoped>
.cmd-line {
  color: #63e2b7;
}
</style>
