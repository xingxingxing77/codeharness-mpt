<template>
  <BlockHead :block="block" label="图表">
    <n-image v-if="url" :src="url" lazy style="max-width: 100%; border-radius: 6px" />
    <div v-else class="loading">图片生成中…</div>
  </BlockHead>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { NImage } from 'naive-ui'
import { useSessionStore } from '../../stores/sessions'
import type { Block } from '../../types'
import BlockHead from './BlockHead.vue'

const props = defineProps<{ block: Block }>()
const store = useSessionStore()
const url = computed(() => (props.block.path ? store.workspaceUrl(props.block.path) : ''))
</script>

<style scoped>
.loading {
  opacity: 0.5;
  font-size: 12.5px;
}
</style>
