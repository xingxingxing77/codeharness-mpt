<template>
  <div class="srow">
    <div class="s-main">
      <div class="s-title" :class="{ b: bold }">{{ title }}</div>
      <div v-if="desc || $slots.desc" class="s-desc">
        <slot name="desc">{{ desc }}</slot>
      </div>
    </div>
    <div class="s-ctrl">
      <slot />
    </div>
  </div>
</template>

<script setup lang="ts">
defineProps<{ title: string; desc?: string; bold?: boolean }>()
</script>
