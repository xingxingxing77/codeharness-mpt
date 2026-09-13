<template>
  <div class="spage">
    <h1 class="sptitle">连接</h1>

    <div class="stabs">
      <span v-for="t in tabs" :key="t" :class="{ on: tab === t }" @click="tab = t">{{ t }}</span>
    </div>

    <template v-if="tab === '控制此电脑'">
      <div class="ssec">可控制此电脑的设备</div>
      <div class="sgroup empty" style="padding: 40px 16px">
        <div class="dev-icons">
          <Icon name="phone" :size="26" />
          <span class="dots">···</span>
          <Icon name="laptop" :size="26" />
        </div>
        <div style="margin-top: 10px">通过手机或其他设备控制此电脑</div>
        <button class="sbtn" style="margin-top: 12px">设置</button>
      </div>
    </template>

    <template v-else-if="tab === '控制其他设备'">
      <div class="ssec">此设备可控制的其他设备</div>
      <div class="sgroup empty">暂无已配对的设备。</div>
    </template>

    <template v-else>
      <div class="ssec">SSH 连接</div>
      <div class="sgroup empty">暂无 SSH 连接。</div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import Icon from '../Icon.vue'

const tab = ref('控制此电脑')
const tabs = ['控制此电脑', '控制其他设备', 'SSH']
</script>

<style scoped>
.dev-icons {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  color: #55524a;
}

.dots {
  color: var(--text-3);
  letter-spacing: 2px;
}
</style>
