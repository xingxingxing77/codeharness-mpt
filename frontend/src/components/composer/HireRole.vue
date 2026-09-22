<template>
  <!-- B9 招人控件。参照系没有「招人」界面 ⇒ 按它的风格取语义最近的类比：
       成员/表单那一族（ui-conversation 的安静行 + 本仓 GoalBar 的 36px 条与胶囊按钮）。
       只有 dynamic 线能招：classic/react 线里没有任何边会指向新节点，招进来是死成员（后端也拒）。 -->
  <div v-if="store.current?.paradigm === 'dynamic'" class="wrap">
    <button v-if="!open" class="chip" type="button" :disabled="busy" @click="start">
      {{ store.current.role_defs?.length ? `已招 ${store.current.role_defs.length} 人 · 再招` : '招人' }}
    </button>
    <!-- B9 余账：已招名册与「摘」。生效点写在下一次起跑，所以这里不假装人立刻从图里消失；
         摘掉后同一个名字可以马上再用（后端同批把 session.roles 里的他也去掉，否则重名闸会永久拒）。 -->
    <div v-if="!open && roster.length" class="roster">
      <span v-for="d in roster" :key="d.name" class="mate">
        {{ d.name }}
        <button class="fire" type="button" :disabled="busy"
                :aria-label="`摘除成员 ${d.name}`" @click="fire(d.name)">摘</button>
      </span>
    </div>
    <div v-else class="panel" role="group" aria-label="招人档案">
      <div class="row">
        <input v-model="form.name" class="inp" placeholder="成员名（字母开头，同时是图节点名）" />
        <button class="chip" type="button" :disabled="busy" @click="draft">让模型写草案</button>
      </div>
      <input v-model="form.profile" class="inp" placeholder="profile · 这个角色是谁、会什么" />
      <input v-model="form.goal" class="inp" placeholder="goal · 它负责达成什么" />
      <input v-model="form.constraints" class="inp" placeholder="constraints · 边界（可空）" />
      <div class="tools">
        <label v-for="t in tools" :key="t.name" class="tool">
          <input type="checkbox" :value="t.name" v-model="form.tools" />
          <span>{{ t.name }}</span><em>{{ t.tier }}</em>
        </label>
      </div>
      <div class="row">
        <button class="chip ok" type="button" :disabled="busy || !form.name.trim()" @click="hire">
          {{ busy ? '提交中…' : '确认招人' }}
        </button>
        <button class="chip" type="button" :disabled="busy" @click="open = false">取消</button>
      </div>
      <p v-if="msg" class="msg" :class="{ err: isErr }">{{ msg }}</p>
    </div>
  </div>
</template>

<script setup lang="ts">
/** 生效点是**下一次起跑/续跑**（后端回执 takes_effect='next_start'）：LangGraph 的节点集在 compile
 *  时固定，热插要重建图——这里不假装「立刻就能点名」。 */
import { computed, reactive, ref } from 'vue'
import { api } from '../../api/client'
import { useSessionStore } from '../../stores/sessions'

const store = useSessionStore()
const open = ref(false)
const busy = ref(false)
const msg = ref('')
const isErr = ref(false)
const tools = ref<{ name: string; tier: string }[]>([])
const form = reactive<{ name: string; profile: string; goal: string; constraints: string; tools: string[] }>({
  name: '', profile: '', goal: '', constraints: '', tools: []
})

async function start() {
  if (!store.currentId) return
  open.value = true
  msg.value = ''
  try {
    tools.value = (await api.hireTools(store.currentId)).tools || []
  } catch (e) {
    msg.value = (e as Error).message
    isErr.value = true
  }
}

async function draft() {
  if (!store.currentId) return
  busy.value = true
  msg.value = ''
  try {
    const d = await api.roleDraft(store.currentId)
    form.name = d.name || form.name
    form.profile = d.profile || form.profile
    form.goal = d.goal || form.goal
    form.constraints = d.constraints || form.constraints
    form.tools = (d.tools || []).filter((t: string) => tools.value.some((x) => x.name === t))
    msg.value = '草案已填好（模型现写，未注册的工具名会被后端当场拒），核对后确认'
    isErr.value = false
  } catch (e) {
    msg.value = (e as Error).message      // 502=模型声明了注册表外的工具，503=模型不通：照原文显示
    isErr.value = true
  } finally {
    busy.value = false
  }
}

const roster = computed(() => (store.current?.role_defs || []) as { name?: string }[])

async function fire(name: string) {
  if (!store.currentId) return
  busy.value = true
  msg.value = ''
  try {
    const r = await api.fireRole(store.currentId, name)
    await store.loadSessions()
    msg.value = `已摘除 ${r.fired || name}：下一次起跑生效（图里现在还在的那一场不受影响）`
    isErr.value = false
  } catch (e) {
    msg.value = (e as Error).message
    isErr.value = true
    open.value = true        // 折叠态没有 msg 的位置：出错就把面板展开，别只闪一下就算说过
  } finally {
    busy.value = false
  }
}

async function hire() {
  if (!store.currentId) return
  busy.value = true
  msg.value = ''
  try {
    const r = await api.hireRole(store.currentId, { ...form })
    await store.loadSessions()
    msg.value = r.message || '已落库：下一次起跑/续跑生效'
    isErr.value = false
    open.value = false
  } catch (e) {
    msg.value = (e as Error).message      // 422 的原文点名了哪个工具没注册/哪个名字重了
    isErr.value = true
  } finally {
    busy.value = false
  }
}
</script>

<style scoped>
.wrap { margin-bottom: 8px; }

.panel {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px 12px;
  border: 1px solid var(--dsw-alias-border-l1);
  border-radius: 12px;
  background: var(--dsw-specific-tip);
}

.row { display: flex; align-items: center; gap: 8px; }

.inp {
  flex: 1;
  min-width: 0;
  height: 26px;
  padding: 0 8px;
  border: 1px solid var(--dsw-alias-border-l2);
  border-radius: 6px;
  background: var(--dsw-specific-menu);
  font-family: inherit;
  font-size: 13px;
  line-height: 20px;
  color: var(--dsw-alias-label-primary);
}

.chip {
  height: 28px;
  padding: 0 10px;
  border: none;
  border-radius: 999px;
  background: transparent;
  font-family: inherit;
  font-size: 13px;
  color: var(--dsw-alias-label-tertiary);
  cursor: pointer;
  white-space: nowrap;
}

.chip:hover:not(:disabled) { background: var(--dsw-alias-interactive-bg-hover); }
.chip:disabled { opacity: 0.5; cursor: default; }
.chip.ok { color: var(--dsw-alias-state-success-primary); }

.tools {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 10px;
  max-height: 96px;
  overflow-y: auto;
}

/* 已招名册：同一行的安静胶囊，尺寸跟着 .chip 的 28px 走（不新造几何） */
.roster { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 4px; }

.mate {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 28px;
  padding: 0 4px 0 10px;
  border: 1px solid var(--dsw-alias-border-l1);
  border-radius: 999px;
  font-size: 13px;
  color: var(--dsw-alias-label-secondary);
}

.fire {
  height: 20px;
  padding: 0 8px;
  border: none;
  border-radius: 999px;
  background: transparent;
  font-family: inherit;
  font-size: 12px;
  color: var(--dsw-alias-label-tertiary);
  cursor: pointer;
}

.fire:hover:not(:disabled) {
  background: var(--dsw-alias-interactive-bg-hover);
  color: var(--dsw-alias-state-error-primary);
}

.fire:disabled { opacity: 0.5; cursor: default; }

.tool {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  line-height: 18px;
  color: var(--dsw-alias-label-secondary);
}

.tool em {
  font-style: normal;
  color: var(--dsw-alias-label-tertiary);
}

.msg {
  margin: 0;
  font-size: 12px;
  line-height: 18px;
  color: var(--dsw-alias-label-secondary);
  white-space: pre-line;
}

.msg.err { color: var(--dsw-alias-state-error-primary); }
</style>
