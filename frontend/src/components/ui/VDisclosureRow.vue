<template>
  <div class="wrap" :data-state="state">
    <div
      class="row"
      role="button"
      tabindex="0"
      :aria-expanded="open"
      @click="$emit('update:open', !open)"
      @keydown.enter.prevent="$emit('update:open', !open)"
      @keydown.space.prevent="$emit('update:open', !open)"
    >
      <span class="slot">
        <VStateDot v-if="state === 'error'" state="error" />
        <template v-else>
          <DsIcon v-if="icon" :name="icon" :size="16" class="lead" />
          <DsIcon name="chevron-down" :size="14" class="chev" :class="{ open }" />
        </template>
      </span>
      <span class="title" :class="{ error: state === 'error' }">{{ title }}</span>
      <span v-if="state === 'running'" class="sep" />
      <span v-if="state !== 'running'" class="dot">·</span>
      <span class="summary" :class="{ following }"><slot name="summary">{{ summary }}</slot></span>
      <span class="trailing"><slot name="trailing" /></span>
    </div>
    <div v-if="open" class="body"><slot /></div>
  </div>
</template>

<script setup lang="ts">
/** 参考项目 DisclosureRow + ToolRow：24px 单行，整行是开关，展开体是兄弟节点
 *  （所以卡里的点击不会把行收起来）。hover 时前导图标 100ms 交叉淡成 chevron。 */
import DsIcon from './DsIcon.vue'
import VStateDot from './VStateDot.vue'

withDefaults(
  defineProps<{
    title: string
    summary?: string
    icon?: string
    state?: 'running' | 'ok' | 'error' | 'stopped'
    open?: boolean
    /** 流式期摘要跟着最新一行滚到行尾 */
    following?: boolean
  }>(),
  { state: 'ok', open: false }
)
defineEmits<{ 'update:open': [v: boolean] }>()
</script>

<style scoped>
.wrap {
  position: relative;
  display: flex;
  flex-direction: column;
}

.row {
  position: relative;
  overflow: hidden;
  display: flex;
  align-items: center;
  gap: 6px;
  height: 24px;
  border-radius: 6px;
  cursor: pointer;
  color: var(--dsw-alias-label-secondary);
  font-size: 14px;
  line-height: 24px;
}

.row:focus-visible {
  outline: 2px solid var(--dsw-alias-state-business-primary);
  outline-offset: 1px;
}

.slot {
  position: relative;
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  color: var(--dsw-alias-label-tertiary);
}

/* 交叉淡入：静止显图标，hover 或已展开显 chevron（用行上的 aria-expanded 驱动） */
.lead,
.chev {
  position: absolute;
  transition: opacity 100ms var(--ds-ease-in-out);
}

.lead {
  opacity: 1;
}

.chev {
  opacity: 0;
}

.row:hover .lead,
.row[aria-expanded='true'] .lead {
  opacity: 0;
}

.row:hover .chev,
.row[aria-expanded='true'] .chev {
  opacity: 1;
}

.title {
  flex: none;
  font-weight: 400;
  color: var(--dsw-alias-label-secondary);
}

.title.error {
  color: var(--dsw-alias-state-error-primary);
}

.dot {
  flex: none;
  color: var(--dsw-alias-label-caption);
}

.sep {
  flex: none;
  width: 2px;
  height: 2px;
  border-radius: 50%;
  background: var(--dsw-alias-label-caption);
}

.summary {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  color: var(--dsw-alias-label-tertiary);
}

/* 跟随最新一行时不能用省略号截断，改由 scrollLeft 定位 */
.summary.following {
  text-overflow: clip;
}

.trailing {
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.body {
  padding: 4px 0 4px 22px;
}

/* 运行中行扫光：300px 带、2.6s ease-out 无限，尾 10% 留一拍再回头 */
.wrap[data-state='running'] .row::after {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  left: -300px;
  width: 300px;
  background: linear-gradient(
    90deg,
    transparent,
    color-mix(in srgb, var(--dsw-alias-bg-base) 60%, transparent) 55%,
    transparent
  );
  animation: sweep 2.6s ease-out infinite;
  pointer-events: none;
}

@keyframes sweep {
  0% {
    left: -300px;
  }
  90%,
  100% {
    left: 100%;
  }
}

@media (prefers-reduced-motion: reduce) {
  .wrap[data-state='running'] .row::after {
    animation: none;
    opacity: 0;
  }
}
</style>
