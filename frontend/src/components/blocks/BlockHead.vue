<template>
  <div class="block-card">
    <div class="block-head">
      <n-tag size="small" :bordered="false" type="info">{{ block.role || 'system' }}</n-tag>
      <n-tag size="small" :bordered="false">{{ label }}</n-tag>
      <slot name="extra" />
      <span v-if="!block.closed" class="stream-cursor" title="生成中" />
      <span class="spacer" />
      <slot name="actions" />
    </div>
    <slot />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { NTag } from 'naive-ui'
import type { Block } from '../../types'

const props = defineProps<{ block: Block; label?: string }>()
const label = computed(() => props.label || props.block.type)
</script>

<style scoped>
.spacer {
  flex: 1;
}
</style>
