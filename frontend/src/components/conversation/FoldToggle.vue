<template>
  <button
    type="button"
    class="fold"
    :aria-expanded="expanded"
    :aria-label="expanded ? '收起全文' : `展开其余 ${hidden} 行`"
    @click="$emit('toggle')"
  >
    {{ expanded ? '收起' : `… 其余 ${hidden} 行` }}
  </button>
</template>

<script setup lang="ts">
/** 参考项目 DiffBlock/TerminalBlock 头尾切片之间那个开关，标签照抄：
 *  `… 其余 N 行` / `收起`。它常驻在头片与尾片之间，所以展开时正文不会被顶下去。 */
defineProps<{ hidden: number; expanded: boolean }>()
defineEmits<{ toggle: [] }>()
</script>

<style scoped>
.fold {
  display: block;
  width: 100%;
  margin: 2px 0;
  padding: 3px 8px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--dsw-alias-label-tertiary);
  font-size: 12px;
  line-height: 18px;
  text-align: left;
  cursor: pointer;
}

.fold:hover {
  background: var(--dsw-alias-interactive-bg-hover);
  color: var(--dsw-alias-label-primary);
}
</style>
