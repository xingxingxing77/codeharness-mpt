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
  /* 参考项目 .expand（TerminalBlock/ReadBlock/SearchBlock 同族）：满宽块级、无框无底、
     三级字、font:inherit（跟随卡片字号，终端卡是 12/18）、左对齐；
     hover 只把颜色提到 label-secondary——没有 margin、没有圆角、没有 hover 底色。 */
  display: block;
  width: 100%;
  padding: 0;
  border: none;
  background: transparent;
  color: var(--dsw-alias-label-tertiary);
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.fold:hover {
  color: var(--dsw-alias-label-secondary);
}
</style>
