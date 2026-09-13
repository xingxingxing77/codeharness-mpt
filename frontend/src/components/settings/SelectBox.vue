<template>
  <div class="sel" :style="{ minWidth }">
    <select :value="modelValue" @change="$emit('update:modelValue', ($event.target as HTMLSelectElement).value)">
      <option v-for="o in options" :key="o" :value="o">{{ o }}</option>
    </select>
    <Icon name="chevron-down" :size="14" class="chev" />
  </div>
</template>

<script setup lang="ts">
import Icon from '../Icon.vue'

defineProps<{ modelValue: string; options: string[]; minWidth?: string }>()
defineEmits(['update:modelValue'])
</script>
