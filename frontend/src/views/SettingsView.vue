<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'
import type { ApiUser } from '@/types/api'
import { Check, RefreshCw, Trash2, UploadCloud } from '@lucide/vue'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const ui = useUiStore()

type Tab = 'general' | 'profile'
const tab = computed<Tab>(() => (route.query.tab === 'profile' ? 'profile' : 'general'))

const tabs: Array<{ key: Tab; label: string }> = [
  { key: 'general', label: '通用' },
  { key: 'profile', label: '个人资料' }
]

const goTab = (t: Tab) => router.replace({ query: { tab: t } })

const newPassword = ref('')
const saving = ref(false)
const saved = ref(false)
const error = ref('')
const fileInput = ref<HTMLInputElement | null>(null)

const pickAvatar = () => fileInput.value?.click()

const onAvatarChange = async (e: Event) => {
  const file = (e.target as HTMLInputElement).files?.[0]
  if (!file) return
  error.value = ''
  try {
    await auth.updateAvatar(file)
  } catch (err) {
    error.value = (err as Error).message
  }
}

const savePassword = async () => {
  if (!newPassword.value.trim()) return
  saving.value = true
  error.value = ''
  try {
    await auth.updateProfile({ password: newPassword.value })
    newPassword.value = ''
    saved.value = true
    setTimeout(() => (saved.value = false), 1600)
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    saving.value = false
  }
}

/* ---- 账号信息（GET /api/users/{id}，§1.3）----
 * ⚠️ 这个响应会**回显明文 password**，这里只取 id / user_name / avatar，密码不存不展示。 */
const remoteUser = ref<ApiUser | null>(null)
const remoteError = ref('')
const remoteLoading = ref(false)

const avatarFileName = computed(() => {
  const raw = remoteUser.value?.avatar
  if (!raw) return '—'
  return raw.startsWith('http') ? raw.replace(/^https?:\/\/[^/]+/, '') : raw
})

const loadRemote = async () => {
  if (!auth.user) return
  remoteLoading.value = true
  remoteError.value = ''
  try {
    remoteUser.value = await auth.refreshProfile()
  } catch (e) {
    remoteError.value = (e as Error).message
  } finally {
    remoteLoading.value = false
  }
}

watch(
  () => tab.value === 'profile',
  (on) => {
    if (on) void loadRemote()
  },
  { immediate: true }
)

/* ---- 注销账号（DELETE /api/users/{id}，§1.5）----
 * ⚠️ 库行由外键级联删除，但**磁盘产物不清理**（exports/u<user_id>/ 会保留）。 */
const confirmName = ref('')
const deleting = ref(false)
const canDelete = computed(
  () => confirmName.value.trim() !== '' && confirmName.value.trim() === (auth.user?.user_name ?? '')
)

const deleteAccount = async () => {
  if (!canDelete.value) return
  if (
    !window.confirm(
      '确认注销账号？\n\n· 库里的项目、成果、工作流会被级联删除，不可恢复；\n· 但磁盘产物（exports/）不会被清理，需要另行处理。'
    )
  )
    return
  deleting.value = true
  error.value = ''
  try {
    await auth.deleteAccount()
    await router.push('/login')
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    deleting.value = false
  }
}
</script>

<template>
  <main class="settings">
    <div class="settings__head">
      <h1 class="settings__title">设置</h1>
      <p class="settings__sub">管理偏好与账户信息</p>
    </div>

    <div class="settings__layout">
      <nav class="settings__nav">
        <button
          v-for="t in tabs"
          :key="t.key"
          type="button"
          class="settings__nav-item"
          :class="{ 'settings__nav-item--active': tab === t.key }"
          @click="goTab(t.key)"
        >
          {{ t.label }}
        </button>
      </nav>

      <div class="settings__content">
        <p v-if="error" class="settings__error">{{ error }}</p>

        <template v-if="tab === 'general'">
          <section class="settings__section">
            <h3 class="settings__label">主题</h3>
            <div class="aw-segment">
              <button
                type="button"
                class="aw-segment__btn"
                :class="{ 'aw-segment__btn--active': ui.theme === 'light' }"
                @click="ui.setTheme('light')"
              >
                浅色
              </button>
              <button
                type="button"
                class="aw-segment__btn"
                :class="{ 'aw-segment__btn--active': ui.theme === 'dark' }"
                @click="ui.setTheme('dark')"
              >
                深色
              </button>
            </div>
          </section>
          <section class="settings__section">
            <h3 class="settings__label">快捷键</h3>
            <div class="settings__row">
              <span class="settings__row-text">在任何页面快速新建任务</span>
              <span class="aw-kbd">Ctrl K</span>
            </div>
          </section>
          <section class="settings__section">
            <h3 class="settings__label">关于</h3>
            <div class="settings__row">
              <span class="settings__row-text">全栈应用开发平台 · 对接后端 API</span>
              <span class="settings__version">v0.2.0</span>
            </div>
          </section>
        </template>

        <template v-else>
          <section class="settings__section">
            <h3 class="settings__label">头像</h3>
            <div class="settings__avatar">
              <img v-if="auth.avatarUrl" :src="auth.avatarUrl" class="settings__avatar-img" alt="" />
              <span v-else class="settings__avatar-mark">{{ (auth.user?.user_name || 'U').charAt(0).toUpperCase() }}</span>
              <div class="settings__avatar-actions">
                <button type="button" class="aw-btn aw-btn--default aw-btn--sm" @click="pickAvatar">
                  <UploadCloud :size="13" :stroke-width="2" />
                  上传头像
                </button>
                <span class="settings__avatar-hint">支持 jpg / png</span>
                <input ref="fileInput" type="file" accept="image/*" class="settings__file" @change="onAvatarChange" />
              </div>
            </div>
          </section>

          <section class="settings__section">
            <h3 class="settings__label">用户名</h3>
            <div class="settings__row">
              <span class="settings__row-text">{{ auth.user?.user_name || '—' }}</span>
              <span class="settings__chip">用户 ID #{{ auth.user?.id }}</span>
            </div>
          </section>

          <section class="settings__section">
            <h3 class="settings__label">修改密码</h3>
            <div class="settings__password">
              <input v-model="newPassword" type="password" class="settings__input" placeholder="输入新密码" />
              <button type="button" class="aw-btn aw-btn--primary" :disabled="!newPassword.trim() || saving" @click="savePassword">
                <Check v-if="saved" :size="14" :stroke-width="2.2" />
                {{ saved ? '已保存' : saving ? '保存中…' : '保存' }}
              </button>
            </div>
          </section>

          <section class="settings__section">
            <h3 class="settings__label">
              账号信息（服务端）
              <button
                type="button"
                class="settings__inline-btn"
                :disabled="remoteLoading"
                title="从后端重新拉取"
                @click="loadRemote"
              >
                <RefreshCw :size="12" :stroke-width="2" :class="{ spin: remoteLoading }" />
              </button>
            </h3>
            <p v-if="remoteError" class="settings__error">{{ remoteError }}</p>
            <template v-else>
              <div class="settings__row">
                <span class="settings__row-text">用户 ID</span>
                <span class="settings__chip">#{{ remoteUser?.id ?? auth.user?.id ?? '—' }}</span>
              </div>
              <div class="settings__row">
                <span class="settings__row-text">用户名</span>
                <span class="settings__chip">{{ remoteUser?.user_name ?? auth.user?.user_name ?? '—' }}</span>
              </div>
              <div class="settings__row">
                <span class="settings__row-text">头像文件</span>
                <span class="settings__chip settings__chip--mono">{{ avatarFileName }}</span>
              </div>
              <p class="settings__hint">
                服务端该接口会回显明文密码，前端不存储、不展示（已登记的后端遗留问题）。
              </p>
            </template>
          </section>

          <section class="settings__section">
            <h3 class="settings__label settings__label--danger">注销账号</h3>
            <div class="settings__danger">
              <p class="settings__danger-text">
                注销后，你在库里的项目、成果与自定义工作流会被级联删除，<strong>不可恢复</strong>。
              </p>
              <p class="settings__danger-warn">
                ⚠️ 磁盘产物不会被清理 —— 后端只删数据库行，<code>exports/</code> 下的文件仍保留。
              </p>
              <div class="settings__danger-row">
                <input
                  v-model="confirmName"
                  class="settings__input"
                  :placeholder="`输入用户名「${auth.user?.user_name ?? ''}」以确认`"
                />
                <button
                  type="button"
                  class="aw-btn settings__danger-btn"
                  :disabled="!canDelete || deleting"
                  @click="deleteAccount"
                >
                  <Trash2 :size="14" :stroke-width="2" />
                  {{ deleting ? '注销中…' : '注销账号' }}
                </button>
              </div>
            </div>
          </section>
        </template>
      </div>
    </div>
  </main>
</template>

<style scoped>
.settings {
  max-width: 860px;
  margin: 0 auto;
  padding: 36px 28px 64px;
}
.settings__head {
  margin-bottom: 26px;
}
.settings__title {
  font-size: var(--fs-24);
  font-weight: 700;
  letter-spacing: -0.02em;
}
.settings__sub {
  margin-top: 4px;
  font-size: var(--fs-13);
  color: var(--color-text-tertiary);
}
.settings__error {
  margin-bottom: 14px;
  font-size: var(--fs-12);
  color: var(--color-danger);
}
.settings__layout {
  display: grid;
  grid-template-columns: 190px 1fr;
  gap: 28px;
  align-items: start;
}
.settings__nav {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.settings__nav-item {
  padding: 9px 12px;
  border-radius: var(--radius-md);
  font-size: var(--fs-13);
  color: var(--color-text-secondary);
  text-align: left;
  transition: all 0.13s var(--ease);
}
.settings__nav-item:hover {
  background: var(--color-surface-hover);
  color: var(--color-text);
}
.settings__nav-item--active {
  background: var(--color-surface);
  color: var(--color-text);
  font-weight: 600;
  box-shadow: var(--shadow-xs);
}
.settings__content {
  min-width: 0;
}
.settings__section {
  padding: 18px 0 22px;
  border-bottom: 1px solid var(--color-border);
}
.settings__section:last-child {
  border-bottom: none;
}
.settings__label {
  font-size: var(--fs-11);
  font-weight: 650;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--color-text-tertiary);
  margin-bottom: 12px;
}
.settings__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-size: var(--fs-13);
  color: var(--color-text-secondary);
}
.settings__version {
  font-size: var(--fs-12);
  color: var(--color-text-tertiary);
}
.settings__chip {
  font-size: 11px;
  color: var(--color-text-tertiary);
  background: var(--color-surface-hover);
  padding: 3px 9px;
  border-radius: 999px;
}
.settings__inline-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  margin-left: 6px;
  border-radius: 7px;
  color: var(--color-text-tertiary);
  vertical-align: middle;
}
.settings__inline-btn:hover:not(:disabled) {
  background: var(--color-surface-hover);
  color: var(--color-text);
}
.settings__hint {
  margin-top: 10px;
  font-size: var(--fs-11);
  color: var(--color-text-tertiary);
  line-height: 1.6;
}
.settings__label--danger {
  color: var(--color-danger);
}
.settings__danger {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 14px;
  border: 1px solid var(--color-danger);
  border-radius: var(--radius-md);
  background: var(--color-danger-soft);
}
.settings__danger-text,
.settings__danger-warn {
  font-size: var(--fs-12);
  line-height: 1.6;
}
.settings__danger-text {
  color: var(--color-text);
}
.settings__danger-warn {
  color: var(--color-danger);
}
.settings__danger-warn code {
  font-family: ui-monospace, Consolas, monospace;
}
.settings__danger-row {
  display: flex;
  gap: 8px;
  margin-top: 2px;
}
.settings__danger-row .settings__input {
  flex: 1;
}
.settings__danger-btn {
  background: var(--color-danger);
  border: 1px solid var(--color-danger);
  color: var(--color-on-accent);
  white-space: nowrap;
}
.settings__danger-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.settings__chip--mono {
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
}
.settings__avatar {
  display: flex;
  align-items: center;
  gap: 16px;
}
.settings__avatar-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 52px;
  height: 52px;
  border-radius: 50%;
  background: var(--gradient-logo);
  color: #fff;
  font-size: var(--fs-20);
  font-weight: 700;
}
.settings__avatar-img {
  width: 52px;
  height: 52px;
  border-radius: 50%;
  object-fit: cover;
}
.settings__avatar-actions {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.settings__avatar-hint {
  font-size: var(--fs-11);
  color: var(--color-text-tertiary);
}
.settings__file {
  display: none;
}
.settings__password {
  display: flex;
  gap: 8px;
}
.settings__input {
  width: min(320px, 100%);
  height: 38px;
  padding: 0 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  font-size: var(--fs-13);
  outline: none;
}
.settings__input:focus {
  border-color: rgba(86, 88, 212, 0.5);
}

@media (max-width: 700px) {
  .settings {
    padding: 24px 14px 40px;
  }
  .settings__layout {
    grid-template-columns: 1fr;
  }
  .settings__nav {
    flex-direction: row;
    flex-wrap: wrap;
  }
}

.spin {
  animation: rot 0.9s linear infinite;
}
@keyframes rot {
  to {
    transform: rotate(360deg);
  }
}
</style>
