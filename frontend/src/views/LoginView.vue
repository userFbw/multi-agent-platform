<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import AgentIcon from '@/components/common/AgentIcon.vue'
import { ArrowRight, Lock, UserRound } from '@lucide/vue'

const router = useRouter()
const auth = useAuthStore()

type Mode = 'login' | 'register'
const mode = ref<Mode>('login')

const userName = ref('')
const password = ref('')
const error = ref('')

const isRegister = computed(() => mode.value === 'register')
const canSubmit = computed(() => userName.value.trim() && password.value.trim())

const switchMode = (m: Mode) => {
  mode.value = m
  error.value = ''
}

const submit = async () => {
  if (!canSubmit.value) {
    error.value = '请填写用户名和密码'
    return
  }
  error.value = ''
  try {
    if (isRegister.value) await auth.register(userName.value.trim(), password.value)
    else await auth.login(userName.value.trim(), password.value)
    router.push('/')
  } catch (e) {
    error.value = (e as Error).message || '操作失败，请稍后重试'
  }
}
</script>

<template>
  <div class="auth">
    <div class="auth__card">
      <div class="auth__brand">
        <span class="auth__mark"><AgentIcon icon="sparkles" :size="20" :stroke-width="2.2" /></span>
        <span class="auth__name">全栈应用开发平台</span>
      </div>
      <p class="auth__tagline">AI Agent 工作平台 · 自然语言驱动全栈开发</p>

      <div class="auth__segment aw-segment">
        <button type="button" class="aw-segment__btn" :class="{ 'aw-segment__btn--active': mode === 'login' }" @click="switchMode('login')">
          登录
        </button>
        <button type="button" class="aw-segment__btn" :class="{ 'aw-segment__btn--active': mode === 'register' }" @click="switchMode('register')">
          注册
        </button>
      </div>

      <form class="auth__form" @submit.prevent="submit">
        <label class="auth__field">
          <span class="auth__label">用户名</span>
          <span class="auth__input-wrap">
            <UserRound :size="15" :stroke-width="2" />
            <input v-model="userName" class="auth__input" placeholder="请输入用户名" autocomplete="username" />
          </span>
        </label>

        <label class="auth__field">
          <span class="auth__label">密码</span>
          <span class="auth__input-wrap">
            <Lock :size="15" :stroke-width="2" />
            <input
              v-model="password"
              class="auth__input"
              type="password"
              placeholder="请输入密码"
              :autocomplete="isRegister ? 'new-password' : 'current-password'"
            />
          </span>
        </label>

        <p v-if="error" class="auth__error">{{ error }}</p>

        <button type="submit" class="aw-btn aw-btn--primary aw-btn--lg auth__submit" :disabled="!canSubmit || auth.loading">
          {{ auth.loading ? '请稍候…' : isRegister ? '创建账号' : '登录' }}
          <ArrowRight v-if="!auth.loading" :size="15" :stroke-width="2.2" />
        </button>
      </form>
    </div>
  </div>
</template>

<style scoped>
.auth {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 32px 20px;
  background: var(--color-bg);
  background-image: radial-gradient(circle at 50% 0%, rgba(86, 88, 212, 0.08), transparent 55%);
}
.auth__card {
  width: min(400px, 100%);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-lg);
  padding: 32px 30px 26px;
}
.auth__brand {
  display: flex;
  align-items: center;
  gap: 10px;
  justify-content: center;
}
.auth__mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  height: 38px;
  border-radius: 12px;
  background: var(--gradient-logo);
  color: #fff;
  box-shadow: 0 6px 16px rgba(86, 88, 212, 0.25);
}
.auth__name {
  font-size: var(--fs-20);
  font-weight: 700;
  letter-spacing: -0.02em;
}
.auth__tagline {
  text-align: center;
  margin-top: 8px;
  font-size: var(--fs-12);
  color: var(--color-text-tertiary);
}
.auth__segment {
  width: 100%;
  margin: 22px 0 18px;
}
.auth__segment .aw-segment__btn {
  flex: 1;
}
.auth__form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.auth__field {
  display: block;
}
.auth__label {
  display: block;
  font-size: var(--fs-12);
  font-weight: 600;
  color: var(--color-text-secondary);
  margin-bottom: 6px;
}
.auth__input-wrap {
  display: flex;
  align-items: center;
  gap: 9px;
  height: 40px;
  padding: 0 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  color: var(--color-text-tertiary);
  transition: border-color 0.15s var(--ease), box-shadow 0.15s var(--ease);
}
.auth__input-wrap:focus-within {
  border-color: rgba(86, 88, 212, 0.5);
  box-shadow: 0 0 0 3px var(--color-accent-ring);
}
.auth__input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  font-size: var(--fs-14);
  color: var(--color-text);
}
.auth__error {
  font-size: var(--fs-12);
  color: var(--color-danger);
}
.auth__submit {
  width: 100%;
  margin-top: 4px;
}
</style>
