<template>
  <span class="dot" :class="state" aria-hidden="true">
    <svg v-if="state === 'ongoing'" viewBox="0 0 10 10" shape-rendering="crispEdges">
      <rect
        v-for="(c, i) in CELLS"
        :key="i"
        :x="c[0]"
        :y="c[1]"
        width="2"
        height="2"
        :style="{ animationDelay: `${(i - 8) * 125}ms` }"
      />
    </svg>
  </span>
</template>

<script setup lang="ts">
defineProps<{ state: 'done' | 'warning' | 'error' | 'ongoing' }>()

// 8 个 2×2 方块沿 10×10 网格拉出像素追逐
const CELLS: [number, number][] = [
  [0, 0],
  [2, 0],
  [4, 0],
  [6, 0],
  [8, 0],
  [8, 2],
  [8, 4],
  [8, 6]
]
</script>

<style scoped>
.dot {
  position: relative;
  flex: none;
  width: 10px;
  height: 10px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

/* 实色态：10px 光晕（currentColor @10%）+ 6px 芯（内缩 20%） */
.dot::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: 50%;
  background: currentColor;
  opacity: 0.1;
}

.dot.done {
  color: var(--dsw-alias-state-success-primary);
}
.dot.warning {
  color: var(--dsw-alias-state-warn-primary);
}
.dot.error {
  color: var(--dsw-alias-state-error-primary);
}
.dot.ongoing {
  color: var(--dsw-alias-state-business-primary);
}

.dot.done::after,
.dot.warning::after,
.dot.error::after {
  content: '';
  position: absolute;
  inset: 20%;
  border-radius: 50%;
  background: currentColor;
}

.dot svg {
  width: 10px;
  height: 10px;
  overflow: visible;
}

.dot rect {
  fill: currentColor;
  animation: chase 1s infinite step-end;
}

@keyframes chase {
  0% {
    opacity: 1;
  }
  25% {
    opacity: 0.6;
  }
  50% {
    opacity: 0.35;
  }
  75% {
    opacity: 0.15;
  }
  100% {
    opacity: 1;
  }
}

@media (prefers-reduced-motion: reduce) {
  .dot rect {
    animation: none;
    opacity: 0.6;
  }
}
</style>
