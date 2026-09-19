import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import AppLayout from '@/components/layout/AppLayout.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/LoginView.vue'),
      meta: { public: true }
    },
    {
      path: '/',
      component: AppLayout,
      children: [
        { path: '', name: 'home', component: () => import('@/views/HomeView.vue') },
        { path: 'tasks', name: 'tasks', component: () => import('@/views/TaskListView.vue') },
        { path: 'tasks/:id', name: 'task-detail', component: () => import('@/views/TaskDetailView.vue') },
        { path: 'workflow', name: 'workflow', component: () => import('@/views/WorkflowView.vue') },
        // 「Agent」页就是原来的「技能」页（两页职责重叠，只留一个）
        { path: 'agents', name: 'agents', component: () => import('@/views/SkillsView.vue') },
        { path: 'stats', name: 'stats', component: () => import('@/views/StatsView.vue') },
        { path: 'skills', redirect: { name: 'agents' } },   // 旧路径保持可用
        { path: 'settings', name: 'settings', component: () => import('@/views/SettingsView.vue') }
      ]
    },
    { path: '/:pathMatch(.*)*', redirect: '/' }
  ]
})

router.beforeEach((to) => {
  const auth = useAuthStore()
  if (!to.meta.public && !auth.isLoggedIn) return { name: 'login' }
  if (to.name === 'login' && auth.isLoggedIn) return { name: 'home' }
  return true
})

export default router
