<template>
  <div class="spage">
    <h1 class="sptitle">已归档对话</h1>
    <!-- C94：原先正文是写死的「暂无已归档的聊天」假空态（不读任何 store）——有归档会话时
         界面仍说没有，用户会拿这句话当「我的会话丢了」。现在读真 store 过滤 archived 渲染
         真实列表；空态只在真空时出现，这句话不再是假话。 -->
    <div v-if="!list.length" class="sgroup empty" style="margin-top: 18px">暂无已归档的聊天。</div>
    <div v-else class="sgroup alist" style="margin-top: 18px">
      <div v-for="s in list" :key="s.id" class="arow">
        <div class="ainfo">
          <div class="aidea">{{ s.idea || '(无标题)' }}</div>
          <div class="asub">{{ s.project_name }}</div>
        </div>
        <button class="btn" type="button" @click="unarchive(s)">取消归档</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useSessionStore } from '../../stores/sessions'
import { useToastStore } from '../../stores/toast'
import type { Session } from '../../types'

const store = useSessionStore()
const toast = useToastStore()

const list = computed(() => store.sessions.filter((s) => s.archived))

async function unarchive(s: Session) {
  try {
    // 与侧栏「取消归档」同一出口（patchSession 把服务端权威对象合并回列表 ⇒ 本页实时刷新）
    await store.patchSession(s.id, { archived: false })
    toast.push('已取消归档', 'success')
  } catch (e) {
    toast.push((e as Error).message, 'error')
  }
}
</script>

<style scoped>
.arow {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 4px;
}
.arow + .arow {
  border-top: 1px solid var(--line, color-mix(in srgb, currentColor 12%, transparent));
}
.aidea {
  font-size: 13px;
}
.asub {
  font-size: 12px;
  opacity: 0.6;
  margin-top: 2px;
}
</style>
