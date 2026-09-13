<template>
  <div class="home-view">
    <div class="stack">
      <h1 class="title">我们应该在 MetaGPT 中构建什么?</h1>
      <InputCard mode="home" />
    </div>
  </div>
</template>

<script setup lang="ts">
import InputCard from './InputCard.vue'
</script>

<style scoped>
.home-view {
  flex: 1;
  min-height: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  /* 标题块略偏上，与参考图一致 */
  padding-bottom: 6vh;
}

.stack {
  width: 720px;
  max-width: calc(100% - 48px);
  display: flex;
  flex-direction: column;
}

.title {
  margin: 0 0 54px;
  text-align: center;
  font-size: 30px;
  font-weight: 500;
  color: #201f1b;
  letter-spacing: 0.5px;
}
</style>
