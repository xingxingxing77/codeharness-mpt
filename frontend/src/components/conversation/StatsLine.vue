<template>
  <div v-if="groups.length" class="band">
    <VTooltip class="tip" :label="line" side="top" :delay="500" :disabled="!truncated">
      <div ref="rootEl" class="root">
        <template v-for="(g, i) in groups" :key="g">
          <span v-if="i" class="sep" aria-hidden="true">|</span>
          <span>{{ g }}</span>
        </template>
      </div>
    </VTooltip>
  </div>
</template>

<script setup lang="ts">
/** 参考项目 chat/StatsLine.tsx：composer 卡下方的会话统计条（composer.dock 的 footer）。
 *  一行居中 12/20 三级文字，竖线分组；超宽时省略号收尾，hover 500ms 才出全文，
 *  且**只有真的被截断才启用** tooltip——没截断就不该有第二份同样的字。 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import VTooltip from '../ui/VTooltip.vue'
import { useSessionStore } from '../../stores/sessions'
import { deriveStats, statsGroups, tokenTotals } from '../../utils/stats'

const store = useSessionStore()

const groups = computed(() =>
  statsGroups(deriveStats(store.blockList, store.spans), tokenTotals(store.spans, store.cost))
)
const line = computed(() => groups.value.join(' | '))

const rootEl = ref<HTMLElement>()
const truncated = ref(false)

function measure() {
  const el = rootEl.value
  truncated.value = !!el && el.scrollWidth > el.clientWidth
}
let ro: ResizeObserver | undefined
watch(
  rootEl,
  async (el) => {
    ro?.disconnect()
    if (!el || typeof ResizeObserver === 'undefined') return
    await nextTick()
    measure()
    ro = new ResizeObserver(measure)
    ro.observe(el)
  },
  { immediate: true }
)
// 分组内容变了但宽度没变时（例如换会话后仍不溢出），ResizeObserver 不会回调，得自己量一次
watch(line, () => nextTick(measure))
onBeforeUnmount(() => ro?.disconnect())
</script>

<style scoped>
/* .band 承担居中，VTooltip 的 .wrap 是 inline-flex，必须在它下面把块级布局还回来 */
.band {
  width: 100%;
  max-width: var(--dsh-chat-content-width);
  margin: 0 auto;
  box-sizing: border-box;
}
.band .tip {
  display: block;
  width: 100%;
}

.root {
  /* 块级而非 flex：text-overflow 只对块的行内内容生效，
     用 flex 的话超长时是从中间被裁断，而不是收在省略号里。 */
  display: block;
  text-align: center;
  width: 100%;
  box-sizing: border-box;
  padding: 4px calc(var(--dsh-composer-side-clearance) + 16px) 0;
  font-size: 12px;
  line-height: 20px;
  color: var(--dsw-alias-label-tertiary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.sep {
  color: var(--dsw-alias-separator-primary);
  margin: 0 10px;
}
</style>
