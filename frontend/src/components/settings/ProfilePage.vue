<template>
  <div class="spage">
    <div class="profile-top">
      <div class="avatar">{{ avatar }}</div>
      <div class="pname">{{ auth.user || '未登录' }}</div>
    </div>

    <!-- 下方只渲染有真数据源的格子：原先五格里三格是写死的死文案、热力图是伪随机
         假分布——没有后端路由就没有前端读数，删而不编（s8 t26 的守卫在扫这些字面量）。 -->
    <div class="stat-card">
      <div class="stat-cell">
        <div class="v">{{ fmt(totalTokens) }}</div>
        <div class="k">累计 Token 数</div>
      </div>
      <div class="stat-cell">
        <div class="v">{{ fmtCost(costCny) }}</div>
        <div class="k">累计费用</div>
      </div>
      <div class="stat-cell">
        <div class="v">{{ fmt(sessionCount) }}</div>
        <div class="k">会话数</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useAuthStore } from '../../stores/auth'
import { useSessionStore } from '../../stores/sessions'

const store = useSessionStore()
const auth = useAuthStore()

const avatar = computed(() => (auth.user || '?').slice(0, 2).toUpperCase())
const cost = computed(() => store.cost || {})
const totalTokens = computed(() => (cost.value.total_prompt_tokens ?? 0) + (cost.value.total_completion_tokens ?? 0))
const costCny = computed(() => cost.value.cny ?? 0)
const sessionCount = computed(() => store.sessions.length)

function fmt(n: number): string {
  if (n >= 10000) return `${(n / 10000).toFixed(1)}万`
  return String(n)
}

function fmtCost(n: number): string {
  return `¥${n.toFixed(4)}`
}
</script>

<style scoped>
.profile-top {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 26px 0 30px;
}

.pname {
  margin-top: 16px;
  font-size: 24px;
  font-weight: 600;
  color: var(--text);
}

.pmeta {
  margin-top: 8px;
  font-size: 13px;
  color: var(--text-3);
  display: flex;
  align-items: center;
  gap: 7px;
}

.pmeta .dot {
  opacity: 0.6;
}

.act-head {
  display: flex;
  align-items: center;
  margin: 30px 0 14px;
}

.act-t {
  font-size: 15px;
  font-weight: 600;
  color: var(--text);
  flex: 1;
}

.act-tabs {
  display: flex;
  gap: 16px;
}

.act-tabs span {
  font-size: 13px;
  color: var(--text-3);
  cursor: pointer;
}

.act-tabs span.on {
  color: var(--text);
  font-weight: 600;
}

.months {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: var(--text-3);
  margin-top: 10px;
  padding: 0 2px;
}
</style>
