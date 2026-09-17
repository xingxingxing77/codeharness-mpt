<template>
  <div ref="host" class="mmd-mount"><div class="mmd-load">图渲染中…</div></div>
</template>

<script setup lang="ts">
/** 一个 .mmd 源串 → 可切换图形/源码、可导出 SVG 的图。
 *  渲染 DOM 复用 utils/mermaid 的 mountFigure——与 markdown 围栏水合同一套样式与行为。 */
import { onMounted, ref, watch } from 'vue'
import { mountFigure } from '../utils/mermaid'

const props = defineProps<{ src: string; name?: string }>()
const host = ref<HTMLElement>()

async function rerender() {
  if (host.value) await mountFigure(host.value, props.src || '', props.name || 'diagram')
}

onMounted(rerender)
watch(() => [props.src, props.name], rerender)
</script>
