<template>
  <button class="tog" :class="{ on: modelValue }" @click="$emit('update:modelValue', !modelValue)">
    <span class="knob" />
  </button>
</template>

<script setup lang="ts">
defineProps<{ modelValue: boolean }>()
defineEmits(['update:modelValue'])
</script>
