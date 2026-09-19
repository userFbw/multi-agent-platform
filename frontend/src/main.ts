import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import { useAuthStore } from './stores/auth'
import { useUiStore } from './stores/ui'
import { setUnauthorizedHandler } from './api/http'
import './styles/index.css'

const app = createApp(App)
const pinia = createPinia()

app.use(pinia)
app.use(router)

// 从 localStorage 恢复登录态（Mock）
useAuthStore().init()

// 恢复主题偏好（浅色/深色）
useUiStore().initTheme()

// token 失效（后端返回 401）→ 清登录态并回登录页，而不是让页面一直报错
const auth = useAuthStore()
setUnauthorizedHandler(() => {
  if (!auth.isLoggedIn) return // 登录接口本身的 401（密码错）不算会话失效
  auth.logout()
  void router.replace({ name: 'login' })
})

app.mount('#app')
