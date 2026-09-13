<template>
  <div class="rc-row">
    <div
      v-for="o in options"
      :key="o.value"
      class="rc"
      :class="{ on: modelValue === o.value }"
      @click="$emit('update:modelValue', o.value)"
    >
      <span v-if="o.icon" class="rc-ic"><Icon :name="o.icon" :size="16" /></span>
      <div>
        <div class="rc-t">{{ o.title }}</div>
        <div class="rc-d">{{ o.desc }}</div>
      </div>
      <span class="rc-dot" />
    </div>
  </div>
</template>

<script setup lang="ts">
import Icon from '../Icon.vue'

defineProps<{
  modelValue: string
  options: { value: string; title: string; desc: string; icon?: string }[]
}>()
defineEmits(['update:modelValue'])
</script>
