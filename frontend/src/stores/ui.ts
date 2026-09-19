import { defineStore } from 'pinia'
import { ref } from 'vue'

export type ThemeMode = 'light' | 'dark'

const THEME_KEY = 'agent-work-theme'

export const useUiStore = defineStore('ui', () => {
  const sidebarCollapsed = ref(false)
  /** 平板/移动端是否打开抽屉侧栏 */
  const drawerOpen = ref(false)
  const theme = ref<ThemeMode>('light')

  function applyTheme() {
    document.documentElement.dataset.theme = theme.value
  }

  function setTheme(next: ThemeMode) {
    theme.value = next
    localStorage.setItem(THEME_KEY, next)
    applyTheme()
  }

  function toggleTheme() {
    setTheme(theme.value === 'dark' ? 'light' : 'dark')
  }

  /** 应用启动时恢复主题：本地偏好优先，其次跟随系统 */
  function initTheme() {
    const saved = localStorage.getItem(THEME_KEY)
    if (saved === 'dark' || saved === 'light') {
      theme.value = saved
    } else if (window.matchMedia?.('(prefers-color-scheme: dark)').matches) {
      theme.value = 'dark'
    }
    applyTheme()
  }

  function toggleSidebar() {
    sidebarCollapsed.value = !sidebarCollapsed.value
  }

  return {
    sidebarCollapsed,
    drawerOpen,
    theme,
    toggleSidebar,
    setTheme,
    toggleTheme,
    initTheme
  }
})
