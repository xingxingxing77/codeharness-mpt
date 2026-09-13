<template>
  <div class="spage">
    <div class="profile-top">
      <div class="avatar">LU</div>
      <div class="pname">沙湾 二哥</div>
      <div class="pmeta">
        @lubiyue931 <span class="dot">·</span> <span class="plan-badge">Free</span>
      </div>
    </div>

    <div class="stat-card">
      <div class="stat-cell">
        <div class="v">{{ fmt(totalTokens) }}</div>
        <div class="k">累计 Token 数</div>
      </div>
      <div class="stat-cell">
        <div class="v">{{ fmt(peakTokens) }}</div>
        <div class="k">峰值 Token 数</div>
      </div>
      <div class="stat-cell">
        <div class="v">1分 11秒</div>
        <div class="k">最长任务时长</div>
      </div>
      <div class="stat-cell">
        <div class="v">0 天</div>
        <div class="k">当前连续天数</div>
      </div>
      <div class="stat-cell">
        <div class="v">1 天</div>
        <div class="k">最长连续天数</div>
      </div>
    </div>

    <div class="act-head">
      <span class="act-t">Token 活动</span>
      <span class="act-tabs">
        <span
          v-for="t in ['每日', '每周', '累计']"
          :key="t"
          :class="{ on: tab === t }"
          @click="tab = t"
        >{{ t }}</span>
      </span>
    </div>
    <div class="heat">
      <i v-for="(c, i) in cells" :key="i" :class="{ on: c }" />
    </div>
    <div class="months">
      <span v-for="m in months" :key="m">{{ m }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useSessionStore } from '../../stores/sessions'

const store = useSessionStore()
const tab = ref('每日')

const cost = computed(() => store.cost || {})
const totalTokens = computed(() => (cost.value.total_prompt_tokens ?? 0) + (cost.value.total_completion_tokens ?? 0))
const peakTokens = computed(() => Math.max(totalTokens.value, 0))

function fmt(n: number): string {
  if (n >= 10000) return `${(n / 10000).toFixed(1)}万`
  return String(n)
}

/* 热力图：53 周 x 7 天，确定性伪随机分布，少量活跃格 */
const cells = computed(() => {
  const arr: boolean[] = []
  for (let i = 0; i < 53 * 7; i++) {
    const h = Math.sin(i * 12.9898) * 43758.5453
    arr.push(h - Math.floor(h) > 0.965)
  }
  return arr
})

const months = ['10月', '11月', '12月', '1月', '2月', '3月', '4月', '5月', '6月', '7月', '8月', '9月']
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
