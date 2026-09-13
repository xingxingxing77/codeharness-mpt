<template>
  <div class="spage">
    <h1 class="sptitle">使用情况和计费</h1>
    <p class="spdesc">如需查看发票、更改付款方式或进行其他操作，请前往<span class="slink">网页版设置</span></p>

    <div class="ssec">当前套餐</div>
    <div class="sgroup">
      <Row title="免费套餐" bold desc="有限 MetaGPT 使用量，GLM-5.3">
        <button class="sbtn dark" @click="upgrade">升级套餐</button>
      </Row>
    </div>

    <div class="ssec">通用使用限额</div>
    <div class="sgroup" style="padding: 16px">
      <div class="limit-head">
        <span class="lh-t">每月使用限制</span>
        <span class="lh-pct">{{ remainingPct }}% remaining</span>
      </div>
      <div class="prog" style="margin-top: 12px">
        <div :style="{ width: usedPct + '%' }" />
      </div>
      <div class="reset-line">重置时间：9月18日</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useMessage } from 'naive-ui'
import { useSessionStore } from '../../stores/sessions'
import Row from './Row.vue'

const store = useSessionStore()
const message = useMessage()

const cost = computed(() => store.cost || {})
const usedPct = computed(() => {
  const used = cost.value.total_cost ?? 0
  const max = cost.value.max_budget ?? 0
  if (!max) return 0
  return Math.min(100, Math.round((used / max) * 100))
})
const remainingPct = computed(() => 100 - usedPct.value)

function upgrade() {
  message.info('演示环境：升级入口未开放')
}
</script>

<style scoped>
.limit-head {
  display: flex;
  align-items: center;
}

.lh-t {
  flex: 1;
  font-size: 13.5px;
  font-weight: 600;
  color: var(--text);
}

.lh-pct {
  font-size: 12.5px;
  color: var(--text-2);
}

.reset-line {
  margin-top: 12px;
  text-align: right;
  font-size: 12px;
  color: var(--text-3);
}
</style>
