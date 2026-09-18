<template>
  <div class="login-wrap">
    <form class="card" @submit.prevent="submit">
      <h1>Codeharness Studio</h1>
      <p class="sub">{{ mode === 'login' ? '登录以继续' : '创建账号（首个账号即管理员）' }}</p>
      <label>
        <span>用户名</span>
        <input v-model.trim="username" autocomplete="username" placeholder="2-32 位字母/数字/_-"
               autofocus />
      </label>
      <label>
        <span>密码</span>
        <input v-model="password" type="password" autocomplete="current-password"
               placeholder="至少 6 位" />
      </label>
      <p v-if="err" class="err">{{ err }}</p>
      <button class="primary" :disabled="busy || !username || !password">
        {{ busy ? '…' : mode === 'login' ? '登录' : '注册并登录' }}
      </button>
      <button class="ghost" type="button" @click="switchMode">
        {{ mode === 'login' ? '没有账号？注册' : '已有账号？登录' }}
      </button>
    </form>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const mode = ref<'login' | 'register'>('login')
const username = ref('')
const password = ref('')
const err = ref('')
const busy = ref(false)

function switchMode() {
  mode.value = mode.value === 'login' ? 'register' : 'login'
  err.value = ''
}

async function submit() {
  err.value = ''
  busy.value = true
  try {
    if (mode.value === 'login') await auth.login(username.value, password.value)
    else await auth.register(username.value, password.value)
  } catch (e: any) {
    err.value = e?.message || '请求失败'
  } finally {
    busy.value = false
  }
}
</script>

<style scoped>
.login-wrap {
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--sb-bg, #f7f5f2);
}
.card {
  width: 340px;
  padding: 32px 28px;
  border: 1px solid var(--sb-line, #e5e1da);
  border-radius: 14px;
  background: #fff;
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.06);
  display: flex;
  flex-direction: column;
  gap: 14px;
}
h1 {
  font-size: 18px;
  margin: 0;
}
.sub {
  margin: 0 0 4px;
  color: #8a857c;
  font-size: 13px;
}
label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
}
input {
  height: 36px;
  border: 1px solid var(--sb-line, #ddd8d0);
  border-radius: 8px;
  padding: 0 10px;
  font-size: 14px;
  outline: none;
}
input:focus {
  border-color: #c9a86a;
}
.err {
  color: #c2543e;
  font-size: 13px;
  margin: 0;
}
button {
  height: 38px;
  border-radius: 8px;
  font-size: 14px;
  cursor: pointer;
  border: 1px solid transparent;
}
.primary {
  background: #2f2a24;
  color: #fff;
}
.ghost {
  background: transparent;
  border: none;
  color: #8a857c;
}
</style>
