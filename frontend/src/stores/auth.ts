import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { userApi } from '@/api/user'
import { assetUrl, setToken } from '@/api/http'
import { clearWorkflowChoices } from '@/utils/workflowChoice'
import type { ApiUser } from '@/types/api'
import type { User } from '@/types/auth'

const STORAGE_KEY = 'agent-work-user'

/**
 * 头像地址归一化。后端三种形态都要能吃下（接口文档 §1.2 / §1.6）：
 *  - 完整 URL：`http://127.0.0.1:8000/avatars/user_9_avatar.png`（upload-avatar 写死了 host，**不能直接用**）
 *  - 绝对路径：`/avatars/xxx.png`
 *  - 裸文件名：`user_9_avatar.png`
 * 统一存成「站内路径」，渲染时再交给 assetUrl 拼 base。
 */
function normalizeAvatar(raw?: string | null): string | null {
  if (!raw) return null
  const s = raw.trim()
  if (!s) return null
  if (/^https?:\/\//i.test(s)) {
    try {
      return new URL(s).pathname
    } catch {
      return null
    }
  }
  return s.startsWith('/') ? s : `/avatars/${s}`
}

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const loading = ref(false)

  const isLoggedIn = computed(() => user.value !== null)

  /** 头像可直接绑定到 <img :src>（已归一化 + 拼好 base） */
  const avatarUrl = computed(() => (user.value?.avatar ? assetUrl(user.value.avatar) : null))

  function init() {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return
    try {
      user.value = JSON.parse(raw) as User
    } catch {
      localStorage.removeItem(STORAGE_KEY)
    }
  }

  function persist(next: User) {
    user.value = next
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
  }

  function toUser(source: { id: number; user_name: string; avatar?: string | null }): User {
    return { id: source.id, user_name: source.user_name, avatar: normalizeAvatar(source.avatar) }
  }

  /** 用户名 + 密码登录 */
  async function login(userName: string, password: string): Promise<void> {
    loading.value = true
    try {
      const res = await userApi.login(userName, password)
      // token 是身份凭证；后端没返回（旧版）就清空，退回 ?user_id= 兼容路径
      setToken(res.token ?? null)
      persist(toUser({ id: res.user_id, user_name: res.user_name, avatar: res.avatar }))
    } finally {
      loading.value = false
    }
  }

  /** 注册（后端创建用户后直接视为登录，并返回 token） */
  async function register(userName: string, password: string): Promise<void> {
    loading.value = true
    try {
      const created = await userApi.create(userName, password)
      setToken(created.token ?? null)
      persist(toUser({ id: created.id, user_name: created.user_name, avatar: created.avatar }))
    } finally {
      loading.value = false
    }
  }

  async function updateAvatar(file: File): Promise<void> {
    if (!user.value) return
    const res = await userApi.uploadAvatar(user.value.id, file)
    // 忽略 avatar_url 里写死的 host，只取路径
    persist({ ...user.value, avatar: normalizeAvatar(res.avatar_url) })
  }

  async function updateProfile(patch: { user_name?: string; password?: string }): Promise<void> {
    if (!user.value) return
    const updated = await userApi.update(user.value.id, patch)
    persist(toUser({ id: updated.id, user_name: updated.user_name, avatar: updated.avatar }))
  }

  /**
   * 从后端拉一次账号信息（GET /api/users/{id}，§1.3）。
   * ⚠️ 响应体里会回显明文 password —— 只取需要的字段，**不要存、不要展示**。
   */
  async function refreshProfile(): Promise<ApiUser> {
    if (!user.value) throw new Error('未登录')
    const remote = await userApi.get(user.value.id)
    persist(toUser({ id: remote.id, user_name: remote.user_name, avatar: remote.avatar }))
    return remote
  }

  /**
   * 注销账号（DELETE /api/users/{id}，§1.5）：库行由外键级联删除，
   * ⚠️ 但**磁盘产物不会清理**（exports/u<user_id>/ 会保留）。
   * 成功后本地登录态与 token 一并清空。
   */
  async function deleteAccount(): Promise<void> {
    if (!user.value) throw new Error('未登录')
    await userApi.remove(user.value.id)
    user.value = null
    localStorage.removeItem(STORAGE_KEY)
    setToken(null)
    clearWorkflowChoices()
  }

  /** 退出登录：清登录态 + token + 与账号相关的本地偏好（主题等设备级偏好保留） */
  function logout() {
    user.value = null
    localStorage.removeItem(STORAGE_KEY)
    setToken(null)
    clearWorkflowChoices()
  }

  return {
    user,
    loading,
    isLoggedIn,
    avatarUrl,
    init,
    login,
    register,
    updateAvatar,
    updateProfile,
    refreshProfile,
    deleteAccount,
    logout
  }
})
