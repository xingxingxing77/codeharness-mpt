<template>
  <div class="composer">
    <InputCard mode="follow" />
  </div>
</template>

<script setup lang="ts">
import InputCard from './InputCard.vue'
</script>

<style scoped>
.composer {
  flex: none;
  padding: 8px 0 18px;
  background: linear-gradient(to top, #fff 70%, rgba(255, 255, 255, 0));
}
</style>
