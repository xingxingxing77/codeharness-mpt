<template>
  <!-- B9 招人控件，住在右侧栏「成员」页签里（原先挂在中间列 composer 上方，把对话区挤掉一屏）。
       参照系没有「招人」界面 ⇒ 按它的风格取语义最近的类比：`ui-subagent` 的名单行
       （`label · secondary` 左、右侧给数值）+ 本仓右栏卡片的 12px 内衬 / radius 12 / border-l1。
       **页签对所有线都出现**：只有 dynamic 线能招人（classic/react 线的图在装配时就固定了，
       后端也拒），但把入口藏起来会让用户以为功能不存在——看得见、并说明为什么不能，才是反馈。 -->
  <div class="wrap">
    <div v-if="!open" class="sec">
      <div class="secTitle">
        <span>本场成员</span>
        <button v-if="canHire" class="act add" type="button" :disabled="busy" @click="start">
          {{ roleDefs.length ? '再招一人' : '招人' }}
        </button>
      </div>
      <p v-if="!canHire" class="why">
        这条线不能招人——{{ paradigmName }} 的图在装配时就固定了，没有指向新节点的边，
        后端也会当场拒。要试就新建一场「动态组队」会话。
      </p>
      <p v-else-if="!roster.length" class="empty">还没有成员。招一个进来，下一次起跑时它就在图里。</p>
      <div v-for="m in roster" :key="m.name" class="mate">
        <span class="name">{{ m.name }}</span>
        <span class="goal">{{ m.goal || m.profile || (m.hired ? '' : '装配自带') }}</span>
        <!-- B9 余账：摘的生效点是下一次起跑，所以这里不假装人立刻从图里消失 -->
        <button v-if="canHire" class="act fire" type="button" :disabled="busy"
                :aria-label="`摘除成员 ${m.name}`" @click="fire(m.name)">摘</button>
        <em v-else class="tier">固定</em>
      </div>
    </div>

    <div v-else class="card" role="group" aria-label="招人档案">
      <label class="field">
        <span>成员名（字母开头，同时是图节点名）</span>
        <span class="line">
          <input v-model="form.name" class="inp" placeholder="如 Tester" />
          <button class="act draft" type="button" :disabled="busy" @click="draft">草案</button>
        </span>
      </label>
      <label class="field"><span>profile · 这个角色是谁、会什么</span>
        <input v-model="form.profile" class="inp" /></label>
      <label class="field"><span>goal · 它负责达成什么</span>
        <input v-model="form.goal" class="inp" /></label>
      <label class="field"><span>constraints · 边界（可空）</span>
        <input v-model="form.constraints" class="inp" /></label>

      <div class="field">
        <span>工具面（勾选它这一轮能点名的命令）</span>
        <div class="tools">
          <label v-for="t in tools" :key="t.name" class="tool">
            <input type="checkbox" :value="t.name" v-model="form.tools" />
            <span class="tname">{{ t.name }}</span><em class="tier">{{ t.tier }}</em>
          </label>
        </div>
      </div>

      <div class="foot">
        <button class="act ok" type="button" :disabled="busy || !form.name.trim()" @click="hire">
          {{ busy ? '提交中…' : '确认招人' }}
        </button>
        <button class="act" type="button" :disabled="busy" @click="open = false">取消</button>
      </div>
      <p v-if="msg" class="msg" :class="{ err: isErr }">{{ msg }}</p>
    </div>
  </div>
</template>

<script setup lang="ts">
/** 生效点是**下一次起跑/续跑**（后端回执 takes_effect='next_start'）：LangGraph 的节点集在 compile
 *  时固定，热插要重建图——这里不假装「立刻就能点名」。 */
import { computed, reactive, ref } from 'vue'
import { api } from '../api/client'
import { useSessionStore } from '../stores/sessions'

const store = useSessionStore()
const open = ref(false)
const busy = ref(false)
const msg = ref('')
const isErr = ref(false)
const tools = ref<{ name: string; tier: string }[]>([])
const form = reactive<{ name: string; profile: string; goal: string; constraints: string; tools: string[] }>({
  name: '', profile: '', goal: '', constraints: '', tools: []
})

/** 只有 dynamic 线能招人与摘人：classic/react 线的图在 compile 时就固定了，
 *  后端 `POST /roles` 对非 dynamic 直接拒（s23 钉着）。这里不隐藏页签，只把动作收掉并说明原因。 */
const canHire = computed(() => store.current?.paradigm === 'dynamic')
const PARADIGM_NAMES: Record<string, string> = {
  classic: '标准模式（SOP 流程）', react: 'ReAct 模式', dynamic: '动态组队'
}
const paradigmName = computed(() => PARADIGM_NAMES[store.current?.paradigm || 'classic'] || '这条线')
const roleDefs = computed(() => (store.current?.role_defs || []) as
  { name?: string; goal?: string; profile?: string }[])

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

const roster = computed(() => [
  // 招进来的成员排在前面（带职责），装配自带的节点跟在后面（只有名字，标「固定」）——
  // 这样 classic/react 线打开这一页也不是空的，看得见"这场到底有谁"。
  ...roleDefs.value.map((d) => ({ name: d.name || '', goal: d.goal, profile: d.profile, hired: true })),
  ...(store.current?.roles || [])
    .filter((n) => !roleDefs.value.some((d) => d.name === n))
    .map((n) => ({ name: n, goal: undefined, profile: undefined, hired: false }))
])

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
.wrap { display: flex; flex-direction: column; gap: 12px; }

/* 页签正文的内衬由 DetailsPanel 的 .body 给（16px），这里只排自己的行 */
.secTitle {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 12px;
  line-height: 18px;
  color: var(--dsw-alias-label-tertiary);
}

.empty {
  margin: 8px 0 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--dsw-alias-label-tertiary);
}

/* 非 dynamic 线的说明：说清"为什么不能"和"要试去哪儿"，不然这一页就是个死胡同 */
.why {
  margin: 8px 0 0;
  padding: 8px 12px;
  border: 1px solid var(--dsw-alias-border-l1);
  border-radius: 12px;
  background: var(--dsw-specific-tip);
  font-size: 12px;
  line-height: 18px;
  color: var(--dsw-alias-label-secondary);
}

.mate .tier {
  flex: none;
  margin-left: auto;
  font-style: normal;
  font-size: 12px;
  line-height: 18px;
  color: var(--dsw-alias-label-tertiary);
}

/* 名单行：名字 + 一句职责（省略号）+ 右侧「摘」，行高 28px 与右栏其它行对齐 */
.mate {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 28px;
  margin-top: 4px;
  border-bottom: 1px solid var(--dsw-alias-border-l1);
}

.mate .name {
  flex: none;
  max-width: 8em;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  line-height: 20px;
  color: var(--dsw-alias-label-primary);
}

.mate .goal {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  line-height: 18px;
  color: var(--dsw-alias-label-tertiary);
}

.card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px;
  border: 1px solid var(--dsw-alias-border-l1);
  border-radius: 12px;
  background: var(--dsw-specific-tip);
}

.field { display: flex; flex-direction: column; gap: 4px; }

.field > span {
  font-size: 12px;
  line-height: 18px;
  color: var(--dsw-alias-label-tertiary);
}

.line { display: flex; align-items: center; gap: 6px; }

.inp {
  flex: 1;
  min-width: 0;
  height: 28px;
  padding: 0 8px;
  border: 1px solid var(--dsw-alias-border-l2);
  border-radius: 6px;
  background: var(--dsw-specific-menu);
  font-family: inherit;
  font-size: 13px;
  line-height: 20px;
  color: var(--dsw-alias-label-primary);
}

/* 焦点环用真实存在的 token：本仓另有一处写 `var(--accent)`，而 `--accent` 在 tokens.css 里
   没有定义（那条 outline 实际不会按预期上色），不照抄这个错。 */
.inp:focus-visible { outline: 2px solid var(--dsw-alias-label-secondary); outline-offset: -1px; }

/* 安静胶囊按钮：28px 高、radius 999、三级字，hover 才上底色（与右栏 .iconBtn 同一族） */
.act {
  flex: none;
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

.act:hover:not(:disabled) { background: var(--dsw-alias-interactive-bg-hover); color: var(--dsw-alias-label-secondary); }
.act:disabled { opacity: 0.5; cursor: default; }
.act.ok { color: var(--dsw-alias-state-success-primary); }
.act.fire:hover:not(:disabled) { color: var(--dsw-alias-state-error-primary); }
.act.add { border: 1px solid var(--dsw-alias-border-l2); }

.tools {
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-height: 180px;
  overflow-y: auto;
}

.tool {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  line-height: 18px;
  color: var(--dsw-alias-label-secondary);
}

.tool .tname {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tool .tier {
  flex: none;
  font-style: normal;
  color: var(--dsw-alias-label-tertiary);
}

.foot { display: flex; gap: 8px; }

.msg {
  margin: 0;
  font-size: 12px;
  line-height: 18px;
  color: var(--dsw-alias-label-secondary);
  white-space: pre-line;
}

.msg.err { color: var(--dsw-alias-state-error-primary); }
</style>
