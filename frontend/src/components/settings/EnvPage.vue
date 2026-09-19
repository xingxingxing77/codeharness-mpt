<template>
  <div class="spage">
    <h1 class="sptitle">环境</h1>
    <p class="spdesc">本地环境用于指示 MetaGPT 如何为项目设置工作树。<span class="slink">了解更多</span>。</p>

    <div class="sec-bar">
      <span class="ssec" style="margin: 0">选择项目</span>
      <span class="grow" />
      <button class="sbtn" @click="addProject">
        添加项目
      </button>
    </div>
    <div v-for="proj in projects" :key="proj.name" class="env-card">
      <Icon name="folder" :size="16" />
      <span class="ename">{{ proj.name }}</span>
      <span class="eorg">{{ proj.org }}</span>
      <span class="grow" />
      <button class="add-btn" @click="addEnv(proj.name)">
        <Icon name="plus" :size="14" />
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useToastStore } from '../../stores/toast'
import { useSessionStore } from '../../stores/sessions'
import Icon from '../Icon.vue'

const store = useSessionStore()
const message = useToastStore()

/* 参考图格式：项目名 + 所属组织（灰字），此处用真实项目 + 占位组织名 */
const ORGS: Record<string, string> = { MetaGPT: 'geekan', OpenHarness: 'HKUDS' }

const projects = computed(() => {
  const names = store.projects().map((p) => p.name)
  if (!names.includes('MetaGPT')) names.unshift('MetaGPT')
  if (!names.includes('OpenHarness')) names.push('OpenHarness')
  return names.map((name) => ({ name, org: ORGS[name] || 'workspace' }))
})

function addProject() {
  message.push('演示环境：项目添加未开放')
}

function addEnv(name: string) {
  message.push(`演示环境：为「${name}」添加环境的入口未接线`)
}
</script>

<style scoped>
.sec-bar {
  display: flex;
  align-items: center;
  margin: 30px 0 10px;
}

.grow {
  flex: 1;
}

.env-card {
  display: flex;
  align-items: center;
  gap: 11px;
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 15px 16px;
  margin-bottom: 12px;
  background: #fff;
}

.env-card svg {
  color: #6f6957;
  flex: none;
}

.ename {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--text);
}

.eorg {
  font-size: 12.5px;
  color: var(--text-3);
}

.add-btn {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  border: 1px solid var(--line);
  background: #fff;
  color: var(--text-2);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}

.add-btn:hover {
  background: #f7f6f3;
}
</style>
